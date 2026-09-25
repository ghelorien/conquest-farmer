"""Once-only restart of a restock whose route failed before any transaction.

Live incident (2026-09-25, town visit 4d2f63a0..., reasons ["restock"]): the
visit began at 04:00:27 (ammo_unavailable), travel toward the Phoenix
Warehouseman started from the Bandit field, and three seconds later the route
failed inside travel care (overnight._run_route -> restock -> fund_restock ->
open_warehouse -> travel -> _travel -> travel_care.check -> worker.request).
No purchase, sale, deposit, transfer, Meteor or merchant work had happened.
Every restart since is refused by require_town_work_complete because no
continuation kind covers "nothing happened yet".  Meanwhile the farmer died in
the field.  No game process, live journal or native input is touched here.

Failure modes, written before the implementation (each is a test below):

 FM1  Any event of this visit other than movement/care/bookkeeping (purchase,
      sale, valuable_stored, silver_*, meteor_*, merchant_*, warehouse_*,
      town_activity, restock_* completion, an unknown event, or an observation
      retry of a transactional town action) -> refuse, no marker, no input.
 FM2  The recorded route failure is missing, precedes required_at, is not the
      exact pre-transaction chain restock -> fund_restock -> open_warehouse ->
      travel -> _travel (e.g. inside transfer, at the warehouse open, inside
      after_shopping), or its trace lost the restock frame -> refuse.
 FM3  A second gameplay failure, or any failure other than a read-only
      restart refusal after the original (a failure with a restock frame is
      never a refusal, even beneath this module) -> refuse.  A visit without
      its required_at -> refuse.
 FM4  A transfers.jsonl row at/after required_at, or one with an invalid time
      -> refuse (money may have moved).
 FM5  A merchant delivery transaction/admission created or updated at/after
      required_at, or any pending/uncertain delivery -> refuse.
 FM6  Meteor banking pending, or its started_at at/after required_at -> refuse.
 FM7  Any other hold (journey, route, delivery, withdrawal, overflow, halt) or
      this visit's Market service visit -> refuse.
 FM8  Process identity: restock_target missing/invalid, a different game
      process in health (the original process died/restarted), or a different
      loop identity -> refuse before any control change.  Route changed ->
      refuse.
 FM9  A recorded starting ownership snapshot that differs from fresh carried
      ownership -> refuse, no marker.  (Equal snapshot -> proceeds.)
 FM10 The once-only marker is already present -> refuse replay without
      reading the game, stopping the farm or restocking.
 FM11 Other continuation kinds (pre-admission tail, operator claim, verified
      tail, urgent state, cash-tail claim), other reasons, completed visits,
      returning visits and an empty visit -> return False untouched.
 FM12 Manual Stop before the marker -> nothing written, no input.
 FM13 A dead farmer is revived through the existing living()/travel-care path
      first; no proof step requires a living character before that.  After
      revival: manual mouse/fence, pause, stale memory, farming still On, an
      unexpected map or profile, or a new hold -> refuse without a marker.
 FM14 The marker is durable before restock()'s first input, restock() runs
      exactly once and re-enters the same town visit (no new visit id).
 FM15 End to end through the real OvernightLoop._run_route from the live
      journals: dead farmer -> revive -> marker -> restock -> warehouse trip
      (with the Fix-1 heal/hover race) -> warehouse reached; a later restart
      refuses the replay.  A JSON artifact under tmp_path is asserted on.
"""

import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace as NS

import field_fakes
import pytest

from conquest import banking, meteor_banking, town_visit
from conquest import restock_cash_tail as tail
from conquest import restock_restart as restart
from conquest import restock_town_recovery as recovery
from conquest.discord_notify import read_json, write_json
from conquest.merchants import delivery_operation, service_visit

VISIT = "4d2f63a06bab4631875c4e9b9ec01401"
R = 1790323227.0192347  # required_at
FAILED = 1790323230.2737021
RESTART = 1790323804.778242
REFUSED = 1790323804.9612815
NOW = 1790324400.0
TARGET = deepcopy(field_fakes.TARGET)
GUARD = (
    "Unfinished town work needs read-only transaction reconciliation before "
    "return; do not replay a warehouse, purchase, or monetary action"
)


