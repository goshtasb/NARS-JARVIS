"""ADR-056 Phase 2: the Multiplexer routing + output unification. Proves a caller cannot tell which brain
ran — the parsed result is identical for local GBNF and cloud strict-JSON. No network (cloud dispatch is
injected); the local brain is a fake recording delegate."""
import json

import pytest

from language.multiplexer import CLAIMS_JSON_SCHEMA, CloudError, Multiplexer
from language.schema import parse_claims
from cloud_egress import CloudRequest, CloudResult


class FakeLocal:
    """Records what the local path was asked, returns canned local-shaped output."""
    def __init__(self):
        self.calls = []
    def generate(self, system, sentence):
        self.calls.append(("generate", system, sentence))
        return '[{"type":"RelationClaim","subject":"Tim","verb":"IsA","object":"duck"}]'
    def generate_json(self, system, user, grammar, max_tokens=256):
        self.calls.append(("generate_json", system, user, grammar, max_tokens))
        return '{"action":"local_action"}'
    def generate_text(self, system, user, max_tokens=64):
        self.calls.append(("generate_text", system, user, max_tokens))
        return "local text"
    def to_claims(self, system, sentence):     # an extra method, to prove pass-through
        return ["passthrough"]


def _cloud(returns):
    """A fake cloud dispatch that records the CloudRequest and returns a canned CloudResult."""
    seen = {}
    def dispatch(req: CloudRequest, *, provider, key, model, now=None):
        seen["req"] = req; seen["provider"] = provider; seen["key"] = key; seen["model"] = model
        return returns
    return dispatch, seen


# ── private mode: verbatim delegation (the unchanged path) ──
def test_private_mode_delegates_to_local_unchanged():
    local = FakeLocal()
    m = Multiplexer(local)                                   # default mode is private
    assert not m.is_cloud
    assert parse_claims(m.generate("sys", "Tim is a duck.")) == parse_claims(local.generate("sys", "Tim is a duck."))
    assert m.generate_json("sys", "u", "GRAMMAR") == '{"action":"local_action"}'
    assert m.generate_text("sys", "u") == "local text"
    assert m.to_claims("sys", "x") == ["passthrough"]        # __getattr__ pass-through
    assert [c[0] for c in local.calls] == ["generate", "generate", "generate_json", "generate_text"]


# ── output unification: cloud claims unwrap to the SAME parsed objects as local ──
def test_cloud_claims_unwrap_matches_local_parse():
    # cloud strict mode is object-rooted: {"claims":[…]}. The Multiplexer must unwrap to the bare array so
    # parse_claims runs identically. Use the same claim the local fake returns -> identical parsed result.
    cloud_payload = json.dumps({"claims": [{"type": "RelationClaim", "subject": "Tim", "verb": "IsA", "object": "duck"}]})
    dispatch, seen = _cloud(CloudResult(ok=True, text=cloud_payload))
    m = Multiplexer(FakeLocal(), cloud_dispatch=dispatch)
    m.set_cloud(mode="general", key="sk-x", provider="openai")
    assert m.is_cloud

    out = m.generate("Extract Narsese.", "Tim is a duck.")
    assert json.loads(out) == [{"type": "RelationClaim", "subject": "Tim", "verb": "IsA", "object": "duck"}]
    assert parse_claims(out) == parse_claims(FakeLocal().generate("s", "x"))     # brain-indistinguishable
    # the seam was handed the fixed claims schema and only the allowed fields
    assert seen["req"].json_schema is CLAIMS_JSON_SCHEMA
    assert seen["req"].system == "Extract Narsese." and seen["req"].user == "Tim is a duck."
    assert seen["key"] == "sk-x" and seen["provider"] == "openai"


def test_cloud_generate_json_uses_strict_schema():
    dispatch, seen = _cloud(CloudResult(ok=True, text='{"action":"cloud_action"}'))
    m = Multiplexer(FakeLocal(), cloud_dispatch=dispatch)
    m.set_cloud(mode="general", key="k", provider="anthropic", model="claude-3-5-sonnet-latest")
    schema = {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"], "additionalProperties": False}
    out = m.generate_json("sys", "do x", "LOCAL_GRAMMAR_IGNORED_IN_CLOUD", json_schema=schema)
    assert json.loads(out) == {"action": "cloud_action"}
    assert seen["req"].json_schema is schema and seen["provider"] == "anthropic" and seen["model"] == "claude-3-5-sonnet-latest"


def test_cloud_generate_text_passes_through():
    dispatch, seen = _cloud(CloudResult(ok=True, text="cloud answer"))
    m = Multiplexer(FakeLocal(), cloud_dispatch=dispatch)
    m.set_cloud(mode="general", key="k")
    assert m.generate_text("sys", "question", max_tokens=200) == "cloud answer"
    assert seen["req"].max_tokens == 200


# ── failures surface as CloudError (for the recovery card), never a silent bad value ──
def test_cloud_failure_raises_cloud_error():
    dispatch, _ = _cloud(CloudResult(ok=False, kind="rate_limit", error="slow down"))
    m = Multiplexer(FakeLocal(), cloud_dispatch=dispatch)
    m.set_cloud(mode="general", key="k")
    with pytest.raises(CloudError) as ei:
        m.generate("s", "x")
    assert ei.value.result.kind == "rate_limit" and "slow down" in str(ei.value)


# ── credential-stateless: clear_cloud drops the key and reverts to local ──
def test_clear_cloud_drops_key_and_reverts_to_local():
    local = FakeLocal()
    m = Multiplexer(local, cloud_dispatch=_cloud(CloudResult(ok=True, text="{}"))[0])
    m.set_cloud(mode="general", key="sk-secret")
    assert m.is_cloud and m._ctx.key == "sk-secret"
    m.clear_cloud()
    assert not m.is_cloud and m._ctx.key == ""                 # key gone from memory
    m.generate_text("s", "u")                                  # routes local again
    assert local.calls[-1][0] == "generate_text"
