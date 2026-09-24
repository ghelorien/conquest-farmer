"""Source/receiver delivery protocol integration with fake memory clients only."""

import copy, threading, time
from types import SimpleNamespace as NS
import pytest

from conquest.merchants.delivery import DeliveryTransaction, ReconciliationBlocked
from conquest.merchants.delivery_peer import DeliveryPeer
from conquest.merchants.journal import Journal
from conquest.merchants import delivery_bridge, delivery_operation, sales


def item(uid):
    return {
        "uid": uid,
        "type_id": 720027,
        "name": "MeteorScroll",
        "plus": 0,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
        "slot": uid,
        "price": None,
        "category": None,
    }


def snapshot(name, uid, items=(), booth=()):
    return {
        "character": name,
        "character_uid": uid,
        "identity": {"pid": uid, "creation": 1},
        "server": "America",
        "timestamp": time.time(),
        "map_id": 1036,
        "hp": 100,
        "silver": 100,
        "capacity": 40,
        "inventory": copy.deepcopy(list(items)),
        "booth": copy.deepcopy(list(booth)),
        "booth_open": True,
        "trade": None,
        "request": None,
        "windows": [],
    }


class WorldDriver:
    def __init__(self, world, mode="delivered"):
        self.world, self.mode, self.operation = world, mode, None
        self.input = []

    def set_operation(self, operation):
        self.operation = operation

    def require_qualified(self, capability):
        if self.mode == "reject":
            raise ValueError("qualified preflight rejected")

    def read_pair(self, merchant):
        return copy.deepcopy(self.world["farmer"]), copy.deepcopy(
            self.world["merchant"]
        )

    def open_trade(self, intent):
        self.operation.before_action("trade_target_mode")
        self.input.append("target")
        self.operation.action_observed("trade_target_mode")
        self.operation.before_action("trade_request")
        self.input.append("request")
        for role, other in (("farmer", "merchant"), ("merchant", "farmer")):
            mine = self.world[role]
            peer = self.world[other]
            mine["trade"] = {
                "participant": peer["character"],
                "participant_uid": peer["character_uid"],
                "own_items": [],
                "items": [],
                "own_silver": 0,
                "other_silver": 0,
                "accepted": False,
                "other_accepted": False,
            }
        self.operation.action_observed("trade_request")

    def place_item(self, intent, selected):
        self.operation.before_action("offer_item:" + str(selected["uid"]))
        self.input.append("item")
        self.world["farmer"]["trade"]["own_items"].append(copy.deepcopy(selected))
        self.world["merchant"]["trade"]["items"].append(copy.deepcopy(selected))
        self.operation.action_observed("offer_item:" + str(selected["uid"]))

    def confirm(self, intent):
        self.operation.before_action("farmer_confirm")
        self.input.append("confirm")

    def wait_pair(self, merchant):
        selected = self.world["farmer"]["trade"]["own_items"]
        moved = (
            selected
            if self.mode == "delivered"
            else selected[:1]
            if self.mode == "partial"
            else []
        )
        ids = {i["uid"] for i in moved}
        self.world["farmer"]["inventory"] = [
            i for i in self.world["farmer"]["inventory"] if i["uid"] not in ids
        ]
        self.world["merchant"]["inventory"] += copy.deepcopy(moved)
        self.world["farmer"]["trade"] = self.world["merchant"]["trade"] = None
        for state in self.world.values():
            state["timestamp"] = time.time()
        return self.read_pair(merchant)


