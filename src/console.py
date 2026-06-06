"""NARS-JARVIS interactive console — the single-threaded interaction surface (run: `python3 console.py`).

Concurrency model (deliberate): ONE thread multiplexes the REPL and the sentinel via
`select(stdin, timeout=poll_interval)`. stdin-ready -> handle a command; timeout -> one sentinel
tick. So the ONA subprocess (L1 cache) and the terminal are single-owner BY CONSTRUCTION — no
locks, no `_drain()` desync race, no torn output. A cbreak line editor owns the input buffer, so an
async alert clears the line, prints, and redraws `prompt + your un-submitted text` (no mangling).

Imperative Shell (S-02): all I/O lives here; it composes domain modules via their public APIs only.
Watchdog file-watching is intentionally NOT enabled — its Observer runs on its own thread and would
touch the Brain off-loop, breaking the single-owner invariant (that needs the queue pattern).
"""
from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys

from brain import Brain
from execution import DecisionStats, build_air_gapped_executor, decide
from jarvis import Jarvis
from language import Translator
from memory import MemoryStore
from sentinel import SurpriseDetector, SystemSentinel
from sentinel.narrate import Narrator

PROMPT = "jarvis> "
_STRONG = DecisionStats(0.95, 0.97, 30, 12)  # a REPL `act` is an explicit, high-confidence request
BANNER = (
    "NARS-JARVIS console — single-thread sentinel + REPL.\n"
    "  learn <english>   tell <narsese.>   ask <narsese?>   act <op> <arg>   status   help   quit\n"
    "  (the sentinel polls CPU/mem every tick and alerts on surprises)\n"
)
HELP = (
    "commands:\n"
    "  learn <english>    translate English -> Narsese, commit to L2+L1 (needs a local LLM)\n"
    "  tell  <narsese.>   add a belief directly, e.g.  tell <cpu --> [pegged]>. {0.0 0.9}\n"
    "  ask   <narsese?>   query L1 (reloads from L2 on a miss), e.g.  ask <tim --> bird>?\n"
    "  act   <op> <arg>   propose an action; Suggestion-Only ones prompt [y/n]\n"
    "                     e.g.  act run_saved_command disk_usage   |   act open_app slack\n"
    "  status             last CPU/mem poll + memory counts\n"
    "  help | quit\n"
)


class _NoNarrationLLM:
    """No GGUF wired -> the Narrator's deterministic, action-forbidden fallback is used."""
    def generate(self, system_prompt: str, user: str) -> str:
        raise RuntimeError("no narration model")


class _DemoClaims:
    """Tiny offline claim source so `learn` works without a model for a couple of demo sentences."""
    _T = {
        "Tim is a duck.": '[{"type":"RelationClaim","subject":"Tim","verb":"IsA","object":"duck"}]',
        "Ducks are birds.": '[{"type":"RelationClaim","subject":"duck","verb":"IsA","object":"bird"}]',
    }
    def generate(self, system_prompt: str, sentence: str) -> str:
        return self._T.get(sentence, "[]")


def _make_claim_source():
    if os.environ.get("NARS_JARVIS_LLM_GGUF"):
        try:
            from language import LocalLLM
            return LocalLLM()
        except Exception as exc:  # noqa: BLE001 — degrade gracefully to offline demo source
            sys.stderr.write(f"[warn] LocalLLM unavailable ({exc}); NL learning limited\n")
    return _DemoClaims()


def _make_embedder():
    """Optional grounding embedder: maps plural/synonym atoms onto existing ones (PRD R1)."""
    if os.environ.get("NARS_JARVIS_EMBED_GGUF"):
        try:
            from language import LocalEmbedder
            return LocalEmbedder()
        except Exception as exc:  # noqa: BLE001 — grounding is optional; degrade to none
            sys.stderr.write(f"[warn] LocalEmbedder unavailable ({exc}); grounding off\n")
    return None


