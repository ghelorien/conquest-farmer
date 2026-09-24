from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import json

import pytest

from conquest.protected_withdrawal import (
    CORE,
    ProtectedWithdrawal,
    ProtectedWithdrawalJournal,
    _withdrawn,
    canonical_artifact_digest,
    core,
    load_plan,
    pending,
)


IDENTITY = {
    "pid": 44,
    "path": "C:/Game/ImConquer.exe",
    "creation_time_100ns": 55,
    "architecture": "x64",
}
BUILD = "a" * 64


def item(uid, type_id=114643, **changes):
    value = {
        "uid": uid,
        "type_id": type_id,
        "plus": 1,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
    }
    value.update(changes)
    return value


def basic(value, slot=0):
    equipment = 100000 <= value["type_id"] < 600000
    amount = 31 if equipment else value["quantity"]
    limit = 40 if equipment else max(1, amount)
    return {
        "uid": value["uid"],
        "type_id": value["type_id"],
        "amount": amount,
        "limit": limit,
        "slot": slot,
        "plus": value["plus"],
    }


def rich(value, slot=0):
    return {**value, "name": "Reviewed", "slot": slot, "price": None, "category": None}


def source(inventory=(), **changes):
    value = {
        "character": "Parasite",
        "character_uid": 1173490,
        "identity": IDENTITY,
        "server": "America",
        "map_id": 1036,
        "position": [256, 204],
        "hp": 100,
        "silver": 999,
        "inventory": list(inventory),
        "booth": [],
        "trade": None,
        "request": None,
        "windows": [],
    }
    value.update(changes)
    return value


def observation(bag=(), stash=()):
    rich = []
    for slot, value in enumerate(stash):
        rich.append(
            {**value, "name": "Reviewed", "slot": slot, "price": None, "category": None}
        )
    return {
        "source": source(bag),
        "equipped_ammo": {
            "uid": 8,
            "type_id": 1050001,
            "amount": 900,
            "limit": 1000,
            "plus": 0,
        },
        "warehouse": {"items": rich, "capacity": 20},
        "npc": {
            "entity_id": 77,
            "object_address": 123,
            "name": "Warehouseman",
            "map_id": 1036,
            "type_id": 0,
            "position": [252, 221],
            "draw_position": [400, 300],
        },
        "grid": {
            "address": 700,
            "name": "Warehouse/ScrollingRegion_A",
            "position": [71.0, 195.0],
            "size": [272.0, 326.0],
            "scroll": [0.0, 0.0],
        },
    }


def plan(first, second):
    visit = {
        "version": 1,
        "visit_id": "old-visit",
        "farmer_profile_id": "farmer-profile",
        "parent_visit_id": "town-visit",
        "town_visit_id": "town-visit",
        "phase": "departed",
        "started_at": 100,
        "deadline": 160,
        "attempts": [],
        "departed_at": 140,
        "arrival_map": 1011,
    }
    value = {
        "version": 1,
        "kind": "controlled_protected_withdrawal_plan",
        "plan_id": "plan-one",
        "created_at": 151,
        "release": {"commit": "b" * 40, "manifest_sha256": "c" * 64},
        "farmer": {
            "character": "Parasite",
            "character_uid": 1173490,
            "server": "America",
            "process_identity": IDENTITY,
            "client_sha256": BUILD,
        },
        "deposit_operation_id": "deposit-batch",
        "market_departure": {
            "market_visit": visit,
            "departed_observation": {
                "observed_at": 145,
                "map_id": 1011,
                "position": [180, 230],
                "hp": 100,
                "identity": IDENTITY,
                "character_uid": 1173490,
            },
        },
        "market_reentry": {
            "observed_at": 150,
            "snapshot": source(),
            "equipped_ammo": observation()["equipped_ammo"],
            "warehouse": {"items": [rich(first, 0), rich(second, 1)], "capacity": 20},
        },
        "items": [
            {
                "item": first,
                "withdrawal_operation_id": "withdraw-one",
                "deposit_receipt": {"path": "unused-one", "sha256": "0" * 64},
            },
            {
                "item": second,
                "withdrawal_operation_id": "withdraw-two",
                "deposit_receipt": {"path": "unused-two", "sha256": "0" * 64},
            },
        ],
    }
    value["plan_sha256"] = canonical_artifact_digest(
        {**value, "plan_sha256": ""}, "plan_sha256"
    )
    return value


