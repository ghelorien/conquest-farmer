"""Isolated historical recovery tests; no native input or live-state access."""

from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from conquest import restock_town_recovery as recovery
from conquest import banking, meteor_banking, town_visit, urgent_town_recovery
from conquest.discord_notify import read_json, write_json
from conquest.merchants import bridge, delivery_operation, service_visit, handoff


@pytest.fixture
def recovered(tmp_path, monkeypatch):
    x = NS(now=1000.0, calls=[], hold=False, stops=[])
    monkeypatch.setattr(recovery.time, "time", lambda: x.now)
    monkeypatch.setattr(recovery, "state_path", lambda path: tmp_path / path)
    monkeypatch.setattr(recovery, "EVENTS", tmp_path / "events.jsonl")
    monkeypatch.setattr(recovery, "_other_holds", lambda: x.hold)
    monkeypatch.setattr(urgent_town_recovery, "transaction_holds", lambda: x.hold)
    monkeypatch.setattr(meteor_banking, "JOURNAL", tmp_path / "meteor.json")
    monkeypatch.setattr(meteor_banking, "POLICY", tmp_path / "meteor-policy.json")
    monkeypatch.setattr(delivery_operation, "JOURNAL", tmp_path / "deliveries.sqlite3")
    with sqlite3.connect(delivery_operation.JOURNAL) as db:
        db.execute("CREATE TABLE delivery_admissions(created REAL)")
        db.execute("CREATE TABLE transactions(created REAL)")
    x.visit = town_visit.TownVisit(
        tmp_path / "town.json", profile="farmer", clock=lambda: x.now, probe=lambda: {}
    )
    x.target = {"pid": 7, "creation_time_100ns": 80, "path": "game.exe"}
    x.row = {
        "town_visit_id": "original-town",
        "farmer_profile_id": "farmer",
        "phase": "town_work",
        "reasons": ["restock"],
        "required_at": 800.0,
        "route_id": "bandit",
        "hunt_map_id": 1011,
        "baseline": {"session_id": 600.0, "cursor": 20},
        "history": [
            {
                "phase": "complete",
                "completed_at": 790.0,
                "return_target": deepcopy(x.target),
                "resumed_hunting": {"session_id": 600.0, "cursor": 10},
            }
        ],
    }
    write_json(x.visit.path, x.row)
    write_json(
        tmp_path / "reports/desktop-farming/kill-session.json", {"started_at": 600.0}
    )
    x.bag = {
        "items": [
            {
                "uid": 1,
                "type_id": 1000020,
                "amount": 5,
                "limit": 1,
                "plus": 0,
                "slot": 0,
            },
            {
                "uid": 3,
                "type_id": 720027,
                "amount": 1,
                "limit": 1,
                "plus": 0,
                "slot": 1,
            },
        ],
        "equipped_ammo": {
            "uid": 2,
            "type_id": 1050002,
            "amount": 5000,
            "limit": 5000,
            "slot": None,
        },
        "silver": 600,
        "capacity": 40,
    }
    x.meteor = {
        "phase": "storing_scroll",
        "origin": 1011,
        "exchange_verified": True,
        "started_at": 820.0,
        "scroll_uid": 3,
        "meteor_uids": list(range(10, 20)),
        "after": deepcopy(x.bag),
    }
    write_json(meteor_banking.JOURNAL, x.meteor)
    write_json(meteor_banking.POLICY, {"origins": {"1011": {"return": {"fare": 100}}}})
    x.market = {
        "visit_id": "original-market",
        "town_visit_id": "original-town",
        "parent_visit_id": "original-town",
        "farmer_profile_id": "farmer",
        "phase": "active",
        "started_at": 840.0,
        "deadline": 900.0,
        "attempts": [],
    }
    x.market_path = tmp_path / "market.json"
    write_json(x.market_path, x.market)
    monkeypatch.setattr(service_visit, "MarketVisit", lambda: NS(path=x.market_path))
    x.failure = {
        "event": "failed",
        "time": 850.0,
        "town_visit_id": "original-town",
        "detail": "Unqualified merchant client fingerprint",
        "error_type": "conquest.merchants.bridge.MerchantRejected",
        "failure_trace": [
            {"file": file, "function": fn, "line": line}
            for file, fn, line in [
                ("banking.py", "after_shopping", 202),
                ("meteor_banking.py", "resume", 640),
                ("meteor_banking.py", "market_bank", 509),
                ("delivery_route.py", "_market_storage", 396),
            ]
        ],
    }
    x.events = [x.failure]
    x.write_events = lambda: recovery.EVENTS.write_text(
        "".join(json.dumps(e) + "\n" for e in x.events)
    )
    x.write_events()
    x.status = {
        "input_owner": None,
        "handoff_requested": None,
        "handoff_granted": False,
        "characters": {
            "Spiritual": {
                "connected": True,
                "refill": {"enabled": True},
                "qualification": {"foreground_open_booth_listing_1078": True},
            }
        },
    }
    x.manual = {}

    def request(body):
        assert body in ({"action": "status"}, {"action": "manual-status"})
        return deepcopy(
            x.status if body["action"] == "status" else {"farmer": x.manual}
        )

    monkeypatch.setattr(bridge, "request", request)
    x.health = {
        "profile_id": "farmer",
        "target": deepcopy(x.target),
        "embedded_controls": {
            "observed_at": 1000.0,
            "control": {"enabled": False, "paused": False},
            "life": {"map_id": 1036, "dead_candidate": False, "current_hp": 100},
            "manual_input_fence": False,
            "manual_mouse": False,
        },
    }

    def town(action, **args):
        if action == "supplies":
            return deepcopy(x.bag)
        assert action == "close" and args["window"] == "Shop"
        x.calls.append(("close", "Shop"))

    x.loop = NS(
        town_visit=x.visit,
        identity=deepcopy(x.target),
        health=lambda: deepcopy(x.health),
        check_stop=lambda: None,
        town=town,
        adopt_ammunition=lambda: None,
        cycles=0,
        route=NS(
            id="bandit",
            map_id=1011,
            restock_map_id=1011,
            supplies=NS(arrow_type=1050002, healing_type=1000020),
        ),
        record=lambda *a, **kw: x.calls.append(("record", a[0])),
    )

    def stop_farm():
        x.stops.append(True)
        x.health["embedded_controls"]["control"]["enabled"] = False

    x.loop.stop_farm = stop_farm
    monkeypatch.setattr(
        banking,
        "open_warehouse",
        lambda loop: (
            x.calls.append(("open",))
            or {"silver": x.bag["silver"], "stored_silver": 1000}
        ),
    )
    monkeypatch.setattr(
        banking,
        "close_warehouse",
        lambda loop: x.calls.append(("close", "Warehouse+Inventory")),
    )
    monkeypatch.setattr(banking, "transport_reserve", lambda: 200)

    def transfer(loop, direction, amount):
        # The fsynced marker must exist before the sole monetary action.
        assert (
            x.visit.state()["pre_admission_restock_tail"]["phase"] == "cash_attempted"
        )
        x.calls.append(("transfer", direction, amount))
        x.bag["silver"] += amount if direction == "withdraw" else -amount
        return {"verified": True, "amount": amount, "direction": direction}

    monkeypatch.setattr(banking, "transfer", transfer)
    monkeypatch.setattr(
        banking,
        "after_shopping",
        lambda *a, **k: pytest.fail("Full shopping/banking must not replay"),
    )
    monkeypatch.setattr(
        handoff, "service_window", lambda loop, **kw: x.calls.append(("service", kw))
    )

    def returned():
        assert recovery.capture_pre_admission_tail(x.loop)
        x.meteor.update(
            phase="completed",
            market_verified_at=910.0,
            completed_at=930.0,
            receipts=[{"stored": 3, "type_id": 720027, "verified_in_warehouse": True}],
        )
        write_json(meteor_banking.JOURNAL, x.meteor)
        x.market.update(phase="departed")
        write_json(x.market_path, x.market)
        x.bag["items"] = [i for i in x.bag["items"] if i["uid"] != 3]
        x.bag["silver"] -= 100
        x.health["embedded_controls"]["life"]["map_id"] = 1011

    x.returned = returned
    return x


