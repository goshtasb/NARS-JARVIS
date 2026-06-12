"""ADR-056 Phase 1: the egress seam + contextual firewall. No real network — the HTTP transport is
injected. Proves the seam can't leak private context, logs every call, and intercepts provider errors."""
import json
import re

import cloud_egress
from cloud_egress import CloudRequest, CloudResult, ExternalTool, openai_complete


def _fake(status: int, body: dict):
    """A transport that records the outbound payload and returns a canned (status, body)."""
    sent: dict = {}
    def transport(url, headers, payload, timeout):
        sent["url"] = url; sent["headers"] = headers; sent["payload"] = payload; sent["timeout"] = timeout
        return status, json.dumps(body).encode()
    return transport, sent


def setup_function(_): cloud_egress.clear_egress_log()


# ── the contextual firewall ──
def test_seam_imports_no_private_stores() -> None:
    # The firewall is the ABSENCE of any way to read private data — assert the actual imports stay clean
    # (parse the AST so the docstring mentioning these words doesn't trip it).
    import ast
    tree = ast.parse(open(cloud_egress.__file__).read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names: imported.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"memory", "persona", "sentinel", "brain", "overnight", "language", "service"}
    assert not (imported & forbidden), f"egress seam imports private modules: {imported & forbidden}"


def test_only_request_fields_leave_the_machine() -> None:
    # A CloudRequest carries only system/user/tools/schema. Seed "private" strings in NOTHING the request
    # exposes, and confirm the outbound payload is built from exactly the allowed fields.
    transport, sent = _fake(200, {"choices": [{"message": {"content": "ok"}}]})
    req = CloudRequest(system="Answer the question.", user="what's the weather in Paris?",
                       tools=[ExternalTool("search_web", "Search the web", {"type": "object", "properties": {}})])
    res = openai_complete(req, api_key="sk-test", now=1000.0, transport=transport)
    assert res.ok and res.text == "ok"
    body = sent["payload"]
    # exactly the allowlisted content, nothing else private
    assert body["messages"][0]["content"] == "Answer the question."
    assert body["messages"][1]["content"] == "what's the weather in Paris?"
    assert body["tools"][0]["function"]["name"] == "search_web"
    serialized = json.dumps(body)
    for leak in ["usage_events", "Cursor", "Cognitive Identity", "RelationClaim", "/Users/"]:
        assert leak not in serialized          # no private context could ride along


def test_strict_json_schema_is_passed_through() -> None:
    transport, sent = _fake(200, {"choices": [{"message": {"content": "{\"action\":\"x\"}"}}]})
    schema = {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"], "additionalProperties": False}
    openai_complete(CloudRequest(system="s", user="u", json_schema=schema), api_key="k", now=1.0, transport=transport)
    rf = sent["payload"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == schema


# ── the egress log (auditable) ──
def test_egress_log_records_every_call() -> None:
    transport, _ = _fake(200, {"choices": [{"message": {"content": "ok"}}]})
    openai_complete(CloudRequest(system="s", user="hello world"), api_key="k", now=42.0, transport=transport)
    log = cloud_egress.egress_log()
    assert len(log) == 1
    rec = log[0]
    assert rec["provider"] == "openai" and rec["t"] == 42.0 and rec["bytes"] > 0
    assert rec["preview"] == "hello world"


# ── checklist: no key, structured failure interception, timeout ──
def test_missing_key_never_calls_the_network() -> None:
    called = {"n": 0}
    def transport(*a): called["n"] += 1; return 200, b"{}"
    res = openai_complete(CloudRequest(system="s", user="u"), api_key="", transport=transport)
    assert not res.ok and res.kind == "auth" and called["n"] == 0


def test_intercepts_rate_limit_and_auth_errors() -> None:
    t429, _ = _fake(429, {"error": {"type": "rate_limit_exceeded", "message": "slow down"}})
    assert openai_complete(CloudRequest(system="s", user="u"), api_key="k", transport=t429).kind == "rate_limit"
    t401, _ = _fake(401, {"error": {"type": "invalid_api_key", "message": "bad key"}})
    assert openai_complete(CloudRequest(system="s", user="u"), api_key="k", transport=t401).kind == "auth"


def test_intercepts_network_timeout() -> None:
    def boom(*a): raise TimeoutError("dropped")
    res = openai_complete(CloudRequest(system="s", user="u"), api_key="k", transport=boom)
    assert not res.ok and res.kind == "timeout" and "Private" in res.error    # graceful recovery message
    assert cloud_egress.EGRESS_TIMEOUT == 12.0


def test_bad_response_body_is_handled() -> None:
    def garbage(*a): return 200, b"not json"
    assert openai_complete(CloudRequest(system="s", user="u"), api_key="k", transport=garbage).kind == "bad_response"
