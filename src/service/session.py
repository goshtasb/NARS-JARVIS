"""The headless JARVIS session — all reasoning/wiring, zero terminal I/O.

This is the brain behind the socket: it builds the core (models, gate, grounding, the two brains,
voice, executor) and exposes `dispatch(cmd, arg) -> (ok, body)` returning plain JSON-able data, plus
async work pushed through `on_event`. Both the terminal console and the future SwiftUI app are dumb
clients of this same surface, so reasoning logic can never be polluted by — or duplicated in — a UI.
"""
from __future__ import annotations

import uuid
from typing import Callable

from brain import Brain
from execution import DecisionStats, build_air_gapped_executor, decide
from jarvis import Jarvis
from language import IngestionGate, Translator, Voice
from memory import MemoryStore, MetricsStore, SqliteGroundingStore
from sentinel import SurpriseDetector, SystemSentinel
from sentinel.narrate import Narrator

from .sentinel_loop import SentinelLoop
from .wiring import NoNarrationLLM, make_claim_source, make_embedder

_STRONG = DecisionStats(0.95, 0.97, 30, 12)  # an explicit REPL `act` is a high-confidence request
EventSink = Callable[[str, dict], None]


class Session:
    def __init__(self, db_path: str = "jarvis.db", on_event: EventSink | None = None) -> None:
        self._emit = on_event or (lambda kind, body: None)
        self._store = MemoryStore(db_path)
        self._brain = Brain(cycles_per_step=50)
        self._act_buf: list[str] = []
        self._executor = build_air_gapped_executor(sink=lambda t: self._act_buf.append(str(t)))
        llm = make_claim_source()
        embedder = make_embedder()
        gate = IngestionGate(embedder) if embedder is not None else None
        grounding = SqliteGroundingStore(db_path) if embedder is not None else None
        self._metrics = MetricsStore(db_path, session_id=uuid.uuid4().hex[:8])
        voice = Voice(formatter=llm if hasattr(llm, "generate_text") else None)
        self._jarvis = Jarvis(Translator(llm, embedder=embedder, cache=grounding),
                              self._store, self._brain, executor=self._executor, gate=gate,
                              metrics=self._metrics, voice=voice)
        # M2 system sentinel (CPU/mem surprise) feeds the knowledge brain; alerts push as events.
        narrator = Narrator(NoNarrationLLM(), on_alert=lambda t: self._emit("alert", {"text": "⚠  " + t}))
        self._sys_detector = SurpriseDetector(self._brain, threshold=0.5, on_surprise=narrator.narrate)
        self._sys_sentinel = SystemSentinel(sink=self._sys_detector.observe, poll_interval=2.0)
        self._last = "no poll yet"
        self._flow = SentinelLoop(db_path, self._emit)
        self._pending_learn: dict[str, dict] = {}
        self._pending_act: dict[str, object] = {}
        self._token = 0

    # ── command plane ─────────────────────────────────────────────────
    def dispatch(self, cmd: str, arg: object = "") -> tuple[bool, object]:
        handler = {
            "ask": self._ask, "tell": self._tell, "learn": self._learn,
            "learn_resolve": self._learn_resolve, "act": self._act, "act_confirm": self._act_confirm,
            "status": self._status, "health": self._health, "sentinel": self._sentinel,
            "intervene": self._intervene,
        }.get(cmd)
        if handler is None:
            return False, {"text": f"unknown command: {cmd!r}"}
        return handler(arg)

    def _ask(self, arg: object) -> tuple[bool, object]:
        text = str(arg).strip()
        if not text:
            return False, {"text": "usage: ask <english question> or ask <narsese?>"}
        raw = text.lstrip()
        if "-->" in raw or raw.startswith(("<", "(")):
            answer = self._jarvis.ask(text)
            return True, {"text": f"answer: {answer}" if answer is not None else "no answer in memory."}
        return True, {"text": self._jarvis.converse(text)}

    def _tell(self, arg: object) -> tuple[bool, object]:
        narsese = str(arg).strip()
        if not narsese:
            return False, {"text": "usage: tell <narsese statement.>"}
        try:
            committed = self._jarvis.tell(narsese)
        except Exception as exc:  # noqa: BLE001 — malformed Narsese -> report, don't crash
            return False, {"text": f"invalid narsese: {exc}"}
        return True, {"text": "committed to L2+L1 (durable)." if committed
                              else "deferred (contradiction flagged)."}

    def _learn(self, arg: object) -> tuple[bool, object]:
        sentence = str(arg).strip()
        if not sentence:
            return False, {"text": "usage: learn <english sentence>"}
        rejects: list[dict] = []
        escalations: list[dict] = []
        def on_rejects(items):
            rejects.extend({"mirror": it.english_mirror, "reason": it.reason, "cosine": it.cosine}
                           for it in items)
        def confirm(item):
            escalations.append({"eid": len(escalations), "mirror": item.english_mirror,
                                "cosine": item.cosine, "statement": item.statement})
            return False  # defer to phase B (client asks the human, then learn_resolve commits)
        committed = self._jarvis.learn(sentence, on_rejects=on_rejects, confirm_escalation=confirm)
        token = ""
        if escalations:
            self._token += 1; token = f"L{self._token}"
            self._pending_learn[token] = {"sentence": sentence,
                                          "by_eid": {e["eid"]: e["statement"] for e in escalations}}
        return True, {"committed": committed, "rejects": rejects,
                      "escalations": escalations, "token": token}

    def _learn_resolve(self, arg: object) -> tuple[bool, object]:
        if not isinstance(arg, dict):
            return False, {"text": "learn_resolve expects {token, accept}"}
        pend = self._pending_learn.pop(arg.get("token", ""), None)
        if pend is None:
            return False, {"text": "unknown or expired escalation token"}
        committed: list[str] = []
        for eid in arg.get("accept", []):
            stmt = pend["by_eid"].get(eid)
            if stmt:
                s = self._jarvis.commit_approved(stmt, pend["sentence"])
                if s:
                    committed.append(s)
        return True, {"committed": committed}

    def _act(self, arg: object) -> tuple[bool, object]:
        parts = str(arg).split()
        if len(parts) != 2:
            return False, {"text": "usage: act <op_name> <arg_name>"}
        try:
            proposal = decide(parts[0], parts[1], _STRONG)
        except Exception as exc:  # noqa: BLE001 — surface the security rejection
            return False, {"text": f"rejected: {exc}"}
        self._act_buf = []
        try:
            self._executor.execute(proposal)
        except Exception as exc:  # noqa: BLE001
            return False, {"text": f"execution error: {exc}"}
        needs = not (proposal.autonomous and self._executor.is_live_eligible(proposal.operation))
        token = ""
        if needs:
            self._token += 1; token = f"A{self._token}"
            self._pending_act[token] = proposal
        return True, {"lines": list(self._act_buf), "needs_confirm": needs, "token": token}

    def _act_confirm(self, arg: object) -> tuple[bool, object]:
        proposal = self._pending_act.pop(arg.get("token", "") if isinstance(arg, dict) else "", None)
        if proposal is None:
            return False, {"text": "unknown action token"}
        self._act_buf = []
        try:
            self._executor.execute_approved(proposal)
        except Exception as exc:  # noqa: BLE001
            return False, {"text": f"execution error: {exc}"}
        return True, {"lines": list(self._act_buf)}

    def _status(self, arg: object) -> tuple[bool, object]:
        return True, {"text": f"last poll: {self._last} | L2 facts: {self._store.count()}"}

    def _health(self, arg: object) -> tuple[bool, object]:
        s = self._metrics.summary()
        out: list[str] = []
        if s["total"] == 0:
            out.append("no ingestion telemetry yet — teach me something with `learn`.")
        else:
            out += _ingestion_health(s)
        out += self._flow.focus_health_lines()
        return True, {"text": "\n".join(out)}

    def _sentinel(self, arg: object) -> tuple[bool, object]:
        return True, {"text": self._flow.cmd(str(arg))}

    def _intervene(self, arg: object) -> tuple[bool, object]:
        if not isinstance(arg, dict):
            return False, {"text": "intervene expects {id, accepted}"}
        return True, {"text": self._flow.resolve_intervention(int(arg.get("id", -1)),
                                                              bool(arg.get("accepted")))}

    # ── select-loop hooks for the daemon ──────────────────────────────
    def sensor_fileno(self) -> int | None:
        return self._flow.fileno()

    def read_sensor(self) -> None:
        self._flow.read()

    def tick(self) -> None:
        """Periodic M2 system-sentinel poll (CPU/mem -> surprise -> alert event)."""
        try:
            self._sys_sentinel.run_once()
            import psutil
            self._last = f"cpu={psutil.cpu_percent():.0f}% mem={psutil.virtual_memory().percent:.0f}%"
        except Exception as exc:  # noqa: BLE001 — a flaky poll must never kill the loop
            self._emit("alert", {"text": f"[sentinel error] {exc}"})

    def close(self) -> None:
        self._flow.close()
        self._brain.close()


