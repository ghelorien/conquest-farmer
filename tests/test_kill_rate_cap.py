"""Rate-bounded kill-counter qualification for dense Scatter routes.

The kill counter is read once per combat loop. A fixed 32 (Scatter) / 10
(single shot) increment cap stopped dense routes whose legitimate increments
between delayed reads exceeded 32. The allowance is now
``min(HARD_MAX, max(base, ceil(elapsed * MAX_KILLS_PER_SECOND)))`` where
``elapsed`` is the time since the previous uninterrupted counter read, and every
journal reader validates the same rule through ``conquest.kill_increment``.

Ways this could fail (each is exercised below):

 1. A dense-route increment of 50 observed 12 s after the previous read is
    still treated as a discontinuity and stops the route (or is excluded).
 2. A burst of 50 observed 0.2 s after the previous read is verified instead
    of stopping (gap recovery off) or being excluded as a gap (gap recovery on).
 3. An increment above HARD_MAX is verified however long the interval was.
 4. The rate allowance is unbounded or misses the boundary (128 verified,
    129 excluded).
 5. A counter decrease or reset to zero is counted, or the increment after a
    reset is measured against the pre-reset value.
 6. Kills made during a manual session are attributed to automation, or the
    manual interval earns a rate allowance.
 7. A non-manual waiting interval (farming off, focus loss, recovery) turns
    idle wall time into a rate allowance that verifies an increment which the
    old fixed cap would have excluded.
 8. The single-shot (non-right-click) path loses its base cap of 10, or never
    receives the rate allowance for a genuinely long interval.
 9. kill_verified payloads omit the elapsed/base/limit evidence, so readers
    cannot re-check the rule; gap/discontinuity evidence omits it too.
10. Readers (Discord recent kills, SessionKills, town-visit checkpoint,
    route windows) reject a new, rule-conforming event above 32.
11. Readers accept malformed events: zero/negative/bool/float/string counts,
    counts above HARD_MAX, a recorded limit that does not match the rule,
    negative/non-finite/bool elapsed, an unknown base, or a count above the
    recorded limit.
12. Historical events (no elapsed field, 1 <= count <= 32) stop being accepted,
    or historical events above 32 start being accepted.
13. Readers keep private copies of the literal 32 instead of the shared rule.
"""

import importlib
import json
import logging
import math
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conquest import trial
from conquest.farmer_profile import CombatSpeed
from conquest.memory_inventory import InventorySnapshot, Item


@pytest.fixture
def rule():
    return importlib.import_module("conquest.kill_increment")


def new_event(rule, count, elapsed, base=32, **override):
    payload = {
        "count": count,
        "counter_elapsed_seconds": elapsed,
        "increment_base": base,
        "increment_limit": rule.allowed_increment(elapsed, base),
    }
    payload.update(override)
    return payload


# --- shared rule ---------------------------------------------------------


def test_constants_are_explicit_and_bounded(rule):
    assert rule.SCATTER_BASE_LIMIT == 32 and rule.SINGLE_BASE_LIMIT == 10
    assert rule.MAX_KILLS_PER_SECOND == 5
    assert rule.HARD_MAX == 128
    # 50 kills after 12 s must fit; 50 after 0.2 s must not.
    assert rule.allowed_increment(12.0, 32) >= 50
    assert rule.allowed_increment(0.2, 32) == 32


@pytest.mark.parametrize(
    "elapsed,base,expected",
    [
        (None, 32, 32),
        (None, 10, 10),
        (0.0, 32, 32),
        (0.2, 32, 32),
        (6.0, 32, 32),
        (12.0, 32, 60),
        (20.0, 32, 100),
        (25.6, 32, 128),
        (60.0, 32, 128),
        (3600.0, 32, 128),
        (0.5, 10, 10),
        (2.0, 10, 10),
        (3.0, 10, 15),
        (1000.0, 10, 128),
    ],
)
def test_allowed_increment(rule, elapsed, base, expected):
    assert rule.allowed_increment(elapsed, base) == expected


@pytest.mark.parametrize(
    "elapsed,base",
    [
        (-0.1, 32),
        (math.nan, 32),
        (math.inf, 32),
        (True, 32),
        ("12", 32),
        (12.0, 7),
        (12.0, True),
        (12.0, 32.0),
    ],
)
def test_allowed_increment_rejects_invalid_inputs(rule, elapsed, base):
    with pytest.raises(ValueError):
        rule.allowed_increment(elapsed, base)


