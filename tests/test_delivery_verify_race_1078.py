"""A merchant delivery must verify even when the farmer reconciles first.

Live 2026-09-24 22:19:50-52 (pre-existing since r38): Parasite delivered a
FireSword (uid 296697994) to Dutch. After Dutch pressed accept and journalled
"submitted", MerchantController.accept_delivery polled memory through
driver.wait_for(..., self.check). Between two 0.15 s polls (187 ms after the
submit) the farmer side (delivery_bridge -> delivery_reservation.finish)
marked the reservation verified. The next poll's check() asked the 1078 input
policy (runtime.trade1078_input_allowed), which requires an active
reservation, so it raised CaptureUnavailable and the merchant transaction was
left "uncertain" although the item was in Dutch's inventory and the trade was
closed. Nothing settled it: step_trade_1078 returns before reconcile when no
reservation is active, and the uncertain row blocked new deliveries, refill,
listing handoffs, sales observation and farmer attach until an operator
Recheck.

This module drives the real MerchantRuntime, InputCoordinator (with the
production 1078 surface block and native trade policy), delivery reservation,
Journal, MerchantController and MerchantDriver.accept_request/accept_trade/
wait_for against a scripted two-client Market. The only fake is memory and the
native click, which records input after the same coordinator/identity checks.
The race scenario writes a repeatable JSON trace artifact
(delivery_verify_race_trace.json under the test's tmp dir) and asserts on it.

Failure modes (written before the fix, per AGENTS.md):

1. Race: the farmer reconciles (reservation -> verified) between two merchant
   verification polls after the item arrived. The merchant must still verify
   its transaction from memory; it must not go uncertain because input
   authorisation (active reservation) disappeared after input finished. The
   same scenario run twice produces an identical trace artifact.
2. Manual Stop (Global Stop / app close) or a merchant Pause during
   verification aborts to "uncertain" at the next poll, even if the item
   arrives afterwards (no verification after Stop), and no input is sent.
   The auto-settle below stays off while stopped and settles after Resume.
3. Changed process identity during verification (process restarted; memory
   now shows the item) aborts to "uncertain"; another process's memory never
   verifies this transaction.
4. The item never arrives within the unchanged 10 s deadline -> "uncertain".
5. An uncertain delivery whose fresh merchant memory shows exact evidence
   (inventory == before + delivered items, silver unchanged, trade closed)
   settles to "verified" from step_trade_1078 on its own after the farmer's
   reservation is finished, with no input and no input lease.
6. An uncertain delivery whose evidence differs stays uncertain and
   step_trade_1078 does not raise: extra item, missing item, changed item
   attributes, silver changed, trade still open. (Exact evidence is the
   positive control of the same parametrization.)
7. Auto-reconcile never runs for non-delivery pending kinds (uncertain
   listing) or for a delivery that is not uncertain (prepared/submitted are
   in flight and belong to their worker).
8. Pre-submit checks are unchanged: without the reserved delivery window the
   merchant sends no accept input and journals no transaction.
"""

import copy
import json
import threading
import time
from types import SimpleNamespace

import pytest

from conquest.capture import CaptureUnavailable
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants import delivery_reservation
from conquest.merchants import driver as driver_module
from conquest.merchants.controller import MerchantController
from conquest.merchants.coordination import InputCoordinator
from conquest.merchants.driver import MerchantDriver
from conquest.merchants.journal import Journal
from conquest.merchants.runtime import MerchantRuntime

MERCHANT = "Dutch"
REQUEST_ID = "delivery-race-20260924"
MERCHANT_UID = 1000888
FARMER_UID = 1000777
SWORD_UID = 296697994
MERCHANT_IDENTITY = {
    "pid": 18532,
    "creation_time_100ns": 134345064188672222,
    "path": "C:/Co/ImConquer.exe",
}
RESTARTED_IDENTITY = dict(MERCHANT_IDENTITY, pid=19044, creation_time_100ns=1)
FARMER_IDENTITY = {
    "pid": 20100,
    "creation_time_100ns": 134345064188670000,
    "path": "C:/Co2/ImConquer.exe",
}
ARTIFACT = "delivery_verify_race_trace.json"