@pytest.fixture
def mixed(recovered, monkeypatch):
    from conquest import memory_health
    from conquest.merchants import open_booth_cancel_1078

    x = recovered
    x.row.update(
        reasons=["urgent_banking", "restock"],
        urgent_target=deepcopy(x.target),
        urgent_intent=[{"uid": 9, "type_id": 121005}],
        urgent_banking_tail_completed_at=805.0,
    )
    write_json(x.visit.path, x.row)
    x.failure.update(
        detail="1078 incoming trade request disagrees with confirmation",
        failure_trace=[
            {"file": file, "function": fn, "line": 0}
            for file, fn in [
                ("banking.py", "stash_valuables"),
                ("meteor_banking.py", "resume"),
                ("meteor_banking.py", "market_bank"),
                ("delivery_route.py", "_market_storage"),
                ("delivery_route.py", "approach_merchant"),
                ("bridge.py", "request"),
            ]
        ],
    )
    x.events = [
        {
            "event": "valuable_stored",
            "time": 803.0,
            "town_visit_id": "original-town",
            "stored": 9,
            "type_id": 121005,
            "verified_in_warehouse": True,
        }
    ]
    for number in range(5):
        x.events.append(
            {
                "event": "purchase",
                "time": 810.0 + number,
                "town_visit_id": "original-town",
                "receipt": {
                    "bought": 1000020,
                    "amount": 1,
                    "price": 60,
                    "silver": 440 - 60 * number,
                },
            }
        )
    x.events.append(x.failure)
    x.write_events()
    x.loop.info = "isolated-worker-info"
    x.loop.route.supplies.healing_restock_to = 5
    monkeypatch.setattr(memory_health, "HealthWorkerSession", lambda *_: NS())
    x.observed = {
        "identity": deepcopy(x.target),
        "map_id": 1036,
        "position": [228, 193],
        "hp": 100,
        "timestamp": 1000.0,
        "silver": x.bag["silver"],
        "booth": [],
        "trade": None,
        "request": None,
        "canonical_manual_ownership": False,
        "inventory": [
            {
                "uid": i["uid"],
                "type_id": i["type_id"],
                "plus": i["plus"],
                "quantity": i["amount"],
            }
            for i in x.bag["items"]
        ],
        "confirmation": {
            "title": "Open Booth###Confirm",
            "message": "Start Vending",
            "model_address": 123,
            "callback_address": 456,
        },
    }
    monkeypatch.setattr(
        open_booth_cancel_1078, "observe", lambda _observer: deepcopy(x.observed)
    )
    return x