def trace(*frames):
    return [
        {"file": "C:\\releases\\r41\\src\\conquest\\" + f, "function": fn, "line": 1}
        for f, fn in frames
    ]


LIVE_FAILURE = trace(
    ("overnight.py", "_run_route"),
    ("overnight.py", "restock"),
    ("banking.py", "fund_restock"),
    ("banking.py", "open_warehouse"),
    ("overnight.py", "travel"),
    ("overnight.py", "_travel"),
    ("travel_care.py", "check"),
    ("worker.py", "request"),
)
REFUSAL = trace(
    ("overnight.py", "run"),
    ("overnight.py", "_run_route"),
    ("town_visit.py", "require_town_work_complete"),
)


def ev(time, event, **fields):
    return {"time": time, "event": event, "town_visit_id": VISIT, **fields}


def live_events():
    return [
        {
            "time": R - 0.03,
            "event": "return_required",
            "phase": "restocking",
            "reason": "ammo_unavailable",
            "town_visit_id": None,
        },
        ev(1790323227.706678, "runback_progress", phase="restocking"),
        ev(1790323227.7148945, "travel", phase="restocking", destination=[230, 250]),
        ev(1790323230.2568846, "runback_finished", phase="restocking"),
        ev(
            FAILED,
            "failed",
            phase="needs_attention",
            detail=field_fakes.HOVER,
            error_type="builtins.ValueError",
            failure_trace=LIVE_FAILURE,
        ),
        ev(RESTART, "started", phase="starting"),
        ev(
            REFUSED,
            "failed",
            phase="needs_attention",
            detail=GUARD,
            error_type="builtins.ValueError",
            failure_trace=REFUSAL,
        ),
    ]


def live_row():
    return {
        "version": 2,
        "town_visit_id": VISIT,
        "farmer_profile_id": "farmer",
        "phase": "town_work",
        "reasons": ["restock"],
        "required_at": R,
        "hunt_map_id": 1011,
        "route_id": "bandit",
        "baseline": {
            "available": True,
            "observed_at": R,
            "cursor": 4610578,
            "session_id": 1790217950.0643559,
            "kills": 52896,
        },
        "history": [],
        "restock_target": deepcopy(TARGET),
    }


def seed_journals(tmp_path, monkeypatch, events_path=None):
    """The live banking/meteor/delivery/Market journals, copied into tmp."""
    monkeypatch.setattr(recovery, "EVENTS", events_path or tmp_path / "events.jsonl")
    monkeypatch.setattr(delivery_operation, "JOURNAL", tmp_path / "deliveries.sqlite3")
    banking.LEDGER.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {"time": 1790300423.007553, "direction": "deposit", "amount": 27713},
                {"time": 1790320780.3451571, "direction": "deposit", "amount": 21595},
            )
        )
    )
    write_json(
        meteor_banking.JOURNAL,
        {
            "phase": "completed",
            "started_at": 1790302348.7322836,
            "completed_at": 1790302899.4116347,
        },
    )
    with sqlite3.connect(delivery_operation.JOURNAL) as db:
        db.execute(
            "CREATE TABLE transactions(id TEXT PRIMARY KEY, kind TEXT, phase TEXT,"
            " created REAL, updated REAL)"
        )
        db.execute(
            "CREATE TABLE delivery_admissions(request_id TEXT PRIMARY KEY,"
            " phase TEXT, created REAL, updated REAL)"
        )
        db.execute(
            "INSERT INTO transactions VALUES(?,?,?,?,?)",
            (
                "route-delivery:e6af",
                "farmer_delivery",
                "verified",
                1790302781.93942,
                1790302792.7273846,
            ),
        )
        db.execute(
            "INSERT INTO delivery_admissions VALUES(?,?,?,?)",
            ("route-delivery:e6af", "transaction_started", 1790302781.85, 1790302781.9),
        )
    market = tmp_path / "market.json"
    write_json(
        market,
        {
            "visit_id": "e4e2ee0ea6fb46ff98508d476f8d68d7",
            "town_visit_id": "faf88a59ca1c4393b0006730098420ba",
            "phase": "departed",
        },
    )
    monkeypatch.setattr(service_visit, "MarketVisit", lambda: NS(path=market))
    return market


