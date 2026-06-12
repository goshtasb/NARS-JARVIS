"""ADR-056 Phase 2 — the concurrency verification Synapse asked for: prove an off-loop CloudJob lets the
daemon's select() loop keep draining local telemetry frames WHILE a multi-second cloud call is in flight.
No network — the 'cloud call' is a thunk that sleeps to simulate latency."""
import os
import select
import threading
import time

from cloud_egress import CloudResult
from service.cloud_job import CloudJob


def test_result_delivered_over_self_pipe():
    job = CloudJob(lambda: CloudResult(ok=True, text="done"))
    # the fd becomes readable exactly when the result is ready
    r, _, _ = select.select([job.fileno()], [], [], 2.0)
    assert r == [job.fileno()]
    res = job.result()
    assert res.ok and res.text == "done"
    job.close()


def test_thrown_driver_becomes_error_result_not_a_crash():
    def boom(): raise RuntimeError("kaboom")
    job = CloudJob(boom)
    select.select([job.fileno()], [], [], 2.0)
    res = job.result()
    assert not res.ok and res.kind == "network" and "kaboom" in res.error
    job.close()


def test_offloop_cloud_call_does_not_stall_the_select_loop():
    """THE proof: a ~1.2s cloud call runs off-loop while a fake Sentinel emits telemetry frames every
    50ms. A select() loop drains both. Assert essentially every frame emitted DURING the call is read
    before the result arrives — i.e. the loop never blocked on the cloud call (no dropped frames)."""
    sensor_r, sensor_w = os.pipe()
    os.set_blocking(sensor_r, False)
    CALL_SECONDS = 1.2
    FRAME_EVERY = 0.05

    def slow_cloud():
        time.sleep(CALL_SECONDS)               # simulate network latency (releases the GIL, like urllib)
        return CloudResult(ok=True, text="cloud done")

    emitted = {"n": 0}
    stop = threading.Event()
    def emit():
        while not stop.is_set():
            try: os.write(sensor_w, b"frame\n"); emitted["n"] += 1
            except OSError: break
            time.sleep(FRAME_EVERY)
    emitter = threading.Thread(target=emit, daemon=True); emitter.start()

    job = CloudJob(slow_cloud)
    frames_read = 0
    result = None
    deadline = time.monotonic() + CALL_SECONDS + 2.0
    while result is None and time.monotonic() < deadline:
        ready, _, _ = select.select([sensor_r, job.fileno()], [], [], 0.1)   # the daemon's loop shape
        for fd in ready:
            if fd == sensor_r:
                os.read(sensor_r, 65536); frames_read += 1                   # drain the Sentinel
            elif fd == job.fileno():
                result = job.result()
    stop.set(); emitter.join(timeout=1.0)

    assert result is not None and result.ok                  # the cloud call completed via the loop
    # frames kept flowing throughout the in-flight call: expect ~CALL_SECONDS/FRAME_EVERY (~24) reads.
    expected = int(CALL_SECONDS / FRAME_EVERY)
    assert frames_read >= expected - 4, f"loop stalled: read {frames_read} frames, expected ~{expected}"
    job.close(); os.close(sensor_r); os.close(sensor_w)