def test_mixed_trip_capture_preserves_original_expired_budget_and_purchase_proof(mixed):
    x = mixed
    before = deepcopy(x.market)
    assert recovery.capture_pre_admission_tail(x.loop)
    claim = x.visit.state()["pre_admission_restock_tail"]
    assert (
        claim["capture_kind"] == "booth_confirmation"
        and len(claim["purchase_receipts"]) == 5
    )
    assert claim["market"] == before and read_json(x.market_path) == before
    assert recovery.capture_pre_admission_tail(x.loop) and not x.calls


def test_mixed_trip_closed_modal_requires_fresh_closed_ownership_without_fake_receipt(
    mixed,
):
    x = mixed
    x.observed["confirmation"] = None
    x.observed["canonical_manual_ownership"] = True
    assert recovery.capture_pre_admission_tail(x.loop)
    claim = x.visit.state()["pre_admission_restock_tail"]
    assert claim["confirmation_observation"] == {
        "state": "closed",
        "confirmation": None,
    }
    assert "cancellation_receipt" not in claim and not x.calls


def test_mixed_trip_retries_only_its_own_read_only_capture_failure(mixed):
    x = mixed
    x.events.extend(
        [
            {"event": "started", "time": 940.0, "town_visit_id": "original-town"},
            {
                "event": "failed",
                "time": 950.0,
                "town_visit_id": "original-town",
                "detail": "1078 health candidate changed",
                "error_type": "conquest.merchants.reader_1078.ObservationUnavailable1078",
                "failure_trace": [
                    {
                        "file": "restock_town_recovery.py",
                        "function": "_capture_booth_confirmation_tail",
                    }
                ],
            },
        ]
    )
    x.write_events()
    assert recovery.capture_pre_admission_tail(x.loop)
    assert x.visit.state()["pre_admission_restock_tail"]["failure"] == x.failure
    assert not x.calls