def item(uid, *, type_id=410339, name="FireSword", plus=3, slot=None, price=None):
    return {
        "uid": uid,
        "type_id": type_id,
        "name": name,
        "plus": plus,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
        "slot": slot,
        "price": price,
    }


class World:
    """Scripted Market memory for Dutch and Parasite; input is only recorded."""

    def __init__(self):
        self.trace = []
        self.clicks = []
        self.clock = 0.0
        self.submitted = False
        self.gaps = 0
        self.hooks = {}
        self.identity_ok = True
        self.sword = item(SWORD_UID, slot=4)
        self.merchant = {
            "character": MERCHANT,
            "character_uid": MERCHANT_UID,
            "identity": dict(MERCHANT_IDENTITY),
            "server": "America",
            "inventory": [item(9001, type_id=130805, name="Coat", plus=2)],
            "booth": [item(9002, type_id=130805, name="Coat", plus=4, price=250000)],
            "capacity": 40,
            "silver": 5000,
            "map_id": 1036,
            "position": [180, 190],
            "hp": 500,
            "booth_open": True,
            "trade": None,
            "request": None,
            "windows": [],
        }
        self.farmer = {
            "character": "Parasite",
            "character_uid": FARMER_UID,
            "identity": dict(FARMER_IDENTITY),
            "server": "America",
            "inventory": [
                item(8001, type_id=1000000, name="Stancher", plus=0, slot=0),
                copy.deepcopy(self.sword),
            ],
            "booth": [],
            "capacity": 40,
            "silver": 700,
            "map_id": 1036,
            "position": [182, 191],
            "hp": 900,
            "trade": None,
            "request": None,
        }

    def log(self, actor, event, **details):
        self.trace.append(
            {"n": len(self.trace), "actor": actor, "event": event, **details}
        )

    # Memory ---------------------------------------------------------------
    def merchant_snapshot(self):
        snapshot = copy.deepcopy(self.merchant)
        snapshot["timestamp"] = time.time()
        return snapshot

    def farmer_snapshot(self):
        snapshot = copy.deepcopy(self.farmer)
        snapshot["timestamp"] = time.time()
        return snapshot

    def read_merchant(self):
        snapshot = self.merchant_snapshot()
        trade = snapshot["trade"]
        self.log(
            "merchant",
            "read",
            trade_open=bool(trade),
            accepted=bool(trade and trade["accepted"]),
            inventory=sorted(i["uid"] for i in snapshot["inventory"]),
            silver=snapshot["silver"],
            pid=snapshot["identity"]["pid"],
        )
        return snapshot

    def assert_identity(self):
        if not self.identity_ok:
            raise ValueError(
                "Process exited or restarted; calibration addresses are invalid"
            )

    # Input and game events -----------------------------------------------
    def click(self, control):
        self.clicks.append(control)
        self.log("merchant", "click", control=control)
        if control == "accept_request":
            request = self.merchant["request"]
            assert request and request["participant"] == "Parasite"
            self.merchant["request"] = None
            self.merchant["trade"] = self._trade("Parasite", FARMER_UID)
            self.farmer["trade"] = self._trade(MERCHANT, MERCHANT_UID)
        elif control == "accept_trade":
            self.merchant["trade"]["accepted"] = True
            self.submitted = True
        else:  # pragma: no cover - the scenario has no other controls
            raise AssertionError(control)

    @staticmethod
    def _trade(participant, uid):
        return {
            "participant": participant,
            "participant_uid": uid,
            "own_items": [],
            "items": [],
            "own_silver": 0,
            "other_silver": 0,
            "accepted": False,
            "other_accepted": False,
        }

    def farmer_request(self):
        self.merchant["request"] = {
            "participant": "Parasite",
            "participant_uid": FARMER_UID,
        }
        self.log("farmer", "trade_request")

    def farmer_offer(self):
        self.farmer["inventory"] = [
            i for i in self.farmer["inventory"] if i["uid"] != SWORD_UID
        ]
        self.farmer["trade"]["own_items"] = [copy.deepcopy(self.sword)]
        self.farmer["trade"]["accepted"] = True
        self.merchant["trade"]["items"] = [copy.deepcopy(self.sword)]
        self.merchant["trade"]["other_accepted"] = True
        self.log("farmer", "offer_confirmed", uids=[SWORD_UID])

    def arrive(self):
        """The server completes the trade: the sword is now Dutch's."""
        self.merchant["trade"] = None
        self.farmer["trade"] = None
        self.merchant["inventory"].append(dict(copy.deepcopy(self.sword), slot=1))
        self.log("world", "item_arrived", uid=SWORD_UID)

    # Driver clock ---------------------------------------------------------
    def monotonic(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds
        if self.submitted:
            self.gaps += 1
            self.log("merchant", "poll_gap", gap=self.gaps)
            for hook in self.hooks.pop(self.gaps, ()):
                hook()


class ScriptedDriver(MerchantDriver):
    """Real MerchantDriver trade methods; memory and the native click are scripted."""

    def __init__(self, world, observer, coordinator):
        self.world, self.observer, self.coordinator = world, observer, coordinator
        self.trade1078 = True
        self.memory = SimpleNamespace(read=world.read_merchant)
        self.target = observer.operations.target

    def require_qualified(self, capability):
        if capability not in ("trade", "trade_request"):
            raise ValueError(f"{capability}: not qualified in this scenario")
        return {}

    def click(
        self, snapshot, control, slot=None, *, validate=None, before_mouse_down=None
    ):
        # The same authorisation boundary as MerchantDriver._click.
        self.coordinator.check()
        self.observer.adapter.assert_identity()
        if self.observer.adapter.identity != snapshot["identity"]:
            raise ValueError("Client identity changed before input")
        self.world.click(control)


@pytest.fixture
def make_rig(tmp_path, monkeypatch):
    monkeypatch.delenv("CONQUEST_DATA_ROOT", raising=False)
    monkeypatch.delenv("CONQUEST_PROFILE_ID", raising=False)
    from conquest.merchants import delivery_probe

    monkeypatch.setattr(
        delivery_probe, "JOURNAL", tmp_path / "delivery-request-probe.json"
    )
    rigs = []

    def make(name="rig"):
        root = tmp_path / name
        root.mkdir()
        world = World()
        journal = Journal(root / "journal.sqlite3")
        coordinator = InputCoordinator(lambda: True, path=root / "input.lock")
        catalog = SimpleNamespace(identities=lambda: [], windows=lambda **_: [])
        runtime = MerchantRuntime(catalog, coordinator, journal=journal)
        observer = SimpleNamespace(
            adapter=SimpleNamespace(
                identity=dict(MERCHANT_IDENTITY),
                expected_sha256=CLIENT_SHA256_1078,
                assert_identity=world.assert_identity,
            ),
            character=MERCHANT,
            hwnd=77,
            lock=threading.RLock(),
            merchant_observation_only=True,
            character_context=None,
            operations=SimpleNamespace(target=SimpleNamespace(hwnd=77)),
            close=lambda: None,
        )
        driver = ScriptedDriver(world, observer, coordinator)
        controller = MerchantController(MERCHANT, journal, driver, coordinator)
        runtime.observers[MERCHANT] = observer
        runtime.controllers[MERCHANT] = controller
        runtime.native1078_farmer_check = lambda: {"farmer": "parked"}
        # Production: a 1078 merchant surface is blocked; only the exact
        # reserved-trade policy authorizes its native input.
        coordinator.surface_blocks[MERCHANT] = True
        coordinator.native_trade1078_policy = runtime.trade1078_input_allowed
        journal.set(MERCHANT, "enabled", True)
        rig = SimpleNamespace(
            root=root,
            world=world,
            journal=journal,
            coordinator=coordinator,
            runtime=runtime,
            controller=controller,
            observer=observer,
        )
        rigs.append(rig)
        return rig

    fake_time = SimpleNamespace(
        monotonic=lambda: rigs[-1].world.monotonic(),
        sleep=lambda seconds: rigs[-1].world.sleep(seconds),
        time=time.time,
    )
    monkeypatch.setattr(driver_module, "time", fake_time)
    return make


def farmer_finish(rig):
    state = delivery_reservation.finish(
        rig.journal,
        MERCHANT,
        REQUEST_ID,
        rig.world.farmer_snapshot(),
        rig.world.merchant_snapshot(),
    )
    rig.world.log("farmer", "reservation_finished", phase=state["phase"])
    return state


def open_reserved_trade(rig):
    """Farmer reserves Dutch, requests, Dutch accepts, farmer offers + confirms."""
    world = rig.world
    delivery_reservation.reserve(
        rig.journal,
        REQUEST_ID,
        world.farmer_snapshot(),
        world.merchant_snapshot(),
        [copy.deepcopy(world.sword)],
    )
    rig.runtime.delivery_window = REQUEST_ID
    world.farmer_request()
    rig.controller.accept_request(world.merchant_snapshot())
    world.farmer_offer()
    delivery_reservation.ready(
        rig.journal,
        MERCHANT,
        REQUEST_ID,
        world.farmer_snapshot(),
        world.merchant_snapshot(),
    )


def transactions(journal):
    with journal.db() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,kind,phase,before_json FROM transactions ORDER BY created"
            )
        ]


