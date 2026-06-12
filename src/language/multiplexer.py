"""The Dual-Brain Multiplexer (ADR-056, Vector 4) — Imperative Shell (S-02).

Presents the EXACT three-method surface the daemon already calls on `LocalLLM`
(`generate` / `generate_json` / `generate_text`), and routes each call to the active brain:

- **Private mode (default):** delegates verbatim to the local llama.cpp `LocalLLM` — the unchanged path.
- **General mode:** builds a closed `CloudRequest` and sends it through `cloud_egress` (the only egress
  seam). Local-only tools never reach the seam (the firewall is structural — see cloud_egress).

**Output unification** is the contract that keeps every caller brain-agnostic:
- `generate` (NARS claims): local emits a top-level JSON *array* via GBNF; the cloud strict schema must be
  object-rooted, so it returns `{"claims":[…]}` — the Multiplexer **unwraps it back to the bare array**,
  so `parse_claims` runs identically on either brain.
- `generate_json` (intent): both brains return the same intent object; cloud uses the caller-supplied
  strict `json_schema` (GBNF parity). `validate_intent` runs unchanged.
- `generate_text` (voice/answers): both return a string; `sanitize_voice` runs unchanged.

The Multiplexer itself runs the cloud call *synchronously* (it is pure routing). Keeping that call OFF the
daemon's select loop is the separate concern of `service.cloud_job.CloudJob`, which runs any callable —
including a Multiplexer cloud call — in a background thread. This separation is deliberate: the
Multiplexer decides *which brain and unifies the output*; CloudJob decides *how to not block the loop*.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

import cloud_egress
from cloud_egress import CloudRequest, CloudResult


class CloudError(Exception):
    """A cloud call failed (auth / rate-limit / timeout / bad-response). Carries the CloudResult so the
    off-loop handler can project the right recovery card instead of the daemon crashing."""
    def __init__(self, result: CloudResult):
        super().__init__(result.error or result.kind or "cloud error")
        self.result = result


# ── the fixed JSON schema for NARS claim extraction (object-rooted for strict mode; unwrapped on return) ──
def _relation(type_name: str) -> dict:
    return {"type": "object",
            "properties": {"type": {"type": "string", "enum": [type_name]},
                           "subject": {"type": "string"}, "verb": {"type": "string"}, "object": {"type": "string"}},
            "required": ["type", "subject", "verb", "object"], "additionalProperties": False}


def _property(type_name: str) -> dict:
    return {"type": "object",
            "properties": {"type": {"type": "string", "enum": [type_name]},
                           "subject": {"type": "string"}, "value": {"type": "string"}},
            "required": ["type", "subject", "value"], "additionalProperties": False}


CLAIMS_JSON_SCHEMA = {
    "type": "object",
    "properties": {"claims": {"type": "array", "items": {"anyOf": [
        _relation("RelationClaim"), _relation("NegatedRelationClaim"),
        _property("PropertyClaim"), _property("NegatedPropertyClaim"),
    ]}}},
    "required": ["claims"], "additionalProperties": False,
}


@dataclass
class CloudContext:
    """The per-request cloud credentials/selection. Set from the socket request (ADR-056: the daemon is
    credential-stateless — the key lives only here, for the life of one request, then is cleared)."""
    mode: str = "private"            # "private" | "general"
    key: str = ""
    provider: str = "openai"         # "openai" | "anthropic"
    model: str = ""                  # "" -> driver default


# cloud_dispatch(req, *, provider, key, model, now) -> CloudResult  (injected in tests; real one below)
CloudDispatch = Callable[..., CloudResult]


def _real_dispatch(req: CloudRequest, *, provider: str, key: str, model: str, now: Optional[float] = None) -> CloudResult:
    if provider == "anthropic":
        return cloud_egress.anthropic_complete(req, api_key=key, **({"model": model} if model else {}), now=now)
    return cloud_egress.openai_complete(req, api_key=key, **({"model": model} if model else {}), now=now)


class Multiplexer:
    """The injected `Brain`. Same surface as LocalLLM; routes by the active CloudContext."""

    def __init__(self, local, cloud_dispatch: CloudDispatch = _real_dispatch):
        self._local = local
        self._dispatch = cloud_dispatch
        self._ctx = CloudContext()

    # ── per-request context (set/cleared by the session around each request) ──
    def set_cloud(self, *, mode: str, key: str = "", provider: str = "openai", model: str = "") -> None:
        self._ctx = CloudContext(mode=mode, key=key, provider=provider, model=model)

    def clear_cloud(self) -> None:
        self._ctx = CloudContext()      # key gone (daemon stays credential-stateless)

    @property
    def is_cloud(self) -> bool:
        return self._ctx.mode == "general"

    def _cloud(self, req: CloudRequest) -> CloudResult:
        res = self._dispatch(req, provider=self._ctx.provider, key=self._ctx.key, model=self._ctx.model)
        if not res.ok:
            raise CloudError(res)
        return res

    def cloud_complete(self, req: CloudRequest, *, key: str, provider: str = "openai",
                       model: str = "") -> CloudResult:
        """A one-shot cloud call with EXPLICIT credentials that does NOT read/write the shared per-request
        context — so it is safe to run inside the off-loop CloudJob thread while the main loop keeps
        serving other requests (no race on `_ctx`). Returns the raw CloudResult (ok or error) so the
        failure `kind` survives into the recovery card; it does not raise."""
        return self._dispatch(req, provider=provider, key=key, model=model)

    # ── the three brain methods (identical signatures to LocalLLM, + optional json_schema for cloud) ──
    def generate(self, system_prompt: str, sentence: str) -> str:
        """NARS claim extraction. Returns a top-level JSON array string (unified across brains)."""
        if not self.is_cloud:
            return self._local.generate(system_prompt, sentence)
        res = self._cloud(CloudRequest(system=system_prompt, user=sentence, json_schema=CLAIMS_JSON_SCHEMA))
        obj = json.loads(res.text or "{}")                      # cloud strict mode -> {"claims":[…]}
        return json.dumps(obj.get("claims", []))                # unwrap to the bare array (local shape)

    def generate_json(self, system_prompt: str, user: str, grammar_text: str,
                      max_tokens: int = 256, json_schema: Optional[dict] = None) -> str:
        """Structured intent. Local uses the GBNF `grammar_text`; cloud uses the strict `json_schema`
        (the caller supplies the schema form alongside the grammar). Both return the same object string."""
        if not self.is_cloud:
            return self._local.generate_json(system_prompt, user, grammar_text, max_tokens)
        res = self._cloud(CloudRequest(system=system_prompt, user=user, json_schema=json_schema, max_tokens=max_tokens))
        return res.text

    def generate_text(self, system_prompt: str, user: str, max_tokens: int = 64) -> str:
        """Free text (voice formatter / answers)."""
        if not self.is_cloud:
            return self._local.generate_text(system_prompt, user, max_tokens)
        res = self._cloud(CloudRequest(system=system_prompt, user=user, max_tokens=max_tokens))
        return res.text

    # pass-through for any other attribute the local model exposes (to_claims, embedder hooks, …)
    def __getattr__(self, name: str):
        return getattr(self._local, name)
