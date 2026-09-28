"""The on-demand thread sampler reports where each thread spends its time."""

import json
import threading
import time

from conquest import thread_sampler


def test_sample_counts_frames_of_other_threads():
    stop = threading.Event()

    def spin_here():
        while not stop.is_set():
            time.sleep(0.001)

    worker = threading.Thread(target=spin_here, name="spinner", daemon=True)
    worker.start()
    try:
        result = thread_sampler.sample(0.1, 0.005)
    finally:
        stop.set()
        worker.join()
    spinner = result["threads"]["spinner"]
    assert spinner["samples"] > 0
    assert spinner["native_id"] == worker.native_id
    assert any("spin_here" in leaf for leaf, _ in spinner["leaves"])
    assert threading.current_thread().name not in result["threads"]


def test_poll_runs_once_per_request_and_writes_the_report(tmp_path):
    request = tmp_path / "thread-profile.request"
    report = tmp_path / "thread-profile.json"
    assert thread_sampler.poll(request, report) is False  # nothing requested
    request.write_text(json.dumps({"seconds": 1}), encoding="utf-8")
    assert thread_sampler.poll(request, report) is True
    assert not request.exists()
    request.write_text("", encoding="utf-8")
    assert thread_sampler.poll(request, report) is False  # one run at a time
    deadline = time.monotonic() + 5
    while not report.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    data = json.loads(report.read_text(encoding="utf-8"))
    assert 1 <= data["seconds"] < 3 and "MainThread" in data["threads"]
    assert thread_sampler._running.acquire(blocking=False)
    thread_sampler._running.release()


def test_poll_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(thread_sampler, "state_path", lambda value: 1 / 0)
    assert thread_sampler.poll() is False