@pytest.mark.parametrize(
    "changed",
    [
        "real_trade",
        "confirmation",
        "inventory",
        "manual_stop",
        "manual_mouse",
        "admission",
        "budget",
        "urgent_receipt",
        "purchase",
    ],
)
def test_mixed_trip_rejects_changed_native_or_durable_proof_before_capture(
    mixed, changed
):
    x = mixed
    if changed == "real_trade":
        x.observed["trade"] = {"participant": "visitor"}
    if changed == "confirmation":
        x.observed["confirmation"]["message"] = "Trade with visitor"
    if changed == "inventory":
        x.observed["inventory"][0]["quantity"] = 4
    if changed == "manual_stop":
        x.loop.check_stop = lambda: (_ for _ in ()).throw(ValueError("Manual Stop"))
    if changed == "manual_mouse":
        x.health["embedded_controls"]["manual_mouse"] = True
    if changed == "admission":
        with sqlite3.connect(delivery_operation.JOURNAL) as db:
            db.execute("INSERT INTO delivery_admissions VALUES(840)")
    if changed == "budget":
        x.market["deadline"] = 1001.0
        write_json(x.market_path, x.market)
    if changed == "urgent_receipt":
        x.events.pop(0)
        x.write_events()
    if changed == "purchase":
        x.events[1]["receipt"]["price"] = 0
        x.write_events()
    with pytest.raises(ValueError):
        recovery.capture_pre_admission_tail(x.loop)
    assert "pre_admission_restock_tail" not in x.visit.state() and not x.calls


def test_mixed_trip_finishes_only_original_cash_tail_without_repeat_shopping(mixed):
    x = mixed
    x.returned()
    assert recovery.resume_pre_admission_tail(x.loop)
    row = x.visit.state()
    assert row["reasons"] == ["urgent_banking", "restock"]
    assert row["town_work_completed_kind"] == "restock"
    assert read_json(x.market_path)["deadline"] == 900.0
    assert [call for call in x.calls if call[0] == "transfer"] == [
        ("transfer", "deposit", 300)
    ]
    assert recovery.resume_pre_admission_tail(x.loop) is False


def test_capture_retains_original_trip_budget_and_accepts_only_pending_qualified_refill(
    recovered,
):
    x = recovered
    x.status["handoff_requested"] = "merchant-refill:Spiritual:123"
    before = deepcopy(x.market)
    assert recovery.capture_pre_admission_tail(x.loop)
    row = x.visit.state()
    claim = row["pre_admission_restock_tail"]
    assert row["town_visit_id"] == "original-town" and claim["market"] == before
    assert read_json(x.market_path) == before and x.status[
        "handoff_requested"
    ].endswith(":123")
    assert claim["failure"] == x.failure and x.calls == []


@pytest.mark.parametrize(
    "blocked",
    [
        "owner",
        "grant",
        "manual",
        "fence",
        "unqualified",
        "paused_refill",
        "unknown_request",
    ],
)
def test_active_or_unqualified_handoff_cannot_capture(recovered, blocked):
    x = recovered
    x.status["handoff_requested"] = "merchant-refill:Spiritual:123"
    if blocked == "owner":
        x.status["input_owner"] = "Spiritual"
    if blocked == "grant":
        x.status["handoff_granted"] = True
    if blocked == "manual":
        x.manual["session"] = {"id": "manual"}
    if blocked == "fence":
        x.manual["input_fenced"] = True
    if blocked == "unqualified":
        x.status["characters"]["Spiritual"]["qualification"] = {}
    if blocked == "paused_refill":
        x.status["characters"]["Spiritual"]["refill"]["enabled"] = False
    if blocked == "unknown_request":
        x.status["handoff_requested"] = "operator-request"
    with pytest.raises(ValueError, match="manual ownership"):
        recovery.capture_pre_admission_tail(x.loop)
    assert "pre_admission_restock_tail" not in x.visit.state() and not x.calls


