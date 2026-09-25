"""Restock cash tail after a restart completed the visit's Meteor batch.

Live incident (town visit faf88a59..., reasons ["restock"]): the original
after_shopping() stopped inside Meteor consolidation ("Meteor route fare was
not verified"), a restart finished the Market work and returned to Phoenix,
then every later start refused to leave town because the visit's own
deposit/withdraw step never ran.  No game process, file outside tmp_path or
native input is touched here.

Failure modes, written before the implementation (each is a test below):

 FM1  A transfers.jsonl row at/after required_at (or an unreadable time):
      money may already have moved -> refuse, no input.  Silver banking
      after shopping disabled -> refuse rather than guess the tail.
 FM2  A silver_* event for the visit, or the original failure is not inside
      after_shopping -> stash_valuables -> consolidate (e.g. at transfer):
      the cash step may have run -> refuse.
 FM3  Meteor journal not completed, or started before required_at: not this
      class -> the generic unfinished-town refusal still applies.
 FM4  Meteor receipts missing/unverified/duplicated, scroll receipt missing or
      wrong type, broken started<=verified<=completed order, no return
      snapshot, unverified exchange, operator scroll confirmations, other
      origin -> refuse.
 FM5  A non-terminal delivery transaction or an unsettled/orphan admission
      since required_at, or this visit's Market service still active ->
      refuse.
 FM6  Any other hold (journey, route, delivery, withdrawal, overflow, halt),
      merchant input owner / granted handoff, or a manual session -> refuse.
 FM7  Different or missing process identity (health, loop or restock_target)
      -> refuse before any control change.
 FM8  Not on the restock map, dead, hp 0, stale observation, paused, manual
      mouse/fence, or farming still enabled after stop -> refuse.
 FM9  Supplies still need town, a stash candidate is carried, or carried
      ownership differs from the verified Market return snapshot (silver
      changed = an unrecorded transfer may have happened) -> refuse.
 FM10 A transaction event after meteor_loop_complete, a missing/duplicated
      meteor_loop_complete or consolidation start, missing events audit, or
      a changed route -> refuse.
 FM11 cash_attempted without cash_verified on a later start (transfer raised,
      unverified or missing receipt, warehouse open failed, bank changed) ->
      refuse; the transfer is never replayed.  cash_verified without
      completion (crash in the panel tail) also refuses.
 FM12 Manual Stop before the claim -> nothing written, no input.
 FM13 Other continuation kinds (pre-admission capture, operator claim,
      verified tail, urgent/acceptance visits, completed visits) are left
      untouched: resume() returns False without reading the game.
 FM14 The claim is durable before the warehouse is opened, and the transfer
      happens at most once; a completed visit is idempotent.
"""

import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from conquest import banking, meteor_banking, town_visit, urgent_town_recovery
from conquest import restock_cash_tail as tail
from conquest import restock_town_recovery as recovery
from conquest.discord_notify import read_json, write_json
from conquest.merchants import bridge, delivery_operation, handoff, service_visit

VISIT = "faf88a59ca1c4393b0006730098420ba"
R = 1790302252.6440544  # required_at
STARTED = 1790302348.7322836
F0 = 1790302357.41
RESTART = 1790302765.61
MARKET_VERIFIED = 1790302853.3027735
RETURN_SUBMITTED = 1790302875.1153297
COMPLETED = 1790302899.4116347
M = 1790302899.43
REQ1 = 1790302900.67
RESTART2 = 1790307066.27
REQ2 = 1790307066.58
NOW = 1790310000.0
SCROLL = 296714065
STORED = 5077817
TARGET = {
    "pid": 18532,
    "path": "C:\\Program Files\\Classic Conquer 2.0\\bin\\64\\ImConquer.exe",
    "creation_time_100ns": 134345064188672222,
    "architecture": "x64",
}
RECEIPTS = [
    {"stored": uid, "type_id": kind, "verified_in_warehouse": True}
    for uid, kind in (
        (296702517, 113404),
        (296703666, 134604),
        (296704555, 118734),
        (296707607, 131624),
        (296709425, 113505),
        (SCROLL, 720027),
    )
]


def item(uid, type_id, slot, amount=1, limit=1, plus=0):
    return {
        "uid": uid,
        "type_id": type_id,
        "amount": amount,
        "limit": limit,
        "slot": slot,
        "plus": plus,
    }


RETURN_BEFORE = {
    "items": [item(296630873, 1050002, 0, 5, 5000)]
    + [
        item(uid, 1000020, n + 1)
        for n, uid in enumerate((296712662, 296712666, 296712667, 296712671, 296712674))
    ],
    "equipped_ammo": item(296630869, 1050002, None, 1850, 5000),
    "silver": 21795,
    "capacity": 40,
}


def trace(*frames):
    return [{"file": f, "function": fn, "line": 0} for f, fn in frames]


