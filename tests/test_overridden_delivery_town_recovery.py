"""Continue a restock whose Market delivery an explicit operator override settled.

Live incident (2026-09-25, town visit 4d2f63a0..., reasons ["restock"]): the
visit was restarted once (zero_transaction_restart), bought supplies, packed ten
Meteors in Market (exchange verified 05:05:59) and banking.after_shopping ->
meteor_banking.market_bank -> delivery_route.market_storage traded four items
(SteelCoronet, Coat+1, TaoRobe+1, MeteorScroll) to Dutch as
route-delivery:6ade0ca4...  Dutch's merchant journal verified exactly those four
items (delivery:Dutch:854fcba7..., 05:06:24), but the farmer transaction went
'uncertain' (a since-fixed sales observation gap) and the route failed at
05:06:27 with the no-transfer reconciliation FAILURE.  The user explicitly
confirmed an operator override (farmer transaction and Dutch's reservation are
'operator_overridden').  The 07:01:37 restart was refused by the no-transfer
proof because items did move.  No game process, live journal or native input
is touched here; the fixture is a read-only copy of the live journals.

Failure modes, written before the implementation (each is a test below):

 FM1  The override exists but Dutch's merchant journal has no verified
      delivery receipt that matches it: missing, still uncertain, another
      merchant (id, process identity or character uid), another trade partner,
      an extra/missing/changed item (uid, type_id, plus, gems), items or silver
      given by the merchant, created/verified outside the farmer transaction
      window, or two candidate receipts -> refuse.  operator_overridden alone
      is never treated as delivered.
 FM2  The override lacks operator confirmation, its confirmation reference or
      its operator, or its original-evidence digest does not bind the
      transaction's before image and exact pre-override journal steps -> refuse.
 FM3  Dutch's reservation is missing, not operator_overridden, or carries a
      different confirmation reference -> refuse.
 FM4  The farmer (the override's fresh evidence or the bag at capture) still
      holds any overridden uid -> refuse.
 FM5  The bag is not exactly meteor['after'] minus the overridden uids (an
      extra item, a missing item, changed silver) -> refuse.
 FM6  Any other delivery transaction or admission since required_at, pending
      or terminal -> refuse.
 FM7  A different game process (health target, restock_target, or the
      transaction's farmer identity) -> refuse.
 FM8  The Market service visit is inconsistent with the one attempt (a
      recorded attempt, another visit id, a still-open budget) -> refuse.
 FM9  The failure is not exactly the no-transfer reconciliation FAILURE with
      the delivery_route.settle trace, or gameplay followed it.  Only
      started/stopped and read-only recovery refusals (including the live
      07:01:37 no-transfer refusal) are tolerated -> otherwise refuse.
 FM10 no-transfer, operator-warehouse and verified-settled kinds are
      unchanged: an override whose fresh evidence still shows the scroll on
      the farmer keeps selecting the operator-warehouse proof.
 FM11 Re-entry re-proves the captured claim.  A crash before the Meteor
      return continues without repeating any delivery, deposit or transfer;
      changed evidence refuses; an attempted cash tail never replays.
 FM12 The completed zero_transaction_restart marker on this visit does not
      block the continuation.
 FM13 E2E from copies of the live journals through the real
      OvernightLoop._run_route: capture -> read-only settlement of the active
      route -> Market warehouse (nothing valuable left to store) -> scroll
      resolved only by the proved override -> free return -> Phoenix cash tail
      once -> visit complete -> hunting resumes.  A second start replays
      nothing.  A JSON artifact under tmp_path is asserted on.
"""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import field_fakes
import numpy as np
import pytest

from conquest import banking, meteor_banking
from conquest import restock_restart as restart
from conquest import restock_town_recovery as recovery
from conquest import settled_delivery_town_recovery as settled
from conquest.discord_notify import read_json, write_json
from conquest.merchants import bridge, delivery_operation, delivery_route, service_visit
from conquest.merchants.journal import Journal

FIXTURE = Path(__file__).parent / "fixtures" / "overridden-delivery-4d2f63a0.json"
VISIT = "4d2f63a06bab4631875c4e9b9ec01401"
PROFILE = "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
DUTCH = "fd719dca-8e66-4100-9f2f-0c4dd6b86a0b"  # Dutch's profile id (journal rows)
KEY = "route-delivery:6ade0ca44fff4fddbf4c3690edfa68fc"
MKEY = "delivery:Dutch:854fcba7fe4046bc896b84d218d077dc"
DELIVERED = (296757794, 296759224, 296759472, 296768164)
SCROLL = 296768164
REFUSED_AT = 1790334097.0255914  # The live 07:01:37 no-transfer refusal.
NOW = REFUSED_AT + 600.0
MARKET_WAREHOUSE = (182, 180)
PHOENIX_WAREHOUSE = (227, 246)
MARK_CONTROLLER = (215, 220)