class Town:
    def __init__(self):
        self.observer = NS(
            character="Parasite",
            adapter=NS(identity=IDENTITY, expected_sha256=BUILD),
            operations=NS(target="target"),
        )
        self.input_attempted = False
        self.checks = 0

    def check_input(self):
        self.checks += 1

    def warehouse_layout(self):
        return NS(assert_current=lambda revision: None), NS(
            client_size=(1416, 1016), gui_size=(1036, 793)
        )

    def warehouse_native_point(self, point, revision):
        return tuple(
            round(v * n / g)
            for v, n, g in zip(point, revision.client_size, revision.gui_size)
        )


def operation(tmp_path, monkeypatch, observations, *, click="success"):
    from conquest import protected_withdrawal as module

    one, two = item(1), item(2, 130403)
    approved = plan(one, two)
    town = Town()
    journal = ProtectedWithdrawalJournal(tmp_path / "withdraw.sqlite3")
    work = ProtectedWithdrawal(
        town, journal=journal, clock=lambda: 200, sleep=lambda _: None
    )
    work._plan = lambda *args: (approved, approved["items"][0])
    values = iter(observations)
    last = [observations[-1]]

    def observe():
        try:
            last[0] = next(values)
        except StopIteration:
            pass
        return deepcopy(last[0])

    work._observe = observe
    monkeypatch.setattr(
        "conquest.merchants.driver.wait_hover_validation", lambda validate, check: None
    )
    calls = []

    def foreground(*args, **kwargs):
        calls.append("click")
        if click == "before":
            raise ValueError("focus rejected before marker")
        kwargs["before_press"]()
        if click == "after":
            raise OSError("lost click acknowledgement")

    monkeypatch.setattr(module, "foreground_click", foreground, raising=False)
    # _run imports foreground_click from conquest.foreground.
    monkeypatch.setattr("conquest.foreground.foreground_click", foreground)
    return work, journal, approved, calls


@pytest.mark.parametrize(
    "field,bad",
    [
        ("uid", 9),
        ("type_id", 130403),
        ("plus", 2),
        ("gem1", 1),
        ("gem2", 2),
        ("quantity", 2),
        ("bound", True),
    ],
)
def test_all_seven_protected_item_attributes_are_identity(field, bad):
    original = item(1)
    changed = {**original, field: bad}
    assert tuple(original[k] for k in CORE) != tuple(changed[k] for k in CORE)
    assert core(changed) == changed


@pytest.mark.parametrize(
    "field,bad",
    [
        ("uid", 9),
        ("type_id", 130403),
        ("plus", 2),
        ("gem1", 1),
        ("gem2", 2),
        ("quantity", 2),
        ("bound", True),
    ],
)
def test_exact_withdrawal_receipt_rejects_each_changed_ownership_field(field, bad):
    original = item(1)
    before = observation(stash=(original, item(2, 130403)))
    changed = {**original, field: bad}
    after = observation(bag=(changed,), stash=(item(2, 130403),))
    assert not _withdrawn(before, after, original)


def test_withdrawal_journal_enforces_cas_and_operation_binding(tmp_path):
    journal = ProtectedWithdrawalJournal(tmp_path / "j.sqlite3", clock=lambda: 10)
    state = {
        "operation_id": "op",
        "plan_id": "plan",
        "uid": 1,
        "phase": "prepared",
        "intent": {},
    }
    journal.begin(state)
    with pytest.raises(ValueError, match="reused"):
        journal.begin({**state, "uid": 2})
    journal.transition("op", "prepared", "input_maybe_sent", attempt={"at": 10})
    with pytest.raises(ValueError, match="Illegal"):
        journal.transition("op", "prepared", "no_transfer")
    assert [row["phase"] for row in journal.history("op")] == [
        "prepared",
        "input_maybe_sent",
    ]


def test_pending_is_read_only_missing_terminal_and_malformed(tmp_path):
    path = tmp_path / "missing.sqlite3"
    assert pending(path) == [] and not path.exists()
    journal = ProtectedWithdrawalJournal(path, clock=lambda: 10)
    state = {
        "operation_id": "op",
        "plan_id": "plan",
        "uid": 1,
        "phase": "prepared",
        "intent": {},
    }
    journal.begin(state)
    assert [row["operation_id"] for row in pending(path)] == ["op"]
    journal.transition("op", "prepared", "no_transfer", receipt={})
    assert pending(path) == []
    path.write_bytes(b"not a sqlite database")
    assert pending(path)[0]["phase"] == "blocked"