@pytest.mark.parametrize(
    "suffix", ["known_guard", "travel", "purchase", "unknown_failure"]
)
def test_only_exact_preinput_suffix_preserves_original_failure(recovered, suffix):
    x = recovered
    event = {
        "event": "failed",
        "time": 960.0,
        "town_visit_id": "original-town",
        "error_type": "builtins.ValueError",
        "detail": "Merchant input or manual ownership still holds the interrupted trip",
        "failure_trace": [
            {
                "file": "restock_town_recovery.py",
                "function": "capture_pre_admission_tail",
            }
        ],
    }
    if suffix in ("travel", "purchase"):
        event["event"] = suffix
    if suffix == "unknown_failure":
        event["detail"] = "another failure"
    x.events.extend(
        [{"event": "started", "time": 950.0, "town_visit_id": "original-town"}, event]
    )
    x.write_events()
    if suffix == "known_guard":
        assert recovery.capture_pre_admission_tail(x.loop)
    else:
        with pytest.raises(ValueError, match="Gameplay"):
            recovery.capture_pre_admission_tail(x.loop)
        assert "pre_admission_restock_tail" not in x.visit.state()
    assert not x.calls


@pytest.mark.parametrize("table", ["delivery_admissions", "transactions"])
def test_any_admission_blocks_before_capture(recovered, table):
    x = recovered
    with sqlite3.connect(delivery_operation.JOURNAL) as db:
        db.execute(f"INSERT INTO {table} VALUES(850)")
    with pytest.raises(ValueError, match="admitted"):
        recovery.capture_pre_admission_tail(x.loop)
    assert not x.calls


@pytest.mark.parametrize(
    "changed",
    ["receipt", "uid", "type", "fare", "deadline", "process", "manual", "supplies"],
)
def test_changed_storage_return_or_safety_never_reaches_cash(recovered, changed):
    x = recovered
    x.returned()
    if changed == "receipt":
        x.meteor["receipts"][0]["verified_in_warehouse"] = False
    if changed == "uid":
        x.meteor["scroll_uid"] = 4
    if changed == "type":
        x.meteor["receipts"][0]["type_id"] = 1088001
    if changed == "fare":
        x.bag["silver"] += 1
    if changed == "deadline":
        x.market["deadline"] += 60
    if changed == "process":
        x.health["target"]["pid"] = 8
    if changed == "manual":
        x.health["embedded_controls"]["manual_mouse"] = True
    if changed == "supplies":
        x.bag["items"][0]["amount"] = 0
    write_json(meteor_banking.JOURNAL, x.meteor)
    write_json(x.market_path, x.market)
    with pytest.raises(ValueError):
        recovery.resume_pre_admission_tail(x.loop)
    assert x.visit.state()["pre_admission_restock_tail"]["phase"] == "captured"
    assert "town_work_completed_at" not in x.visit.state()
    assert all(call == ("record", "restock_ownership_mismatch") for call in x.calls)


def test_verified_cash_closes_panels_and_completes_same_trip_once(recovered):
    x = recovered
    x.returned()
    assert recovery.resume_pre_admission_tail(x.loop)
    row = x.visit.state()
    assert row["town_visit_id"] == "original-town" and row["phase"] == "town_work"
    assert row["verified_tail"]["ownership"]["silver"] == 200
    assert row["town_work_completed_kind"] == "restock" and x.loop.cycles == 1
    assert read_json(x.market_path)["deadline"] == 900.0
    assert x.calls == [
        ("open",),
        ("transfer", "deposit", 300),
        ("close", "Warehouse+Inventory"),
        ("close", "Shop"),
        ("service", {"town": True}),
        ("record", "restock_complete"),
    ]
    before = list(x.calls)
    assert recovery.resume_pre_admission_tail(x.loop) is False and x.calls == before


def test_completed_meteor_restart_stops_enabled_farm_before_tail(recovered):
    x = recovered
    x.returned()
    x.health["embedded_controls"]["control"]["enabled"] = True
    assert recovery.resume_pre_admission_tail(x.loop)
    assert x.stops == [True]
    assert x.health["embedded_controls"]["control"]["enabled"] is False


