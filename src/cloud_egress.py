"""The single sanctioned network egress seam (ADR-056) — Imperative Shell (S-02). The `safespawn` of the
network: the ONLY place in the codebase that POSTs to a third-party model provider.

Privacy is enforced by CONSTRUCTION, not by discipline:
- This module imports NONE of the private stores (memory / persona / sentinel / brain / overnight /
  usage). It literally cannot read the Cognitive-Identity baseline, the NARS graph, `usage_events`, the
  persona, or the grounding cache — so it cannot leak them (a test asserts the import list stays clean).
- The outbound payload is built field-by-field from a CLOSED `CloudRequest` envelope (allowlist, no
  pass-through path), so an extra field cannot ride along.
- Every call appends an auditable record to the egress log (what left, how big, where to).

Custom urllib HTTP (no LiteLLM — a privacy-first app must keep egress small and auditable). Hard 12 s
timeout. Never raises to the caller — returns a `CloudResult` so the Multiplexer can project a recovery
card instead of crashing/hanging (ADR-056 checklist).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

EGRESS_TIMEOUT = 12.0   # hard network cap — no zombie workers on a dropped/handed-off connection


@dataclass(frozen=True)
class ExternalTool:
    """An EXTERNAL-only tool the cloud brain may call (search_web, read_article, …). Local tools
    (summarize_file, report_usage, set_volume, …) are stripped by the Multiplexer and never reach here."""
    name: str
    description: str
    parameters: dict


@dataclass(frozen=True)
class CloudRequest:
    """The ONLY shape the egress seam accepts. There is no field for persona / usage / NARS / grounding /
    file contents — the contextual firewall is the absence of any way to express them."""
    system: str
    user: str
    tools: list = field(default_factory=list)          # [ExternalTool] — external-only
    json_schema: Optional[dict] = None                 # strict structured output (intent / NARS claims)
    max_tokens: int = 1024


@dataclass(frozen=True)
class CloudResult:
    ok: bool
    text: str = ""
    error: str = ""          # human-facing recovery message (shown in the Chat recovery card)
    kind: str = ""           # "" | "auth" | "rate_limit" | "timeout" | "network" | "bad_response"


# Transport seam — injected so the test suite never touches the real network.
# (url, headers, json-body, timeout) -> (http_status, raw_bytes)
HTTPTransport = Callable[[str, dict, dict, float], "tuple[int, bytes]"]


def _urllib_post(url: str, headers: dict, body: dict, timeout: float) -> "tuple[int, bytes]":
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:        # noqa: S310 — one vetted endpoint
            return r.getcode(), r.read()
    except urllib.error.HTTPError as e:                                # 4xx/5xx carry a JSON error body
        return e.code, e.read()
    # URLError / socket.timeout propagate -> caller maps to a timeout/network CloudResult


# ── the auditable egress log ──
_EGRESS_LOG: list[dict] = []


def egress_log() -> list[dict]:
    """A copy of the records of everything that has left the machine this session (for the UI ledger)."""
    return list(_EGRESS_LOG)


def clear_egress_log() -> None:
    _EGRESS_LOG.clear()


def _record(provider: str, endpoint: str, payload: dict, req: CloudRequest, now: float) -> None:
    _EGRESS_LOG.append({
        "t": now,
        "provider": provider,
        "endpoint": endpoint,
        "bytes": len(json.dumps(payload)),
        "tools": [t.name for t in req.tools],
        "preview": (req.user or "")[:80],     # the user's own query — it's what left, by definition
    })


# ── OpenAI driver (provider #1) ──
def openai_complete(req: CloudRequest, *, api_key: str, model: str = "gpt-4o-mini",
                    now: Optional[float] = None, transport: HTTPTransport = _urllib_post) -> CloudResult:
    """Build an OpenAI chat-completions payload from ONLY the CloudRequest fields, POST it, parse the
    result. Never raises. Maps provider errors (auth / rate-limit / network) to a CloudResult."""
    t = time.time() if now is None else now
    if not api_key:
        return CloudResult(ok=False, kind="auth",
                           error="No API key set — add one in Settings to use Cloud mode (or switch to Private).")
    payload: dict = {
        "model": model,
        "messages": [{"role": "system", "content": req.system},
                     {"role": "user", "content": req.user}],
        "max_tokens": req.max_tokens,
        "temperature": 0,
    }
    if req.json_schema is not None:                       # strict structured output (GBNF parity)
        payload["response_format"] = {"type": "json_schema",
                                      "json_schema": {"name": "out", "strict": True, "schema": req.json_schema}}
    if req.tools:                                         # external-only tool schemas
        payload["tools"] = [{"type": "function",
                             "function": {"name": x.name, "description": x.description, "parameters": x.parameters}}
                            for x in req.tools]
    _record("openai", "/v1/chat/completions", payload, req, t)
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    try:
        status, raw = transport("https://api.openai.com/v1/chat/completions", headers, payload, EGRESS_TIMEOUT)
    except Exception:  # noqa: BLE001 — timeout / DNS / connection drop (cellular handoff etc.)
        return CloudResult(ok=False, kind="timeout",
                           error="The cloud request timed out or the network dropped. Retry, or switch to Private mode.")
    return _parse_openai(status, raw)


def _parse_openai(status: int, raw: bytes) -> CloudResult:
    try:
        body = json.loads(raw or b"{}")
    except Exception:  # noqa: BLE001
        return CloudResult(ok=False, kind="bad_response", error="The cloud returned an unreadable response.")
    if status == 200:
        try:
            text = (body["choices"][0]["message"].get("content") or "")
            return CloudResult(ok=True, text=text)
        except Exception:  # noqa: BLE001
            return CloudResult(ok=False, kind="bad_response", error="The cloud response was missing content.")
    return _error_result(status, body.get("error") or {})


# ── Anthropic driver (provider #2, ADR-056 ruling) ──
def anthropic_complete(req: CloudRequest, *, api_key: str, model: str = "claude-3-5-sonnet-latest",
                       now: Optional[float] = None, transport: HTTPTransport = _urllib_post) -> CloudResult:
    """Same contract as openai_complete, mapped to Anthropic's Messages API. Structured output is forced
    via a single tool (`tool_choice`), and its `input` is serialized to a JSON string so the Multiplexer
    sees the IDENTICAL output shape as OpenAI (a JSON string the caller parses). Never raises."""
    t = time.time() if now is None else now
    if not api_key:
        return CloudResult(ok=False, kind="auth",
                           error="No API key set — add one in Settings to use Cloud mode (or switch to Private).")
    payload: dict = {
        "model": model, "max_tokens": req.max_tokens, "system": req.system,
        "messages": [{"role": "user", "content": req.user}],
    }
    if req.json_schema is not None:                       # strict structured output via a forced tool
        payload["tools"] = [{"name": "out", "description": "Return the structured result.",
                             "input_schema": req.json_schema}]
        payload["tool_choice"] = {"type": "tool", "name": "out"}
    elif req.tools:
        payload["tools"] = [{"name": x.name, "description": x.description, "input_schema": x.parameters}
                            for x in req.tools]
    _record("anthropic", "/v1/messages", payload, req, t)
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    try:
        status, raw = transport("https://api.anthropic.com/v1/messages", headers, payload, EGRESS_TIMEOUT)
    except Exception:  # noqa: BLE001
        return CloudResult(ok=False, kind="timeout",
                           error="The cloud request timed out or the network dropped. Retry, or switch to Private mode.")
    return _parse_anthropic(status, raw, structured=req.json_schema is not None)


def _parse_anthropic(status: int, raw: bytes, structured: bool) -> CloudResult:
    try:
        body = json.loads(raw or b"{}")
    except Exception:  # noqa: BLE001
        return CloudResult(ok=False, kind="bad_response", error="The cloud returned an unreadable response.")
    if status == 200:
        for block in body.get("content", []):
            if structured and block.get("type") == "tool_use":
                return CloudResult(ok=True, text=json.dumps(block.get("input", {})))   # -> JSON string (unified)
            if not structured and block.get("type") == "text":
                return CloudResult(ok=True, text=block.get("text", ""))
        return CloudResult(ok=False, kind="bad_response", error="The cloud response had no usable content.")
    return _error_result(status, body.get("error") or {})


def _error_result(status: int, err: dict) -> CloudResult:
    etype = str(err.get("type") or err.get("code") or "")
    msg = str(err.get("message") or f"HTTP {status}")
    if status in (401, 403) or "invalid_api_key" in etype or "authentication" in etype:
        return CloudResult(ok=False, kind="auth", error="Your API key was rejected — check it in Settings.")
    if status == 429 or "rate_limit" in etype or "overloaded" in etype:
        return CloudResult(ok=False, kind="rate_limit", error="Rate-limited by the provider — wait a moment and retry.")
    return CloudResult(ok=False, kind="bad_response", error=f"Cloud error: {msg[:120]}")