FARE_FAILURE = trace(
    ("overnight.py", "run"),
    ("overnight.py", "restock"),
    ("banking.py", "after_shopping"),
    ("banking.py", "stash_valuables"),
    ("meteor_banking.py", "consolidate"),
    ("meteor_banking.py", "resume"),
    ("meteor_banking.py", "trip"),
)
GUARD_FAILURE = trace(
    ("overnight.py", "run"),
    ("overnight.py", "_run_route"),
    ("town_visit.py", "require_town_work_complete"),
)
GUARD = (
    "Unfinished town work needs read-only transaction reconciliation before "
    "return; do not replay a warehouse, purchase, or monetary action"
)


def ev(time, event, **fields):
    return {"time": time, "event": event, "town_visit_id": VISIT, **fields}


def original_events():
    """Events of the first process, up to the unverified-fare failure."""
    return [
        *(ev(1790302318.52 + n / 2, "purchase") for n in range(5)),
        ev(1790302348.74, "meteor_consolidation_started"),
        ev(1790302355.36, "meteor_travel"),
        ev(
            F0,
            "failed",
            detail="Meteor route fare was not verified",
            error_type="builtins.ValueError",
            failure_trace=FARE_FAILURE,
        ),
    ]


def restart_events():
    """Events of the 22:19 restart and the later refused 23:31 restart."""
    return [
        ev(RESTART, "started"),
        ev(1790302776.54, "meteor_exchange_verified", scroll_uid=SCROLL),
        ev(1790302793.07, "merchant_delivery_verified"),
        *(
            ev(1790302842.36 + n, "valuable_stored", **r)
            for n, r in enumerate(RECEIPTS)
        ),
        ev(1790302865.37, "meteor_travel"),
        ev(M, "meteor_loop_complete"),
        ev(
            REQ1,
            "failed",
            detail=GUARD,
            error_type="builtins.ValueError",
            failure_trace=GUARD_FAILURE,
        ),
        ev(RESTART2, "started"),
        ev(
            REQ2,
            "failed",
            detail=GUARD,
            error_type="builtins.ValueError",
            failure_trace=GUARD_FAILURE,
        ),
    ]


def completed_meteor():
    return {
        "origin": 1011,
        "meteor_uids": list(range(296645651, 296645661)),
        "started_at": STARTED,
        "phase": "completed",
        "departure_attempted": True,
        "scroll_uid": SCROLL,
        "exchange_verified": True,
        "receipts": deepcopy(RECEIPTS),
        "market_verified_at": MARKET_VERIFIED,
        "return_submitted_at": RETURN_SUBMITTED,
        "return_before": deepcopy(RETURN_BEFORE),
        "completed_at": COMPLETED,
    }