def test_completed_meteor_manual_stop_prevents_control_and_cash(recovered):
    x = recovered
    x.returned()
    x.loop.check_stop = lambda: (_ for _ in ()).throw(ValueError("Manual Stop"))
    with pytest.raises(ValueError, match="Manual Stop"):
        recovery.resume_pre_admission_tail(x.loop)
    assert not x.stops and not x.calls


@pytest.mark.parametrize(
    "outcome", ["raises", "unverified", "missing", "post_cash_crash"]
)
def test_uncertain_cash_or_later_crash_never_replays(recovered, monkeypatch, outcome):
    x = recovered
    x.returned()
    if outcome == "post_cash_crash":
        monkeypatch.setattr(
            banking,
            "close_warehouse",
            lambda _: (_ for _ in ()).throw(OSError("crash")),
        )
    else:

        def transfer(*args):
            assert (
                x.visit.state()["pre_admission_restock_tail"]["phase"]
                == "cash_attempted"
            )
            x.calls.append(("attempt",))
            if outcome == "raises":
                raise OSError("uncertain")
            return {"verified": False} if outcome == "unverified" else None

        monkeypatch.setattr(banking, "transfer", transfer)
    with pytest.raises((OSError, ValueError)):
        recovery.resume_pre_admission_tail(x.loop)
    before = list(x.calls)
    with pytest.raises(ValueError, match="replay"):
        recovery.resume_pre_admission_tail(x.loop)
    assert x.calls == before and "town_work_completed_at" not in x.visit.state()


STALL_TRACE = [
    ("banking.py", "stash_valuables"),
    ("meteor_banking.py", "consolidate"),
    ("meteor_banking.py", "resume"),
    ("meteor_banking.py", "market_bank"),
    ("meteor_banking.py", "approach_market_warehouse"),
    ("overnight.py", "travel"),
    ("overnight.py", "_travel"),
    ("travel_progress.py", "observe"),
]
REFUSAL_TRACE = [
    ("overnight.py", "run"),
    ("overnight.py", "_run_route"),
    ("restock_town_recovery.py", "capture_pre_admission_tail"),
]


def trace(frames):
    return [{"file": f, "function": fn, "line": 0} for f, fn in frames]


@pytest.fixture
def stalled(recovered, monkeypatch, tmp_path):
    """The live shape: earlier settled Market visit, then a second batch stall."""
    x = recovered
    x.row["restock_target"] = deepcopy(x.target)
    write_json(x.visit.path, x.row)
    x.market.update(
        phase="departed",
        started_at=802.0,
        deadline=862.0,
        departed_at=815.0,
        arrival_map=1011,
        attempts=[{"merchant": "Dutch", "outcome": "transferred", "at": 808.0}],
    )
    write_json(x.market_path, x.market)
    with sqlite3.connect(delivery_operation.JOURNAL) as db:
        db.execute("INSERT INTO delivery_admissions VALUES(804)")
        db.execute("INSERT INTO transactions VALUES(804)")
    monkeypatch.setattr(recovery, "MONEY", tmp_path / "transfers.jsonl")
    recovery.MONEY.write_text(json.dumps({"time": 700.0}) + "\n")
    x.failure = {
        "event": "failed",
        "time": 850.0,
        "phase": "needs_attention",
        "town_visit_id": "original-town",
        "detail": "Route made no improving progress after bounded recovery",
        "error_type": "conquest.travel_progress.TravelStalled",
        "failure_trace": trace(STALL_TRACE),
    }
    x.events = [
        {"event": "merchant_delivery_verified", "time": 808.0},
        {"event": "valuable_stored", "time": 812.0, "stored": 99},
        {"event": "meteor_loop_complete", "time": 816.0},
        {"event": "meteor_consolidation_started", "time": 820.5},
        {"event": "meteor_exchange_verified", "time": 830.0, "scroll_uid": 3},
        {"event": "market_movement_recovery", "time": 840.0},
        x.failure,
    ]
    for event in x.events:
        event["town_visit_id"] = "original-town"
    x.write_events()
    x.manual = {
        "observation": {"available": True, "windows_absent": True, "observed_at": 999.0}
    }
    return x