def load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def failure_time(fx):
    return next(
        e["time"]
        for e in fx["events"]
        if e.get("event") == "failed"
        and e.get("detail")
        == "Merchant delivery needs reconciliation; valuables remain protected"
    )


class HuntResumed(Exception):
    """The fake game ends a scenario when farming is switched back On."""


def _terrain(map_id):
    from conquest.navigation import TerrainMap

    if map_id == 1011:
        return field_fakes.terrain(1011)
    return TerrainMap(
        map_id, 400, 400, np.zeros((400, 400), dtype=bool), "market", (), ()
    )


class MarketGame(field_fakes.FakeGame):
    """The live farmer in Market after the overridden four-item trade."""

    def __init__(self, clock, fx):
        super().__init__(clock, position=(225, 217), hp=1499, max_hp=1499)
        after = deepcopy(fx["meteor"]["after"])
        self.map_id = 1036
        self.bag_items = [i for i in after["items"] if i["uid"] not in DELIVERED]
        self.ammo = after["equipped_ammo"]
        self.silver = after["silver"]
        self.stored = {1036: [], 1011: []}
        self.stored_silver = fx["bank_status"]["stored_silver"]
        self.dialog = None
        self.fail_once = {}  # town action -> error raised on its next call

    def health(self):
        result = super().health()
        result["profile_id"] = PROFILE
        return result

    def request(self, info, operation, body=None):
        if operation == "controls" and (body or {}).get("enabled") is True:
            self.ops.append({"op": "controls", "enabled": True})
            raise HuntResumed()
        return super().request(info, operation, body)

    def warehouse(self):
        return MARKET_WAREHOUSE if self.map_id == 1036 else PHOENIX_WAREHOUSE

    def town(self, body):
        action = body["action"]
        if action in self.fail_once:
            raise self.fail_once.pop(action)
        if action == "gear":
            return {
                "level": 95,
                "equipment": {
                    "arrows": {
                        "uid": self.ammo["uid"],
                        "type_id": self.ammo["type_id"],
                        "level": 73,
                    }
                },
            }
        if action == "service-close-panel":
            self.open_panels.discard(body["window"])
            return {"closed": True}
        if action == "vendor-status":
            near = max(abs(a - b) for a, b in zip(self.position, self.warehouse()))
            return {"reachable": body.get("vendor_type") == 0 and near <= 6}
        if action == "warehouse-locate":
            return {"position": list(self.warehouse())}
        if action == "open-bank":
            assert max(abs(a - b) for a, b in zip(self.position, self.warehouse())) <= 6
            self.open_panels |= {"Warehouse", "Inventory"}
            return {"opened": True}
        if action == "warehouse-money":
            assert "Warehouse" in self.open_panels
            return {"silver": self.silver, "stored_silver": self.stored_silver}
        if action == "warehouse-items":
            assert "Warehouse" in self.open_panels
            return {"items": deepcopy(self.stored[self.map_id]), "capacity": 40}
        if action == "warehouse-deposit":
            assert "Warehouse" in self.open_panels
            item = next(i for i in self.bag_items if i["uid"] == body["uid"])
            self.bag_items.remove(item)
            self.stored[self.map_id].append(item)
            return {
                "stored": item["uid"],
                "type_id": item["type_id"],
                "verified_in_warehouse": True,
            }
        if action in ("warehouse-money-deposit", "warehouse-money-withdraw"):
            assert "Warehouse" in self.open_panels
            sign = 1 if action.endswith("deposit") else -1
            self.silver -= sign * body["amount"]
            self.stored_silver += sign * body["amount"]
            return {
                "direction": action.rsplit("-", 1)[1],
                "amount": body["amount"],
                "silver": self.silver,
                "stored_silver": self.stored_silver,
                "verified": True,
            }
        if action == "service-locate":
            assert body["name"] == "Mark.Controller" and self.map_id == 1036
            return {
                "identity": {
                    "map_id": 1036,
                    "type_id": 0,
                    "name": "Mark.Controller",
                    "model": 417,
                    "position": list(MARK_CONTROLLER),
                },
                "npc": {"position": list(MARK_CONTROLLER), "draw_position": [518, 300]},
            }
        if action == "service-open":
            assert body["name"] == "Mark.Controller"
            assert max(abs(a - b) for a, b in zip(self.position, MARK_CONTROLLER)) <= 6
            self.dialog = body["name"]
            return {"opened": True}
        if action == "service-dialog":
            if self.dialog != "Mark.Controller":
                raise ValueError("Dialog is not active")
            policy = json.loads(
                Path("profiles/meteor-banking.json").read_text(encoding="utf-8")
            )
            records = policy["origins"]["1011"]["return"]["dialogs"][0]["records"]
            return {
                "records": deepcopy(records),
                "window": {
                    "position": (378.0, 20.0),
                    "size": (280.0, 198.0),
                    "scroll": (0.0, 0.0),
                },
                "table": (398.0, 130.0, 618.0, 22.0),
            }
        if action == "service-select":
            assert self.dialog == "Mark.Controller"
            assert body["option"] == "Yeah. Thanks."
            self.dialog = None
            self.map_id = 1011
            self.position = [193, 266]
            return {"selected": True}
        return super().town(body)