class Console:
    def __init__(self, db_path: str = "jarvis.db", poll_interval: float = 2.0) -> None:
        self._poll = poll_interval
        self._buf = ""
        self._quit = False
        self._fd = sys.stdin.fileno()
        self._last = "no poll yet"

        self._store = MemoryStore(db_path)
        self._brain = Brain(cycles_per_step=50)  # boots ONA with *motorbabbling=0.0
        self._executor = build_air_gapped_executor(sink=self._out)  # sync output during `act`
        self._jarvis = Jarvis(Translator(_make_claim_source(), embedder=_make_embedder()),
                              self._store, self._brain, executor=self._executor)
        narrator = Narrator(_NoNarrationLLM(), on_alert=self._on_alert)
        self._detector = SurpriseDetector(self._brain, threshold=0.5, on_surprise=narrator.narrate)
        self._sentinel = SystemSentinel(sink=self._detector.observe, poll_interval=poll_interval)

        # macOS Notification Center channel (fire-and-forget; jarvis runs OUTSIDE the sandbox).
        self._notify_on = sys.platform == "darwin" and shutil.which("osascript") is not None
        self._notif_procs: list[subprocess.Popen] = []

    # ── terminal writers ──────────────────────────────────────────────
    def _w(self, s: str) -> None:
        sys.stdout.write(s); sys.stdout.flush()

    def _out(self, text: object) -> None:
        """Synchronous output (we're already on a fresh line after Enter)."""
        for line in str(text).splitlines() or [""]:
            self._w(line + "\n")

    def _emit(self, text: object) -> None:
        """Asynchronous output: clear the live input line, print, redraw prompt + un-submitted buffer."""
        self._w("\r\x1b[K")
        for line in str(text).splitlines() or [""]:
            self._w(line + "\n")
        self._w(PROMPT + self._buf)

    def _redraw(self) -> None:
        self._w("\r\x1b[K" + PROMPT + self._buf)

    # ── surprise alert: terminal redraw + concurrent OS banner ────────
    def _on_alert(self, text: str) -> None:
        self._emit("⚠  " + text)  # terminal: clear line, print, redraw prompt+buffer (instant)
        self._notify(text)        # macOS banner: fire-and-forget, runs off-thread (instant return)

    def _notify(self, body: str, title: str = "NARS-JARVIS sentinel") -> None:
        """Spawn osascript WITHOUT waiting — a sluggish Notification Center can't stall the loop.

        Popen does only fork+exec and returns immediately; the banner is rendered by an independent
        OS process. stdio -> DEVNULL (no pipe backpressure), start_new_session (detached). Args are
        passed to AppleScript via `argv`, never interpolated, so the alert text can't inject script.
        """
        if not self._notify_on:
            return
        try:
            proc = subprocess.Popen(
                ["osascript",
                 "-e", "on run argv",
                 "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
                 "-e", "end run", "--", body[:240], title],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            self._notif_procs.append(proc)
        except Exception:  # noqa: BLE001 — notifications are best-effort; never break the loop
            pass

    def _reap_notifs(self) -> None:
        """Drop finished banner processes (poll() reaps them) so no zombies accumulate."""
        self._notif_procs = [p for p in self._notif_procs if p.poll() is None]

    # ── main loop ─────────────────────────────────────────────────────
    def run(self) -> None:
        if not sys.stdin.isatty():
            return self._run_plain()
        import termios, tty
        old = termios.tcgetattr(self._fd)
        try:
            tty.setcbreak(self._fd)
            self._w(BANNER)
            self._redraw()
            while not self._quit:
                ready, _, _ = select.select([sys.stdin], [], [], self._poll)
                if ready:
                    self._read_ready()
                else:
                    self._tick()
        finally:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, old)
            self._w("\n")
            self._brain.close()

    def _read_ready(self) -> None:
        for ch in os.read(self._fd, 1024).decode(errors="ignore"):
            if ch in ("\r", "\n"):
                self._w("\n")
                line, self._buf = self._buf.strip(), ""
                if line:
                    self._dispatch(line)
                if not self._quit:
                    self._redraw()
            elif ch in ("\x7f", "\b"):
                if self._buf:
                    self._buf = self._buf[:-1]; self._w("\b \b")
            elif ch == "\x03":  # Ctrl-C clears the line
                self._buf = ""; self._w("^C\n"); self._redraw()
            elif ch == "\x04":  # Ctrl-D quits
                self._quit = True
            elif ch.isprintable():
                self._buf += ch; self._w(ch)

    def _tick(self) -> None:
        self._reap_notifs()
        try:
            self._sentinel.run_once()  # poll CPU/mem -> ONA; surprises -> narrator -> self._on_alert
            import psutil
            self._last = f"cpu={psutil.cpu_percent():.0f}% mem={psutil.virtual_memory().percent:.0f}%"
        except Exception as exc:  # noqa: BLE001 — a flaky poll must never kill the loop
            self._emit(f"[sentinel error] {exc}")

    # ── commands ──────────────────────────────────────────────────────
    def _dispatch(self, line: str) -> None:
        cmd, _, rest = line.partition(" ")
        rest = rest.strip()
        handler = {
            "help": lambda: self._out(HELP), "?": lambda: self._out(HELP),
            "quit": self._do_quit, "exit": self._do_quit,
            "status": self._do_status,
            "learn": lambda: self._do_learn(rest), "tell": lambda: self._do_tell(rest),
            "ask": lambda: self._do_ask(rest), "act": lambda: self._do_act(rest),
        }.get(cmd)
        if handler is None:
            self._out(f"unknown command: {cmd!r} (try 'help')")
        else:
            handler()

    def _do_quit(self) -> None:
        self._quit = True

    def _do_status(self) -> None:
        self._out(f"last poll: {self._last} | L2 facts: {self._store.count()}")

    def _do_learn(self, text: str) -> None:
        if not text:
            return self._out("usage: learn <english sentence>")
        committed = self._jarvis.learn(text)
        self._out(f"committed: {committed}" if committed
                  else "nothing committed (no local LLM, or contradiction deferred)")

    def _do_tell(self, narsese: str) -> None:
        if not narsese:
            return self._out("usage: tell <narsese statement.>  e.g.  tell <a --> b>.")
        try:
            committed = self._jarvis.tell(narsese)  # durable: write-through to L2 + feed L1
        except Exception as exc:  # noqa: BLE001 — malformed Narsese -> report, don't crash
            return self._out(f"invalid narsese: {exc}")
        self._out("committed to L2+L1 (durable)." if committed else "deferred (contradiction flagged).")

    def _do_ask(self, narsese: str) -> None:
        if not narsese:
            return self._out("usage: ask <narsese question?>  e.g.  ask <tim --> bird>?")
        answer = self._jarvis.ask(narsese)
        self._out(f"answer: {answer}" if answer is not None else "no answer in memory.")

    def _do_act(self, rest: str) -> None:
        parts = rest.split()
        if len(parts) != 2:
            return self._out("usage: act <op_name> <arg_name>  e.g.  act run_saved_command disk_usage")
        try:
            proposal = decide(parts[0], parts[1], _STRONG)  # closed catalog; raises if unregistered
        except Exception as exc:  # noqa: BLE001 — surface the security rejection, don't crash
            return self._out(f"rejected: {exc}")
        try:
            self._executor.execute(proposal)  # prints [EXECUTED] or [SUGGEST] via self._out
            if not (proposal.autonomous and self._executor.is_live_eligible(proposal.operation)):
                if self._confirm("approve and run now?"):
                    self._executor.execute_approved(proposal)  # enforces the same safety gates
        except Exception as exc:  # noqa: BLE001 — never let one action kill the loop
            self._out(f"execution error: {exc}")

    def _confirm(self, question: str) -> bool:
        self._w(f"{question} [y/N] ")
        ch = os.read(self._fd, 1).decode(errors="ignore") if sys.stdin.isatty() else "n"
        self._w(("y" if ch in ("y", "Y") else "n") + "\n")
        return ch in ("y", "Y")

    # ── non-tty fallback (piped stdin): line loop, no async alerts ─────
    def _run_plain(self) -> None:
        self._w(BANNER)
        try:
            for raw in sys.stdin:
                line = raw.strip()
                if line:
                    self._dispatch(line)
                if self._quit:
                    break
        finally:
            self._brain.close()


def main() -> None:
    if os.environ.get("NARS_JARVIS_LLM_GGUF"):
        print("Loading local models (first start ~10-20s)…", flush=True)
    Console(db_path=os.environ.get("NARS_JARVIS_DB", "jarvis.db")).run()


if __name__ == "__main__":
    main()
