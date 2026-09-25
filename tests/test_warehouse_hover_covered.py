"""Covered warehouse drag endpoints: self-diagnosing, bounded, never replayed.

Evidence (another PC, from 2026-09-24 22:16): every after-shopping banking
step failed with "Warehouse drag endpoint is covered by another window" while
nothing was visibly over the game. That text is raised only by
TownTrade.require_warehouse_hover, used only by the item-deposit drag
(TownTrade.warehouse_deposit). after_shopping() stores items before its silver
lines, so the silver deposit never ran. The money transfer itself has no hover
check. The error was an untyped ValueError: OvernightLoop.town did not retry it
and the restock visit was left in town_work.

Ways this could fail (each is exercised below):

 1. The error names no windows: the hovered ImGui window's name/geometry, the
    expected window, the pointer target (GUI and native) and the Warehouse
    window geometry are missing from the exception text and the recorded event.
 2. A failed diagnostic read masks the covered error or turns it into another
    (possibly uncertain) error.
 3. A covered source (checked before mouse-down) is not typed as the safe
    pre-press error, so no bounded retry is possible; or a covered destination
    (checked while the item is held) is typed as the safe error and retried
    after a press.
 4. The bridge/worker drops the typed code or its diagnostic, so the farmer
    sees an untyped ValueError.
 5. OvernightLoop.town blindly retries the covered error (80 attempts with no
    remedy) instead of returning it to the banking layer.
 6. Covered once, then clear: the deposit is pressed more than once, stored
    twice, or not stored at all.
 7. Covered persistently: more than one reopen, any button press, a
    valuable_stored row, a transfer row caused by the item, or changed
    carried/stored/silver balances.
 8. Covered persistently for an ordinary (non-urgent, non-special) stash item
    during after_shopping: the whole visit fails (stranded town work) although
    provably nothing moved, instead of deferring that item and finishing the
    silver deposit.
 9. Urgent valuables or special loot (Dragonballs, Meteors) are deferred
    instead of failing closed with the diagnostic.
10. Carried/stored items or silver changed around the covered failure, and
    the item is still retried.
11. Hover is fine but the receipt is unverified: the drag is retried, the
    error is swallowed, or the item is deferred (strict behaviour must stay).
12. The silver transfer path changes: a hover requirement is added or its
    single geometry retry changes.
"""

import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace as NS

import pytest

from conquest import banking
from conquest import town_trade as module
from conquest.memory_inventory import Item
from conquest.memory_shop import GuiWindow
from conquest.memory_warehouse import WarehouseSnapshot
from conquest.merchants.memory import HoverNotReady

SUPER_GEAR = 410009  # Ordinary stash candidate: not urgent, not special loot.
METEOR = 1088001
DRAGONBALL = 1088000

INVENTORY = GuiWindow(0x1000, "Inventory", (557.0, 380.0), (447.0, 260.0), (0, 0))
GRID = GuiWindow(
    0x1100, "Inventory/##ItemGrid_AB12", (577.0, 435.0), (407.0, 175.0), (0, 0)
)
WAREHOUSE = GuiWindow(0x2000, "Warehouse", (520.0, 300.0), (312.0, 460.0), (0, 0))
WAREHOUSE_GRID = GuiWindow(
    0x2100, "Warehouse/ScrollingRegion_CD34", (540.0, 396.0), (272.0, 326.0), (0, 0)
)


def registry_window(window):
    return {
        "name": window.name,
        "address": window.address,
        "geometry": (*window.position, *window.size),
        "scroll": window.scroll,
    }