@pytest.fixture
def rig(tmp_path, monkeypatch):
    x = NS(now=NOW, calls=[], hold=False, tmp=tmp_path, dead=False)
    monkeypatch.setattr(restart.time, "time", lambda: x.now)
    x.market_path = seed_journals(tmp_path, monkeypatch)
    monkeypatch.setattr(recovery, "_other_holds", lambda: x.hold)
    x.visit = town_visit.TownVisit(
        tmp_path / "town-visit.json",
        profile="farmer",
        clock=lambda: x.now,
        probe=lambda: {"available": True},
    )
    write_json(x.visit.path, live_row())
    x.events = live_events()
    x.write_events = lambda: recovery.EVENTS.write_text(
        "".join(json.dumps(e) + "\n" for e in x.events)
    )
    x.write_events()
    x.bag = {
        "items": [{"uid": 700, "type_id": 1000020, "amount": 1}],
        "equipped_ammo": {"uid": 690, "type_id": 1050002, "amount": 0},
        "silver": 26430,
        "capacity": 40,
    }
    x.life = {
        "map_id": 1011,
        "dead_candidate": False,
        "current_hp": 700,
        "position": [191, 250],
    }
    x.controls = {
        "control": {"enabled": False, "paused": False},
        "manual_input_fence": False,
        "manual_mouse": False,
    }
    x.health_target = deepcopy(TARGET)
    x.profile = "farmer"
    x.stale = 0.0
    x.after_revive = lambda: None

    def health():
        x.calls.append("health")
        return {
            "profile_id": x.profile,
            "target": deepcopy(x.health_target),
            "embedded_controls": {
                **deepcopy(x.controls),
                "life": {**deepcopy(x.life), "dead_candidate": x.dead},
                "observed_at": x.now - x.stale,
            },
        }

    def living():
        if x.dead:
            x.calls.append("revive")  # The existing travel-care revive path.
            x.dead = False
            x.after_revive()
        x.calls.append("living")
        return health()

    def town(action, **args):
        assert action == "supplies", action
        x.calls.append("supplies")
        return deepcopy(x.bag)

    def restock():
        state = x.visit.state()
        # The once-only marker is durable before the restarted visit's input.
        assert state[restart.MARKER]["target"] == TARGET
        assert state["town_visit_id"] == VISIT
        x.calls.append("restock")
        # The real restock() re-enters (never replaces) the same visit.
        x.visit.begin("restock", hunt_map_id=1011, route_id="bandit", target=TARGET)
        assert x.visit.state()["town_visit_id"] == VISIT

    def record(event, **fields):
        x.calls.append(("record", event))
        fields.setdefault("town_visit_id", x.visit.active_id())
        x.events.append({"time": x.now, "event": event, **fields})
        x.write_events()

    def stop_farm():
        x.calls.append("stop_farm")
        x.controls["control"]["enabled"] = x.farming_stays_on

    x.farming_stays_on = False
    x.loop = NS(
        town_visit=x.visit,
        identity=deepcopy(TARGET),
        health=health,
        living=living,
        check_stop=lambda: x.calls.append("check_stop"),
        town=town,
        stop_farm=stop_farm,
        record=record,
        restock=restock,
        route=NS(id="bandit", map_id=1011, restock_map_id=1011),
    )
    return x


def assert_untouched(x):
    row = x.visit.state()
    assert restart.MARKER not in row
    assert "restock" not in x.calls
    assert "town_work_completed_at" not in row
    with pytest.raises(ValueError, match="Unfinished town work"):
        x.visit.require_town_work_complete()


