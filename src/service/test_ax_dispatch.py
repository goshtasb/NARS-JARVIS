"""Unit tests for AX verb dispatch (ADR-021): validation + consent-gating, with a real ConsentService
and a fake actuate emitter. No Session/socket/OS — proves nothing actuates until approval and that an
invalid verb/id is a safe refusal that never opens a consent."""
from service.ax_dispatch import dispatch_ax
from service.consent_service import ConsentService


def _setup():
    events: list[tuple[str, dict]] = []
    emitted: list[tuple] = []
    consent = ConsentService(lambda k, b: events.append((k, b)))
    emit = lambda epoch, eid, verb, args: emitted.append((epoch, eid, verb, args))
    return consent, emit, events, emitted


def test_valid_press_opens_consent_but_does_not_actuate() -> None:
    consent, emit, events, emitted = _setup()
    out = dispatch_ax(consent, emit, ids={"btn_1"}, epoch=5, verb="ax_press", arg="btn_1")
    assert "Awaiting your approval" in out
    assert emitted == []                                  # nothing actuated yet
    assert any(k == "consent_request" for k, _ in events)


def test_approval_emits_actuate() -> None:
    consent, emit, events, emitted = _setup()
    dispatch_ax(consent, emit, ids={"btn_1"}, epoch=5, verb="ax_press", arg="btn_1")
    cid = [b["id"] for k, b in events if k == "consent_request"][0]
    consent.resolve(cid, accepted=True)
    assert emitted == [(5, "btn_1", "ax_press", {})]      # emitted only on approve


def test_deny_does_not_emit() -> None:
    consent, emit, events, emitted = _setup()
    dispatch_ax(consent, emit, ids={"btn_1"}, epoch=5, verb="ax_press", arg="btn_1")
    cid = [b["id"] for k, b in events if k == "consent_request"][0]
    consent.resolve(cid, accepted=False)
    assert emitted == []


def test_set_value_parses_and_carries_value() -> None:
    consent, emit, events, emitted = _setup()
    dispatch_ax(consent, emit, ids={"sld_1"}, epoch=2, verb="ax_set_value", arg="sld_1 45")
    cid = [b["id"] for k, b in events if k == "consent_request"][0]
    consent.resolve(cid, accepted=True)
    assert emitted == [(2, "sld_1", "ax_set_value", {"value": 45.0})]


def test_unknown_verb_refused_no_consent() -> None:
    consent, emit, events, _ = _setup()
    out = dispatch_ax(consent, emit, ids={"btn_1"}, epoch=1, verb="ax_drag", arg="btn_1")
    assert "unknown UI action" in out
    assert not any(k == "consent_request" for k, _ in events)


def test_id_not_on_screen_refused() -> None:
    consent, emit, events, _ = _setup()
    out = dispatch_ax(consent, emit, ids={"btn_1"}, epoch=1, verb="ax_press", arg="ghost_9")
    assert "isn't on the screen" in out
    assert not any(k == "consent_request" for k, _ in events)


def test_set_value_without_value_refused() -> None:
    consent, emit, events, _ = _setup()
    out = dispatch_ax(consent, emit, ids={"sld_1"}, epoch=1, verb="ax_set_value", arg="sld_1")
    assert "needs a value" in out
    assert not any(k == "consent_request" for k, _ in events)


def test_non_numeric_value_refused() -> None:
    consent, emit, events, _ = _setup()
    out = dispatch_ax(consent, emit, ids={"sld_1"}, epoch=1, verb="ax_set_value", arg="sld_1 bright")
    assert "isn't a number" in out
    assert not any(k == "consent_request" for k, _ in events)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("service/test_ax_dispatch: OK")
