"""NARS-JARVIS terminal console — a THIN CLIENT of the headless daemon (`service/`).

All reasoning, the two brains, and the actuator live in the daemon; this file is presentation only.
It spawns the daemon (or attaches to a running one), then multiplexes the keyboard and the daemon
socket via select(): keystrokes become requests; the daemon's async events (sentinel alerts,
intervention prompts) clear the input line, print, and redraw. The same daemon surface will back the
SwiftUI app (Phase 2), so the brain is never duplicated in or polluted by a UI. See service/README.
"""
from __future__ import annotations

import os
import select
import subprocess
import sys
import tempfile
import time

from service import Client, socket_path

PROMPT = "jarvis> "
BANNER = (
    "NARS-JARVIS console — thin client of the headless daemon.\n"
    "  learn <english>   tell <narsese.>   ask <english?>   act <op> <arg>   status   health   help   quit\n"
    "  (the sentinel runs in the daemon and pushes alerts here)\n"
)
HELP = (
    "commands:\n"
    "  learn <english>    translate English -> Narsese, commit to L2+L1 (needs a local LLM)\n"
    "  tell  <narsese.>   add a belief directly, e.g.  tell <cpu --> [pegged]>. {0.0 0.9}\n"
    "  ask   <english?>   ask in plain English  (answered + cited, no hallucination)\n"
    "                     (expert: `ask <tim --> bird>?` runs a raw Narsese query)\n"
    "  act   <op> <arg>   propose an action; Suggestion-Only ones prompt [y/n]\n"
    "  forget <fact>      soft-delete a remembered fact (undoable)\n"
    "  restore <fact>     undo a forget / bring a superseded memory back\n"
    "  status             last CPU/mem poll + memory counts\n"
    "  health             ingestion rejection-rate decay + focus-sentinel KPI\n"
    "  sentinel on|off    always-on flow sentinel (runs in the daemon)\n"
    "  shutdown           stop the daemon entirely (whole system off)\n"
    "  help | quit        quit this console (quit also stops a daemon it started)\n"
)