def seed(fx, tmp_path):
    """Write the (possibly mutated) live evidence into test-owned journals."""
    farmer = Journal(delivery_operation.JOURNAL)
    merchant = Journal(tmp_path / "merchant-journal.sqlite3")
    for journal, part in ((farmer, "farmer_journal"), (merchant, "merchant_journal")):
        with journal.db() as db:
            for table, rows in fx[part].items():
                db.execute(f"DELETE FROM {table}")
                for row in rows:
                    columns = ",".join(row)
                    marks = ",".join("?" for _ in row)
                    db.execute(
                        f"INSERT INTO {table}({columns}) VALUES({marks})",
                        tuple(row.values()),
                    )
    write_json(delivery_route.STATE, fx["route"])
    write_json(meteor_banking.JOURNAL, fx["meteor"])
    banking.LEDGER.write_text(
        "".join(json.dumps(r) + "\n" for r in fx["money"]), encoding="utf-8"
    )
    write_json(banking.STATUS, fx["bank_status"])


def tx_json(fx, part, key, column, update):
    """Decode, mutate and re-encode one JSON column of a journal row."""
    table = "transactions"
    row = next(r for r in fx[part][table] if r["id"] == key)
    value = json.loads(row[column])
    update(value)
    row[column] = json.dumps(value, sort_keys=True)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from conquest import (
        navigation,
        no_transfer_town_recovery,
        runback_monitor,
        town_visit,
    )

    fx = load()
    clock = field_fakes.FakeClock(NOW)
    game = MarketGame(clock, fx)
    loop = field_fakes.install(monkeypatch, tmp_path, game, clock)
    fake_time = NS(time=clock.time, monotonic=clock.monotonic, sleep=clock.sleep)
    import conquest.overridden_delivery_town_recovery as overridden

    for module in (
        recovery,
        settled,
        restart,
        meteor_banking,
        banking,
        delivery_route,
        runback_monitor,
        overridden,
    ):
        monkeypatch.setattr(module, "time", fake_time, raising=False)
    monkeypatch.setattr(
        navigation, "read_terrain", lambda root, map_id: _terrain(map_id)
    )
    monkeypatch.setattr(
        delivery_operation, "JOURNAL", tmp_path / "merchant-deliveries.sqlite3"
    )
    receiver = tmp_path / "merchant-journal.sqlite3"
    for module in (overridden, no_transfer_town_recovery, settled):
        monkeypatch.setattr(module, "state_path", lambda _: str(receiver))
    market_path = tmp_path / "merchant-service-visit.json"
    real_visit = service_visit.MarketVisit
    monkeypatch.setattr(
        service_visit, "MarketVisit", lambda: real_visit(market_path, clock=clock.time)
    )
    write_json(
        meteor_banking.POLICY,
        json.loads(Path("profiles/meteor-banking.json").read_text(encoding="utf-8")),
    )
    banking.CONFIG.write_text(
        json.dumps({"enabled": True, "withdraw_essentials": True}), encoding="utf-8"
    )
    loop.terrain = _terrain(1036)
    loop.town_visit = town_visit.TownVisit(
        tmp_path / "town-visit.json",
        profile=PROFILE,
        clock=clock.time,
        probe=lambda: {"available": True, "cursor": 1, "observed_at": clock.time()},
    )
    x = NS(fx=fx, clock=clock, game=game, loop=loop, tmp=tmp_path, bridge=[])
    x.market_path = market_path
    x.receiver = receiver
    x.status = {
        "input_owner": None,
        "handoff_granted": False,
        "handoff_requested": None,
        "characters": {},
    }

    def merchant(body):
        action = body["action"]
        x.bridge.append(action)
        if action == "status":
            return deepcopy(x.status)
        if action == "manual-status":
            return {
                "farmer": {
                    "session": None,
                    "input_fenced": False,
                    "observation": {
                        "available": True,
                        "windows_absent": True,
                        "observed_at": clock.time(),
                    },
                }
            }
        if action in ("delivery-status", "delivery-reconcile"):
            receipt = delivery_operation.status(
                Journal(delivery_operation.JOURNAL), body["request_id"]
            )
            if receipt and receipt["character"] == DUTCH:
                # The bridge process resolves the stored profile id to its name.
                receipt["character"] = "Dutch"
            return {
                "request_id": body["request_id"],
                "running": False,
                "receipt": receipt,
            }
        raise AssertionError(f"Unexpected merchant bridge action {body}")

    monkeypatch.setattr(bridge, "request", merchant)

    def write():
        seed(x.fx, tmp_path)
        write_json(loop.town_visit.path, x.fx["town_visit"])
        write_json(market_path, x.fx["market"])
        (loop.output / "events.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in x.fx["events"]), encoding="utf-8"
        )

    x.write = write
    write()
    return x