def test_approach_stall_captures_unchanged_trip_and_reenters_without_input(stalled):
    from conquest.no_transfer_town_recovery import warehouse_fallback_only

    x = stalled
    market = deepcopy(x.market)
    assert recovery.capture_pre_admission_tail(x.loop)
    claim = x.visit.state()["pre_admission_restock_tail"]
    assert claim["capture_kind"] == "approach_stall" and claim["phase"] == "captured"
    assert claim["target"] == x.target and claim["failure"] == x.failure
    assert claim["market"] == market == read_json(x.market_path)
    assert claim["meteor"] == x.meteor and claim["bag"] == x.bag
    # Re-entry validates, then native Meteor recovery skips any new admission.
    assert recovery.capture_pre_admission_tail(x.loop)
    assert warehouse_fallback_only(x.loop, read_json(meteor_banking.JOURNAL))
    assert x.visit.state()["pre_admission_restock_tail"] == claim and not x.calls


@pytest.mark.parametrize(
    "later",
    [
        "valuable_stored",
        "purchase",
        "sale",
        "silver_deposit",
        "merchant_journey_started",
        "merchant_repositioning",
        "second_exchange",
        "delivery_admissions",
        "transactions",
        "money",
    ],
)
def test_approach_stall_rejects_any_transaction_after_meteor_start(stalled, later):
    x = stalled
    if later in ("delivery_admissions", "transactions"):
        with sqlite3.connect(delivery_operation.JOURNAL) as db:
            db.execute(f"INSERT INTO {later} VALUES(825)")
    elif later == "money":
        recovery.MONEY.write_text(json.dumps({"time": 825.0}) + "\n")
    else:
        event = {"event": later, "time": 835.0, "town_visit_id": "original-town"}
        if later == "second_exchange":
            event.update(event="meteor_exchange_verified", scroll_uid=4)
        x.events.insert(-1, event)
        x.write_events()
    with pytest.raises(ValueError):
        recovery.capture_pre_admission_tail(x.loop)
    assert "pre_admission_restock_tail" not in x.visit.state() and not x.calls


@pytest.mark.parametrize(
    "changed",
    ["phoenix_travel", "after_deposit", "no_market_bank", "detail", "before_batch"],
)
def test_approach_stall_requires_exact_movement_only_warehouse_stall(stalled, changed):
    x = stalled
    frames = list(STALL_TRACE)
    if changed == "phoenix_travel":
        frames = [("meteor_banking.py", "trip")] + frames[5:]
    if changed == "after_deposit":
        frames.insert(5, ("banking.py", "open_warehouse"))
    if changed == "no_market_bank":
        frames.remove(("meteor_banking.py", "market_bank"))
    x.failure["failure_trace"] = trace(frames)
    if changed == "detail":
        x.failure["detail"] = "Town route remains obstructed"
    if changed == "before_batch":
        x.failure["time"] = 819.0
    x.write_events()
    with pytest.raises(ValueError, match="approach stall|exchange"):
        recovery.capture_pre_admission_tail(x.loop)
    assert "pre_admission_restock_tail" not in x.visit.state() and not x.calls


@pytest.mark.parametrize("changed", ["bag", "silver", "ammo", "manual", "holds"])
def test_approach_stall_rejects_changed_post_exchange_ownership(stalled, changed):
    x = stalled
    if changed == "bag":
        x.bag["items"].pop()
    if changed == "silver":
        x.bag["silver"] -= 1
    if changed == "ammo":
        x.bag["equipped_ammo"]["amount"] -= 1
    if changed == "manual":
        x.manual["observation"]["windows_absent"] = False
    if changed == "holds":
        x.hold = True
    with pytest.raises(ValueError):
        recovery.capture_pre_admission_tail(x.loop)
    assert "pre_admission_restock_tail" not in x.visit.state() and not x.calls