def test_reader_accepts_historical_events(rule):
    assert rule.verified_kill_count({"count": 1}) == 1
    assert rule.verified_kill_count({"count": 20, "total": 99}) == 20
    assert rule.verified_kill_count({"count": 32}) == 32
    assert rule.verified_kill_count({"count": 33}) is None
    assert rule.verified_kill_count({"count": 128}) is None


def test_reader_accepts_rule_conforming_new_events(rule):
    assert rule.verified_kill_count(new_event(rule, 50, 12.0)) == 50
    assert rule.verified_kill_count(new_event(rule, 128, 60.0)) == 128
    assert rule.verified_kill_count(new_event(rule, 20, None)) == 20
    assert rule.verified_kill_count(new_event(rule, 15, 3.0, 10)) == 15
    assert rule.verified_kill_count(new_event(rule, 10, 0.2, 10)) == 10


@pytest.mark.parametrize(
    "payload",
    [
        {"count": 0},
        {"count": -1},
        {"count": True},
        {"count": 2.0},
        {"count": "3"},
        {"count": None},
        {},
        [],
        None,
        "count",
    ],
)
def test_reader_rejects_malformed_historical_events(rule, payload):
    assert rule.verified_kill_count(payload) is None


@pytest.mark.parametrize(
    "override",
    [
        {"count": 50, "counter_elapsed_seconds": 0.2, "increment_limit": 32},
        {"count": 50, "counter_elapsed_seconds": 0.2, "increment_limit": 60},
        {"count": 129, "counter_elapsed_seconds": 100.0, "increment_limit": 129},
        {"count": 129, "counter_elapsed_seconds": 100.0, "increment_limit": 128},
        {"count": 50, "counter_elapsed_seconds": -12.0},
        {"count": 50, "counter_elapsed_seconds": math.inf},
        {"count": 50, "counter_elapsed_seconds": True},
        {"count": 50, "counter_elapsed_seconds": "12"},
        {"count": 50, "increment_base": 64},
        {"count": 50, "increment_base": None},
        {"count": 50, "increment_limit": "60"},
        {"count": 50, "increment_limit": None},
        {
            "count": 11,
            "counter_elapsed_seconds": 0.5,
            "increment_base": 10,
            "increment_limit": 10,
        },
        {
            "count": 20,
            "counter_elapsed_seconds": None,
            "increment_base": 10,
            "increment_limit": 10,
        },
        {"count": 0},
        {"count": True},
    ],
)
def test_reader_rejects_nonconforming_new_events(rule, override):
    payload = new_event(rule, 50, 12.0)
    payload.update(override)
    assert rule.verified_kill_count(payload) is None


def test_reader_rejects_partial_rule_evidence(rule):
    for missing in ("counter_elapsed_seconds", "increment_base", "increment_limit"):
        payload = new_event(rule, 20, 12.0)
        del payload[missing]
        assert rule.verified_kill_count(payload) is None, missing


def test_readers_share_the_rule_instead_of_literals():
    root = Path(__file__).resolve().parents[1] / "src" / "conquest"
    for name in (
        "discord_notify.py",
        "session_kills.py",
        "town_visit.py",
        "route_optimization.py",
    ):
        text = (root / name).read_text(encoding="utf-8")
        assert not re.search(r"<=\s*32\b", text), name
        assert "kill_increment" in text, name


# --- run_trial ------------------------------------------------------------