READ_ONLY = {("controls", False), ("town", "supplies"), ("town", "gear")}


def input_ops(ops):
    return [
        o for o in ops if (o["op"], o.get("enabled", o.get("action"))) not in READ_ONLY
    ]


def assert_refused_untouched(x, before_route):
    row = x.loop.town_visit.state()
    assert "pre_admission_restock_tail" not in row
    assert "town_work_completed_at" not in row
    assert input_ops(x.game.ops) == []
    assert read_json(meteor_banking.JOURNAL) == x.fx["meteor"]
    assert read_json(x.market_path) == x.fx["market"]
    assert read_json(delivery_route.STATE) == before_route
    assert [r for r in recovery._rows(banking.LEDGER)] == x.fx["money"]


def summarize(ops):
    return [
        o["op"]
        + (":" + o["action"] if "action" in o else "")
        + (":" + o["window"] if "window" in o else "")
        + (":" + str(o["uid"]) if "uid" in o else "")
        for o in ops
        if o.get("action") != "vendor-status"
    ]


# --------------------------------------------------------------------------
# FM13 end to end from the live journals through the real route controller.
# --------------------------------------------------------------------------


def test_e2e_live_override_stores_returns_banks_once_and_completes(rig):
    x = rig
    with pytest.raises(HuntResumed):
        x.loop._run_route()
    first = list(x.game.ops)
    # FM11/FM13: a later start replays nothing from the finished visit.
    with pytest.raises(HuntResumed):
        x.loop._run_route()
    second = x.game.ops[len(first) :]

    row = x.loop.town_visit.state()
    claim = row["pre_admission_restock_tail"]
    meteor = read_json(meteor_banking.JOURNAL)
    route = read_json(delivery_route.STATE)
    events = [e for e in field_fakes.events(x.loop) if e["time"] >= NOW]
    artifact = {
        "scenario": "live_operator_overridden_restock_delivery_continuation",
        "town_visit": {
            "id": row["town_visit_id"],
            "reasons": row["reasons"],
            "completed_kind": row.get("town_work_completed_kind"),
            "completed": bool(row.get("town_work_completed_at")),
            "zero_transaction_restart_retained": restart.MARKER in row,
        },
        "claim": {
            "capture_kind": claim["capture_kind"],
            "phase": claim["phase"],
            "delivery_proofs": claim["delivery_proofs"],
            "bag_uids": sorted(i["uid"] for i in claim["bag"]["items"]),
            "cash_receipt": claim.get("cash_receipt"),
        },
        "route": {
            "active": route["active"],
            "operation": next(
                {
                    k: o[k]
                    for k in (
                        "request_id",
                        "outcome",
                        "items",
                        "remaining",
                        "proof_digest",
                    )
                }
                for o in route["operations"]
                if o["request_id"] == KEY
            ),
            "receipt_for_key": [r for r in route["receipts"] if r["request_id"] == KEY],
        },
        "meteor": {
            k: meteor.get(k)
            for k in ("phase", "receipts", "scroll_uid", "scroll_resolution")
        },
        "market": read_json(x.market_path),
        "ledger_new": [r for r in recovery._rows(banking.LEDGER) if r["time"] >= NOW],
        "first_start": {
            "ops": summarize(first),
            "events": [e["event"] for e in events if e["event"] != "runback_progress"],
            "bridge": x.bridge,
        },
        "second_start_town_ops": [
            o
            for o in summarize(second)
            if o.startswith("town:") and "supplies" not in o
        ],
        "final": {
            "map_id": x.game.map_id,
            "silver": x.game.silver,
            "stored_silver": x.game.stored_silver,
            "bag_uids": sorted(i["uid"] for i in x.game.bag_items),
            "market_storage": x.game.stored[1036],
        },
    }
    path = x.tmp / "overridden-delivery-e2e.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    saved = json.loads(path.read_text(encoding="utf-8"))

    visit = saved["town_visit"]
    assert visit["id"] == VISIT and visit["reasons"] == ["restock"]
    assert visit["completed"] and visit["completed_kind"] == "restock"
    assert visit["zero_transaction_restart_retained"] is True  # FM12
    claim = saved["claim"]
    assert claim["capture_kind"] == "operator_delivery"
    assert claim["phase"] == "cash_verified"
    proof = claim["delivery_proofs"][0]
    assert proof["request_id"] == KEY and proof["uids"] == sorted(DELIVERED)
    assert proof["merchant_transaction"] == MKEY
    assert proof["confirmation_reference"] == (
        "f774f09f1b54476af3030a8646bfa1d962ea988b951e3f27ca8c6a7d6f24c9c4"
    )
    assert claim["bag_uids"] == sorted(
        i["uid"] for i in x.fx["meteor"]["after"]["items"] if i["uid"] not in DELIVERED
    )
    # The active route was consumed read-only; the override never became a
    # delivery receipt and was never resubmitted.
    route = saved["route"]
    assert route["active"] is None and route["receipt_for_key"] == []
    assert route["operation"]["outcome"] == "operator_overridden"
    assert route["operation"]["items"] == [] and route["operation"]["remaining"] == []
    assert saved["first_start"]["bridge"].count("delivery-reconcile") == 1
    assert "delivery-start" not in saved["first_start"]["bridge"]
    # Nothing valuable was left to store; the scroll is resolved only by the
    # proved Dutch receipt, never found in or deposited to the warehouse.
    meteor = saved["meteor"]
    assert meteor["phase"] == "completed" and meteor["receipts"] in (None, [])
    assert meteor["scroll_resolution"] == "merchant_verified_operator_override"
    assert saved["final"]["market_storage"] == []
    assert saved["market"]["phase"] == "departed"
    assert saved["market"]["attempts"] == []
    ops = saved["first_start"]["ops"]
    assert not any(o.startswith("town:warehouse-deposit") for o in ops)
    assert ops.count("town:service-select") == 1  # One free return, no fare.
    assert ops.count("town:warehouse-money-deposit") == 1
    assert ops.index("town:service-select") < ops.index("town:warehouse-money-deposit")
    assert ops[-1] == "controls"  # Farming resumes only after completion.
    ledger = saved["ledger_new"]
    assert len(ledger) == 1 and ledger[0]["direction"] == "deposit"
    assert claim["cash_receipt"]["amount"] == ledger[0]["amount"]
    assert (
        saved["final"]["silver"] + ledger[0]["amount"]
        == x.fx["meteor"]["after"]["silver"]
    )
    assert saved["final"]["map_id"] == 1011
    names = saved["first_start"]["events"]
    assert "failed" not in names
    assert names.index("meteor_loop_complete") < names.index("silver_deposit")
    assert names.index("silver_deposit") < names.index("restock_complete")
    assert "valuable_stored" not in names and "merchant_delivery_verified" not in names
    # The second start performs no town transaction at all.
    assert not any(
        o.split(":")[1]
        in (
            "open-bank",
            "warehouse-deposit",
            "warehouse-money-deposit",
            "warehouse-money-withdraw",
            "service-open",
            "service-select",
        )
        for o in saved["second_start_town_ops"]
    )