def phases(journal):
    return [(row["kind"], row["phase"]) for row in transactions(journal)]


def clicks_after_submit(world):
    return world.clicks[world.clicks.index("accept_trade") + 1 :]


def run_race(rig):
    """Item arrives and the farmer reconciles between merchant polls 2 and 3."""
    rig.world.hooks = {2: [rig.world.arrive, lambda: farmer_finish(rig)]}
    open_reserved_trade(rig)
    error = None
    try:
        rig.controller.accept_delivery()
    except Exception as caught:  # recorded in the artifact, asserted below
        error = f"{type(caught).__name__}: {caught}"
    (row,) = transactions(rig.journal)
    artifact = {
        "scenario": "farmer_reconciles_between_merchant_verification_polls",
        "live_incident": "2026-09-24 22:19:50 Parasite -> Dutch FireSword 296697994",
        "error": error,
        "clicks": rig.world.clicks,
        "trace": rig.world.trace,
        "merchant_transaction": {
            "kind": row["kind"],
            "phase": row["phase"],
            "steps": [s["status"] for s in rig.journal.trace(row["id"])],
        },
        "reservation_phase": rig.journal.get(MERCHANT, delivery_reservation.KEY)[
            "phase"
        ],
        "pending": [r["phase"] for r in rig.journal.pending(MERCHANT)],
    }
    path = rig.root / ARTIFACT
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