# FM14 -- the live class restarts exactly once.
def test_live_zero_transaction_restock_restarts_once(rig):
    x = rig
    assert restart.resume(x.loop) is True
    row = x.visit.state()
    marker = row[restart.MARKER]
    assert marker["target"] == TARGET
    assert marker["failure"]["time"] == FAILED
    assert marker["failure"]["detail"] == field_fakes.HOVER
    assert marker["claimed_at"] == NOW
    assert row["town_visit_id"] == VISIT and row["phase"] == "town_work"
    assert x.calls.count("restock") == 1
    assert x.calls.index("stop_farm") < x.calls.index("living")
    assert x.calls.index("living") < x.calls.index("restock")
    assert ("record", "restock_restart_started") in x.calls
    # FM10: a later start never replays it.
    before = list(x.calls)
    x.loop.health = lambda: pytest.fail("A replay refusal must not read the game")
    x.loop.stop_farm = lambda: pytest.fail("A replay refusal must not stop farming")
    with pytest.raises(ValueError, match="already attempted"):
        restart.resume(x.loop)
    assert x.calls == before


# FM13 -- a dead farmer is revived by the existing path before anything else.
def test_dead_farmer_is_revived_before_the_marker_and_restock(rig):
    x = rig
    x.dead = True
    assert restart.resume(x.loop) is True
    calls = [c for c in x.calls if isinstance(c, str)]
    assert calls.index("stop_farm") < calls.index("revive") < calls.index("restock")
    assert restart.MARKER in x.visit.state()


def _add_events(x, *events):
    x.events[5:5] = list(events)  # After the original failure, before restart.