def rig(tmp_path, monkeypatch, items):
    world = {"farmer": snapshot("Parasite", 1, items), "merchant": snapshot("Dutch", 2)}
    source = Journal(tmp_path / "source.sqlite3")
    receiver = Journal(tmp_path / "receiver.sqlite3")
    monkeypatch.setattr(delivery_operation, "JOURNAL", source.path)
    monkeypatch.setattr(
        delivery_bridge,
        "pair",
        lambda ui, character: (
            copy.deepcopy(world["farmer"]),
            copy.deepcopy(world["merchant"]),
        ),
    )
    qualified = NS(require_qualified=lambda capability: None)
    ui = NS(
        coordinator=NS(lock=threading.RLock(), owner=None, stopped=False),
        app=NS(control=NS(snapshot=lambda: {"enabled": False})),
        runtime=NS(
            enabled=lambda character: True,
            controllers={"Dutch": NS(driver=qualified)},
            journal=receiver,
            lock=threading.RLock(),
        ),
    )
    peer = DeliveryPeer(lambda body: delivery_bridge.dispatch(ui, body))
    return world, source, receiver, peer


def test_preinput_rejection_creates_no_durable_intent(tmp_path, monkeypatch):
    world, source, receiver, peer = rig(tmp_path, monkeypatch, [item(10)])
    driver = WorldDriver(world, "reject")
    with pytest.raises(ValueError, match="preflight rejected"):
        DeliveryTransaction(source, driver, peer).run(
            "Dutch", world["farmer"]["inventory"], request_id="pre"
        )
    assert (
        not source.pending("Dutch")
        and receiver.get("Dutch", "delivery_reservation") is None
    )
    assert driver.input == []


def test_lost_no_transfer_disposition_ack_restarts_read_only(tmp_path, monkeypatch):
    world, source, receiver, real_peer = rig(tmp_path, monkeypatch, [item(10)])
    driver = WorldDriver(world, "none")

    class LostPeer:
        lost = True

        def __getattr__(self, name):
            original = getattr(real_peer, name)

            def call(*args):
                result = original(*args)
                if name == "disposition" and self.lost:
                    self.lost = False
                    raise OSError("lost disposition acknowledgement")
                return result

            return call

    clock = [time.time()]
    mono = [0.0]

    def sleep(seconds):
        clock[0] += seconds
        mono[0] += seconds

    tx = DeliveryTransaction(
        source,
        driver,
        LostPeer(),
        clock=lambda: clock[0],
        monotonic=lambda: mono[0],
        sleep=sleep,
    )
    driver.open_trade = lambda intent: (_ for _ in ()).throw(
        OSError("request rejected before input")
    )
    with pytest.raises(OSError, match="lost disposition"):
        tx.run("Dutch", world["farmer"]["inventory"], request_id="lost")
    assert source.pending("Dutch")[0]["phase"] == "uncertain"
    assert (
        receiver.get("Dutch", "delivery_reservation")["phase"]
        == "no_transfer_reconciled"
    )
    calls = list(driver.input)
    clock[0] += 4
    mono[0] += 4
    DeliveryTransaction(
        Journal(source.path),
        driver,
        real_peer,
        clock=lambda: clock[0],
        monotonic=lambda: mono[0],
        sleep=sleep,
    ).recover("lost")
    assert driver.input == calls and not Journal(source.path).pending("Dutch")
    assert (
        delivery_operation.status(Journal(source.path), "lost")["outcome"]
        == "no_transfer"
    )


def test_lost_finish_ack_restart_only_finalizes_receiver_receipt(tmp_path, monkeypatch):
    world, source, receiver, real_peer = rig(tmp_path, monkeypatch, [item(10)])
    driver = WorldDriver(world)

    class LostFinishPeer:
        lost = True

        def __getattr__(self, name):
            original = getattr(real_peer, name)

            def call(*args):
                result = original(*args)
                if name == "finish" and self.lost:
                    self.lost = False
                    raise OSError("lost finish acknowledgement")
                return result

            return call

    with pytest.raises(OSError, match="lost finish"):
        DeliveryTransaction(source, driver, LostFinishPeer()).run(
            "Dutch", world["farmer"]["inventory"], request_id="finish"
        )
    assert (
        delivery_operation.status(source, "finish")["next_action"]
        == "finalize_receiver_receipt"
    )
    assert receiver.get("Dutch", "delivery_reservation")["phase"] == "verified"
    calls = list(driver.input)
    DeliveryTransaction(Journal(source.path), driver, real_peer).recover("finish")
    assert driver.input == calls
    assert (
        delivery_operation.status(Journal(source.path), "finish")["next_action"]
        == "release_route"
    )


