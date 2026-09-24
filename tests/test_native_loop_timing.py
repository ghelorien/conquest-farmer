import pytest

from conquest.native_loop_timing import NativeLoopTiming


def test_nested_commit_cost_is_excluded_from_read_cost_and_emitted_once_per_minute():
    now = [0.0]
    timing = NativeLoopTiming(clock=lambda: now[0])
    timing.begin(True)
    timing.stage("supervisor")
    now[0] = 2
    with timing.measure("event_commit"):
        now[0] = 5
    now[0] = 7
    timing.stage(None)
    assert timing.finish() is None
    now[0] = 60
    summary = timing.finish()
    assert summary["window_seconds"] == 60
    assert summary["timings"]["supervisor"] == {
        "count": 2,
        "total_seconds": 4,
        "max_seconds": 2,
    }
    assert summary["timings"]["event_commit"] == {
        "count": 1,
        "total_seconds": 3,
        "max_seconds": 3,
    }
    assert (
        summary["timings"]["moving_event_commit"] == summary["timings"]["event_commit"]
    )
    assert timing.finish() is None
    timing.sample("supervisor", 100)
    assert summary["timings"]["supervisor"]["total_seconds"] == 4  # Detached window.


def test_failure_and_early_continue_costs_are_retained_by_finally():
    now = [0.0]
    timing = NativeLoopTiming(clock=lambda: now[0])
    reports = []
    for stage in ("inventory_player_projection", "escape_scene", "care_skill_checks"):
        try:
            timing.begin(False)
            timing.stage(stage)
            now[0] += 20
            if stage == "escape_scene":
                raise ValueError("Transient read gap")
            continue
        except ValueError:
            pass
        finally:
            summary = timing.finish()
            if summary:
                reports.append(summary)
    assert len(reports) == 1
    for stage in ("inventory_player_projection", "escape_scene", "care_skill_checks"):
        assert reports[0]["timings"][stage]["total_seconds"] == 20
        assert "moving_" + stage not in reports[0]["timings"]


def test_dispatch_failure_retains_latency_without_suppressing_exception():
    now = [0.0]
    timing = NativeLoopTiming(clock=lambda: now[0])
    timing.begin(True)
    with pytest.raises(ValueError, match="Manual stop"):
        with timing.measure("movement_dispatch"):
            now[0] = 2
            raise ValueError("Manual stop")
    now[0] = 60
    row = timing.finish()["timings"]["movement_dispatch"]
    assert row == {"count": 1, "total_seconds": 2, "max_seconds": 2}


def test_arrival_metrics_use_existing_exact_position_once_for_each_move():
    now = [10.0]
    timing = NativeLoopTiming(clock=lambda: now[0])
    move = ((0, 0), 10.0, (12, 0))
    now[0] = 10.2
    timing.observe_arrival(move, (11, 0))
    timing.verifier(move)
    assert not timing.values
    now[0] = 10.3
    timing.observe_arrival(move, (12, 0))
    now[0] = 11.5
    timing.verifier(move)
    now[0] = 12
    timing.observe_arrival(move, (12, 0))
    timing.verifier(move)
    assert timing.values["first_exact_arrival_seconds"]["count"] == 1
    assert timing.values["first_exact_arrival_seconds"][
        "total_seconds"
    ] == pytest.approx(0.3)
    assert timing.values["exact_arrival_to_verifier_seconds"][
        "total_seconds"
    ] == pytest.approx(1.2)
    assert timing.values["exact_arrival_verifier_elapsed_seconds"][
        "total_seconds"
    ] == pytest.approx(1.5)
    second = ((12, 0), 12.0, (24, 0))
    now[0] = 13
    timing.observe_arrival(second, (24, 0))
    timing.verifier(second)
    assert timing.values["first_exact_arrival_seconds"]["count"] == 2


def test_diagnostic_clock_failure_disables_timing_without_masking_game_error():
    broken = [False]

    def clock():
        if broken[0]:
            raise RuntimeError("Diagnostic clock failed")
        return 1.0

    timing = NativeLoopTiming(clock=clock)
    with pytest.raises(ValueError, match="Original input error"):
        with timing.measure("movement_dispatch"):
            broken[0] = True
            raise ValueError("Original input error")
    assert not timing.enabled and timing.finish() is None
    with timing.measure("another_input"):
        pass


def test_bookkeeping_or_summary_write_failure_cannot_stop_gameplay():
    now = [0.0]
    timing = NativeLoopTiming(clock=lambda: now[0])
    timing.values = None
    timing.sample("supervisor", 1)
    assert not timing.enabled
    timing = NativeLoopTiming(clock=lambda: now[0])
    now[0] = 60

    def unavailable_sink(summary):
        raise OSError("Diagnostic journal unavailable")

    with pytest.raises(ValueError, match="Original failure"):
        try:
            raise ValueError("Original failure")
        finally:
            timing.flush(unavailable_sink)
    assert not timing.enabled


def test_initial_clock_failure_leaves_disabled_noop_instrumentation():
    def clock():
        raise RuntimeError("Clock unavailable")

    timing = NativeLoopTiming(clock=clock)
    timing.begin(True)
    timing.stage("supervisor")
    timing.observe_arrival(((0, 0), 0, (1, 1)), (1, 1))
    with timing.measure("event_commit"):
        pass
    assert not timing.enabled and timing.finish() is None