def _mutate(x, change):
    row = read_json(x.visit.path)
    market = read_json(x.market_path)
    meteor = read_json(meteor_banking.JOURNAL)
    after = FAILED - 1.0
    # FM1 transactions / unknown events
    simple = {
        "event_purchase": "purchase",
        "event_sale": "sale",
        "event_valuable_stored": "valuable_stored",
        "event_silver_withdraw": "silver_withdraw",
        "event_silver_deposit": "silver_deposit",
        "event_meteor": "meteor_consolidation_started",
        "event_merchant": "merchant_journey_started",
        "event_warehouse": "warehouse_open_retry",
        "event_town_activity": "town_activity",
        "event_restock_complete": "restock_complete",
        "event_restock_cash_tail": "restock_cash_tail_complete",
        "event_urgent": "urgent_banking_started",
        "event_unknown": "equipment_review",
        "event_arrow_deferred": "arrow_purchase_deferred",
    }
    if change in simple:
        _add_events(x, ev(after, simple[change]))
    if change == "retry_of_buy":
        _add_events(x, ev(after, "town_observation_retry", action="buy"))
    if change == "action_failed":
        _add_events(x, ev(after, "town_action_failed", action="open-bank"))
    # FM2 original failure
    if change == "failure_missing":
        del x.events[4]
    if change == "failure_before_required":
        x.events[4]["time"] = R - 1
    for name, frames in {
        "failure_in_transfer": (
            ("overnight.py", "_run_route"),
            ("overnight.py", "restock"),
            ("banking.py", "fund_restock"),
            ("banking.py", "transfer"),
            ("overnight.py", "town"),
            ("worker.py", "request"),
        ),
        "failure_at_bank_open": (
            ("overnight.py", "_run_route"),
            ("overnight.py", "restock"),
            ("banking.py", "fund_restock"),
            ("banking.py", "open_warehouse"),
            ("overnight.py", "town"),
            ("worker.py", "request"),
        ),
        "failure_in_after_shopping": (
            ("overnight.py", "restock"),
            ("banking.py", "after_shopping"),
            ("banking.py", "open_warehouse"),
            ("overnight.py", "travel"),
            ("overnight.py", "_travel"),
            ("travel_care.py", "check"),
        ),
        "failure_trace_truncated": (
            ("banking.py", "open_warehouse"),
            ("overnight.py", "travel"),
            ("overnight.py", "_travel"),
            ("travel_care.py", "check"),
            ("worker.py", "request"),
        ),
        "failure_below_travel_in_unknown_code": (
            ("overnight.py", "restock"),
            ("banking.py", "fund_restock"),
            ("banking.py", "open_warehouse"),
            ("overnight.py", "travel"),
            ("overnight.py", "_travel"),
            ("banking.py", "transfer"),
        ),
        "failure_nested_restock": (
            ("overnight.py", "restock"),
            ("overnight.py", "restock"),
            ("banking.py", "fund_restock"),
            ("banking.py", "open_warehouse"),
            ("overnight.py", "travel"),
            ("overnight.py", "_travel"),
        ),
    }.items():
        if change == name:
            x.events[4]["failure_trace"] = trace(*frames)
    # FM3 later failures
    if change == "second_gameplay_failure":
        x.events.append(
            ev(NOW - 5, "failed", detail="x", failure_trace=deepcopy(LIVE_FAILURE))
        )
    if change == "failure_inside_a_restarted_restock":
        # Even beneath this module's own frame, restock() work is gameplay.
        x.events.append(
            ev(
                NOW - 5,
                "failed",
                detail="x",
                failure_trace=trace(
                    ("overnight.py", "_run_route"),
                    ("restock_restart.py", "resume"),
                    ("overnight.py", "restock"),
                    ("banking.py", "fund_restock"),
                    ("banking.py", "transfer"),
                ),
            )
        )
    if change == "required_at_missing":
        del row["required_at"]
    if change == "unknown_later_failure":
        x.events.append(
            ev(
                NOW - 5,
                "failed",
                detail="x",
                failure_trace=trace(
                    ("overnight.py", "_run_route"), ("banking.py", "after_shopping")
                ),
            )
        )
    # FM4 money
    if change == "ledger_after_required":
        with banking.LEDGER.open("a") as out:
            out.write(json.dumps({"time": R + 60, "verified": True}) + "\n")
    if change == "ledger_at_required":
        with banking.LEDGER.open("a") as out:
            out.write(json.dumps({"time": R, "verified": True}) + "\n")
    if change == "ledger_bad_time":
        with banking.LEDGER.open("a") as out:
            out.write(json.dumps({"time": "later", "verified": True}) + "\n")
    # FM5 deliveries
    with sqlite3.connect(delivery_operation.JOURNAL) as db:
        if change == "delivery_after_required":
            db.execute(
                "INSERT INTO transactions VALUES('n','farmer_delivery','verified',?,?)",
                (R + 10, R + 20),
            )
        if change == "delivery_updated_after_required":
            db.execute(
                "INSERT INTO transactions VALUES('o','farmer_delivery','aborted',?,?)",
                (R - 50, R + 5),
            )
        if change == "admission_after_required":
            db.execute(
                "INSERT INTO delivery_admissions VALUES('a','rejected',?,?)",
                (R + 5, R + 5),
            )
        if change == "pending_delivery":
            db.execute(
                "INSERT INTO transactions VALUES('p','farmer_delivery','uncertain',?,?)",
                (R - 900, R - 800),
            )
    # FM6 Meteor
    if change == "meteor_pending":
        meteor["phase"] = "travelling"
    if change == "meteor_started_after_required":
        meteor["started_at"] = R + 1
    if change == "meteor_started_at_required":
        meteor["started_at"] = R
    # FM7 holds / Market
    if change == "other_hold":
        x.hold = True
    if change == "market_visit_for_this_visit":
        market["town_visit_id"] = VISIT
    # FM8 identity / route
    if change == "restock_target_missing":
        del row["restock_target"]
    if change == "restock_target_invalid":
        row["restock_target"] = {"pid": 18532}
    if change == "new_game_process":
        x.health_target["creation_time_100ns"] += 1
    if change == "restarted_pid":
        x.health_target["pid"] = 99
    if change == "loop_identity":
        x.loop.identity = {**TARGET, "pid": 1}
    if change == "route_changed":
        row["route_id"] = "poltergeist"
    if change == "hunt_map_changed":
        row["hunt_map_id"] = 1002
    # FM9 ownership snapshot
    if change == "ownership_differs":
        row[restart.START_OWNERSHIP] = {
            **deepcopy(x.bag),
            "silver": x.bag["silver"] + 1,
        }
    if change == "ownership_item_differs":
        snapshot = deepcopy(x.bag)
        snapshot["items"][0]["amount"] = 2
        row[restart.START_OWNERSHIP] = snapshot
    # FM13 post-revival safety
    if change == "manual_mouse":
        x.controls["manual_mouse"] = True
    if change == "manual_fence":
        x.controls["manual_input_fence"] = True
    if change == "paused":
        x.controls["control"]["paused"] = True
    if change == "stale_observation":
        x.stale = 5.0
    if change == "farming_stays_on":
        x.farming_stays_on = True
    if change == "unexpected_map":
        x.life["map_id"] = 1036
    if change == "other_profile":
        x.profile = "someone-else"
    if change == "hold_after_revive":
        x.dead = True

        def hold():
            x.hold = True

        x.after_revive = hold
    write_json(x.visit.path, row)
    write_json(x.market_path, market)
    write_json(meteor_banking.JOURNAL, meteor)
    x.write_events()