class FakeGame:
    """Memory, GUI and input of one client; nothing here touches a process."""

    def __init__(self, items, *, covered=(), receipt=True, silver=21_795):
        self.carried = [
            Item(uid, kind, 1, 1, slot, plus) for uid, kind, slot, plus in items
        ]
        self.stored = []
        self.silver, self.stored_silver = silver, 5_000_000
        # covered: set of (phase, deposit_call_number); "*" = every call.
        self.covered = set(covered)
        self.receipt = receipt
        self.deposit_calls = 0
        self.presses = 0
        self.releases_over_cover = 0
        self.panels = ["Warehouse", "Inventory"]
        self.gui_error = None

    def is_covered(self, phase):
        return (phase, "*") in self.covered or (
            phase,
            self.deposit_calls,
        ) in self.covered

    # --- memory ---------------------------------------------------------
    def inventory(self):
        return NS(items=tuple(self.carried), silver=self.silver, equipped_ammo=None)

    def warehouse(self):
        return WarehouseSnapshot(tuple(self.stored), 20, self.stored_silver)

    def gui_read(self, name):
        if self.gui_error:
            raise ValueError(self.gui_error)
        return {
            "Inventory/##ItemGrid_": GRID,
            "Warehouse/ScrollingRegion_": WAREHOUSE_GRID,
            "Warehouse": WAREHOUSE,
            "Inventory": INVENTORY,
        }[name]

    def hovered_gui_window(self):
        if self.gui_error:
            raise ValueError(self.gui_error)
        # Registry order is back to front; the Warehouse panel is topmost.
        return WAREHOUSE.address, [
            registry_window(w) for w in (INVENTORY, GRID, WAREHOUSE, WAREHOUSE_GRID)
        ]

    # --- input ----------------------------------------------------------
    def drag(self, target, source, destination, size, **guards):
        guards["layout_guard"]()
        guards["before_press"]()  # a raise here sends no button press
        self.presses += 1
        try:
            guards["layout_guard"]()
            guards["before_release"]()
        except Exception:
            self.releases_over_cover += 1  # button released; outcome unknown
            raise
        if self.receipt:
            (item,) = [i for i in self.carried if (i.uid, i.slot) == self.dragging]
            self.carried.remove(item)
            self.stored.append(item)


def make_trade(game, monkeypatch):
    trade = module.TownTrade.__new__(module.TownTrade)
    trade.observer = NS(adapter=None, operations=NS(target="target"))
    trade.vendor = lambda kind: 123
    trade.inventory = NS(read=game.inventory)
    trade.shop = NS(gui=NS(read=game.gui_read))
    trade.life = lambda **kwargs: NS(map_id=1002)
    trade.warehouse_layout = lambda: (
        NS(assert_current=lambda revision: None),
        NS(client_size=(1416, 1016), gui_size=(1036, 793)),
    )
    trade.hovered_gui_window = game.hovered_gui_window

    def require_hover(window):
        phase = "source" if window is GRID else "destination"
        if game.is_covered(phase):
            raise HoverNotReady("Warehouse drag endpoint is covered by another window")

    trade.require_warehouse_hover = require_hover

    def verified_read(read, accept, failure, **kwargs):
        if accept(read()):
            return read()
        raise ValueError(failure)

    trade.verified_read = verified_read
    monkeypatch.setattr(
        module,
        "MemoryWarehouseReader",
        lambda adapter: NS(read=game.warehouse, gui=NS(read=game.gui_read)),
    )
    monkeypatch.setattr(module, "foreground_drag", game.drag)
    return trade


def deposit(game, monkeypatch, uid):
    game.deposit_calls += 1
    game.dragging = next((i.uid, i.slot) for i in game.carried if i.uid == uid)
    return make_trade(game, monkeypatch)({"action": "warehouse-deposit", "uid": uid})


@pytest.fixture
def instant_hover_wait(monkeypatch):
    from conquest.merchants import driver

    monkeypatch.setattr(
        driver, "wait_hover_validation", lambda validate, check: (check(), validate())
    )


# --- bridge-side classification and diagnostics ----------------------------