# 1 -------------------------------------------------------------------------


def test_farmer_reconciling_between_polls_still_verifies_the_merchant_delivery(
    make_rig,
):
    first = run_race(make_rig("first"))
    second = run_race(make_rig("second"))
    artifact = json.loads(first.read_text(encoding="utf-8"))
    # Repeatable: an identical second run writes an identical artifact.
    assert artifact == json.loads(second.read_text(encoding="utf-8"))
    trace = artifact["trace"]
    finished = next(e["n"] for e in trace if e["event"] == "reservation_finished")
    arrived = next(e["n"] for e in trace if e["event"] == "item_arrived")
    verifying_read = next(
        e["n"]
        for e in trace
        if e["event"] == "read" and not e["trade_open"] and SWORD_UID in e["inventory"]
    )
    # The live ordering: farmer settles first, between two merchant polls.
    assert arrived < finished < verifying_read
    assert [e["gap"] for e in trace if e["event"] == "poll_gap"] == [1, 2]
    assert artifact["reservation_phase"] == "verified"
    assert artifact["error"] is None
    assert artifact["merchant_transaction"] == {
        "kind": "delivery",
        "phase": "verified",
        "steps": ["prepared", "submitted", "verified"],
    }
    assert artifact["pending"] == []
    # Exactly one accept of the request and one of the trade; nothing after.
    assert artifact["clicks"] == ["accept_request", "accept_trade"]
    assert not [e for e in trace[verifying_read:] if e["event"] == "click"]


# 2 -------------------------------------------------------------------------