def test_town_dispatch_accepts_only_fixed_protected_withdrawal_identifiers(monkeypatch):
    from conquest import protected_withdrawal as module
    from conquest.town_trade import TownTrade

    calls = []
    monkeypatch.setattr(
        module, "withdraw", lambda *args: calls.append(args) or {"phase": "withdrawn"}
    )
    trade = TownTrade.__new__(TownTrade)
    body = {
        "action": "warehouse-withdraw-protected",
        "plan_id": "plan",
        "operation_id": "op",
        "uid": 7,
    }
    assert trade(body) == {"phase": "withdrawn"}
    assert calls == [(trade, "plan", "op", 7)]
    with pytest.raises((ValueError, AttributeError)):
        trade({**body, "approved": True})


def test_exact_move_is_receipted_and_lost_ack_never_replays(tmp_path, monkeypatch):
    one = item(1)
    before = observation(stash=(one, item(2, 130403)))
    after = observation(bag=(one,), stash=(item(2, 130403),))
    work, journal, approved, calls = operation(
        tmp_path, monkeypatch, [before, after], click="after"
    )
    result = work._run("plan-one", "withdraw-one", 1)
    assert result["phase"] == "withdrawn" and result["receipt"]["verified_in_inventory"]
    assert calls == ["click"] and work.town.input_attempted
    work._observe = lambda: pytest.fail(
        "terminal receipt should not need another memory read"
    )
    assert work._run("plan-one", "withdraw-one", 1)["phase"] == "withdrawn"
    assert calls == ["click"]


def test_preinput_rejection_with_exact_nochange_is_no_transfer(tmp_path, monkeypatch):
    one = item(1)
    before = observation(stash=(one, item(2, 130403)))
    work, journal, approved, calls = operation(
        tmp_path, monkeypatch, [before, before], click="before"
    )
    result = work._run("plan-one", "withdraw-one", 1)
    assert result["phase"] == "no_transfer" and result["input_attempted"] is False
    assert result["reason"] == "focus rejected before marker"
    assert result["receipt"]["preinput_failure"] == {
        "stage": "pre_input",
        "error_type": "ValueError",
        "reason": "focus rejected before marker",
        "at": 200,
    }
    saved = journal.get("withdraw-one")
    assert saved["failure"] == result["receipt"]["preinput_failure"]
    payload = json.loads(journal.history("withdraw-one")[-1]["payload"])
    assert payload["failure"] == result["receipt"]["preinput_failure"]
    assert [row["phase"] for row in journal.history("withdraw-one")] == [
        "prepared",
        "no_transfer",
    ]


def test_attempted_unchanged_state_blocks_and_restart_never_clicks(
    tmp_path, monkeypatch
):
    one = item(1)
    before = observation(stash=(one, item(2, 130403)))
    work, journal, approved, calls = operation(
        tmp_path, monkeypatch, [before, before, before], click="after"
    )
    result = work._run("plan-one", "withdraw-one", 1)
    assert (
        result["phase"] == "blocked" and result["next_action"] == "reconcile_read_only"
    )
    assert journal.get("withdraw-one")["failure"] == {
        "stage": "input_maybe_sent",
        "error_type": "OSError",
        "reason": "lost click acknowledgement",
        "at": 200,
    }
    work._observe = lambda: deepcopy(before)
    assert work._run("plan-one", "withdraw-one", 1)["phase"] == "blocked"
    assert calls == ["click"]


def test_change_between_attempt_marker_and_final_layout_guard_never_presses(
    tmp_path, monkeypatch
):
    from conquest import protected_withdrawal as module

    one = item(1)
    before = observation(stash=(one, item(2, 130403)))
    changed = deepcopy(before)
    changed["warehouse"]["items"][0]["slot"] = 4
    work, journal, approved, calls = operation(
        tmp_path, monkeypatch, [before, changed, changed]
    )
    pressed = []

    def foreground(*args, **kwargs):
        kwargs["before_press"]()  # durable input_maybe_sent marker
        kwargs["layout_guard"]()  # final guard rejects changed slot
        pressed.append(True)  # represents native mouse-down

    monkeypatch.setattr("conquest.foreground.foreground_click", foreground)
    result = work._run("plan-one", "withdraw-one", 1)
    assert result["phase"] == "blocked" and pressed == []
    assert [row["phase"] for row in journal.history("withdraw-one")] == [
        "prepared",
        "input_maybe_sent",
        "blocked",
    ]