def test_covered_source_is_typed_pre_press_and_self_diagnosing(
    monkeypatch, instant_hover_wait
):
    game = FakeGame([(42, SUPER_GEAR, 1, 0)], covered={("source", "*")})
    with pytest.raises(module.WarehouseHoverCovered) as raised:
        deposit(game, monkeypatch, 42)
    error = raised.value
    assert isinstance(error, HoverNotReady)
    assert error.code == "warehouse_hover_covered"
    assert game.presses == 0 and game.carried and not game.stored
    diagnostic = error.diagnostic
    assert diagnostic["phase"] == "source" and diagnostic["button_pressed"] is False
    assert diagnostic["item"] == {"uid": 42, "type_id": SUPER_GEAR, "slot": 1}
    assert diagnostic["hovered_window"] == {
        "name": "Warehouse",
        "address": "0x2000",
        "position": [520.0, 300.0],
        "size": [312.0, 460.0],
    }
    assert diagnostic["expected_window"]["name"] == "Inventory/##ItemGrid_AB12"
    assert diagnostic["expected_window"]["position"] == [577.0, 435.0]
    # Slot 1: GUI (637, 455), scaled into the 1416x1016 native client.
    assert diagnostic["pointer"] == {"logical": [637, 455], "native": [871, 583]}
    assert diagnostic["warehouse_window"]["position"] == [520.0, 300.0]
    assert diagnostic["warehouse_window"]["size"] == [312.0, 460.0]
    assert diagnostic["inventory_window"]["name"] == "Inventory"
    assert [w["name"] for w in diagnostic["windows_at_pointer"]] == [
        "Inventory",
        "Inventory/##ItemGrid_AB12",
        "Warehouse",
        "Warehouse/ScrollingRegion_CD34",
    ]
    text = str(error)
    for part in (
        "Warehouse drag endpoint is covered by another window",
        "source",
        "'Warehouse' at (520, 300) 312x460",
        "'Inventory/##ItemGrid_AB12' at (577, 435) 407x175",
        "pointer GUI (637, 455) native (871, 583)",
        "no button pressed",
    ):
        assert part in text, part
    assert json.loads(text.split("diagnostic=", 1)[1]) == diagnostic


def test_diagnostic_read_failure_keeps_the_typed_covered_error(
    monkeypatch, instant_hover_wait
):
    game = FakeGame([(42, SUPER_GEAR, 1, 0)], covered={("source", "*")})
    trade = make_trade(game, monkeypatch)
    game.dragging = (42, 1)
    game.deposit_calls = 1

    def broken():
        raise ValueError("GUI registry changed")

    trade.hovered_gui_window = broken
    with pytest.raises(module.WarehouseHoverCovered) as raised:
        trade({"action": "warehouse-deposit", "uid": 42})
    diagnostic = raised.value.diagnostic
    assert diagnostic["hovered_window"] == {"error": "GUI registry changed"}
    assert diagnostic["button_pressed"] is False and game.presses == 0


def test_covered_destination_while_held_is_uncertain_not_retryable(
    monkeypatch, instant_hover_wait
):
    game = FakeGame([(42, SUPER_GEAR, 1, 0)], covered={("destination", "*")})
    with pytest.raises(module.WarehouseDropCovered) as raised:
        deposit(game, monkeypatch, 42)
    error = raised.value
    assert not isinstance(error, HoverNotReady)
    assert getattr(error, "code", None) is None
    assert error.diagnostic["phase"] == "destination"
    assert error.diagnostic["button_pressed"] is True
    assert error.diagnostic["expected_window"]["name"] == WAREHOUSE_GRID.name
    assert "no repeat input" in str(error)
    assert game.presses == 1 and game.releases_over_cover == 1


def test_bridge_fields_and_worker_round_trip_keep_type_and_diagnostic(
    tmp_path, monkeypatch
):
    from conquest.worker import request

    diagnostic = {"phase": "source", "button_pressed": False, "hovered_window": {}}
    covered = module.WarehouseHoverCovered("covered text", diagnostic)
    assert module.town_error_fields(covered) == {
        "code": "warehouse_hover_covered",
        "diagnostic": diagnostic,
    }
    assert module.town_error_fields(HoverNotReady("x")) == {}
    assert module.town_error_fields(module.WarehouseDropCovered("x", {})) == {}
    assert module.town_error_fields(module.TownObservationUnavailable("x")) == {
        "code": "town_observation_unavailable"
    }
    info = tmp_path / "worker.json"
    info.write_text(json.dumps({"port": 12345, "token": "test"}))
    body = {"error": str(covered), **module.town_error_fields(covered)}

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "http://127.0.0.1", 400, "bad", {}, io.BytesIO(json.dumps(body).encode())
        )

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fail)
    with pytest.raises(module.WarehouseHoverCovered) as raised:
        request(info, "town", {"action": "warehouse-deposit", "uid": 42})
    assert raised.value.diagnostic == diagnostic and str(raised.value) == "covered text"