@pytest.mark.parametrize("stop", ["global_stop", "merchant_pause"])
def test_stop_during_verification_is_uncertain_and_settles_only_after_resume(
    make_rig, stop
):
    rig = make_rig()

    def halt():
        if stop == "global_stop":
            rig.coordinator.stop()
        else:
            rig.journal.set(MERCHANT, "enabled", False)
        rig.world.log("operator", stop)

    # Stop lands between poll 1 and 2; the item would arrive only afterwards.
    rig.world.hooks = {1: [halt], 2: [rig.world.arrive]}
    open_reserved_trade(rig)
    with pytest.raises(CaptureUnavailable):
        rig.controller.accept_delivery()
    assert phases(rig.journal) == [("delivery", "uncertain")]
    assert rig.world.gaps == 1  # aborted at the next poll, not after a deadline
    assert clicks_after_submit(rig.world) == []
    # The item arrives and the farmer settles; while stopped nothing changes.
    rig.world.arrive()
    farmer_finish(rig)
    rig.runtime.step_trade_1078(MERCHANT, rig.world.merchant_snapshot())
    assert phases(rig.journal) == [("delivery", "uncertain")]
    if stop == "global_stop":
        rig.coordinator.resume()
    else:
        rig.journal.set(MERCHANT, "enabled", True)
    rig.runtime.step_trade_1078(MERCHANT, rig.world.merchant_snapshot())
    assert phases(rig.journal) == [("delivery", "verified")]
    assert rig.world.clicks == ["accept_request", "accept_trade"]


# 3 -------------------------------------------------------------------------


def test_changed_process_identity_during_verification_is_uncertain(make_rig):
    rig = make_rig()

    def restart():
        rig.world.identity_ok = False
        rig.world.merchant["identity"] = dict(RESTARTED_IDENTITY)
        rig.world.arrive()  # Memory of another process shows the item.
        rig.world.log("world", "process_restarted")

    rig.world.hooks = {1: [restart]}
    open_reserved_trade(rig)
    with pytest.raises(ValueError):
        rig.controller.accept_delivery()
    assert phases(rig.journal) == [("delivery", "uncertain")]
    assert rig.world.gaps == 1
    assert clicks_after_submit(rig.world) == []


# 4 -------------------------------------------------------------------------


def test_item_never_arriving_is_uncertain_after_the_unchanged_deadline(make_rig):
    rig = make_rig()
    open_reserved_trade(rig)
    with pytest.raises(ValueError, match="not verified"):
        rig.controller.accept_delivery()
    assert phases(rig.journal) == [("delivery", "uncertain")]
    assert rig.world.clock >= 10 and 60 <= rig.world.gaps <= 70
    assert clicks_after_submit(rig.world) == []


# 5 and 6 -------------------------------------------------------------------


def uncertain_after_deadline(rig):
    open_reserved_trade(rig)
    with pytest.raises(ValueError, match="not verified"):
        rig.controller.accept_delivery()
    assert phases(rig.journal) == [("delivery", "uncertain")]


def forbid_input(rig, monkeypatch):
    def lease(*args, **kwargs):
        raise AssertionError("Read-only reconciliation must not take an input lease")

    monkeypatch.setattr(rig.coordinator, "lease", lease)


def test_uncertain_delivery_with_exact_evidence_settles_without_input(
    make_rig, monkeypatch
):
    rig = make_rig()
    uncertain_after_deadline(rig)
    # The item arrives late; the farmer side reconciles its reservation.
    rig.world.arrive()
    assert farmer_finish(rig)["phase"] == "verified"
    assert delivery_reservation.active(rig.journal, MERCHANT) is None
    forbid_input(rig, monkeypatch)
    clicks = list(rig.world.clicks)
    rig.runtime.step_trade_1078(MERCHANT, rig.world.merchant_snapshot())
    assert phases(rig.journal) == [("delivery", "verified")]
    assert rig.journal.pending(MERCHANT) == []
    assert rig.world.clicks == clicks
    (row,) = transactions(rig.journal)
    assert [s["status"] for s in rig.journal.trace(row["id"])] == [
        "prepared",
        "submitted",
        "uncertain",
        "verified",
    ]