# FM11 -- a crash after capture continues without replaying anything.
def test_crash_after_capture_continues_once_without_replay(rig):
    x = rig
    x.game.fail_once["open-bank"] = OSError("worker connection lost")
    with pytest.raises(OSError):
        x.loop._run_route()
    row = x.loop.town_visit.state()
    assert row["pre_admission_restock_tail"]["phase"] == "captured"
    assert read_json(delivery_route.STATE)["active"] is None
    with pytest.raises(HuntResumed):
        x.loop._run_route()
    ops = summarize(x.game.ops)
    assert ops.count("town:service-select") == 1
    assert ops.count("town:warehouse-money-deposit") == 1
    assert x.bridge.count("delivery-reconcile") == 1
    assert x.loop.town_visit.state().get("town_work_completed_at")


# FM11 -- changed ownership after capture refuses re-entry before input.
def test_changed_bag_after_capture_refuses_reentry(rig):
    x = rig
    x.game.fail_once["open-bank"] = OSError("worker connection lost")
    with pytest.raises(OSError):
        x.loop._run_route()
    x.game.bag_items.append(
        {"uid": 1, "type_id": 1000020, "amount": 1, "limit": 1, "slot": 30, "plus": 0}
    )
    before = len(x.game.ops)
    with pytest.raises(ValueError):
        x.loop._run_route()
    assert input_ops(x.game.ops[before:]) == []