def run_counter_script(tmp_path, monkeypatch, steps, *, button="right", gaps=False):
    """Drive run_trial through scripted counter reads.

    Each step advances the fake clock by ``delay`` before the loop observes it.
    ``waiting`` = "manual" / "other" makes the supervisor report a wait (no
    counter read); otherwise the loop samples ``counter``.
    """
    import win32api

    now = [10.0]
    index = [0]
    current = [steps[0]["counter"]]
    config = yaml.safe_load(Path("profiles/pheasant-foreground-trial.yaml").read_text())
    config.update(
        character="CounterTest",
        observation_mode="memory_only",
        kite_when_surrounded=False,
        adaptive_scatter=False,
        jump_scatter=False,
        attack_button=button,
        route=[],
        healing_enabled=False,
        loot_allowlist=[],
        interval=0.15,
        client_size=[1036, 793],
        player_anchor=[518, 396],
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(trial.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(trial.time, "time", lambda: 1_000_000.0 + now[0])
    monkeypatch.setattr(
        trial.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay)
    )
    monkeypatch.setattr(win32api, "GetAsyncKeyState", lambda _: 0)
    monkeypatch.setattr(
        trial, "load_combat_speed", lambda _: CombatSpeed(counter_gap_recovery=gaps)
    )

    def observe():
        if index[0] >= len(steps):
            return {"stop": True, "waiting": True, "health_ratio": 1.0}
        step = steps[index[0]]
        index[0] += 1
        now[0] += step.get("delay", 0)
        if "counter" in step:
            current[0] = step["counter"]
        if step.get("waiting") == "manual":
            return {"waiting": True, "manual_session": True, "health_ratio": 1.0}
        if step.get("waiting") == "other":
            return {"waiting": True, "health_ratio": 1.0}
        return {"health_ratio": 1.0, "waiting": False, "defending": False}

    class Session:
        def request(self, operation, body=None):
            if operation == "health":
                return {"input_revision": 7, "window": {"hwnd": 1}}
            assert operation == "sample"
            if len(body["fields"]) == 2:
                return {
                    "fields": [
                        {"name": "name", "value": "CounterTest"},
                        {"name": "kill_counter", "value": [current[0]]},
                    ]
                }
            values = {
                "name": "CounterTest",
                "position": [423, 455],
                "max_hp": [100],
                "kill_counter": [current[0]],
                "level": [18],
                "map": [1002],
            }
            return {"fields": [{"name": k, "value": v} for k, v in values.items()]}

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.01
            return InventorySnapshot(
                now[0],
                now[0],
                (Item(1, 1000000, 1, 1, 0),),
                Item(2, 1050000, 200, 200, None),
                0,
                40,
            )

    supervisor = SimpleNamespace(
        last_target=None,
        recovery=SimpleNamespace(terrain=SimpleNamespace(width=1000, height=1000)),
        observe=observe,
        memory_targets=lambda *a: [],
        loot_step=lambda *a: False,
        finish_target=lambda *a: None,
    )
    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    # No monsters are scripted; idle patrol expansion is outside this rule.
    monkeypatch.setattr(
        trial,
        "AdaptivePatrol",
        lambda *a: SimpleNamespace(
            expand_config=lambda *a, **k: None, attacked=lambda *a: None
        ),
    )
    monkeypatch.setattr(
        trial,
        "resolve_player",
        lambda *a: dict.fromkeys(
            ("name", "position", "max_hp", "kill_counter", "level", "map"), 1
        ),
    )
    camera = SimpleNamespace(geometry=lambda: (0, 0), close=lambda: None)
    output = tmp_path / "run"
    result = trial.run_trial(
        path,
        None,
        output,
        1800,
        logging.getLogger("test"),
        session_override=Session(),
        camera_factory=lambda *a: camera,
        supervisor=supervisor,
    )
    with sqlite3.connect(output / "trial.sqlite3") as db:
        events = [
            (t, e, json.loads(p))
            for t, e, p in db.execute("select time,event,payload from events")
        ]
    return result, events, output


def kills_of(events):
    return [p for _, e, p in events if e == "kill_verified"]


def gaps_of(events):
    return [p for _, e, p in events if e == "kill_counter_gap"]


@pytest.mark.parametrize("gaps", [False, True])
def test_dense_route_increment_after_long_interval_is_verified(
    tmp_path, monkeypatch, rule, gaps
):
    result, events, _ = run_counter_script(
        tmp_path,
        monkeypatch,
        [
            {"counter": 1000},
            {"delay": 0.5, "counter": 1010},
            {"delay": 12.0, "counter": 1060},
        ],
        gaps=gaps,
    )
    assert result["reason"] == "control_changed"
    kills = kills_of(events)
    assert [k["count"] for k in kills] == [10, 50]
    assert kills[-1]["total"] == 60 and not gaps_of(events)
    dense = kills[-1]
    assert dense["counter_elapsed_seconds"] >= 12.0
    assert dense["increment_base"] == 32
    assert dense["increment_limit"] == rule.allowed_increment(
        dense["counter_elapsed_seconds"], 32
    )
    assert all(rule.verified_kill_count(k) == k["count"] for k in kills)


def test_burst_without_gap_recovery_stops_with_evidence(tmp_path, monkeypatch):
    result, events, _ = run_counter_script(
        tmp_path,
        monkeypatch,
        [{"counter": 1000}, {"delay": 0.2, "counter": 1050}],
    )
    assert result["reason"] == "kill_counter_discontinuity"
    assert not kills_of(events)
    (stop,) = [p for _, e, p in events if e == "kill_counter_discontinuity"]
    assert stop["unverified_increment"] == 50
    assert stop["increment_limit"] == 32
    assert 0.2 <= stop["counter_elapsed_seconds"] < 6


def test_burst_with_gap_recovery_is_excluded_with_evidence(tmp_path, monkeypatch):
    result, events, _ = run_counter_script(
        tmp_path,
        monkeypatch,
        [
            {"counter": 1000},
            {"delay": 0.2, "counter": 1050},
            {"delay": 0.5, "counter": 1055},
        ],
        gaps=True,
    )
    assert result["reason"] == "control_changed"
    (gap,) = gaps_of(events)
    assert gap["unverified_increment"] == 50 and gap["increment_limit"] == 32
    assert 0.2 <= gap["counter_elapsed_seconds"] < 6
    assert [k["count"] for k in kills_of(events)] == [5]
    assert kills_of(events)[-1]["total"] == 5


@pytest.mark.parametrize("increment,verified", [(128, True), (129, False)])
def test_hard_maximum_bounds_any_interval(tmp_path, monkeypatch, increment, verified):
    _, events, _ = run_counter_script(
        tmp_path,
        monkeypatch,
        [{"counter": 1000}, {"delay": 60.0, "counter": 1000 + increment}],
        gaps=True,
    )
    assert [k["count"] for k in kills_of(events)] == ([increment] if verified else [])
    assert len(gaps_of(events)) == int(not verified)
    if not verified:
        assert gaps_of(events)[0]["increment_limit"] == 128


def test_decrease_and_reset_rebaseline_without_counting(tmp_path, monkeypatch):
    result, events, _ = run_counter_script(
        tmp_path,
        monkeypatch,
        [
            {"counter": 1000},
            {"delay": 12.0, "counter": 900},
            {"delay": 12.0, "counter": 950},
            {"delay": 1.0, "counter": 0},
            {"delay": 0.5, "counter": 5},
        ],
        gaps=True,
    )
    assert [k["count"] for k in kills_of(events)] == [50, 5]
    assert [k["counter"] for k in kills_of(events)] == [950, 5]
    assert not gaps_of(events)
    assert result["reason"] == "control_changed"


def test_manual_session_kills_are_never_attributed(tmp_path, monkeypatch):
    _, events, _ = run_counter_script(
        tmp_path,
        monkeypatch,
        [
            {"counter": 1000},
            {"delay": 5.0, "counter": 1020, "waiting": "manual"},
            {"delay": 5.0, "counter": 1040, "waiting": "manual"},
            {"delay": 0.5, "counter": 1040},
            {"delay": 0.5, "counter": 1050},
        ],
        gaps=True,
    )
    assert [k["count"] for k in kills_of(events)] == [10]
    assert not gaps_of(events)


def test_non_manual_wait_earns_no_rate_allowance(tmp_path, monkeypatch):
    _, events, _ = run_counter_script(
        tmp_path,
        monkeypatch,
        [
            {"counter": 1000},
            {"delay": 15.0, "counter": 1040, "waiting": "other"},
            {"delay": 0.5, "counter": 1040},
        ],
        gaps=True,
    )
    assert not kills_of(events)
    (gap,) = gaps_of(events)
    assert gap["unverified_increment"] == 40 and gap["increment_limit"] == 32
    assert gap["counter_elapsed_seconds"] is None


def test_non_manual_wait_keeps_existing_fixed_cap(tmp_path, monkeypatch):
    # Pre-existing behaviour within the base cap is unchanged.
    _, events, _ = run_counter_script(
        tmp_path,
        monkeypatch,
        [
            {"counter": 1000},
            {"delay": 15.0, "counter": 1020, "waiting": "other"},
            {"delay": 0.5, "counter": 1020},
        ],
        gaps=True,
    )
    (kill,) = kills_of(events)
    assert kill["count"] == 20 and kill["counter_elapsed_seconds"] is None
    assert kill["increment_limit"] == 32


@pytest.mark.parametrize(
    "delay,increment,verified",
    [(0.2, 10, True), (0.5, 11, False), (3.0, 11, True), (3.0, 40, False)],
)
def test_single_shot_path_keeps_base_ten_unless_rate_allows(
    tmp_path, monkeypatch, rule, delay, increment, verified
):
    result, events, _ = run_counter_script(
        tmp_path,
        monkeypatch,
        [{"counter": 1000}, {"delay": delay, "counter": 1000 + increment}],
        button="left",
    )
    kills = kills_of(events)
    if verified:
        assert result["reason"] == "control_changed"
        (kill,) = kills
        assert kill["count"] == increment and kill["increment_base"] == 10
        assert rule.verified_kill_count(kill) == increment
    else:
        assert result["reason"] == "kill_counter_discontinuity" and not kills


# --- readers ------------------------------------------------------------


def journal(path, rows):
    with sqlite3.connect(path) as db:
        db.execute(
            "create table if not exists events(time real,event text,payload text)"
        )
        db.executemany(
            "insert into events values(?,?,?)",
            [(t, e, json.dumps(p)) for t, e, p in rows],
        )


def test_discord_recent_kills_uses_shared_rule(tmp_path, rule):
    from conquest.discord_notify import recent_kills

    path = tmp_path / "trial.sqlite3"
    journal(
        path,
        [
            (500, "kill_verified", {"count": 20}),
            (600, "kill_verified", new_event(rule, 50, 12.0)),
        ],
    )
    assert recent_kills(path, 1000, 3600) == 70
    journal(path, [(700, "kill_verified", new_event(rule, 50, 0.2))])
    assert recent_kills(path, 1000, 3600) is None


def test_session_kills_uses_shared_rule(tmp_path, rule):
    from conquest.session_kills import SessionKills

    clock = [100.0]
    session = SessionKills(tmp_path, clock=lambda: clock[0])
    session.begin()
    path = tmp_path / "trial.sqlite3"
    journal(
        path,
        [
            (101, "kill_verified", {"count": 32}),
            (102, "kill_verified", new_event(rule, 128, 30.0)),
        ],
    )
    clock[0] = 200
    assert session.refresh()["kills"] == 160
    journal(path, [(103, "kill_verified", new_event(rule, 129, 30.0))])
    snapshot = session.refresh()
    assert snapshot["kills"] == 160 and snapshot["kill_metrics_note"]


def test_town_visit_checkpoint_uses_shared_rule(tmp_path, rule):
    from conquest.town_visit import kill_checkpoint

    (tmp_path / "app-state.json").write_text(
        json.dumps(
            {
                "character": "Parasite",
                "updated_at": 1000,
                "kill_session_active": True,
                "kill_session_started_at": 900,
                "kills": 50,
                "kill_metrics_note": None,
            }
        )
    )
    path = tmp_path / "trial.sqlite3"
    journal(path, [(999, "kill_verified", new_event(rule, 50, 12.0))])
    checkpoint = kill_checkpoint(now=1000, output=tmp_path)
    assert checkpoint["available"]
    assert checkpoint["last_kill"] == {"rowid": 1, "time": 999, "count": 50}
    journal(
        path, [(1000, "kill_verified", new_event(rule, 50, 12.0, increment_limit=64))]
    )
    assert not kill_checkpoint(now=1000, output=tmp_path)["available"]


def test_route_windows_accept_payloads_under_shared_rule(rule):
    from conquest.route_optimization import fixed_route_windows

    kills = [
        (10, 4),
        (20, {"count": 32}),
        (30, new_event(rule, 60, 12.0)),
        (40, 33),
        (50, {"count": 33}),
        (60, new_event(rule, 129, 60.0)),
        (70, new_event(rule, 50, 0.2)),
        (80, True),
    ]
    (window,) = fixed_route_windows(kills, started_at=0, now=900)
    assert window["kills"] == 96


# --- end to end -----------------------------------------------------------


def dense_route_artifact(tmp_path, monkeypatch, rule):
    """A dense Scatter route through run_trial and every journal reader."""
    from conquest.discord_notify import recent_kills
    from conquest.route_optimization import fixed_route_windows
    from conquest.session_kills import SessionKills
    from conquest.town_visit import kill_checkpoint

    wall = [1_000_000.0]
    session = SessionKills(tmp_path / "run", clock=lambda: wall[0])
    session.begin()
    steps = [
        {"counter": 5000},
        {"delay": 0.4, "counter": 5012},  # ordinary Scatter batch
        {"delay": 12.0, "counter": 5062},  # 50 after 12 s: dense, verified
        {"delay": 0.2, "counter": 5112},  # 50 after 0.2 s: excluded gap
        {"delay": 20.0, "counter": 5212},  # 100 after 20 s: verified
        {"delay": 60.0, "counter": 5352},  # 140 > HARD_MAX: excluded gap
        {"delay": 1.0, "counter": 4000},  # reset/decrease: rebaseline only
        {"delay": 0.5, "counter": 4020},  # verified 20
        {"delay": 5.0, "counter": 4060, "waiting": "manual"},  # manual kills
        {"delay": 0.5, "counter": 4060},  # rebaseline after manual session
        {"delay": 0.5, "counter": 4070},  # verified 10
        {"delay": 15.0, "counter": 4110, "waiting": "other"},  # idle wait
        {"delay": 0.5, "counter": 4110},  # 40 with no allowance: gap
    ]
    result, events, output = run_counter_script(tmp_path, monkeypatch, steps, gaps=True)
    end_wall = max(t for t, _, _ in events)
    wall[0] = end_wall
    sessions = session.refresh()
    (output / "app-state.json").write_text(
        json.dumps(
            {
                "character": "Parasite",
                "updated_at": end_wall,
                "kill_session_active": True,
                "kill_session_started_at": 1_000_000.0,
                "kills": sessions["kills"],
                "kill_metrics_note": None,
            }
        )
    )
    checkpoint = kill_checkpoint(now=end_wall, output=output)
    kills = kills_of(events)
    windows = fixed_route_windows(
        [(t, p) for t, e, p in events if e == "kill_verified"],
        started_at=1_000_000.0,
        now=1_000_000.0 + 900,
        window_seconds=900,
    )
    artifact = {
        "rule": {
            "scatter_base": rule.SCATTER_BASE_LIMIT,
            "single_base": rule.SINGLE_BASE_LIMIT,
            "max_kills_per_second": rule.MAX_KILLS_PER_SECOND,
            "hard_max": rule.HARD_MAX,
        },
        "stop_reason": result["reason"],
        "verified": [
            {
                "count": k["count"],
                "counter": k["counter"],
                "elapsed": None
                if k["counter_elapsed_seconds"] is None
                else round(k["counter_elapsed_seconds"], 2),
                "limit": k["increment_limit"],
                "reader_accepts": rule.verified_kill_count(k) == k["count"],
            }
            for k in kills
        ],
        "excluded_gaps": [
            {
                "increment": g["unverified_increment"],
                "limit": g["increment_limit"],
            }
            for g in gaps_of(events)
        ],
        "readers": {
            "trial_total": kills[-1]["total"],
            "discord_recent_kills": recent_kills(
                output / "trial.sqlite3", end_wall, 3600
            ),
            "session_kills": sessions["kills"],
            "session_note": sessions["kill_metrics_note"],
            "town_visit_available": checkpoint["available"],
            "town_visit_last_kill": checkpoint["last_kill"]["count"],
            "route_window_kills": windows[0]["kills"],
        },
    }
    target = tmp_path / "kill-rate-cap-e2e.json"
    target.write_text(json.dumps(artifact, indent=2, sort_keys=True))
    return target


def test_dense_route_end_to_end_artifact(tmp_path, monkeypatch, rule):
    first = dense_route_artifact(tmp_path / "first", monkeypatch, rule)
    second = dense_route_artifact(tmp_path / "second", monkeypatch, rule)
    # Repeatable: an independent rerun of the same scripted route is identical.
    assert first.read_bytes() == second.read_bytes()
    loaded = json.loads(first.read_text())

    assert loaded["stop_reason"] == "control_changed"
    assert [v["count"] for v in loaded["verified"]] == [12, 50, 100, 20, 10]
    assert all(v["reader_accepts"] for v in loaded["verified"])
    assert [g["increment"] for g in loaded["excluded_gaps"]] == [50, 140, 40]
    assert [g["limit"] for g in loaded["excluded_gaps"]] == [32, 128, 32]
    assert loaded["readers"] == {
        "trial_total": 192,
        "discord_recent_kills": 192,
        "session_kills": 192,
        "session_note": None,
        "town_visit_available": True,
        "town_visit_last_kill": 10,
        "route_window_kills": 192,
    }