def test_embedded_bridge_uses_the_shared_error_fields():
    from pathlib import Path

    source = (
        Path(module.__file__)
        .with_name("embedded_bridge.py")
        .read_text(encoding="utf-8")
    )
    assert "town_error_fields(error)" in source


def test_overnight_town_does_not_blindly_retry_the_covered_error(monkeypatch):
    from conquest import overnight

    calls, records = [], []
    covered = module.WarehouseHoverCovered("covered", {"phase": "source"})

    def request(info, operation, body):
        calls.append(body["action"])
        raise covered

    monkeypatch.setattr(overnight, "request", request)
    loop = NS(
        info=None,
        living=lambda: None,
        record=lambda name, **fields: records.append(name),
    )
    with pytest.raises(module.WarehouseHoverCovered):
        overnight.OvernightLoop.town(loop, "warehouse-deposit", uid=42)
    assert calls == ["warehouse-deposit"] and records == ["town_action_failed"]


def test_silver_transfer_path_has_no_hover_requirement():
    import inspect

    from conquest import warehouse_money

    assert "hover" not in inspect.getsource(warehouse_money).lower()
    source = inspect.getsource(banking.transfer)
    assert source.count("Warehouse money control geometry differs") == 1


# --- banking layer through the real OvernightLoop.town and worker ---------


class Harness:
    """Farmer loop → OvernightLoop.town → worker.request → bridge → TownTrade."""

    def __init__(self, game, monkeypatch, tmp_path):
        from conquest import overnight

        self.game, self.events = game, []
        self.actions = []
        info = tmp_path / "worker.json"
        info.write_text(json.dumps({"port": 12345, "token": "test"}))
        self.loop = NS(
            info=info,
            record=lambda name, **fields: self.events.append((name, fields)),
            living=lambda: {
                "embedded_controls": {"life": {"map_id": 1002, "position": [409, 353]}}
            },
            terrain=NS(walkable=lambda point: True),
        )
        self.loop.town = lambda action, **fields: overnight.OvernightLoop.town(
            self.loop, action, **fields
        )
        harness = self

        def open_(opener, call, timeout=None):
            body = json.loads(call.data)
            try:
                result = harness.dispatch(body)
            except (ValueError, OSError) as error:
                payload = {"error": str(error), **module.town_error_fields(error)}
                raise urllib.error.HTTPError(
                    call.full_url,
                    400,
                    "bad",
                    {},
                    io.BytesIO(json.dumps(payload).encode()),
                ) from None
            return io.BytesIO(json.dumps(result).encode())

        monkeypatch.setattr(urllib.request.OpenerDirector, "open", open_)
        self.monkeypatch = monkeypatch

    def dispatch(self, body):
        game, action = self.game, body["action"]
        self.actions.append(action)
        if action == "supplies":
            bag = game.inventory()
            return {
                "items": [_item_dict(i) for i in bag.items],
                "silver": bag.silver,
                "equipped_ammo": None,
                "capacity": 40,
            }
        if action == "warehouse-items":
            stash = game.warehouse()
            return {
                "items": [_item_dict(i) for i in stash.items],
                "capacity": stash.capacity,
                "bank_silver": stash.bank_silver,
            }
        if action == "warehouse-money":
            return {"silver": game.silver, "stored_silver": game.stored_silver}
        if action == "vendor-status":
            return {"reachable": True}
        if action == "warehouse-locate":
            return {"npc_id": 7, "position": [409, 351], "map_id": 1002}
        if action == "close":
            if body["window"] in game.panels:
                game.panels.remove(body["window"])
            return {"closed": body["window"]}
        if action == "open-bank":
            game.panels = ["Warehouse", "Inventory"]
            return {"opened": True}
        if action == "warehouse-deposit":
            return deposit(game, self.monkeypatch, body["uid"])
        if action == "warehouse-money-deposit":
            amount = body["amount"]
            game.silver -= amount
            game.stored_silver += amount
            return {
                "direction": "deposit",
                "amount": amount,
                "silver": game.silver,
                "stored_silver": game.stored_silver,
                "verified": True,
            }
        raise AssertionError(f"Unexpected town action {action}")

    def names(self):
        return [name for name, _ in self.events]