@pytest.fixture
def rig(tmp_path, monkeypatch):
    x = NS(
        now=NOW,
        calls=[],
        hold=False,
        stops=[],
        stop_disables=True,
        stored=STORED,
        deposit=None,
        tmp=tmp_path,
    )
    monkeypatch.setattr(tail.time, "time", lambda: x.now)
    monkeypatch.setattr(recovery, "EVENTS", tmp_path / "events.jsonl")
    monkeypatch.setattr(recovery, "_other_holds", lambda: x.hold)
    monkeypatch.setattr(urgent_town_recovery, "transaction_holds", lambda: x.hold)
    monkeypatch.setattr(meteor_banking, "JOURNAL", tmp_path / "meteor.json")
    monkeypatch.setattr(meteor_banking, "POLICY", tmp_path / "meteor-policy.json")
    monkeypatch.setattr(delivery_operation, "JOURNAL", tmp_path / "deliveries.sqlite3")
    write_json(meteor_banking.POLICY, {"origins": {"1011": {"return": {"fare": 0}}}})
    write_json(banking.CONFIG, {"enabled": True, "deposit_after_shopping": True})
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
            ("route-delivery:old", "farmer_delivery", "aborted", R - 900, R - 800),
        )
    x.add_delivery = lambda: _add_delivery(delivery_operation.JOURNAL)
    x.add_delivery()
    # Ledger: the last verified deposit precedes this visit.
    banking.LEDGER.write_text(
        json.dumps(
            {
                "time": 1790300423.007553,
                "direction": "deposit",
                "amount": 27713,
                "silver": 200,
                "stored_silver": STORED,
                "verified": True,
            }
        )
        + "\n"
    )
    x.market_path = tmp_path / "market.json"
    write_json(
        x.market_path,
        {
            "visit_id": "e4e2ee0ea6fb46ff98508d476f8d68d7",
            "town_visit_id": VISIT,
            "farmer_profile_id": "farmer",
            "phase": "departed",
            "started_at": 1790302777.28,
            "departed_at": 1790302875.78,
        },
    )
    monkeypatch.setattr(service_visit, "MarketVisit", lambda: NS(path=x.market_path))
    x.visit = town_visit.TownVisit(
        tmp_path / "town-visit.json",
        profile="farmer",
        clock=lambda: x.now,
        probe=lambda: {"available": True},
    )
    write_json(
        x.visit.path,
        {
            "version": 2,
            "town_visit_id": VISIT,
            "farmer_profile_id": "farmer",
            "phase": "town_work",
            "reasons": ["restock"],
            "required_at": R,
            "hunt_map_id": 1011,
            "route_id": "bandit",
            "baseline": {"available": True, "cursor": 4599231},
            "history": [],
            "restock_target": deepcopy(TARGET),
        },
    )
    x.meteor = completed_meteor()
    write_json(meteor_banking.JOURNAL, x.meteor)
    x.events = original_events() + restart_events()
    x.write_events = lambda: recovery.EVENTS.write_text(
        "".join(json.dumps(e) + "\n" for e in x.events)
    )
    x.write_events()
    x.bag = deepcopy(RETURN_BEFORE)
    x.status = {
        "input_owner": None,
        "handoff_requested": None,
        "handoff_granted": False,
    }
    x.manual = {}
    monkeypatch.setattr(
        bridge,
        "request",
        lambda body: deepcopy(
            x.status if body["action"] == "status" else {"farmer": x.manual}
        ),
    )
    x.life = {"map_id": 1011, "dead_candidate": False, "current_hp": 1000}
    x.controls = {
        "control": {"enabled": False, "paused": False},
        "manual_input_fence": False,
        "manual_mouse": False,
    }
    x.health_target = deepcopy(TARGET)
    x.stale = 0.0

    def health():
        return {
            "profile_id": "farmer",
            "target": deepcopy(x.health_target),
            "embedded_controls": {
                **deepcopy(x.controls),
                "life": deepcopy(x.life),
                "observed_at": x.now - x.stale,
            },
        }

    def claim_phase():
        return (x.visit.state().get(tail.CLAIM) or {}).get("phase")

    x.claim_phase = claim_phase

    def town(action, **args):
        if action == "supplies":
            return deepcopy(x.bag)
        if action == "close":
            x.calls.append(("close", args["window"]))
            return None
        if action == "warehouse-money":
            return {"silver": x.bag["silver"], "stored_silver": x.stored}
        if action in ("warehouse-money-deposit", "warehouse-money-withdraw"):
            # The fsynced once-only marker precedes the sole monetary input.
            assert claim_phase() == "cash_attempted"
            direction = action.rsplit("-", 1)[1]
            amount = args["amount"]
            x.calls.append((direction, amount))
            if x.deposit is not None:
                return x.deposit(direction, amount)
            sign = -1 if direction == "deposit" else 1
            x.bag["silver"] += sign * amount
            x.stored -= sign * amount
            return {
                "direction": direction,
                "amount": amount,
                "silver": x.bag["silver"],
                "stored_silver": x.stored,
                "verified": True,
            }
        pytest.fail(f"Unexpected game action {action} {args}")

    def record(event, **fields):
        x.calls.append(("record", event))
        fields.setdefault("town_visit_id", x.visit.active_id())
        x.events.append({"time": x.now, "event": event, **fields})
        x.write_events()
        x.now += 0.25

    def stop_farm():
        x.stops.append(True)
        if x.stop_disables:
            x.controls["control"]["enabled"] = False

    x.loop = NS(
        town_visit=x.visit,
        identity=deepcopy(TARGET),
        health=health,
        check_stop=lambda: None,
        town=town,
        adopt_ammunition=lambda: None,
        stop_farm=stop_farm,
        record=record,
        cycles=0,
        terrain=NS(map_id=1011),
        route=NS(
            id="bandit",
            map_id=1011,
            restock_map_id=1011,
            supplies=NS(arrow_type=1050002, healing_type=1000020, healing_restock_to=5),
        ),
    )

    def open_warehouse(loop):
        # The durable claim exists before the first warehouse input.
        assert claim_phase() == "cash_attempted"
        x.calls.append(("open",))
        return town("warehouse-money")

    monkeypatch.setattr(banking, "open_warehouse", open_warehouse)
    monkeypatch.setattr(
        banking,
        "close_warehouse",
        lambda loop: x.calls.append(("close", "Warehouse+Inventory")),
    )
    monkeypatch.setattr(banking, "transport_reserve", lambda: 200)
    monkeypatch.setattr(
        banking,
        "after_shopping",
        lambda *a, **k: pytest.fail("Full shopping/banking must not replay"),
    )
    monkeypatch.setattr(
        handoff, "service_window", lambda loop, **kw: x.calls.append(("service", kw))
    )
    return x


def _add_delivery(path):
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT OR REPLACE INTO transactions VALUES(?,?,?,?,?)",
            (
                "route-delivery:e6af050afe5e482983cfdbbf00c80b42",
                "farmer_delivery",
                "verified",
                1790302781.93942,
                1790302792.7273846,
            ),
        )
        db.execute(
            "INSERT OR REPLACE INTO delivery_admissions VALUES(?,?,?,?)",
            (
                "route-delivery:e6af050afe5e482983cfdbbf00c80b42",
                "transaction_started",
                1790302781.8552265,
                1790302781.93942,
            ),
        )