def _ingestion_health(s: dict) -> list[str]:
    glob = (s["global_rate"] or 0.0) * 100
    out = [f"ingestion health — {s['total']} attempts ({s['session_total']} this session)"]
    sr, pr = s["session_rate"], s["prior_rate"]
    if sr is None:
        out.append(f"  rejection rate: {glob:.0f}% all-time (nothing learned this session yet)")
    elif pr is None:
        out.append(f"  rejection rate: {sr * 100:.0f}% this session (first session — no history)")
    else:
        srp, prp = sr * 100, pr * 100
        trend = ("trending healthy ↓ (learning the dialect)" if srp < prp - 1 else
                 "friction rising ↑ (scope may be too narrow)" if srp > prp + 1 else "steady →")
        small = "  [n small — not yet significant]" if s["session_total"] < 5 else ""
        out.append(f"  rejection rate: {srp:.0f}% this session vs {prp:.0f}% prior -> {trend}{small}")
    fails = {k: v for k, v in s["taxonomy"].items() if k not in ("COMMIT_CLEAN", "ESCALATE_ACCEPTED")}
    if fails:
        label = {"REJECT_STRUCTURAL": "structural", "REJECT_FUSED": "fused",
                 "REJECT_SEMANTIC": "semantic", "ESCALATE_DECLINED": "esc-declined"}
        out.append("  failures: " + " · ".join(f"{label.get(k, k)} {v}" for k, v in sorted(fails.items())))
    return out