@pytest.mark.parametrize("changed", ["attempt", "active", "meteor", "bag"])
def test_approach_stall_reentry_rejects_changed_market_meteor_or_bag(stalled, changed):
    from conquest.no_transfer_town_recovery import warehouse_fallback_only

    x = stalled
    assert recovery.capture_pre_admission_tail(x.loop)
    if changed == "attempt":
        x.market["attempts"].append({"merchant": "Dutch", "at": 900.0})
    if changed == "active":
        x.market["phase"] = "active"
    if changed == "meteor":
        x.meteor["receipts"] = [{"stored": 3, "verified_in_warehouse": True}]
    if changed == "bag":
        x.bag["items"].pop()
    write_json(x.market_path, x.market)
    write_json(meteor_banking.JOURNAL, x.meteor)
    with pytest.raises(ValueError, match="changed"):
        recovery.capture_pre_admission_tail(x.loop)
    with pytest.raises(ValueError, match="changed"):
        warehouse_fallback_only(x.loop, read_json(meteor_banking.JOURNAL))
    assert not x.calls


@pytest.mark.parametrize("suffix", ["refusal", "gameplay", "foreign_refusal"])
def test_approach_stall_tolerates_only_its_own_earlier_capture_refusal(stalled, suffix):
    x = stalled
    refusal = {
        "event": "failed",
        "time": 960.0,
        "town_visit_id": "original-town",
        "detail": "Restock continuation is not the exact pre-admission grant rejection",
        "error_type": "builtins.ValueError",
        "failure_trace": trace(REFUSAL_TRACE),
    }
    if suffix == "gameplay":
        refusal = {"event": "travel", "time": 960.0, "town_visit_id": "original-town"}
    if suffix == "foreign_refusal":
        refusal["failure_trace"] = trace(
            REFUSAL_TRACE + [("meteor_banking.py", "market_bank")]
        )
    x.events += [
        {"event": "started", "time": 950.0, "town_visit_id": "original-town"},
        refusal,
    ]
    x.write_events()
    if suffix == "refusal":
        assert recovery.capture_pre_admission_tail(x.loop)
        claim = x.visit.state()["pre_admission_restock_tail"]
        assert claim["failure"] == x.failure
    else:
        with pytest.raises(ValueError):
            recovery.capture_pre_admission_tail(x.loop)
        assert "pre_admission_restock_tail" not in x.visit.state()
    assert not x.calls


def test_approach_stall_resume_completes_cash_tail_once(stalled):
    x = stalled
    x.returned()
    assert x.visit.state()["pre_admission_restock_tail"]["capture_kind"] == (
        "approach_stall"
    )
    assert recovery.resume_pre_admission_tail(x.loop)
    row = x.visit.state()
    assert row["town_work_completed_kind"] == "restock" and x.loop.cycles == 1
    assert read_json(x.market_path) == row["pre_admission_restock_tail"]["market"]
    assert x.calls == [
        ("open",),
        ("transfer", "deposit", 300),
        ("close", "Warehouse+Inventory"),
        ("close", "Shop"),
        ("service", {"town": True}),
        ("record", "restock_complete"),
    ]
    before = list(x.calls)
    assert recovery.resume_pre_admission_tail(x.loop) is False and x.calls == before


def test_approach_stall_resume_rejects_changed_market_before_cash(stalled):
    x = stalled
    x.returned()
    x.market["attempts"].append({"merchant": "Dutch", "at": 905.0})
    write_json(x.market_path, x.market)
    with pytest.raises(ValueError, match="Market budget"):
        recovery.resume_pre_admission_tail(x.loop)
    assert x.visit.state()["pre_admission_restock_tail"]["phase"] == "captured"
    assert not x.calls


def test_approach_stall_uncertain_cash_never_replays(stalled, monkeypatch):
    x = stalled
    x.returned()

    def transfer(*args):
        assert (
            x.visit.state()["pre_admission_restock_tail"]["phase"] == "cash_attempted"
        )
        x.calls.append(("attempt",))
        raise OSError("uncertain")

    monkeypatch.setattr(banking, "transfer", transfer)
    with pytest.raises(OSError):
        recovery.resume_pre_admission_tail(x.loop)
    before = list(x.calls)
    with pytest.raises(ValueError, match="replay"):
        recovery.resume_pre_admission_tail(x.loop)
    with pytest.raises(ValueError):
        recovery.capture_pre_admission_tail(x.loop)
    assert x.calls == before and "town_work_completed_at" not in x.visit.state()