def test_journal_failure_before_action_trace_never_reaches_bridge_or_input(
    tmp_path, monkeypatch
):
    world, source, receiver, peer = rig(tmp_path, monkeypatch, [item(10)])
    driver = WorldDriver(world)
    original_step = source.step
    failed = [False]

    def fault(key, stage, status, *args, **kwargs):
        if stage == "action_trace" and not failed[0]:
            failed[0] = True
            raise OSError("journal unavailable")
        return original_step(key, stage, status, *args, **kwargs)

    monkeypatch.setattr(source, "step", fault)
    with pytest.raises(OSError, match="journal unavailable"):
        DeliveryTransaction(source, driver, peer).run(
            "Dutch", world["farmer"]["inventory"], request_id="journal"
        )
    assert source.pending("Dutch")[0]["phase"] == "prepared"
    assert receiver.get("Dutch", "delivery_reservation") is None
    assert driver.input == []
    with pytest.raises(ReconciliationBlocked, match="no new input is authorized"):
        DeliveryTransaction(Journal(source.path), driver, peer).recover("journal")
    assert driver.input == []


def test_partial_confirm_restart_and_exact_verified_unrelated_sale(
    tmp_path, monkeypatch
):
    rows = [item(10), item(11)]
    world, source, receiver, peer = rig(tmp_path, monkeypatch, rows)
    driver = WorldDriver(world, "partial")
    clock = [time.time()]
    tx = DeliveryTransaction(
        source,
        driver,
        peer,
        clock=lambda: clock[0],
        monotonic=lambda: clock[0],
        sleep=lambda s: None,
    )
    with pytest.raises(ReconciliationBlocked, match="stable terminal settlement"):
        tx.run("Dutch", rows, request_id="partial")
    assert (
        source.pending("Dutch")[0]["phase"] == "uncertain"
        and driver.input.count("confirm") == 1
    )
    clock[0] += 4
    with pytest.raises(ReconciliationBlocked, match="no new input is authorized"):
        DeliveryTransaction(
            Journal(source.path),
            driver,
            peer,
            clock=lambda: clock[0],
            monotonic=lambda: clock[0],
            sleep=lambda s: None,
        ).recover("partial")
    assert (
        driver.input.count("confirm") == 1
        and source.pending("Dutch")[0]["phase"] == "uncertain"
    )
    # A full-attribute receipt inside this operation's observation interval can
    # account for unrelated booth stock and silver changes on both peers.
    world2, source2, receiver2, peer2 = rig(tmp_path / "sale", monkeypatch, [item(20)])
    listing = item(99)
    listing.update(name="Hat", price=100)
    world2["merchant"]["booth"] = [listing]
    sales.observe(receiver2, copy.deepcopy(world2["merchant"]))
    intent_driver = WorldDriver(world2)
    intent = __import__("conquest.merchants.delivery", fromlist=["prepare"]).prepare(
        world2["farmer"], world2["merchant"], world2["farmer"]["inventory"]
    )
    source2.begin("sale", "Dutch", "farmer_delivery", intent)
    source2.step("sale", "action_trace", "initialized", {"version": 1})
    peer2.reserve("sale", intent)
    changed = copy.deepcopy(world2["merchant"])
    changed["timestamp"] += 0.001
    changed["booth"] = []
    changed["silver"] += 97
    sales.observe(receiver2, copy.deepcopy(changed))
    assert any(event["event"] == "sale_verified" for event in receiver2.events())
    world2["merchant"] = changed
    receipt_source = lambda intent, farmer, merchant: sales.qualified_delivery_receipts(
        receiver2, intent, merchant
    )
    DeliveryTransaction(
        source2, intent_driver, peer2, sale_receipts=receipt_source
    ).recover("sale")
    result = delivery_operation.status(source2, "sale")
    assert (
        result["outcome"] == "no_transfer" and result["next_action"] == "retry_delivery"
    )
