"""Voice pipeline, daemon side: whisper.cpp STT as a select()-multiplexed child + offline `say` TTS.

Control plane only ever carries a tiny JSON pointer (the WAV path); the audio bytes live on the local
filesystem (written by the Swift client). Whisper runs as a Popen child whose stdout the daemon's
select loop watches exactly like the sensor pipe — so transcription never blocks the reasoning loop.
TTS is fire-and-forget `say`. All offline. See ADR-005.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_DEFAULT_MODEL = str(Path(__file__).resolve().parents[2] / "models" / "ggml-base.en.bin")


def _bin() -> str:
    return os.environ.get("NARS_JARVIS_WHISPER", "whisper-cli")


def _model() -> str:
    return os.environ.get("NARS_JARVIS_WHISPER_MODEL", _DEFAULT_MODEL)


def whisper_available() -> bool:
    # Read env at call time (not import) so the daemon picks up configuration set after import.
    return shutil.which(_bin()) is not None and Path(_model()).exists()


class WhisperJob:
    """One in-flight transcription: whisper.cpp on a WAV, stdout collected via the daemon's select().

    -nt (no timestamps) and -np (no progress prints) so stdout is just the transcript text.
    """

    def __init__(self, wav_path: str) -> None:
        self.wav = wav_path
        self._proc = subprocess.Popen(
            [_bin(), "-m", _model(), "-f", wav_path, "-nt", "-np"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._chunks: list[bytes] = []

    def fileno(self) -> int:
        return self._proc.stdout.fileno()

    def read(self) -> str | None:
        """Drain whatever is readable now; return the transcript at EOF, else None (more to come)."""
        data = os.read(self._proc.stdout.fileno(), 65536)  # readable per select() -> won't block
        if data:
            self._chunks.append(data)
            return None
        self._proc.wait()
        return b"".join(self._chunks).decode(errors="ignore").strip()

    def cleanup(self) -> None:
        try:
            self._proc.stdout.close()
        except OSError:
            pass
        try:
            os.unlink(self.wav)  # the utterance is transient; never persist captured audio
        except OSError:
            pass


def speak(text: str) -> None:
    """Offline TTS via macOS `say`, fire-and-forget so it never blocks the loop. NO_TTS skips audio."""
    if not text or os.environ.get("NARS_JARVIS_NO_TTS"):
        return
    try:
        subprocess.Popen(["say", text[:600]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001 — TTS is best-effort; never break the loop
        pass