REFUSALS = [
    "event_purchase",
    "event_sale",
    "event_valuable_stored",
    "event_silver_withdraw",
    "event_silver_deposit",
    "event_meteor",
    "event_merchant",
    "event_warehouse",
    "event_town_activity",
    "event_restock_complete",
    "event_restock_cash_tail",
    "event_urgent",
    "event_unknown",
    "event_arrow_deferred",
    "retry_of_buy",
    "action_failed",
    "failure_missing",
    "failure_before_required",
    "failure_in_transfer",
    "failure_at_bank_open",
    "failure_in_after_shopping",
    "failure_trace_truncated",
    "failure_below_travel_in_unknown_code",
    "failure_nested_restock",
    "second_gameplay_failure",
    "failure_inside_a_restarted_restock",
    "unknown_later_failure",
    "required_at_missing",
    "ledger_after_required",
    "ledger_at_required",
    "ledger_bad_time",
    "delivery_after_required",
    "delivery_updated_after_required",
    "admission_after_required",
    "pending_delivery",
    "meteor_pending",
    "meteor_started_after_required",
    "meteor_started_at_required",
    "other_hold",
    "market_visit_for_this_visit",
    "restock_target_missing",
    "restock_target_invalid",
    "new_game_process",
    "restarted_pid",
    "loop_identity",
    "route_changed",
    "hunt_map_changed",
    "ownership_differs",
    "ownership_item_differs",
    "manual_mouse",
    "manual_fence",
    "paused",
    "stale_observation",
    "farming_stays_on",
    "unexpected_map",
    "other_profile",
    "hold_after_revive",
]

# Refusals decided before any control change or game observation.
BEFORE_INPUT = {
    name
    for name in REFUSALS
    if name.startswith(("event_", "failure_", "ledger_", "delivery_", "meteor_"))
    or name
    in (
        "retry_of_buy",
        "action_failed",
        "second_gameplay_failure",
        "failure_inside_a_restarted_restock",
        "unknown_later_failure",
        "required_at_missing",
        "admission_after_required",
        "pending_delivery",
        "other_hold",
        "market_visit_for_this_visit",
        "restock_target_missing",
        "restock_target_invalid",
        "new_game_process",
        "restarted_pid",
        "loop_identity",
        "route_changed",
        "hunt_map_changed",
    )
}


# FM1-FM9, FM13: every missing proof fails closed before the marker/restock.
@pytest.mark.parametrize("change", REFUSALS)
def test_missing_evidence_never_reaches_marker_or_restock(rig, change):
    x = rig
    _mutate(x, change)
    with pytest.raises(ValueError):
        restart.resume(x.loop)
    assert_untouched(x)
    if change in BEFORE_INPUT:
        assert "stop_farm" not in x.calls and "living" not in x.calls


def test_equal_ownership_snapshot_is_accepted(rig):
    x = rig
    row = read_json(x.visit.path)
    row[restart.START_OWNERSHIP] = deepcopy(x.bag)
    write_json(x.visit.path, row)
    assert restart.resume(x.loop) is True


def test_allowed_care_and_movement_events_do_not_block(rig):
    x = rig
    _add_events(
        x,
        ev(FAILED - 2, "travel_heal_verified"),
        ev(FAILED - 1.9, "travel_heal_close_deferred"),
        ev(FAILED - 1.8, "travel_panel_closed"),
        ev(FAILED - 1.7, "town_movement_stalled"),
        ev(FAILED - 1.6, "town_observation_retry", action="vendor-status"),
        ev(FAILED - 1.5, "xp_skill_state"),
    )
    x.write_events()
    assert restart.resume(x.loop) is True


