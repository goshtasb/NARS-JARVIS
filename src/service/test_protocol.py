"""Wire-protocol codec — pure, deterministic, no socket."""
from service.protocol import EVT, REQ, RES, LineBuffer, encode, event, request, response


def test_frame_builders() -> None:
    assert request(1, "ask", "hi") == {"t": REQ, "id": 1, "cmd": "ask", "arg": "hi"}
    assert response(1, True, {"x": 1}) == {"t": RES, "id": 1, "ok": True, "body": {"x": 1}}
    assert event("alert", {"text": "boom"}) == {"t": EVT, "kind": "alert", "body": {"text": "boom"}}


def test_roundtrip_and_arg_can_be_structured() -> None:
    frame = request(7, "learn_resolve", {"token": "L1", "accept": [0, 2]})
    [decoded] = LineBuffer().feed(encode(frame))
    assert decoded == frame                                  # structured arg survives the wire


def test_linebuffer_handles_partial_and_multiple_frames() -> None:
    buf = LineBuffer()
    a, b = encode(request(1, "ask", "x")), encode(response(1, True, "y"))
    assert buf.feed(a[:5]) == []                             # partial -> nothing yet
    frames = buf.feed(a[5:] + b)                             # rest of A + all of B in one read
    assert [f["id"] for f in frames] == [1, 1]
    assert frames[0]["t"] == REQ and frames[1]["t"] == RES


def test_blank_lines_ignored() -> None:
    assert LineBuffer().feed(b"\n\n" + encode(event("e", {})) + b"\n") == [event("e", {})]


if __name__ == "__main__":
    test_frame_builders()
    test_roundtrip_and_arg_can_be_structured()
    test_linebuffer_handles_partial_and_multiple_frames()
    test_blank_lines_ignored()
    print("service/test_protocol: OK")