def differ(world, variant):
    if variant in ("exact", "extra_item", "silver_changed", "attributes_changed"):
        world.arrive()
    if variant == "extra_item":
        world.merchant["inventory"].append(
            item(9100, type_id=130805, name="Coat", plus=1)
        )
    elif variant == "silver_changed":
        world.merchant["silver"] += 1
    elif variant == "attributes_changed":
        world.merchant["inventory"][-1]["plus"] = 4
    elif variant == "missing_item":
        # Trade closed without the sword: it went back to the farmer.
        world.merchant["trade"] = None
        world.farmer["trade"] = None
    elif variant == "trade_still_open":
        pass  # Accepted trade window remains open with the offer inside.


@pytest.mark.parametrize(
    "variant",
    [
        "exact",
        "extra_item",
        "missing_item",
        "attributes_changed",
        "silver_changed",
        "trade_still_open",
    ],
)
def test_only_exact_evidence_settles_an_uncertain_delivery(
    make_rig, monkeypatch, variant
):
    rig = make_rig()
    uncertain_after_deadline(rig)
    differ(rig.world, variant)
    # The farmer's delivery window has closed (its route stopped), so no
    # reservation-bound trade work runs for Dutch.
    rig.runtime.delivery_window = None
    forbid_input(rig, monkeypatch)
    clicks = list(rig.world.clicks)
    for _ in range(2):
        rig.runtime.step_trade_1078(MERCHANT, rig.world.merchant_snapshot())
    expected = "verified" if variant == "exact" else "uncertain"
    assert phases(rig.journal) == [("delivery", expected)]
    assert rig.world.clicks == clicks


# 7 -------------------------------------------------------------------------


def seed(rig, kind, phase):
    before = rig.world.merchant_snapshot()
    if kind == "delivery":
        before["trade"] = dict(
            World._trade("Parasite", FARMER_UID),
            items=[copy.deepcopy(rig.world.sword)],
            accepted=True,
        )
        record = before
    else:
        record = {
            "uid": 9001,
            "price": 1000,
            "item": before["inventory"][0],
            "snapshot": before,
        }
    rig.journal.begin(f"{kind}:{MERCHANT}:seed", MERCHANT, kind, record)
    for step in {
        "prepared": [],
        "submitted": ["submitted"],
        "uncertain": ["uncertain"],
    }[phase]:
        rig.journal.transition(f"{kind}:{MERCHANT}:seed", step)


@pytest.mark.parametrize(
    "kind,phase",
    [("listing", "uncertain"), ("delivery", "prepared"), ("delivery", "submitted")],
)
def test_auto_reconcile_never_runs_for_other_pending_work(
    make_rig, monkeypatch, kind, phase
):
    rig = make_rig()
    seed(rig, kind, phase)
    rig.world.arrive()  # Memory would even satisfy a delivery receipt.
    calls = []
    monkeypatch.setattr(rig.controller, "reconcile", lambda s: calls.append(s))
    rig.runtime.step_trade_1078(MERCHANT, rig.world.merchant_snapshot())
    assert calls == []
    assert phases(rig.journal) == [(kind, phase)]


def test_uncertain_delivery_seed_is_the_positive_control(make_rig):
    rig = make_rig()
    seed(rig, "delivery", "uncertain")
    rig.world.arrive()
    rig.runtime.step_trade_1078(MERCHANT, rig.world.merchant_snapshot())
    assert phases(rig.journal) == [("delivery", "verified")]
    assert rig.world.clicks == []


# 8 -------------------------------------------------------------------------


def test_accept_still_requires_the_reserved_delivery_window(make_rig):
    rig = make_rig()
    open_reserved_trade(rig)
    rig.runtime.delivery_window = None  # Farmer's window revoked before accept.
    with pytest.raises(CaptureUnavailable):
        rig.controller.accept_delivery()
    assert rig.world.clicks == ["accept_request"]
    assert transactions(rig.journal) == []


def test_accept_still_requires_an_active_reservation(make_rig):
    rig = make_rig()
    open_reserved_trade(rig)
    state = rig.journal.get(MERCHANT, delivery_reservation.KEY)
    delivery_reservation.save(
        rig.journal, MERCHANT, dict(state, phase="cancelled_before_input")
    )
    with pytest.raises(CaptureUnavailable):
        rig.controller.accept_delivery()
    assert rig.world.clicks == ["accept_request"]
    assert transactions(rig.journal) == []