# FM11 -- an attempted cash tail never replays.
def test_uncertain_cash_tail_is_never_replayed(rig):
    x = rig
    x.game.fail_once["warehouse-money-deposit"] = OSError("lost")
    with pytest.raises(OSError):
        x.loop._run_route()
    claim = x.loop.town_visit.state()["pre_admission_restock_tail"]
    assert claim["phase"] == "cash_attempted"
    before = len(x.game.ops)
    with pytest.raises(ValueError, match="cash tail was attempted"):
        x.loop._run_route()
    assert "town:warehouse-money-deposit" not in summarize(x.game.ops[before:])


def _events_after(fx, event):
    fx["events"].insert(
        next(i for i, e in enumerate(fx["events"]) if e["time"] > failure_time(fx)),
        event,
    )


def _mutate(x, change):
    fx = x.fx
    failed = failure_time(fx)
    mtx = fx["merchant_journal"]["transactions"][0]

    def merchant_result(update):
        tx_json(fx, "merchant_journal", MKEY, "result_json", update)

    def merchant_before(update):
        tx_json(fx, "merchant_journal", MKEY, "before_json", update)

    def override(update):
        tx_json(
            fx,
            "farmer_journal",
            KEY,
            "result_json",
            lambda r: update(r["operator_override"]),
        )

    # FM1 merchant receipt
    if change == "merchant_receipt_missing":
        fx["merchant_journal"]["transactions"] = []
        fx["merchant_journal"]["transaction_steps"] = []
    if change == "merchant_receipt_uncertain":
        mtx["phase"] = "uncertain"
    if change == "merchant_receipt_other_merchant":
        mtx["id"] = MKEY.replace("Dutch", "Spiritual")
    if change == "merchant_receipt_other_process":
        merchant_before(lambda b: b["identity"].update(pid=1))
    if change == "merchant_receipt_other_partner":
        merchant_before(lambda b: b["trade"].update(participant_uid=5))
    if change == "merchant_receipt_extra_item":
        merchant_result(
            lambda r: r["items"].append({**r["items"][0], "uid": 296700000})
        )
    if change == "merchant_receipt_missing_item":
        merchant_result(lambda r: r["items"].pop())
    if change == "merchant_receipt_changed_plus":
        merchant_result(lambda r: r["items"][1].update(plus=2))
    if change == "merchant_receipt_changed_gem":
        merchant_result(lambda r: r["items"][0].update(gem1=255))
    if change == "merchant_receipt_changed_type":
        merchant_result(lambda r: r["items"][2].update(type_id=134905))
    if change == "merchant_gave_items":
        merchant_before(lambda b: b["trade"]["own_items"].append({"uid": 1}))
    if change == "merchant_gave_silver":
        merchant_result(lambda r: r.update(silver=100))
    if change == "merchant_receipt_before_farmer_transaction":
        mtx["created"] = 1790327160.0
    if change == "merchant_receipt_after_farmer_uncertain":
        mtx["updated"] = 1790327186.9
    if change == "merchant_receipt_after_failure":
        mtx["created"] = mtx["updated"] = failed + 5
    if change == "two_merchant_receipts":
        fx["merchant_journal"]["transactions"].append(
            {**mtx, "id": MKEY[:-4] + "ffff", "created": mtx["created"] + 0.1}
        )
    # FM2 override record
    if change == "override_not_confirmed":
        override(lambda o: o.update(operator_confirmed=False))
    if change == "override_without_reference":
        override(lambda o: o.update(confirmation_reference=""))
    if change == "override_without_operator":
        override(lambda o: o.update(operator=None))
    if change == "override_digest_forged":
        override(lambda o: o["original_evidence"].update(phase="submitted"))
    if change == "override_original_not_uncertain":
        override(lambda o: o.update(original_phase="submitted"))
    if change == "override_steps_differ":
        # A step the override digest never covered (appended after it).
        fx["farmer_journal"]["transaction_steps"].append(
            {
                "id": 501,
                "transaction_id": KEY,
                "stage": "receiver_receipt",
                "status": "observed",
                "payload": "{}",
                "timestamp": 1790327175.7,
            }
        )
    # FM3 receiver reservation
    if change == "reservation_missing":
        fx["merchant_journal"]["delivery_reservations"] = []
    if change == "reservation_not_overridden":
        row = fx["merchant_journal"]["delivery_reservations"][0]
        state = json.loads(row["state"])
        state["phase"] = "verified"
        row["state"] = json.dumps(state)
    if change == "reservation_other_reference":
        row = fx["merchant_journal"]["delivery_reservations"][0]
        state = json.loads(row["state"])
        state["operator_override"]["confirmation_reference"] = "someone else"
        row["state"] = json.dumps(state)
    # FM4 farmer still holds an overridden uid
    if change == "fresh_evidence_farmer_holds_scroll":
        override(
            lambda o: o["fresh_evidence"]["farmer"]["inventory"].append(
                {
                    "bound": False,
                    "category": None,
                    "gem1": 0,
                    "gem2": 0,
                    "name": "MeteorScroll",
                    "plus": 0,
                    "price": None,
                    "quantity": 1,
                    "slot": 9,
                    "type_id": 720027,
                    "uid": SCROLL,
                }
            )
        )
    if change == "bag_holds_overridden_uid":
        x.game.bag_items.append(
            next(i for i in fx["meteor"]["after"]["items"] if i["uid"] == 296759224)
        )
    # FM5 bag differs from meteor['after'] minus the overridden uids
    if change == "bag_extra_item":
        x.game.bag_items.append(
            {
                "uid": 1,
                "type_id": 1000020,
                "amount": 1,
                "limit": 1,
                "slot": 30,
                "plus": 0,
            }
        )
    if change == "bag_missing_item":
        x.game.bag_items.pop(0)
    if change == "bag_silver_changed":
        x.game.silver += 1
    # FM6 another delivery since required_at
    if change in ("other_pending_delivery", "other_terminal_admission"):
        other = deepcopy(fx["farmer_journal"]["transactions"][0])
        other.update(id="route-delivery:other", created=failed + 1, updated=failed + 2)
        admission = deepcopy(
            next(
                a
                for a in fx["farmer_journal"]["delivery_admissions"]
                if a["request_id"] == KEY
            )
        )
        admission.update(request_id="route-delivery:other", created=failed + 1)
        if change == "other_pending_delivery":
            other["phase"] = "uncertain"
            fx["farmer_journal"]["transactions"].append(other)
        else:
            admission["phase"] = "rejected"
        fx["farmer_journal"]["delivery_admissions"].append(admission)
    # FM7 process identity
    if change == "health_other_process":
        x.game_target = {**field_fakes.TARGET, "pid": 4242}
    if change == "restock_target_other_process":
        fx["town_visit"]["restock_target"]["creation_time_100ns"] += 1
    if change == "transaction_other_farmer_process":
        tx_json(
            fx,
            "farmer_journal",
            KEY,
            "before_json",
            lambda b: b["farmer"]["identity"].update(pid=4242),
        )
    # FM8 Market visit
    if change == "market_attempt_recorded":
        fx["market"]["attempts"] = [
            {
                "merchant": "Dutch",
                "position": [232, 205],
                "outcome": "transferred",
                "at": 1790327186.8,
            }
        ]
    if change == "market_other_visit":
        fx["market"]["visit_id"] = "0" * 32
    if change == "market_budget_open":
        fx["market"]["deadline"] = NOW + 60
    # FM9 failure and later events
    if change == "failure_other_detail":
        for e in fx["events"]:
            if e.get("time") == failed:
                e["detail"] = settled.FAILURE
    if change == "failure_without_settle":
        for e in fx["events"]:
            if e.get("time") == failed:
                e["failure_trace"] = [
                    f for f in e["failure_trace"] if f["function"] != "settle"
                ]
    if change == "gameplay_after_failure":
        _events_after(
            fx,
            {
                "time": failed + 30,
                "event": "valuable_stored",
                "town_visit_id": VISIT,
                "stored": 296757794,
            },
        )
    if change == "unknown_failure_after_failure":
        _events_after(
            fx,
            {
                "time": failed + 30,
                "event": "failed",
                "town_visit_id": VISIT,
                "detail": "x",
                "error_type": "builtins.ValueError",
                "failure_trace": [
                    {
                        "file": "C:\\r\\conquest\\banking.py",
                        "function": "transfer",
                        "line": 1,
                    }
                ],
            },
        )
    x.write()