class Console:
    def __init__(self) -> None:
        self._buf = ""
        self._quit = False
        self._fd = sys.stdin.fileno()
        self._pending: dict | None = None      # an intervention awaiting [y/n]
        self._client = Client()
        self._daemon: subprocess.Popen | None = None

    # ── daemon lifecycle ──────────────────────────────────────────────
    def _attach(self) -> None:
        """Connect to a running daemon, else spawn one and wait for it to come up."""
        try:
            self._client.connect(); return
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        log = open(os.path.join(tempfile.gettempdir(), "nars-jarvisd.log"), "w")
        self._daemon = subprocess.Popen([sys.executable, "-m", "service"],
                                        stdout=log, stderr=subprocess.STDOUT)
        self._w("Starting JARVIS daemon (first start loads local models ~10-20s)…\n")
        for _ in range(600):                   # up to ~60s for model load + bind
            if self._daemon.poll() is not None:
                raise RuntimeError(f"daemon exited early; see {log.name}")
            try:
                self._client.connect(); return
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.1)
        raise RuntimeError(f"daemon did not come up; see {log.name}")

    # ── terminal writers ──────────────────────────────────────────────
    def _w(self, s: str) -> None:
        sys.stdout.write(s); sys.stdout.flush()

    def _out(self, text: object) -> None:
        for line in str(text).splitlines() or [""]:
            self._w(line + "\n")

    def _emit(self, text: object) -> None:
        """Async output: clear the live input line, print, redraw prompt + un-submitted buffer."""
        self._w("\r\x1b[K")
        for line in str(text).splitlines() or [""]:
            self._w(line + "\n")
        self._w(PROMPT + self._buf)

    def _redraw(self) -> None:
        self._w("\r\x1b[K" + PROMPT + self._buf)

    def _on_event(self, kind: str, body: dict) -> None:
        if kind == "intervention":
            self._pending = {"id": body.get("id")}
            self._emit(body.get("prompt", ""))
        else:                                  # "alert"
            self._emit(body.get("text", ""))

    # ── main loop ─────────────────────────────────────────────────────
    def run(self) -> None:
        if not sys.stdin.isatty():
            return self._run_plain()
        import termios
        import tty
        self._attach()
        self._client.set_event_handler(self._on_event)
        old = termios.tcgetattr(self._fd)
        try:
            tty.setcbreak(self._fd)
            self._w(BANNER)
            self._redraw()
            while not self._quit:
                ready, _, _ = select.select([sys.stdin, self._client], [], [], None)
                for r in ready:
                    if r is self._client:
                        try:
                            self._client.pump()    # async events -> _on_event
                        except ConnectionError:
                            self._quit = True
                    else:
                        self._read_ready()
        finally:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, old)
            self._w("\n")
            self._client.close()
            if self._daemon is not None:
                self._daemon.terminate()

    def _read_ready(self) -> None:
        for ch in os.read(self._fd, 1024).decode(errors="ignore"):
            if ch in ("\r", "\n"):
                self._w("\n")
                line, self._buf = self._buf.strip(), ""
                if self._pending is not None:      # an intervention [y/n] takes priority over commands
                    self._resolve_intervention(line)
                elif line:
                    self._dispatch(line)
                if not self._quit:
                    self._redraw()
            elif ch in ("\x7f", "\b"):
                if self._buf:
                    self._buf = self._buf[:-1]; self._w("\b \b")
            elif ch == "\x03":                     # Ctrl-C clears the line
                self._buf = ""; self._w("^C\n"); self._redraw()
            elif ch == "\x04":                     # Ctrl-D quits
                self._quit = True
            elif ch.isprintable():
                self._buf += ch; self._w(ch)

    # ── command dispatch (all reasoning happens in the daemon) ────────
    def _dispatch(self, line: str) -> None:
        cmd, _, rest = line.partition(" ")
        rest = rest.strip()
        if cmd in ("help", "?"):
            return self._out(HELP)
        if cmd in ("quit", "exit"):
            self._quit = True; return
        if cmd == "shutdown":                       # stop the daemon (whole system off), then exit
            self._client.call("shutdown")
            self._quit = True; return
        if cmd == "learn":
            return self._do_learn(rest)
        if cmd == "act":
            return self._do_act(rest)
        if cmd in ("ask", "tell", "status", "health", "sentinel", "forget", "restore"):
            _, body = self._client.call(cmd, rest)
            return self._render(body)
        self._out(f"unknown command: {cmd!r} (try 'help')")

    def _render(self, body: object) -> None:
        if isinstance(body, dict):
            if body.get("text") is not None:
                self._out(body["text"])
            for line in body.get("lines", []):
                self._out(line)

    def _do_learn(self, rest: str) -> None:
        if not rest:
            return self._out("usage: learn <english sentence>")
        _, body = self._client.call("learn", rest)
        rejects = body.get("rejects", [])
        if rejects:
            self._out(f"✗ didn't save {len(rejects)} (I store facts, not cause/effect — and only what I can verify):")
            for it in rejects:
                self._out(f"    • understood as: \"{it['mirror']}\"  —  {it['reason']}")
        accept: list[int] = []
        for e in body.get("escalations", []):       # Phase 2: ask the human locally, [y/n]
            sim = f" (similarity {e['cosine']:.2f})" if e.get("cosine") is not None else ""
            self._out(f"? unsure{sim}: I understood \"{e['mirror']}\".")
            if self._confirm("   save it?"):
                accept.append(e["eid"])
        committed = list(body.get("committed", []))
        if accept and body.get("token"):
            _, more = self._client.call("learn_resolve", {"token": body["token"], "accept": accept})
            committed += more.get("committed", [])
        self._out("✓ saved: " + " · ".join(committed) if committed else "nothing saved.")

    def _do_act(self, rest: str) -> None:
        ok, body = self._client.call("act", rest)
        if not ok:
            return self._render(body)
        self._render(body)
        if body.get("needs_confirm") and body.get("token"):
            if self._confirm("approve and run now?"):
                _, more = self._client.call("act_confirm", {"token": body["token"]})
                self._render(more)

    def _resolve_intervention(self, line: str) -> None:
        pend, self._pending = self._pending, None
        accepted = line.strip().lower() in ("y", "yes")
        _, body = self._client.call("intervene", {"id": pend["id"], "accepted": accepted})
        self._render(body)

    def _confirm(self, question: str) -> bool:
        self._w(f"{question} [y/N] ")
        ch = os.read(self._fd, 1).decode(errors="ignore") if sys.stdin.isatty() else "n"
        self._w(("y" if ch in ("y", "Y") else "n") + "\n")
        return ch in ("y", "Y")

    # ── non-tty fallback (piped stdin): line loop, no async events ─────
    def _run_plain(self) -> None:
        self._attach()
        self._w(BANNER)
        try:
            for raw in sys.stdin:
                line = raw.strip()
                if line:
                    self._dispatch(line)
                if self._quit:
                    break
        finally:
            self._client.close()
            if self._daemon is not None:
                self._daemon.terminate()


def main() -> None:
    Console().run()


if __name__ == "__main__":
    main()