def test_deadline_expiring_during_final_observation_never_presses(
    tmp_path, monkeypatch
):
    from conquest.merchants import memory as merchant_memory

    one = item(1)
    before = observation(stash=(one, item(2, 130403)))
    now = [0]
    work, journal, approved, calls = operation(
        tmp_path, monkeypatch, [before, before, before]
    )
    work.clock = lambda: now[0]
    work.journal.clock = lambda: now[0]
    original = work._observe
    reads = [0]

    def observe():
        value = original()
        reads[0] += 1
        if reads[0] == 2:
            now[0] = 6
        return value

    work._observe = observe
    pressed = []

    def foreground(*args, **kwargs):
        kwargs["before_press"]()
        kwargs["layout_guard"]()
        pressed.append(True)

    monkeypatch.setattr(
        merchant_memory,
        "GuiReader",
        NS(
            for_session=lambda adapter: NS(
                session="session", base=0, context_rva=0x6966F0
            )
        ),
    )
    monkeypatch.setattr(
        merchant_memory,
        "unpack",
        lambda session, address, fmt: (123 if address == 0x6966F0 else 700,),
    )
    monkeypatch.setattr("conquest.foreground.foreground_click", foreground)
    result = work._run("plan-one", "withdraw-one", 1)
    assert result["phase"] == "blocked" and pressed == []


def test_control_revoked_after_final_observation_never_presses(tmp_path, monkeypatch):
    from conquest.merchants import memory as merchant_memory

    one = item(1)
    before = observation(stash=(one, item(2, 130403)))
    work, journal, approved, calls = operation(
        tmp_path, monkeypatch, [before, before, before]
    )
    checks = [0]

    def check():
        checks[0] += 1
        if checks[0] >= 3:
            raise ValueError("town permission revoked")

    work.town.check_input = check
    pressed = []

    def foreground(*args, **kwargs):
        kwargs["before_press"]()
        kwargs["layout_guard"]()
        pressed.append(True)

    monkeypatch.setattr(
        merchant_memory,
        "GuiReader",
        NS(
            for_session=lambda adapter: NS(
                session="session", base=0, context_rva=0x6966F0
            )
        ),
    )
    monkeypatch.setattr(
        merchant_memory,
        "unpack",
        lambda session, address, fmt: (123 if address == 0x6966F0 else 700,),
    )
    monkeypatch.setattr("conquest.foreground.foreground_click", foreground)
    result = work._run("plan-one", "withdraw-one", 1)
    assert result["phase"] == "blocked" and pressed == [] and checks[0] == 3


@pytest.mark.parametrize(
    "change,accepted",
    [
        ({"draw_position": [401, 299]}, True),
        ({"map_id": 1011}, False),
        ({"entity_id": 78}, False),
        ({"object_address": 124}, False),
        ({"name": "Other"}, False),
        ({"type_id": 1}, False),
        ({"position": [253, 221]}, False),
    ],
)
def test_warehouse_input_guard_ignores_only_npc_draw_drift(
    tmp_path, monkeypatch, change, accepted
):
    one, two = item(1), item(2, 130403)
    before = observation(stash=(one, two))
    changed = deepcopy(before)
    changed["npc"].update(change)
    after = observation(bag=(one,), stash=(two,))
    work, journal, approved, calls = operation(
        tmp_path, monkeypatch, [before, changed, after if accepted else before]
    )
    pressed = []

    def foreground(*args, **kwargs):
        kwargs["layout_guard"]()
        kwargs["before_press"]()
        pressed.append(True)
        raise OSError("read-only settlement after one click")

    monkeypatch.setattr("conquest.foreground.foreground_click", foreground)
    result = work._run("plan-one", "withdraw-one", 1)
    assert pressed == ([True] if accepted else [])
    assert result["phase"] == ("withdrawn" if accepted else "no_transfer")