def _item_dict(item):
    return {
        "uid": item.uid,
        "type_id": item.type_id,
        "amount": item.amount,
        "limit": item.limit,
        "slot": item.slot,
        "plus": item.plus,
    }


@pytest.fixture
def banking_env(monkeypatch, tmp_path):
    from conquest import merchant_loop_acceptance, meteor_banking
    from conquest.merchants import bank_stock_alerts, delivery_journey, delivery_route

    (tmp_path / "banking.json").write_text(
        json.dumps(
            {"enabled": True, "transport_reserve": 200, "deposit_after_shopping": True}
        )
    )
    monkeypatch.setattr(banking, "transport_reserve", lambda: 200)
    monkeypatch.setattr(delivery_journey, "start", lambda loop, **kw: None)
    monkeypatch.setattr(delivery_journey, "pending", lambda: False)
    monkeypatch.setattr(delivery_route, "pending", lambda: False)
    monkeypatch.setattr(meteor_banking, "completed_stored_scroll", lambda: None)
    monkeypatch.setattr(merchant_loop_acceptance, "cycle_pending", lambda: False)
    monkeypatch.setattr(bank_stock_alerts, "record", lambda loop: None)
    return tmp_path


def run_after_shopping(game, monkeypatch, tmp_path):
    harness = Harness(game, monkeypatch, tmp_path)
    error = None
    try:
        result = banking.after_shopping(harness.loop)
    except ValueError as raised:
        result, error = None, raised
    return harness, result, error


def test_covered_once_then_clear_deposits_exactly_once(
    banking_env, monkeypatch, instant_hover_wait
):
    game = FakeGame([(42, SUPER_GEAR, 1, 0)], covered={("source", 1)})
    harness, result, error = run_after_shopping(game, monkeypatch, banking_env)
    assert error is None and result is True
    assert game.presses == 1 and [i.uid for i in game.stored] == [42]
    assert harness.names().count("valuable_stored") == 1
    assert harness.names().count("warehouse_hover_covered") == 1
    assert harness.actions.count("warehouse-deposit") == 2
    assert harness.actions.count("open-bank") == 2  # initial open plus one reopen
    assert harness.names().count("silver_deposit") == 1
    covered = dict(harness.events)["warehouse_hover_covered"]
    assert covered["attempt"] == 1 and covered["diagnostic"]["phase"] == "source"
    assert covered["diagnostic"]["hovered_window"]["name"] == "Warehouse"


def test_ordinary_item_persistently_covered_is_deferred_and_visit_finishes(
    banking_env, monkeypatch, instant_hover_wait
):
    game = FakeGame([(42, SUPER_GEAR, 1, 0)], covered={("source", "*")})
    harness, result, error = run_after_shopping(game, monkeypatch, banking_env)
    assert error is None and result is True
    assert game.presses == 0 and [i.uid for i in game.carried] == [42]
    assert not game.stored and "valuable_stored" not in harness.names()
    assert harness.actions.count("warehouse-deposit") == 2
    assert harness.actions.count("open-bank") == 2
    assert harness.names().count("warehouse_hover_covered") == 2
    deferred = dict(harness.events)["valuable_storage_deferred"]
    assert deferred["uid"] == 42 and deferred["diagnostic"]["button_pressed"] is False
    # The cash lines still ran once: the restock visit is not stranded.
    assert harness.names().count("silver_deposit") == 1
    assert game.silver == 200


@pytest.mark.parametrize("kind", [METEOR, DRAGONBALL])
def test_special_or_urgent_item_persistently_covered_fails_closed(
    banking_env, monkeypatch, instant_hover_wait, kind
):
    game = FakeGame([(42, kind, 1, None)], covered={("source", "*")})
    before = (game.silver, game.stored_silver, list(game.carried), list(game.stored))
    harness, result, error = run_after_shopping(game, monkeypatch, banking_env)
    assert result is None and error is not None
    assert "stayed covered" in str(error) and "no button pressed" in str(error)
    assert "'Warehouse' at (520, 300) 312x460" in str(error)
    assert game.presses == 0
    assert (game.silver, game.stored_silver, game.carried, game.stored) == before
    assert "valuable_stored" not in harness.names()
    assert "valuable_storage_deferred" not in harness.names()
    assert not any(n.startswith("silver_") for n in harness.names())
    assert not banking.LEDGER.exists()
    assert harness.actions.count("warehouse-deposit") == 2