# FM10: an existing marker (any content) always refuses a replay.
@pytest.mark.parametrize("marker", [{"target": TARGET}, {"phase": "anything"}, True])
def test_marker_present_refuses_without_reading_game(rig, marker):
    x = rig
    row = read_json(x.visit.path)
    row[restart.MARKER] = marker
    write_json(x.visit.path, row)
    x.loop.health = lambda: pytest.fail("must not read the game")
    x.loop.living = lambda: pytest.fail("must not revive or move")
    x.loop.stop_farm = lambda: pytest.fail("must not stop farming")
    with pytest.raises(ValueError, match="already attempted"):
        restart.resume(x.loop)
    assert "restock" not in x.calls


# FM11: other continuation kinds keep their existing behaviour.
@pytest.mark.parametrize(
    "field,value",
    [
        ("pre_admission_restock_tail", {"phase": "captured"}),
        ("restock_recovery_claim", {"reference": "operator"}),
        ("restock_recovery_attempted_at", 1.0),
        ("verified_tail", {"kind": "restock"}),
        ("urgent_intent", [{"uid": 1, "type_id": 2}]),
        ("urgent_target", deepcopy(TARGET)),
        ("urgent_recovery_claim", {"reference": "operator"}),
        ("urgent_banking_tail_completed_at", 1.0),
        (tail.CLAIM, {"phase": "cash_attempted"}),
        ("reasons", ["urgent_banking", "restock"]),
        ("reasons", ["merchant_acceptance"]),
        ("phase", "returning_to_hunt"),
        ("phase", "complete"),
        ("town_work_completed_at", 5.0),
    ],
)
def test_other_kinds_are_not_touched(rig, field, value):
    x = rig
    row = read_json(x.visit.path)
    row[field] = value
    write_json(x.visit.path, row)
    before = read_json(x.visit.path)
    x.loop.health = lambda: pytest.fail("Another kind must not be read here")
    x.loop.stop_farm = lambda: pytest.fail("Another kind must not be stopped here")
    assert restart.resume(x.loop) is False
    assert read_json(x.visit.path) == before and x.calls == []


def test_empty_visit_is_not_touched(rig):
    x = rig
    write_json(x.visit.path, {})
    x.loop.health = lambda: pytest.fail("No visit must not read the game")
    assert restart.resume(x.loop) is False and x.calls == []


# FM12
def test_manual_stop_prevents_marker_and_input(rig):
    x = rig

    def stop():
        raise ValueError("Stopped by user")

    x.loop.check_stop = stop
    with pytest.raises(ValueError, match="Stopped by user"):
        restart.resume(x.loop)
    assert_untouched(x)
    assert "stop_farm" not in x.calls


def test_events_audit_missing_refuses(rig):
    x = rig
    recovery.EVENTS.unlink()
    with pytest.raises(ValueError):
        restart.resume(x.loop)
    assert_untouched(x)


def test_visit_changed_during_revival_refuses(rig):
    x = rig
    x.dead = True

    def changed():
        row = read_json(x.visit.path)
        row["reasons"] = ["restock", "urgent_banking"]
        write_json(x.visit.path, row)

    x.after_revive = changed
    with pytest.raises(ValueError):
        restart.resume(x.loop)
    assert restart.MARKER not in x.visit.state() and "restock" not in x.calls


def test_restock_failure_after_marker_is_never_replayed(rig):
    x = rig

    def failing():
        x.calls.append("restock")
        raise ValueError("Pointer is not over the memory-identified merchant control")

    x.loop.restock = failing
    with pytest.raises(ValueError, match="Pointer"):
        restart.resume(x.loop)
    assert restart.MARKER in x.visit.state()
    x.loop.restock = lambda: pytest.fail("must not replay")
    with pytest.raises(ValueError, match="already attempted"):
        restart.resume(x.loop)
    assert x.calls.count("restock") == 1


# --------------------------------------------------------------------------
# FM15 end to end: the live journals through the real route controller.
# --------------------------------------------------------------------------