@pytest.mark.parametrize(
    "change,accepted",
    [
        ({"draw_position": (401, 299)}, True),
        ({"entity_id": 78}, False),
        ({"position": (253, 221)}, False),
    ],
)
def test_double_warehouse_observation_uses_stable_npc_identity(
    tmp_path, monkeypatch, change, accepted
):
    from dataclasses import make_dataclass
    from conquest.memory_npcs import NpcObservation
    from conquest import memory_warehouse
    from conquest.merchants import delivery_bridge

    one, two = item(1), item(2, 130403)
    before = observation(stash=(one, two))

    def record(row):
        return make_dataclass("Record", [(key, object) for key in row])(**row)

    stored = [record(row) for row in before["warehouse"]["items"]]
    grid = record(
        {
            **before["grid"],
            "position": (71.0, 195.0),
            "size": (272.0, 326.0),
            "scroll": (0.0, 0.0),
        }
    )
    monkeypatch.setattr(
        memory_warehouse,
        "MemoryWarehouseReader",
        lambda adapter: NS(
            read=lambda **kw: NS(items=stored, capacity=20),
            gui=NS(read=lambda name: grid),
        ),
    )
    # _observe reads the farmer through delivery_bridge.source_memory, which
    # binds MerchantMemory at import; BUILD is not the 1078 hash, so this is
    # the MerchantMemory path.
    monkeypatch.setattr(
        delivery_bridge,
        "MerchantMemory",
        lambda observer: NS(read=lambda **kw: source(), item=None),
    )
    town = Town()
    town.inventory = NS(read=lambda: NS(items=(), equipped_ammo=None))
    npc = {**before["npc"], "position": (252, 221), "draw_position": (400, 300)}
    values = iter([NpcObservation(**npc), NpcObservation(**{**npc, **change})])

    def vendor(kind, *, stable_identity_only=False):
        assert kind == 0 and stable_identity_only is True
        return next(values)

    town.vendor = vendor
    task = ProtectedWithdrawal(
        town, journal=ProtectedWithdrawalJournal(tmp_path / "sample.sqlite3")
    )
    if accepted:
        assert task._observe()["npc"]["draw_position"] == (401, 299)
    else:
        with pytest.raises(ValueError, match="changed across"):
            task._observe()


def test_preflight_rejects_wrong_process_map_and_exact_item(tmp_path, monkeypatch):
    one = item(1)
    base = observation(stash=(one, item(2, 130403)))
    for mutate in (
        lambda value: value["source"].update(identity={**IDENTITY, "pid": 99}),
        lambda value: value["source"].update(map_id=1011),
        lambda value: value["warehouse"]["items"][0].update(gem1=1),
    ):
        changed = deepcopy(base)
        mutate(changed)
        work, journal, approved, calls = operation(tmp_path, monkeypatch, [changed])
        with pytest.raises(ValueError):
            work._run("plan-one", "withdraw-one", 1)
        assert calls == [] and journal.get("withdraw-one") is None


def test_second_item_requires_verified_first_and_adjusted_full_state(
    tmp_path, monkeypatch
):
    one, two = item(1), item(2, 130403)
    approved = plan(one, two)
    town = Town()
    journal = ProtectedWithdrawalJournal(tmp_path / "j.sqlite3", clock=lambda: 10)
    current = observation(bag=(one,), stash=(two,))
    work = ProtectedWithdrawal(town, journal=journal)
    with pytest.raises(ValueError, match="reviewed item order"):
        work._expected_preflight(approved, approved["items"][1], current)
    first = {
        "operation_id": "withdraw-one",
        "plan_id": "plan-one",
        "uid": 1,
        "phase": "prepared",
        "intent": {
            "plan_sha256": approved["plan_sha256"],
            "item": one,
            "deposit_receipt": approved["items"][0]["deposit_receipt"],
        },
    }
    journal.begin(first)
    journal.transition("withdraw-one", "prepared", "input_maybe_sent")
    journal.transition("withdraw-one", "input_maybe_sent", "withdrawn", receipt={})
    work._expected_preflight(approved, approved["items"][1], current)
    changed = deepcopy(current)
    changed["source"]["silver"] -= 1
    with pytest.raises(ValueError, match="ownership changed"):
        work._expected_preflight(approved, approved["items"][1], changed)


