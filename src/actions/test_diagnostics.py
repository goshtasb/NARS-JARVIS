"""Unit tests for system diagnostics (ADR-019): the live report renders, and the anomaly flags fire
on injected readings (deterministic — no host faking)."""
from actions import diagnostics
from actions.diagnostics import anomaly_flags, system_report


def test_live_report_has_core_metrics() -> None:
    # Real psutil read (read-only, safe in CI). Just assert the shape, not the values.
    out = system_report()
    assert "System report:" in out
    assert "CPU:" in out and "Memory:" in out and "Disk (/):" in out


def test_injected_nominal_readings_flag_nothing() -> None:
    out = system_report({"cpu": 12.0, "mem": 40.0, "disk": 55.0,
                         "battery": (80.0, False), "top": [("Finder", 3.0)]})
    assert "Nothing looks wrong" in out
    assert "Finder 3%" in out


def test_injected_high_readings_raise_the_right_flags() -> None:
    r = {"cpu": 99.0, "mem": 95.0, "disk": 97.0, "battery": (8.0, False), "top": []}
    flags = anomaly_flags(r)
    assert "⚠ CPU pegged" in flags
    assert "⚠ memory pressure high" in flags
    assert "⚠ disk almost full" in flags
    assert "⚠ battery low" in flags
    assert "Anomalies:" in system_report(r)


def test_plugged_in_low_battery_is_not_flagged() -> None:
    # Low charge while charging is normal — only on-battery + low triggers the flag.
    assert anomaly_flags({"cpu": 5, "mem": 5, "disk": 5, "battery": (8.0, True)}) == []


def test_thresholds_are_inclusive_boundaries() -> None:
    assert anomaly_flags({"cpu": diagnostics.CPU_HIGH, "mem": 0, "disk": 0}) == ["⚠ CPU pegged"]
    assert anomaly_flags({"cpu": 89.9, "mem": 0, "disk": 0}) == []


# ── audio sensor (ADR-040) ──
class _AudioSpawn:
    """Fake spawn returning a canned `get volume settings` line."""
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.calls: list[list[str]] = []
    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return type("R", (), {"returncode": 0, "stdout": self.stdout})()


def test_parse_volume_settings_normal_and_garbage() -> None:
    s = diagnostics.parse_volume_settings(
        "output volume:19, input volume:55, alert volume:100, output muted:false")
    assert s == {"output": 19, "input": 55, "alert": 100, "muted": False}
    s = diagnostics.parse_volume_settings(
        "output volume:missing value, input volume:55, alert volume:100, output muted:true")
    assert s is not None and s["output"] is None and s["muted"] is True   # headless/odd hardware
    assert diagnostics.parse_volume_settings("execution error: blah") is None
    assert diagnostics.parse_volume_settings("") is None


def test_audio_report_flags_muted_and_silent_states() -> None:
    spawn = _AudioSpawn("output volume:64, input volume:50, alert volume:100, output muted:false")
    out = diagnostics.audio_report(spawn)
    assert spawn.calls == [["osascript", "-e", "get volume settings"]]     # read-only, single call
    assert "Output volume: 64/100" in out and "Muted: no" in out
    assert "Software audio state looks fine" in out                       # scope-honest verdict
    assert "does not test speakers" in out                                # names what it can't see

    out = diagnostics.audio_report(
        _AudioSpawn("output volume:64, input volume:50, alert volume:100, output muted:true"))
    assert "MUTED" in out                                                  # the actual "why no sound"

    out = diagnostics.audio_report(
        _AudioSpawn("output volume:0, input volume:50, alert volume:100, output muted:false"))
    assert "volume is 0" in out

    out = diagnostics.audio_report(_AudioSpawn("execution error: not allowed"))
    assert out.startswith("Couldn't read the sound state")                 # honest failure, no fake


def test_system_report_verdict_names_its_scope() -> None:
    # ADR-040: a clean report must say WHAT it measured — it can never read as "your audio is fine".
    out = system_report({"cpu": 5.0, "mem": 10.0, "disk": 10.0, "battery": (90.0, True), "top": []})
    assert "CPU / memory / disk / battery" in out