def test_urgent_path_never_defers(banking_env, monkeypatch, instant_hover_wait):
    game = FakeGame([(42, DRAGONBALL, 1, None)], covered={("source", "*")})
    harness = Harness(game, monkeypatch, banking_env)
    with pytest.raises(ValueError, match="stayed covered"):
        banking.stash_urgent_valuables(harness.loop)
    assert game.presses == 0 and "valuable_storage_deferred" not in harness.names()


@pytest.mark.parametrize("change", ["silver_during_reopen", "item_gone"])
def test_changed_state_around_covered_failure_is_never_retried(
    banking_env, monkeypatch, instant_hover_wait, change
):
    game = FakeGame(
        [(42, SUPER_GEAR, 1, 0), (43, SUPER_GEAR, 2, 0)], covered={("source", 1)}
    )
    harness = Harness(game, monkeypatch, banking_env)
    original = harness.dispatch

    def dispatch(body):
        try:
            return original(body)
        finally:
            if change == "silver_during_reopen" and body["action"] == "open-bank":
                game.silver -= 1  # something outside this deposit moved money
            if change == "item_gone" and body["action"] == "warehouse-deposit":
                game.carried = [i for i in game.carried if i.uid != 42]

    harness.dispatch = dispatch
    with pytest.raises(ValueError, match="changed around a covered deposit"):
        banking.deposit_item(
            harness.loop, _item_dict(game.carried[0]), allow_defer=True
        )
    assert harness.actions.count("warehouse-deposit") == 1 and game.presses == 0


def test_unverified_receipt_keeps_strict_behaviour(
    banking_env, monkeypatch, instant_hover_wait
):
    game = FakeGame([(42, SUPER_GEAR, 1, 0)], receipt=False)
    harness, _, error = run_after_shopping(game, monkeypatch, banking_env)
    assert error is not None and "not verified" in str(error)
    assert game.presses == 1 and harness.actions.count("warehouse-deposit") == 1
    assert "valuable_storage_deferred" not in harness.names()
    assert "warehouse_hover_covered" not in harness.names()
    assert not any(n.startswith("silver_") for n in harness.names())


def test_destination_cover_after_press_is_never_retried_or_deferred(
    banking_env, monkeypatch, instant_hover_wait
):
    game = FakeGame([(42, SUPER_GEAR, 1, 0)], covered={("destination", "*")})
    harness, _, error = run_after_shopping(game, monkeypatch, banking_env)
    assert error is not None and "no repeat input" in str(error)
    assert game.presses == 1 and harness.actions.count("warehouse-deposit") == 1
    assert "valuable_storage_deferred" not in harness.names()
    assert not any(n.startswith("silver_") for n in harness.names())


# --- end to end -------------------------------------------------------------


SCENARIOS = {
    "covered_once_then_clear": ([(42, SUPER_GEAR, 1, 0)], {("source", 1)}, True),
    "ordinary_item_covered_persistently": (
        [(42, SUPER_GEAR, 1, 0)],
        {("source", "*")},
        True,
    ),
    "meteor_covered_persistently": ([(42, METEOR, 1, None)], {("source", "*")}, True),
    "hover_fine_receipt_unverified": ([(42, SUPER_GEAR, 1, 0)], set(), False),
    "destination_covered_while_held": (
        [(42, SUPER_GEAR, 1, 0)],
        {("destination", "*")},
        True,
    ),
}