def test_e2e_live_incident_revives_restarts_restock_once(tmp_path, monkeypatch):
    clock = field_fakes.FakeClock(NOW)
    game = field_fakes.FakeGame(clock, position=field_fakes.FIELD, dead=True)
    game.hunt_empties_ammo = False
    game.ammo["amount"] = 0  # Scatter ammo was exhausted in the field.
    # The restarted Warehouseman trip meets the same heal/hover race (Fix 1).
    game.close_failures = [ValueError(field_fakes.HOVER)]
    loop = field_fakes.install(monkeypatch, tmp_path, game, clock)
    monkeypatch.setattr(restart, "time", clock)
    monkeypatch.setattr(recovery, "time", clock)  # _native_tail_safe freshness
    market = seed_journals(tmp_path, monkeypatch, loop.output / "events.jsonl")
    banking.CONFIG.write_text(
        json.dumps({"enabled": True, "withdraw_essentials": True}), encoding="utf-8"
    )
    write_json(loop.town_visit.path, live_row())
    (loop.output / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in live_events()), encoding="utf-8"
    )
    # The app's native death recovery was mid-return when the route restarts.
    from conquest import overnight

    write_json(
        overnight.RECOVERY_CHECKPOINT,
        {"identity": TARGET, "phase": "returning_with_farmer", "revive_attempts": 1},
    )
    game.death_return = overnight.RECOVERY_CHECKPOINT

    with pytest.raises(field_fakes.ReachedWarehouse):
        loop._run_route()
    first_ops = list(game.ops)
    with pytest.raises(ValueError, match="already attempted"):
        loop._run_route()
    second_ops = game.ops[len(first_ops) :]

    row = loop.town_visit.state()
    events = field_fakes.events(loop)
    artifact = {
        "scenario": "live_zero_transaction_restock_restart",
        "town_visit": {
            "town_visit_id": row["town_visit_id"],
            "phase": row["phase"],
            "reasons": row["reasons"],
            "completed": bool(row.get("town_work_completed_at")),
            "marker": {
                k: row[restart.MARKER][k]
                for k in ("target", "failure", "map_id", "position")
            },
        },
        "first_start": {
            "ops": [
                o["op"]
                + (":" + o["action"] if "action" in o else "")
                + (":" + o["window"] if "window" in o else "")
                for o in first_ops
                if o.get("action") != "vendor-status"
            ],
            "events": [
                e["event"]
                for e in events
                if e["time"] >= NOW and e["event"] != "runback_progress"
            ],
        },
        "second_start_ops": second_ops,
        "death_return_phase": read_json(overnight.RECOVERY_CHECKPOINT).get("phase"),
        "potions_left": game.potions(),
        "final_position": game.position,
        "ledger_after_required": [
            r for r in recovery._rows(banking.LEDGER) if r["time"] >= R
        ],
        "market_town_visit": read_json(market)["town_visit_id"],
    }
    path = tmp_path / "restock-restart-e2e.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    saved = json.loads(path.read_text(encoding="utf-8"))

    visit = saved["town_visit"]
    assert visit["town_visit_id"] == VISIT and visit["phase"] == "town_work"
    assert visit["reasons"] == ["restock"] and visit["completed"] is False
    assert visit["marker"]["target"] == TARGET
    assert visit["marker"]["failure"]["time"] == FAILED
    assert visit["marker"]["map_id"] == 1011
    ops = saved["first_start"]["ops"]
    # Order: Farming Off (+ release of the native death return), revive, then
    # the restarted restock; no town input before the revive.
    assert ops[:3] == ["controls", "controls", "revive-click"]
    assert saved["death_return_phase"] == "cancelled"
    names = saved["first_start"]["events"]
    assert names.index("travel_revive") < names.index("restock_restart_started")
    assert names.index("restock_restart_started") < names.index("travel")
    assert "failed" not in names
    heal = names.index("travel_heal_verified")
    assert names[heal + 1] == "travel_heal_close_deferred"
    assert ops.count("town:consume-healing") == 1
    assert ops[-3:] == ["town:warehouse-locate", "town:close:Shop", "town:open-bank"]
    assert saved["potions_left"] == 3
    assert max(abs(a - b) for a, b in zip(saved["final_position"], (227, 246))) <= 12
    assert saved["ledger_after_required"] == []
    # The second start refuses the replay without any worker operation.
    assert saved["second_start_ops"] == []