def ledger():
    if not banking.LEDGER.exists():
        return []
    return [json.loads(line) for line in banking.LEDGER.read_text().splitlines()]


def assert_untouched(x, ledger_rows=1):
    row = x.visit.state()
    assert tail.CLAIM not in row
    assert "town_work_completed_at" not in row and "verified_tail" not in row
    assert x.calls == []
    assert len(ledger()) == ledger_rows


# FM14 -- the live class finishes exactly once.
def test_live_state_finishes_cash_tail_once(rig):
    x = rig
    assert tail.resume(x.loop) is True
    row = x.visit.state()
    assert row["town_visit_id"] == VISIT and row["phase"] == "town_work"
    assert row["town_work_completed_kind"] == "restock"
    assert row["verified_tail"]["ownership"]["silver"] == 200
    claim = row[tail.CLAIM]
    assert claim["phase"] == "cash_verified"
    assert claim["cash_receipt"]["verified"] is True
    assert claim["cash_receipt"]["amount"] == 21795 - 200
    assert claim["target"] == TARGET
    assert x.calls == [
        ("open",),
        ("deposit", 21595),
        ("record", "silver_deposit"),
        ("close", "Warehouse+Inventory"),
        ("close", "Shop"),
        ("service", {"town": True}),
        ("record", "restock_cash_tail_complete"),
        ("record", "restock_complete"),
    ]
    assert [r["amount"] for r in ledger()] == [27713, 21595]
    assert x.loop.cycles == 1
    before = list(x.calls)
    assert tail.resume(x.loop) is False
    assert x.calls == before and len(ledger()) == 2
    x.visit.require_town_work_complete()


def test_withdraws_only_the_reserve_deficit(rig):
    x = rig
    for snapshot in (x.meteor["return_before"], x.bag):
        snapshot["silver"] = 50
    write_json(meteor_banking.JOURNAL, x.meteor)
    assert tail.resume(x.loop) is True
    assert ("withdraw", 150) in x.calls and x.bag["silver"] == 200
    assert x.visit.state()[tail.CLAIM]["cash_receipt"]["direction"] == "withdraw"


def test_exact_reserve_completes_without_money_input(rig):
    x = rig
    for snapshot in (x.meteor["return_before"], x.bag):
        snapshot["silver"] = 200
    write_json(meteor_banking.JOURNAL, x.meteor)
    assert tail.resume(x.loop) is True
    assert not any(c[0] in ("deposit", "withdraw") for c in x.calls)
    assert x.visit.state()[tail.CLAIM]["phase"] == "cash_verified"
    assert len(ledger()) == 1


def test_stopped_farm_is_disabled_before_native_safety(rig):
    x = rig
    x.controls["control"]["enabled"] = True
    assert tail.resume(x.loop) is True
    assert x.stops and x.controls["control"]["enabled"] is False