REFUSALS = [
    "merchant_receipt_missing",
    "merchant_receipt_uncertain",
    "merchant_receipt_other_merchant",
    "merchant_receipt_other_process",
    "merchant_receipt_other_partner",
    "merchant_receipt_extra_item",
    "merchant_receipt_missing_item",
    "merchant_receipt_changed_plus",
    "merchant_receipt_changed_gem",
    "merchant_receipt_changed_type",
    "merchant_gave_items",
    "merchant_gave_silver",
    "merchant_receipt_before_farmer_transaction",
    "merchant_receipt_after_farmer_uncertain",
    "merchant_receipt_after_failure",
    "two_merchant_receipts",
    "override_not_confirmed",
    "override_without_reference",
    "override_without_operator",
    "override_digest_forged",
    "override_original_not_uncertain",
    "override_steps_differ",
    "reservation_missing",
    "reservation_not_overridden",
    "reservation_other_reference",
    "fresh_evidence_farmer_holds_scroll",
    "bag_holds_overridden_uid",
    "bag_extra_item",
    "bag_missing_item",
    "bag_silver_changed",
    "other_pending_delivery",
    "other_terminal_admission",
    "health_other_process",
    "restock_target_other_process",
    "transaction_other_farmer_process",
    "market_attempt_recorded",
    "market_other_visit",
    "market_budget_open",
    "failure_other_detail",
    "failure_without_settle",
    "gameplay_after_failure",
    "unknown_failure_after_failure",
]