def test_drop_nominal_verdict_is_selective() -> None:
    # ADR-045: drop the "all clear" line for a data question; ALWAYS keep a real anomaly line.
    clean = system_report({"cpu": 5.0, "mem": 88.0, "disk": 7.0, "battery": (100.0, True),
                           "top": [("Python", 29.0)]})
    assert diagnostics.NOMINAL_VERDICT in clean
    stripped = diagnostics.drop_nominal_verdict(clean)
    assert "Nothing looks wrong" not in stripped
    assert "Top memory: Python 29%" in stripped and "CPU: 5%" in stripped   # data preserved

    anomalous = system_report({"cpu": 99.0, "mem": 95.0, "disk": 10.0, "battery": (90.0, True), "top": []})
    assert diagnostics.drop_nominal_verdict(anomalous) == anomalous          # anomaly line never dropped
    assert "⚠ CPU pegged" in diagnostics.drop_nominal_verdict(anomalous)
    assert diagnostics.drop_nominal_verdict("ran report_system") == "ran report_system"  # no-op on junk


# ── network sensor (ADR-046) ──
def test_parse_nettop_delta_keeps_last_occurrence() -> None:
    # nettop -d emits each process twice: cumulative THEN delta. Last value = the interval delta.
    raw = (",bytes_in,bytes_out,\n"
           "launchd.1,0,0,\n"
           "Chrome.500,126765500,17034038,\n"          # sample 1 (cumulative — must be discarded)
           "Spotify.600,9000,1000,\n"
           ",bytes_in,bytes_out,\n"
           "launchd.1,0,0,\n"
           "Chrome.500,40000,8000,\n"                  # sample 2 (delta — kept)
           "Spotify.600,0,0,\n")
    out = diagnostics.parse_nettop_delta(raw)
    assert out == [("Chrome", 48000)]                  # last Chrome = 48000; Spotify delta 0 dropped
    assert diagnostics.parse_nettop_delta("") == []


def test_parse_connections_counts_and_skips_loopback() -> None:
    raw = ("COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
           "Google\\x20Chrome 1 u IPv4 1 0t0 TCP 192.168.0.10:50->1.1.1.1:443 (ESTABLISHED)\n"
           "Google\\x20Chrome 1 u IPv4 1 0t0 TCP 192.168.0.10:51->8.8.8.8:443 (ESTABLISHED)\n"
           "loopd 2 u IPv4 2 0t0 TCP 127.0.0.1:5->127.0.0.1:6 (ESTABLISHED)\n")
    out = diagnostics.parse_connections(raw)
    assert out == [("Google Chrome", 2)]               # \x20 -> space; loopback skipped


def test_parse_wifi_reads_current_network_block() -> None:
    raw = ("Other Local Wi-Fi Networks:\n  PHY Mode: ignored\n"
           "Current Network Information:\n      MyNet:\n"
           "        PHY Mode: 802.11ac\n        Channel: 149 (5GHz, 80MHz)\n"
           "        Signal / Noise: -55 dBm / -94 dBm\n        Transmit Rate: 468\n")
    w = diagnostics.parse_wifi(raw)
    assert w == {"phy": "802.11ac", "channel": "149 (5GHz, 80MHz)",
                 "signal": "-55 dBm / -94 dBm", "rate": "468"}
    assert diagnostics.parse_wifi("no wifi here") == {}


def test_net_report_composes_and_is_scope_honest() -> None:
    # Fake spawn returns canned output per command — no real network access.
    def spawn(argv, **kw):
        tool = argv[0]
        out = {"nettop": ",bytes_in,bytes_out,\nSpotify.6,0,0,\n,bytes_in,bytes_out,\nSpotify.6,500000,2000,\n",
               "lsof": "H\nSpotify 1 u IPv4 1 0 TCP 192.168.0.10:5->1.2.3.4:443 (ESTABLISHED)\n",
               "system_profiler": "Current Network Information:\n  PHY Mode: 802.11ac\n  Channel: 149\n"
                                  "  Signal / Noise: -55 dBm / -94 dBm\n  Transmit Rate: 468\n"}[tool]
        return type("R", (), {"stdout": out, "returncode": 0})()
    rep = diagnostics.net_report(spawn)
    assert "Spotify" in rep and "KB/2s" in rep                  # top consumer named with a rate
    assert "Wi-Fi: 802.11ac" in rep and "468" in rep           # link quality surfaced
    assert "not JARVIS" in rep and "can't see your router" in rep   # scope-honest verdict