def test_preflight_preserves_normal_supplies_and_unselected_warehouse_stock(tmp_path):
    one, two = item(1), item(2, 130403)
    potion = item(30, 1000020, plus=0, quantity=5)
    arrows = item(31, 1050002, plus=0, quantity=5000)
    scroll = item(32, 720027, plus=0)
    approved = plan(one, two)
    approved["market_reentry"]["snapshot"] = source((potion, arrows))
    approved["market_reentry"]["warehouse"] = {
        "items": [rich(one, 0), rich(two, 1), rich(scroll, 2)],
        "capacity": 20,
    }
    town = Town()
    work = ProtectedWithdrawal(
        town, journal=ProtectedWithdrawalJournal(tmp_path / "j.sqlite3")
    )
    current = observation(bag=(potion, arrows), stash=(one, two, scroll))
    work._expected_preflight(approved, approved["items"][0], current)


def write_artifact(path, value, field):
    value[field] = canonical_artifact_digest({**value, field: ""}, field)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return {
        "path": str(path.resolve().relative_to(Path.cwd())).replace("\\", "/"),
        "sha256": value[field],
    }


def test_plan_verifies_immutable_deposit_receipt_and_chain(tmp_path, monkeypatch):
    from conquest import protected_withdrawal as module

    monkeypatch.chdir(tmp_path)
    root = Path("reports/banking/controlled-warehouse-deposits")
    monkeypatch.setattr(module, "RECEIPT_ROOT", root)
    one, two = item(1), item(2, 130403)
    approved = plan(one, two)
    previous = None
    active = {**approved["market_departure"]["market_visit"], "phase": "active"}
    active.pop("departed_at")
    active.pop("arrival_map")
    for sequence, value in enumerate((one, two), 1):
        before = {
            "farmer": source((value,)),
            "equipped_ammo": observation()["equipped_ammo"],
            "warehouse": {"items": [], "capacity": 20},
        }
        after = {
            "farmer": source(()),
            "equipped_ammo": observation()["equipped_ammo"],
            "warehouse": {"items": [rich(value)], "capacity": 20},
        }
        attempt = {
            "version": 1,
            "kind": "controlled_warehouse_deposit_attempt",
            "deposit_operation_id": "deposit-batch",
            "sequence": sequence,
            "created_at": 120 + sequence,
            "release": approved["release"],
            "farmer": approved["farmer"],
            "market_visit": active,
            "item": value,
            "before": before,
            "previous_receipt_sha256": previous,
            "attempt_sha256": "",
        }
        attempt_ref = write_artifact(
            root / f"{value['uid']}-attempt.json", attempt, "attempt_sha256"
        )
        receipt = {
            "version": 1,
            "kind": "controlled_warehouse_deposit",
            "deposit_operation_id": "deposit-batch",
            "sequence": sequence,
            "attempt": attempt_ref,
            "verified_at": 130 + sequence,
            "release": approved["release"],
            "farmer": approved["farmer"],
            "market_visit": active,
            "item": value,
            "before": before,
            "native_receipt": {
                "stored": value["uid"],
                "type_id": value["type_id"],
                "verified_in_warehouse": True,
            },
            "after": after,
            "conservation": {
                "removed_uid": value["uid"],
                "inventory_unchanged_except_uid": True,
                "silver_unchanged": True,
                "equipped_ammo_unchanged": True,
                "warehouse_added_exact_basic": True,
                "essential_uids_preserved": True,
            },
            "previous_receipt_sha256": previous,
            "receipt_sha256": "",
        }
        reference = write_artifact(
            root / f"{value['uid']}.json", receipt, "receipt_sha256"
        )
        approved["items"][sequence - 1]["deposit_receipt"] = reference
        previous = reference["sha256"]
    approved["plan_sha256"] = canonical_artifact_digest(approved, "plan_sha256")
    plan_path = Path("plan.json")
    plan_path.write_text(json.dumps(approved))
    loaded, selected = load_plan(
        "plan-one",
        "withdraw-one",
        1,
        path=plan_path,
        current_visit=approved["market_departure"]["market_visit"],
    )
    assert selected["item"] == one and loaded["plan_id"] == "plan-one"
    receipt_path = Path(approved["items"][0]["deposit_receipt"]["path"])
    tampered = json.loads(receipt_path.read_text())
    tampered["after"]["farmer"]["silver"] -= 1
    receipt_path.write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="digest changed"):
        load_plan(
            "plan-one",
            "withdraw-one",
            1,
            path=plan_path,
            current_visit=approved["market_departure"]["market_visit"],
        )