def _mutate(x, change):
    meteor, events = x.meteor, x.events
    row = read_json(x.visit.path)
    market = read_json(x.market_path)
    # FM1 money
    if change == "ledger_after_required":
        with banking.LEDGER.open("a") as out:
            out.write(json.dumps({"time": R + 60, "verified": True}) + "\n")
    if change == "ledger_at_required":
        with banking.LEDGER.open("a") as out:
            out.write(json.dumps({"time": R, "verified": True}) + "\n")
    if change == "ledger_bad_time":
        with banking.LEDGER.open("a") as out:
            out.write(json.dumps({"time": "later", "verified": True}) + "\n")
    if change == "banking_disabled":
        write_json(banking.CONFIG, {"enabled": True, "deposit_after_shopping": False})
    # FM2 original interruption / silver events
    if change == "silver_event":
        events.insert(3, ev(1790302330.0, "silver_withdraw", amount=100))
    if change == "failure_at_transfer":
        events[7]["failure_trace"] = trace(
            ("overnight.py", "restock"),
            ("banking.py", "after_shopping"),
            ("banking.py", "transfer"),
        )
    if change == "no_original_failure":
        del events[7]
    if change == "failure_without_consolidate":
        events[7]["failure_trace"] = trace(
            ("overnight.py", "restock"),
            ("banking.py", "after_shopping"),
            ("banking.py", "stash_valuables"),
            ("banking.py", "deposit_stash_items"),
        )
    if change == "later_after_shopping_failure":
        events.insert(
            9,
            ev(
                1790302780.0,
                "failed",
                failure_trace=trace(
                    ("overnight.py", "_run_route"),
                    ("banking.py", "after_shopping"),
                    ("banking.py", "transfer"),
                ),
            ),
        )
    # FM3 not this class
    if change == "meteor_not_completed":
        meteor["phase"] = "returning"
    if change == "meteor_before_required":
        meteor["started_at"] = R - 1
    # FM4 meteor evidence
    if change == "receipts_missing":
        meteor["receipts"] = []
    if change == "receipt_unverified":
        meteor["receipts"][0]["verified_in_warehouse"] = False
    if change == "scroll_receipt_missing":
        meteor["receipts"] = meteor["receipts"][:-1]
    if change == "scroll_receipt_type":
        meteor["receipts"][-1]["type_id"] = 1088001
    if change == "duplicate_receipt":
        meteor["receipts"].append(deepcopy(meteor["receipts"][0]))
    if change == "verified_after_completed":
        meteor["market_verified_at"] = COMPLETED + 1
    if change == "completed_in_future":
        meteor["completed_at"] = NOW + 60
    if change == "return_snapshot_missing":
        del meteor["return_before"]
    if change == "return_not_submitted":
        del meteor["return_submitted_at"]
    if change == "exchange_unverified":
        meteor["exchange_verified"] = False
    if change == "scroll_transfer_confirmed":
        meteor["user_confirmed_scroll_transfer"] = True
    if change == "other_origin":
        meteor["origin"] = 1002
    if change == "fare_unknown":
        write_json(meteor_banking.POLICY, {"origins": {}})
    # FM5 deliveries / Market
    with sqlite3.connect(delivery_operation.JOURNAL) as db:
        if change == "pending_delivery":
            db.execute(
                "INSERT INTO transactions VALUES('p','farmer_delivery','uncertain',?,?)",
                (R + 10, R + 10),
            )
        if change == "old_delivery_updated_uncertain":
            db.execute(
                "INSERT INTO transactions VALUES('o','farmer_delivery','reconciling',?,?)",
                (R - 50, R + 5),
            )
        if change == "unsettled_admission":
            db.execute(
                "INSERT INTO delivery_admissions VALUES('a','admitted',?,?)",
                (R + 5, R + 5),
            )
        if change == "orphan_admission":
            db.execute(
                "INSERT INTO delivery_admissions VALUES('b','transaction_started',?,?)",
                (R + 5, R + 5),
            )
    if change == "market_active":
        market["phase"] = "active"
    # FM6 holds / ownership of input
    if change == "other_hold":
        x.hold = True
    if change == "merchant_input_owner":
        x.status["input_owner"] = "Dutch"
    if change == "handoff_granted":
        x.status["handoff_granted"] = True
    if change == "handoff_requested":
        x.status["handoff_requested"] = "merchant-refill:Dutch:1"
    if change == "manual_session":
        x.manual["session"] = {"id": "visitor"}
    if change == "manual_fenced":
        x.manual["input_fenced"] = True
    # FM7 identity
    if change == "other_process":
        x.health_target["creation_time_100ns"] += 1
    if change == "loop_identity":
        x.loop.identity = {**TARGET, "pid": 1}
    if change == "restock_target_missing":
        del row["restock_target"]
    if change == "restock_target_differs":
        row["restock_target"] = {**TARGET, "pid": 99}
    # FM8 native safety
    if change == "not_restock_map":
        x.life["map_id"] = 1036
    if change == "dead":
        x.life["dead_candidate"] = True
    if change == "hp_zero":
        x.life["current_hp"] = 0
    if change == "stale_observation":
        x.stale = 5.0
    if change == "paused":
        x.controls["control"]["paused"] = True
    if change == "manual_mouse":
        x.controls["manual_mouse"] = True
    if change == "manual_fence":
        x.controls["manual_input_fence"] = True
    if change == "farming_stays_on":
        x.controls["control"]["enabled"] = True
        x.stop_disables = False
    # FM9 supplies / ownership
    if change == "needs_town":
        for snapshot in (meteor["return_before"], x.bag):
            snapshot["items"] = snapshot["items"][:1]
    if change == "valuable_carried":
        for snapshot in (meteor["return_before"], x.bag):
            snapshot["items"].append(item(296700638, 1088001, 9))
    if change == "silver_changed":
        x.bag["silver"] = 200
    if change == "item_changed":
        x.bag["items"][1]["amount"] = 0
    if change == "item_added":
        x.bag["items"].append(item(5, 1000020, 20))
    # FM10 event chain / route
    if change == "transaction_after_meteor":
        events.append(ev(NOW - 10, "valuable_stored", stored=1))
    if change == "purchase_after_meteor":
        events.append(ev(NOW - 10, "purchase"))
    if change == "meteor_complete_event_missing":
        events[:] = [e for e in events if e["event"] != "meteor_loop_complete"]
    if change == "second_consolidation":
        events.append(ev(NOW - 10, "meteor_consolidation_started"))
    if change == "events_missing":
        events.clear()
    if change == "route_changed":
        row["route_id"] = "other"
    write_json(meteor_banking.JOURNAL, meteor)
    write_json(x.visit.path, row)
    write_json(x.market_path, market)
    if change == "events_missing":
        recovery.EVENTS.unlink()
    else:
        x.write_events()


