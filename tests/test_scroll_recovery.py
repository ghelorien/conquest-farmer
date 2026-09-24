from types import SimpleNamespace as NS

import pytest

from conquest import protected_withdrawal as protected, route_controller
from conquest.discord_notify import read_json, write_json
from conquest.merchants import delivery_journey as journey
from conquest.scroll_withdrawal import ScrollWithdrawal


@pytest.fixture
def held_scroll(tmp_path, monkeypatch):
    path = tmp_path / "reports/banking/protected-withdrawals.sqlite3"
    journey_path = tmp_path / "reports/banking/merchant-journey.json"
    monkeypatch.setattr(protected, "JOURNAL", path)
    monkeypatch.setattr(journey, "JOURNAL", journey_path)
    journal = protected.ProtectedWithdrawalJournal(path)
    operation = {"uid": 99, "operation_id": "journey-scroll:crashed"}
    item = {
        "uid": 99,
        "type_id": 720027,
        "plus": 0,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
    }
    journal.begin(
        {
            **operation,
            "plan_id": ScrollWithdrawal.plan_id(operation["operation_id"]),
            "phase": "prepared",
            "intent": {
                "item": item,
                "deposit_receipt": {
                    "kind": "fresh_stored_meteor_scroll",
                    "farmer_profile_id": "farmer-uuid",
                },
            },
        }
    )
    journal.transition(operation["operation_id"], "prepared", "input_maybe_sent")
    write_json(
        journey_path,
        {
            "phase": "market",
            "origin": 1036,
            "market_only": True,
            "route": {},
            "stored_scroll_uid": 99,
            "scroll_withdrawal": operation,
            "receipts": [],
        },
    )
    return NS(
        journal=journal,
        operation=operation,
        item=item,
        root=tmp_path,
        path=journey_path,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state: state.update(phase="return_pending"),
        lambda state: state["scroll_withdrawal"].update(uid=100),
        lambda state: state["scroll_withdrawal"].update(operation_id="other"),
        lambda state: state.update(scroll_preparation_done=True),
    ],
)
def test_recovery_exception_rejects_mismatched_journey(held_scroll, mutation):
    locks = protected.pending()
    assert journey.matching_scroll_recovery(locks)
    state = read_json(held_scroll.path)
    mutation(state)
    write_json(held_scroll.path, state)
    assert not journey.matching_scroll_recovery(locks)


def test_recovery_exception_rejects_other_kind_profile_or_multiple_holds(
    held_scroll, monkeypatch
):
    locks = protected.pending()
    assert not journey.matching_scroll_recovery(locks + locks)
    monkeypatch.setattr(
        "conquest.character_context.current",
        lambda: NS(profile=NS(id="another-profile")),
    )
    assert not journey.matching_scroll_recovery(locks)
    monkeypatch.setattr("conquest.character_context.current", lambda: None)
    locks[0]["intent"]["deposit_receipt"]["kind"] = "equipment"
    assert not journey.matching_scroll_recovery(locks)


def test_matching_scroll_hold_can_launch_only_a_reconciling_route(
    held_scroll, monkeypatch
):
    root = held_scroll.root
    for name in ("src/conquest", "profiles/routes", "scripts"):
        (root / name).mkdir(parents=True)
    (root / "pyproject.toml").touch()
    (root / "scripts/run_overnight.py").touch()
    calls = []
    monkeypatch.setattr(
        route_controller.subprocess,
        "Popen",
        lambda *a, **kw: calls.append((a, kw)) or NS(pid=2),
    )
    monkeypatch.setattr(
        "conquest.routes.RouteLibrary", lambda path: NS(load=lambda route: None)
    )
    assert route_controller.ensure_running("bandit", root=root)
    assert len(calls) == 1
    state = read_json(held_scroll.path)
    state["scroll_withdrawal"]["uid"] = 100
    write_json(held_scroll.path, state)
    assert not route_controller.ensure_running("bandit", root=root)
    assert len(calls) == 1


def test_route_start_reconciles_exact_hold_before_guard_or_any_stop_input(
    held_scroll, monkeypatch
):
    from conquest.overnight import OvernightLoop
    from conquest.merchants import delivery_operation

    events = []
    loop = OvernightLoop.__new__(OvernightLoop)
    receipt = {
        "item": held_scroll.item,
        "verified_in_inventory": True,
        "verified_absent_from_warehouse": True,
    }

    def town(action, **fields):
        assert (
            action == "warehouse-reconcile-scroll" and fields == held_scroll.operation
        )
        events.append("read_only_reconcile")
        held_scroll.journal.transition(
            fields["operation_id"], "input_maybe_sent", "withdrawn", receipt=receipt
        )
        return {**fields, "phase": "withdrawn", "receipt": receipt}

    loop.town = town

    def guard():
        assert not protected.pending()
        events.append("asset_guard")

    monkeypatch.setattr(delivery_operation, "guard_protected_assets", guard)

    def stop():
        events.append("first_stop_input")
        raise RuntimeError("stop test here")

    loop.stop_farm = stop
    with pytest.raises(RuntimeError, match="stop test here"):
        loop._run_route()
    assert events == ["read_only_reconcile", "asset_guard", "first_stop_input"]
    assert read_json(held_scroll.path)["scroll_preparation_done"]


def test_unresolved_recovery_read_never_reaches_any_route_input(held_scroll):
    from conquest.overnight import OvernightLoop

    loop = OvernightLoop.__new__(OvernightLoop)
    events = []

    def town(action, **fields):
        events.append(action)
        return {**fields, "phase": "blocked", "receipt": None}

    loop.town = town
    loop.stop_farm = lambda: pytest.fail("unresolved recovery issued stop/input")
    loop.living = lambda: pytest.fail("unresolved recovery began normal route")
    with pytest.raises(ValueError, match="read-only reconciliation"):
        loop._run_route()
    assert events == ["warehouse-reconcile-scroll"]