# FM1-FM9: every missing or changed proof fails closed before any input.
@pytest.mark.parametrize("change", REFUSALS)
def test_missing_or_changed_evidence_refuses_before_any_input(rig, change, monkeypatch):
    x = rig
    x.game_target = None
    _mutate(x, change)
    if x.game_target:
        health = x.game.health

        def other():
            result = health()
            result["target"] = deepcopy(x.game_target)
            return result

        monkeypatch.setattr(x.game, "health", other)
    before_route = read_json(delivery_route.STATE)
    with pytest.raises(ValueError):
        x.loop._run_route()
    assert_refused_untouched(x, before_route)
    assert "delivery-reconcile" not in x.bridge


# FM9: the live 07:01:37 refusal and restart bookkeeping are tolerated, and a
# refusal by this proof on one start does not strand the next start.
def test_own_earlier_refusal_is_tolerated(rig):
    x = rig
    _events_after(
        x.fx,
        {
            "time": REFUSED_AT + 60,
            "event": "failed",
            "town_visit_id": VISIT,
            "detail": "Restock overridden delivery lacks a verified merchant receipt",
            "error_type": "builtins.ValueError",
            "failure_trace": [
                {
                    "file": "C:\\r\\conquest\\overnight.py",
                    "function": "_run_route",
                    "line": 1,
                },
                {
                    "file": "C:\\r\\conquest\\restock_town_recovery.py",
                    "function": "capture_pre_admission_tail",
                    "line": 1,
                },
                {
                    "file": "C:\\r\\conquest\\settled_delivery_town_recovery.py",
                    "function": "capture",
                    "line": 1,
                },
                {
                    "file": "C:\\r\\conquest\\overridden_delivery_town_recovery.py",
                    "function": "proof",
                    "line": 1,
                },
            ],
        },
    )
    x.fx["events"].sort(key=lambda e: e["time"])
    x.write()
    with pytest.raises(HuntResumed):
        x.loop._run_route()
    assert x.loop.town_visit.state().get("town_work_completed_at")


# FM10: an override that still shows the scroll on the farmer is not ours.
def test_override_with_scroll_still_carried_selects_operator_warehouse(rig):
    import conquest.overridden_delivery_town_recovery as overridden
    from conquest import no_transfer_town_recovery

    x = rig
    row = x.fx["town_visit"]
    assert overridden.requested(row) is True
    assert no_transfer_town_recovery.operator_warehouse_requested(row) is True
    _mutate(x, "fresh_evidence_farmer_holds_scroll")
    assert overridden.requested(row) is False
    assert no_transfer_town_recovery.operator_warehouse_requested(row) is True


# FM1: operator_overridden alone is never a delivery for Market storage.
def test_market_bank_never_treats_an_uncaptured_override_as_delivered(rig):
    import conquest.overridden_delivery_town_recovery as overridden

    x = rig
    state = read_json(meteor_banking.JOURNAL)
    assert overridden.delivered_scroll(x.loop, state, {"items": []}) is None
    row = x.loop.town_visit.state()
    row["pre_admission_restock_tail"] = {
        "capture_kind": "operator_warehouse",
        "phase": "captured",
        "delivery_proofs": [{"request_id": KEY, "uids": sorted(DELIVERED)}],
    }
    write_json(x.loop.town_visit.path, row)
    assert overridden.delivered_scroll(x.loop, state, {"items": []}) is None