REFUSALS = [
    "ledger_after_required",
    "ledger_at_required",
    "ledger_bad_time",
    "banking_disabled",
    "silver_event",
    "failure_at_transfer",
    "no_original_failure",
    "failure_without_consolidate",
    "later_after_shopping_failure",
    "meteor_not_completed",
    "meteor_before_required",
    "receipts_missing",
    "receipt_unverified",
    "scroll_receipt_missing",
    "scroll_receipt_type",
    "duplicate_receipt",
    "verified_after_completed",
    "completed_in_future",
    "return_snapshot_missing",
    "return_not_submitted",
    "exchange_unverified",
    "scroll_transfer_confirmed",
    "other_origin",
    "fare_unknown",
    "pending_delivery",
    "old_delivery_updated_uncertain",
    "unsettled_admission",
    "orphan_admission",
    "market_active",
    "other_hold",
    "merchant_input_owner",
    "handoff_granted",
    "handoff_requested",
    "manual_session",
    "manual_fenced",
    "other_process",
    "loop_identity",
    "restock_target_missing",
    "restock_target_differs",
    "not_restock_map",
    "dead",
    "hp_zero",
    "stale_observation",
    "paused",
    "manual_mouse",
    "manual_fence",
    "farming_stays_on",
    "needs_town",
    "valuable_carried",
    "silver_changed",
    "item_changed",
    "item_added",
    "transaction_after_meteor",
    "purchase_after_meteor",
    "meteor_complete_event_missing",
    "second_consolidation",
    "events_missing",
    "route_changed",
]


# FM1-FM10: every missing proof fails closed before any claim or input.
@pytest.mark.parametrize("change", REFUSALS)
def test_missing_evidence_never_reaches_claim_or_input(rig, change):
    x = rig
    _mutate(x, change)
    ledger_rows = len(ledger())
    try:
        outcome = tail.resume(x.loop)
    except ValueError:
        outcome = "refused"
    assert outcome in (False, "refused")
    assert_untouched(x, ledger_rows)
    # The existing generic guard still keeps the route in town.
    with pytest.raises(ValueError, match="Unfinished town work"):
        x.visit.require_town_work_complete()


# FM3: a different class is left to the generic guard rather than refused here.
@pytest.mark.parametrize("change", ["meteor_not_completed", "meteor_before_required"])
def test_not_this_class_returns_false_without_reading_the_game(rig, change):
    x = rig
    _mutate(x, change)
    x.loop.health = lambda: pytest.fail("Game must not be read for another class")
    assert tail.resume(x.loop) is False
    assert_untouched(x)