def scenario_record(name, monkeypatch, tmp_path):
    items, covered, receipt = SCENARIOS[name]
    game = FakeGame(items, covered=covered, receipt=receipt)
    before = {
        "silver": game.silver,
        "stored_silver": game.stored_silver,
        "carried": [i.uid for i in game.carried],
    }
    harness, result, error = run_after_shopping(game, monkeypatch, tmp_path)
    ledger = (
        [json.loads(line) for line in banking.LEDGER.read_text().splitlines()]
        if banking.LEDGER.exists()
        else []
    )
    if banking.LEDGER.exists():
        banking.LEDGER.unlink()
    covered_events = [f for n, f in harness.events if n == "warehouse_hover_covered"]
    return {
        "completed": result is True,
        "error": None if error is None else str(error).split("; diagnostic=")[0],
        "button_presses": game.presses,
        "deposit_requests": harness.actions.count("warehouse-deposit"),
        "warehouse_opens": harness.actions.count("open-bank"),
        "events": [
            n
            for n in harness.names()
            if n
            in (
                "warehouse_hover_covered",
                "valuable_stored",
                "valuable_storage_deferred",
                "silver_deposit",
                "town_action_failed",
            )
        ],
        "covered": [
            {
                "attempt": f["attempt"],
                "phase": f["diagnostic"]["phase"],
                "button_pressed": f["diagnostic"]["button_pressed"],
                "hovered": f["diagnostic"]["hovered_window"],
                "expected": f["diagnostic"]["expected_window"]["name"],
                "pointer": f["diagnostic"]["pointer"],
                "warehouse_window": f["diagnostic"]["warehouse_window"],
            }
            for f in covered_events
        ],
        "transfer_rows": [
            {k: row[k] for k in ("direction", "amount", "verified")} for row in ledger
        ],
        "before": before,
        "after": {
            "silver": game.silver,
            "stored_silver": game.stored_silver,
            "carried": [i.uid for i in game.carried],
            "stored": [i.uid for i in game.stored],
        },
    }


def test_covered_warehouse_deposit_end_to_end_artifact(banking_env, monkeypatch):
    """Real wait_hover_validation, OvernightLoop.town, worker decode, TownTrade."""
    artifact = {
        name: scenario_record(name, monkeypatch, banking_env) for name in SCENARIOS
    }
    target = banking_env / "warehouse-hover-e2e.json"
    target.write_text(json.dumps(artifact, indent=2, sort_keys=True))
    loaded = json.loads(target.read_text())

    once = loaded["covered_once_then_clear"]
    assert once["completed"] and once["button_presses"] == 1
    assert once["deposit_requests"] == 2 and once["warehouse_opens"] == 2
    assert once["after"]["stored"] == [42] and once["after"]["carried"] == []
    assert once["events"].count("valuable_stored") == 1
    assert once["transfer_rows"] == [
        {"direction": "deposit", "amount": 21_595, "verified": True}
    ]

    ordinary = loaded["ordinary_item_covered_persistently"]
    assert ordinary["completed"] and ordinary["button_presses"] == 0
    assert ordinary["after"]["carried"] == [42] and ordinary["after"]["stored"] == []
    assert ordinary["events"] == [
        "town_action_failed",
        "warehouse_hover_covered",
        "town_action_failed",
        "warehouse_hover_covered",
        "valuable_storage_deferred",
        "silver_deposit",
    ]
    assert [c["attempt"] for c in ordinary["covered"]] == [1, 2]
    for covered in ordinary["covered"]:
        assert covered["phase"] == "source" and covered["button_pressed"] is False
        assert covered["hovered"]["name"] == "Warehouse"
        assert covered["expected"] == "Inventory/##ItemGrid_AB12"
        assert covered["pointer"] == {"logical": [637, 455], "native": [871, 583]}
        assert covered["warehouse_window"]["size"] == [312.0, 460.0]

    meteor = loaded["meteor_covered_persistently"]
    assert not meteor["completed"] and meteor["button_presses"] == 0
    assert "stayed covered" in meteor["error"]
    assert meteor["transfer_rows"] == []
    assert meteor["after"] == {**meteor["before"], "stored": []}

    unverified = loaded["hover_fine_receipt_unverified"]
    assert not unverified["completed"] and unverified["button_presses"] == 1
    assert unverified["deposit_requests"] == 1 and unverified["covered"] == []
    assert unverified["transfer_rows"] == []

    held = loaded["destination_covered_while_held"]
    assert not held["completed"] and held["button_presses"] == 1
    assert held["deposit_requests"] == 1 and held["transfer_rows"] == []
    assert held["covered"] == []  # never offered to the bounded retry