# FM13: other kinds of continuation keep their existing behaviour.
@pytest.mark.parametrize(
    "field,value",
    [
        ("pre_admission_restock_tail", {"phase": "captured"}),
        ("restock_recovery_claim", {"reference": "operator"}),
        ("restock_recovery_attempted_at", 1.0),
        ("verified_tail", {"kind": "restock"}),
        ("urgent_intent", [{"uid": 1, "type_id": 2}]),
        ("urgent_recovery_claim", {"reference": "operator"}),
        ("reasons", ["urgent_banking", "restock"]),
        ("reasons", ["merchant_acceptance"]),
        ("phase", "returning_to_hunt"),
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
    assert tail.resume(x.loop) is False
    assert read_json(x.visit.path) == before and x.calls == []


def test_empty_visit_is_not_touched(rig):
    x = rig
    write_json(x.visit.path, {})
    x.loop.health = lambda: pytest.fail("No visit must not read the game")
    assert tail.resume(x.loop) is False and x.calls == []


# FM12
def test_manual_stop_prevents_claim_and_input(rig):
    x = rig

    def stop():
        raise ValueError("Manual Stop")

    x.loop.check_stop = stop
    with pytest.raises(ValueError, match="Manual Stop"):
        tail.resume(x.loop)
    assert_untouched(x)
    assert not x.stops


# FM11: an uncertain cash step is never replayed.
@pytest.mark.parametrize(
    "outcome",
    [
        "transfer_raises",
        "receipt_unverified",
        "receipt_missing",
        "open_fails",
        "bank_differs",
        "post_cash_crash",
    ],
)
def test_attempted_cash_is_never_replayed(rig, monkeypatch, outcome):
    x = rig
    if outcome == "transfer_raises":

        def deposit(direction, amount):
            raise OSError("client closed after submit")

        x.deposit = deposit
    if outcome == "receipt_unverified":
        x.deposit = lambda direction, amount: {"verified": False}
    if outcome == "receipt_missing":
        monkeypatch.setattr(
            banking,
            "transfer",
            lambda loop, direction, amount: x.calls.append((direction, amount)),
        )
    if outcome == "open_fails":

        def open_fails(loop):
            assert x.claim_phase() == "cash_attempted"
            x.calls.append(("open",))
            raise ValueError("Warehouse opening unverified; no repeat input issued")

        monkeypatch.setattr(banking, "open_warehouse", open_fails)
    if outcome == "bank_differs":
        x.stored = STORED
        monkeypatch.setattr(
            banking,
            "open_warehouse",
            lambda loop: (
                x.calls.append(("open",)) or {"silver": 200, "stored_silver": STORED}
            ),
        )
    if outcome == "post_cash_crash":
        monkeypatch.setattr(
            banking,
            "close_warehouse",
            lambda loop: (_ for _ in ()).throw(OSError("crash")),
        )
    with pytest.raises((OSError, ValueError)):
        tail.resume(x.loop)
    state = x.visit.state()
    assert "town_work_completed_at" not in state
    expected = "cash_verified" if outcome == "post_cash_crash" else "cash_attempted"
    assert state[tail.CLAIM]["phase"] == expected
    before, rows = list(x.calls), len(ledger())
    assert sum(c[0] in ("deposit", "withdraw") for c in before) <= 1
    x.loop.health = lambda: pytest.fail("A replay refusal must not read the game")
    with pytest.raises(ValueError, match="replay"):
        tail.resume(x.loop)
    assert x.calls == before and len(ledger()) == rows
    assert "town_work_completed_at" not in x.visit.state()
    with pytest.raises(ValueError, match="Unfinished town work"):
        x.visit.require_town_work_complete()


# --------------------------------------------------------------------------
# End-to-end: the live sequence through the real overnight route resume.
# --------------------------------------------------------------------------


class ReachedHunting(Exception):
    pass


@pytest.fixture
def route(rig, monkeypatch):
    """Drive OvernightLoop._run_route with only unrelated subsystems stubbed."""
    from conquest import (
        city_travel,
        manual_storage_recovery,
        merchant_loop_acceptance,
        storage_overflow,
    )
    from conquest.merchants import delivery_journey
    from conquest.overnight import OvernightLoop

    x = rig
    x.trace = []
    monkeypatch.setattr(
        delivery_journey,
        "reconcile_pending_scroll",
        lambda loop: x.trace.append("scroll_reconcile"),
    )
    monkeypatch.setattr(delivery_journey, "pending", lambda: False)
    monkeypatch.setattr(delivery_operation, "guard_protected_assets", lambda: None)
    monkeypatch.setattr(manual_storage_recovery, "resume", lambda loop: None)
    monkeypatch.setattr(storage_overflow, "pending", lambda: False)
    monkeypatch.setattr(merchant_loop_acceptance, "cycle_pending", lambda: False)
    monkeypatch.setattr(
        meteor_banking,
        "pending",
        lambda: (
            read_json(meteor_banking.JOURNAL).get("phase") in meteor_banking.PENDING
        ),
    )
    monkeypatch.setattr(
        city_travel, "ensure_city_visit", lambda loop: x.trace.append("city_check")
    )
    closing = banking.close_warehouse
    monkeypatch.setattr(
        banking,
        "close_warehouse",
        lambda loop: (x.trace.append("close_warehouse"), closing(loop))[1],
    )

    def reached():
        x.trace.append("prepare_supplies")
        raise ReachedHunting()

    x.loop.living = lambda: {"embedded_controls": {"life": deepcopy(x.life)}}
    x.loop.prepare_supplies = reached
    x.run_route = lambda: OvernightLoop._run_route(x.loop)
    return x


def _artifact(x, name, start):
    row = x.visit.state()
    artifact = {
        "town_visit_id": row["town_visit_id"],
        "route_trace": x.trace,
        "events": [
            {"time": round(e["time"], 3), "event": e["event"]}
            for e in x.events
            if e.get("town_visit_id") == VISIT and e["time"] >= start
        ],
        "meteor": {
            k: read_json(meteor_banking.JOURNAL).get(k)
            for k in ("phase", "started_at", "market_verified_at", "completed_at")
        },
        "cash_tail": row.get(tail.CLAIM),
        "ledger_after_required": [r for r in ledger() if r["time"] >= R],
        "town_visit": {
            "phase": row["phase"],
            "town_work_completed_kind": row.get("town_work_completed_kind"),
            "verified_tail_silver": row["verified_tail"]["ownership"]["silver"],
        },
    }
    path = x.tmp / name
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


def test_e2e_fare_failure_restart_meteor_then_cash_tail(route, monkeypatch):
    """fare failure -> restart -> Meteor completes -> cash tail -> hunt."""
    x = route
    # State at the 22:19 restart: the original process failed at the fare.
    x.events = original_events()
    x.write_events()
    in_flight = {
        k: v
        for k, v in completed_meteor().items()
        if k
        in ("origin", "meteor_uids", "started_at", "departure_attempted", "scroll_uid")
    }
    write_json(meteor_banking.JOURNAL, {**in_flight, "phase": "travelling"})
    with sqlite3.connect(delivery_operation.JOURNAL) as db:
        db.execute("DELETE FROM transactions WHERE created>=?", (R,))
        db.execute("DELETE FROM delivery_admissions WHERE created>=?", (R,))
    x.bag["items"] += [
        item(r["stored"], r["type_id"], 20 + n) for n, r in enumerate(RECEIPTS)
    ]
    x.now = RESTART

    def native_meteor_resume(loop):
        """The verified live Market work of the restarted process."""
        x.trace.append("meteor_resume")
        loop.record("started")
        loop.record("meteor_exchange_verified", scroll_uid=SCROLL)
        x.add_delivery()
        loop.record("merchant_delivery_verified")
        for receipt in RECEIPTS:
            loop.record("valuable_stored", **receipt)
        stored = {r["stored"] for r in RECEIPTS}
        x.bag["items"] = [i for i in x.bag["items"] if i["uid"] not in stored]
        journal = read_json(meteor_banking.JOURNAL)
        journal.update(
            exchange_verified=True,
            receipts=deepcopy(RECEIPTS),
            market_verified_at=x.now,
            return_before=deepcopy(x.bag),
            return_submitted_at=x.now + 0.1,
            phase="returning",
        )
        loop.record("meteor_travel")
        journal.update(phase="completed", completed_at=x.now)
        write_json(meteor_banking.JOURNAL, journal)
        loop.record("meteor_loop_complete")
        return True

    monkeypatch.setattr(meteor_banking, "resume", native_meteor_resume)
    x.loop.stop_farm = lambda: x.trace.append("stop_farm")
    with pytest.raises(ReachedHunting):
        x.run_route()
    artifact = _artifact(x, "restock-cash-tail-e2e.json", RESTART)
    assert artifact["route_trace"] == [
        "scroll_reconcile",
        "stop_farm",  # pending Meteor on restart
        "meteor_resume",
        "close_warehouse",
        "stop_farm",  # cash tail: before its native-safety check
        "close_warehouse",  # cash tail: after the verified transfer
        "city_check",
        "prepare_supplies",
    ]
    assert [e["event"] for e in artifact["events"]] == [
        "started",
        "meteor_exchange_verified",
        "merchant_delivery_verified",
        *["valuable_stored"] * 6,
        "meteor_travel",
        "meteor_loop_complete",
        "silver_deposit",
        "restock_cash_tail_complete",
        "restock_complete",
    ]
    assert artifact["meteor"]["phase"] == "completed"
    assert artifact["cash_tail"]["phase"] == "cash_verified"
    assert [
        (r["direction"], r["amount"]) for r in artifact["ledger_after_required"]
    ] == [("deposit", 21595)]
    assert artifact["town_visit"] == {
        "phase": "town_work",
        "town_work_completed_kind": "restock",
        "verified_tail_silver": 200,
    }
    # A later start goes straight to hunting with no further bank input.
    calls = list(x.calls)
    x.trace.clear()
    with pytest.raises(ReachedHunting):
        x.run_route()
    assert x.calls == calls and len(ledger()) == 2
    assert x.trace == ["scroll_reconcile", "city_check", "prepare_supplies"]


def test_e2e_live_state_after_refused_restarts_now_completes(route, monkeypatch):
    """The current live journals (two guard refusals) finish on next start."""
    x = route
    monkeypatch.setattr(
        meteor_banking, "resume", lambda loop: pytest.fail("Meteor is complete")
    )
    x.loop.stop_farm = lambda: x.trace.append("stop_farm")
    with pytest.raises(ReachedHunting):
        x.run_route()
    artifact = _artifact(x, "restock-cash-tail-live-e2e.json", NOW)
    assert artifact["route_trace"] == [
        "scroll_reconcile",
        "stop_farm",
        "close_warehouse",
        "city_check",
        "prepare_supplies",
    ]
    assert [e["event"] for e in artifact["events"]] == [
        "silver_deposit",
        "restock_cash_tail_complete",
        "restock_complete",
    ]
    assert artifact["cash_tail"]["cash_receipt"]["amount"] == 21595


def test_e2e_uncertain_cash_keeps_route_in_town_on_every_restart(route):
    x = route

    def deposit(direction, amount):
        raise OSError("client closed after submit")

    x.deposit = deposit
    x.loop.stop_farm = lambda: x.trace.append("stop_farm")
    with pytest.raises(OSError):
        x.run_route()
    attempts = [c for c in x.calls if c[0] == "deposit"]
    assert attempts == [("deposit", 21595)]
    for _ in range(2):
        with pytest.raises(ValueError, match="replay"):
            x.run_route()
    assert [c for c in x.calls if c[0] == "deposit"] == attempts
    assert "prepare_supplies" not in x.trace
