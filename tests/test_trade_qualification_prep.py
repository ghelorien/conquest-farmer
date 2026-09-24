from types import SimpleNamespace
import copy
import os
import threading
import time

import pytest

from conquest.merchants import trade_qualification_prep as prep


def item(uid, kind=410008, *, plus=1, slot=0, **fields):
    value = {
        "uid": uid,
        "type_id": kind,
        "plus": plus,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
        "slot": slot,
        "name": "item",
    }
    value.update(fields)
    return value


def account(name, uid, inventory, *, map_id=1011, identity=None):
    identity = identity or {
        "pid": uid,
        "creation_time_100ns": uid * 100,
        "path": f"C:/game/{name}.exe",
    }
    return {
        "character": name,
        "character_uid": uid,
        "identity": identity,
        "server": "America",
        "timestamp": time.time(),
        "map_id": map_id,
        "hp": 100,
        "silver": 1000,
        "capacity": 40,
        "position": [10, 10],
        "inventory": inventory,
        "booth": [],
        "booth_open": False,
        "own_booth_uid": 0,
        "trade": None,
        "request": None,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"bound": True},
        {"plus": 2},
        {"gem1": 1},
        {"gem2": 1},
        {"quantity": 2},
        {"type_id": 410009},
        {"type_id": 1088001},
    ],
)
def test_selected_uid_is_exactly_one_ordinary_plus_one(change):
    selected = item(7)
    selected.update(change)
    with pytest.raises(ValueError, match="Qualification requires"):
        prep._selected(account("Parasite", 1, [selected]), 7)


@pytest.mark.parametrize("selected", [item(7), item(7, 720027, plus=0)])
def test_selected_accepts_one_exact_ordinary_plus_one_or_meteor_scroll(selected):
    assert prep._selected(account("Parasite", 1, [selected]), 7) is selected


@pytest.mark.parametrize(
    "change",
    [
        {"bound": True},
        {"bound": None},
        {"bound": 0},
        {"plus": 1},
        {"plus": False},
        {"gem1": 1},
        {"gem1": False},
        {"gem2": 1},
        {"gem2": False},
        {"quantity": 2},
        {"quantity": True},
        {"quantity": 1.0},
        {"type_id": 1088001},
        {"type_id": 720027.0},
        {"slot": -1},
        {"slot": True},
    ],
)
def test_selected_scroll_rejects_nonexact_attributes(change):
    selected = item(7, 720027, plus=0)
    selected.update(change)
    with pytest.raises(ValueError, match="Qualification requires"):
        prep._selected(account("Parasite", 1, [selected]), 7)


@pytest.mark.parametrize(
    "inventory", [[], [item(7, 720027, plus=0), item(7, 720027, plus=0)]]
)
def test_selected_scroll_requires_unique_carried_uid(inventory):
    with pytest.raises(ValueError, match="no longer carried"):
        prep._selected(account("Parasite", 1, inventory), 7)


@pytest.mark.parametrize(
    "change", ["loose_meteor", "extra_item", "identity", "selected_item"]
)
def test_scroll_payload_preserves_bank_and_identity_guards(change):
    selected = item(10, 720027, plus=0)
    farmer = account("Parasite", 1, [selected], map_id=1036)
    state = {
        "farmer": copy.deepcopy(farmer),
        "selected_uid": 10,
        "selected_item": copy.deepcopy(selected),
    }
    if change == "loose_meteor":
        farmer["inventory"].append(item(20, 1088001, plus=0, slot=1))
    elif change == "extra_item":
        farmer["inventory"].append(item(20, slot=1))
    elif change == "identity":
        farmer["identity"]["creation_time_100ns"] += 1
    else:
        farmer["inventory"][0] = item(10)
    with pytest.raises(
        ValueError,
        match="Loose Meteors|More than one|identity changed|Selected item changed",
    ):
        prep._payload_is_safe(farmer, state)


def test_corrupt_prep_journal_is_a_fail_closed_pending_hold(tmp_path, monkeypatch):
    path = tmp_path / "prep.json"
    path.write_text("{", encoding="utf-8")
    monkeypatch.setattr(prep, "JOURNAL", path)
    assert prep.pending() is True
    with pytest.raises(ValueError, match="unreadable"):
        prep._read()


def deposit_state(farmer, bank, moved):
    return {
        "phase": "deposit_pending",
        "farmer": farmer,
        "banked_uids": [],
        "deposit": {
            "item": moved,
            "bag_before": list(farmer["inventory"]),
            "warehouse_before": list(bank),
            "warehouse_capacity": 40,
        },
    }


def test_lost_deposit_ack_requires_exact_rich_ownership_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    selected, meteor = item(10), item(22, 1088001, plus=0, slot=1)
    before = account("Parasite", 1, [selected, meteor])
    after = account("Parasite", 1, [selected])
    state = deposit_state(before, [], meteor)

    class Loop:
        def town(self, action, **fields):
            assert (action, fields) == ("warehouse-items", {"rich": True})
            return {"items": [copy.deepcopy(meteor)], "capacity": 40}

    send = lambda body: {"farmer": copy.deepcopy(after)}
    assert prep._reconcile_deposit(Loop(), state, send=send) is True
    assert state["phase"] == "warehouse_opening" and state["deposit"] is None
    assert state["banked_uids"] == [22]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda bag, bank: bank[0].update(bound=True),
        lambda bag, bank: bank[0].update(gem1=1),
        lambda bag, bank: bank.append(item(99, 720027, plus=0)),
        lambda bag, bank: bag.append(item(98, 1088001, plus=0)),
    ],
)
def test_deposit_recovery_rejects_any_nonexact_delta(tmp_path, monkeypatch, mutation):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    selected, meteor = item(10), item(22, 1088001, plus=0, slot=1)
    before = account("Parasite", 1, [selected, meteor])
    bag, bank = [copy.deepcopy(selected)], [copy.deepcopy(meteor)]
    mutation(bag, bank)
    state = deposit_state(before, [], meteor)

    class Loop:
        def town(self, action, **fields):
            return {"items": copy.deepcopy(bank), "capacity": 40}

    with pytest.raises(ValueError, match="changed ownership unexpectedly"):
        prep._reconcile_deposit(
            Loop(),
            state,
            send=lambda body: {"farmer": account("Parasite", 1, copy.deepcopy(bag))},
        )


def test_unchanged_rich_deposit_baseline_is_the_only_retryable_state(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    selected, meteor = item(10), item(22, 1088001, plus=0, slot=1)
    before = account("Parasite", 1, [selected, meteor])
    state = deposit_state(before, [], meteor)

    class Loop:
        def town(self, action, **fields):
            return {"items": [], "capacity": 40}

    assert (
        prep._reconcile_deposit(
            Loop(), state, send=lambda body: {"farmer": copy.deepcopy(before)}
        )
        is False
    )
    assert state["phase"] == "deposit_pending"


@pytest.mark.parametrize("scroll", [False, True])
def test_banking_keeps_only_selected_item_with_rich_delta_receipts(
    tmp_path, monkeypatch, scroll
):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    selected = item(10, 720027, plus=0) if scroll else item(10)
    other = item(11, 111008, slot=1)
    meteor = item(12, 1088001, plus=0, slot=2)
    arrow = item(13, 1050002, plus=0, slot=3, quantity=5000)
    farmer = account("Parasite", 1, [selected, other, meteor, arrow])
    bank, actions = [], []

    class Loop:
        def town(self, action, **fields):
            if action == "warehouse-items":
                return {"items": copy.deepcopy(bank), "capacity": 40}
            if action == "warehouse-deposit":
                uid = fields["uid"]
                actions.append(uid)
                moved = next(
                    value for value in farmer["inventory"] if value["uid"] == uid
                )
                farmer["inventory"].remove(moved)
                bank.append(copy.deepcopy(moved))
                return {"verified_in_warehouse": True, "uid": uid}
            if action == "warehouse-money":
                return {"silver": 500, "stored_silver": 1000}
            raise AssertionError((action, fields))

    monkeypatch.setattr("conquest.banking.open_warehouse", lambda loop: None)
    monkeypatch.setattr("conquest.banking.close_warehouse", lambda loop: None)
    state = {
        "phase": "prepared",
        "selected_uid": 10,
        "selected_item": copy.deepcopy(selected),
        "farmer": copy.deepcopy(farmer),
        "route": {"outbound": {"fare": 100}},
        "banked_uids": [],
    }
    prep._bank_nonselected(
        Loop(), state, send=lambda body: {"farmer": copy.deepcopy(farmer)}
    )
    assert actions == [11, 12] and 10 not in actions
    assert state["phase"] == "banked" and state["banked_uids"] == [11, 12]


def test_withdraw_recovery_never_resubmits_an_unchanged_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    state = {
        "phase": "withdraw_submitted",
        "withdraw": {"amount": 100, "before": {"silver": 0, "stored_silver": 1000}},
    }

    class Loop:
        def town(self, action, **fields):
            return {"silver": 0, "stored_silver": 1000}

    monkeypatch.setattr(
        "conquest.banking.transfer",
        lambda *args, **fields: pytest.fail(
            "Recovery must never resubmit the withdrawal"
        ),
    )
    with pytest.raises(ValueError, match="needs attention"):
        prep._reconcile_withdraw(Loop(), state)


def test_withdraw_recovery_accepts_only_the_exact_saved_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    state = {
        "phase": "withdraw_submitted",
        "withdraw": {"amount": 100, "before": {"silver": 0, "stored_silver": 1000}},
    }

    class Loop:
        def town(self, action, **fields):
            return {"silver": 100, "stored_silver": 900}

    prep._reconcile_withdraw(Loop(), state)
    assert state["phase"] == "warehouse_opening" and state["withdraw"] is None


@pytest.mark.parametrize(
    "extra", [item(20, 1088001, plus=0), item(21, 720027, plus=0), item(22)]
)
def test_banked_payload_revalidation_rejects_new_protected_stock(extra):
    selected = item(10)
    farmer = account("Parasite", 1, [selected])
    state = {
        "farmer": copy.deepcopy(farmer),
        "selected_uid": 10,
        "selected_item": selected,
    }
    changed = copy.deepcopy(farmer)
    changed["inventory"].append(extra)
    with pytest.raises(ValueError, match="Loose Meteors|More than one"):
        prep._payload_is_safe(changed, state)


def test_resumed_banked_phase_revalidates_before_any_fare(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    selected = item(10)
    saved = account("Parasite", 1, [selected])
    changed = copy.deepcopy(saved)
    changed["inventory"].append(item(20, 1088001, plus=0))
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = {
        "phase": "banked",
        "work_window": "w",
        "character": "Spiritual",
        "origin": 1011,
        "farmer": saved,
        "merchant": merchant,
        "selected_uid": 10,
        "selected_item": selected,
        "route": {"outbound": {}},
        "control_revision": 1,
        "started_at": time.time(),
        "route_id": "bandit",
    }
    fake = SimpleNamespace(refresh=lambda: None)
    monkeypatch.setattr(prep, "PrepLoop", lambda ui, state: fake)
    monkeypatch.setattr(prep, "_reserve_window", lambda ui, key: None)
    monkeypatch.setattr(prep, "_release_window", lambda ui, key: None)
    monkeypatch.setattr(
        prep,
        "pair",
        lambda *args, **fields: (copy.deepcopy(changed), copy.deepcopy(merchant)),
    )
    with pytest.raises(ValueError, match="Loose Meteors"):
        prep._run(
            object(),
            state,
            send=lambda body: pytest.fail("No bridge/fare call is allowed"),
        )
    assert state["phase"] == "banked"


def test_fare_boundary_revalidates_payload_immediately_before_submission(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    selected = item(10)
    safe = account("Parasite", 1, [selected])
    unsafe = copy.deepcopy(safe)
    unsafe["inventory"].append(item(20, 1088001, plus=0))
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = {
        "phase": "banked",
        "work_window": "w",
        "character": "Spiritual",
        "origin": 1011,
        "farmer": safe,
        "merchant": merchant,
        "selected_uid": 10,
        "selected_item": selected,
        "route": {"outbound": {}},
        "control_revision": 1,
        "started_at": time.time(),
        "route_id": "bandit",
    }
    fake = SimpleNamespace(
        refresh=lambda: None,
        town=lambda *args, **fields: pytest.fail(
            "Fare baseline must not be saved for unsafe payload"
        ),
    )
    sequence = iter([(safe, merchant), (safe, merchant), (unsafe, merchant)])
    issued = []

    def trip(loop, route, before_submit):
        before_submit()
        issued.append("fare")

    monkeypatch.setattr(prep, "PrepLoop", lambda ui, state: fake)
    monkeypatch.setattr(prep, "_reserve_window", lambda ui, key: None)
    monkeypatch.setattr(prep, "_release_window", lambda ui, key: None)
    monkeypatch.setattr(
        prep, "pair", lambda *args, **fields: tuple(map(copy.deepcopy, next(sequence)))
    )
    monkeypatch.setattr("conquest.meteor_banking.trip", trip)
    with pytest.raises(ValueError, match="Loose Meteors"):
        prep._run(object(), state)
    assert issued == [] and state["phase"] == "banked"


def test_completion_revalidates_payload_after_actionability_read(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    selected = item(10)
    safe = account("Parasite", 1, [selected], map_id=1036)
    unsafe = account("Parasite", 1, [selected, item(20, 1088001, plus=0)], map_id=1036)
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = {
        "phase": "market",
        "work_window": "w",
        "character": "Spiritual",
        "origin": 1011,
        "farmer": safe,
        "merchant": merchant,
        "selected_uid": 10,
        "selected_item": selected,
        "route": {"outbound": {}},
        "control_revision": 1,
        "started_at": time.time(),
        "route_id": "bandit",
    }
    fake = SimpleNamespace(refresh=lambda: None, terrain=SimpleNamespace(map_id=1036))
    sequence = iter(
        [(safe, merchant), (safe, merchant), (safe, merchant), (unsafe, merchant)]
    )
    monkeypatch.setattr(prep, "PrepLoop", lambda ui, state: fake)
    monkeypatch.setattr(prep, "_reserve_window", lambda ui, key: None)
    monkeypatch.setattr(prep, "_release_window", lambda ui, key: None)
    monkeypatch.setattr(
        prep, "pair", lambda *args, **fields: tuple(map(copy.deepcopy, next(sequence)))
    )
    monkeypatch.setattr(
        "conquest.merchants.delivery_route.approach_merchant",
        lambda *args, **fields: True,
    )
    send = lambda body: {
        "farmer_position": [10, 10],
        "merchant_position": [10, 10],
        "ready": True,
        "actionable": True,
    }
    with pytest.raises(ValueError, match="Loose Meteors"):
        prep._run(object(), state, send=send)
    assert state["phase"] == "approaching" and "completed_at" not in state


def market_prep_state(farmer, merchant, phase="prepared"):
    return {
        "phase": phase,
        "work_window": "w",
        "request_id": "r",
        "character": "Spiritual",
        "origin": 1036,
        "farmer": copy.deepcopy(farmer),
        "merchant": copy.deepcopy(merchant),
        "selected_uid": 10,
        "selected_item": copy.deepcopy(farmer["inventory"][1]),
        "route": {
            "outbound": {
                "verified": True,
                "source_map": 1036,
                "destination_map": 1036,
                "fare": 0,
            }
        },
        "control_revision": 1,
        "started_at": time.time(),
        "route_id": "bandit",
        "banked_uids": [],
        "events": [],
    }


def market_payload():
    arrow = item(9, 1050002, plus=0, slot=0, quantity=5000, name="SpeedArrow")
    selected = item(10, 500008, slot=1, name="BambooBow")
    return arrow, selected


def run_market_prep(
    tmp_path, monkeypatch, state, current_farmer, current_merchant, *, pair_read=None
):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    fake = SimpleNamespace(
        refresh=lambda: None,
        terrain=SimpleNamespace(map_id=1036),
        town=lambda *args, **fields: pytest.fail(
            "Market fast path must not use town or warehouse input"
        ),
    )
    monkeypatch.setattr(prep, "PrepLoop", lambda ui, state: fake)
    monkeypatch.setattr(prep, "_reserve_window", lambda ui, key: None)
    monkeypatch.setattr(prep, "_release_window", lambda ui, key: None)
    if pair_read is None:
        pair_read = lambda *args, **fields: (
            copy.deepcopy(current_farmer),
            copy.deepcopy(current_merchant),
        )
    monkeypatch.setattr(prep, "pair", pair_read)
    monkeypatch.setattr(
        "conquest.banking.open_warehouse",
        lambda *args, **fields: pytest.fail(
            "Market fast path must not open the warehouse"
        ),
    )
    monkeypatch.setattr(
        "conquest.banking.close_warehouse",
        lambda *args, **fields: pytest.fail(
            "Market fast path must not close the warehouse"
        ),
    )
    monkeypatch.setattr(
        "conquest.banking.transfer",
        lambda *args, **fields: pytest.fail("Market fast path must not withdraw"),
    )
    monkeypatch.setattr(
        "conquest.merchants.delivery_route.approach_merchant",
        lambda *args, **fields: True,
    )
    target = {
        "farmer_position": current_farmer["position"],
        "merchant_position": current_merchant["position"],
        "ready": True,
        "actionable": True,
    }
    prep._run(object(), state, send=lambda body: copy.deepcopy(target))


@pytest.mark.parametrize("scroll", [False, True])
def test_prepared_market_fast_path_writes_proof_and_never_opens_warehouse(
    tmp_path, monkeypatch, scroll
):
    arrow, selected = market_payload()
    if scroll:
        selected = item(10, 720027, plus=0, slot=1)
    farmer = account("Parasite", 1, [arrow, selected], map_id=1036)
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = market_prep_state(farmer, merchant)
    phases = []
    save = prep._save

    def saving(current, phase=None, **fields):
        save(current, phase, **fields)
        if phase is not None:
            phases.append(phase)

    monkeypatch.setattr(prep, "_save", saving)
    run_market_prep(tmp_path, monkeypatch, state, farmer, merchant)
    assert phases[:2] == ["banked", "market"]
    assert phases[-1] == "completed" and state["phase"] == "completed"
    proof = state["no_banking_required"]
    assert proof["kind"] == "market_no_banking_required"
    assert proof["outbound"]["fare"] == 0 and proof["farmer_inventory_digest"]


@pytest.mark.parametrize(
    "failure",
    [
        "fare",
        "unverified",
        "route_extra",
        "meteor",
        "eligible_extra",
        "stash_extra",
        "farmer_trade",
        "merchant_request",
        "deposit",
        "withdraw",
        "identity",
        "stale",
        "wrong_map",
    ],
)
def test_prepared_market_fast_path_fails_closed_for_changed_or_unsafe_evidence(
    tmp_path, monkeypatch, failure
):
    arrow, selected = market_payload()
    farmer = account("Parasite", 1, [arrow, selected], map_id=1036)
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = market_prep_state(farmer, merchant)
    if failure == "fare":
        state["route"]["outbound"]["fare"] = 1
    elif failure == "unverified":
        state["route"]["outbound"]["verified"] = False
    elif failure == "route_extra":
        state["route"]["outbound"]["extra"] = True
    elif failure == "meteor":
        farmer["inventory"].append(item(20, 1088001, plus=0, slot=2))
    elif failure == "eligible_extra":
        farmer["inventory"].append(item(21, 720027, plus=0, slot=2))
    elif failure == "stash_extra":
        farmer["inventory"].append(item(22, 410008, plus=2, slot=2, bound=True))
    elif failure == "farmer_trade":
        farmer["trade"] = {"participant": "Spiritual"}
    elif failure == "merchant_request":
        merchant["request"] = {"participant": "Parasite"}
    elif failure == "deposit":
        state["deposit"] = {}
    elif failure == "withdraw":
        state["withdraw"] = {}
    elif failure == "identity":
        farmer["identity"] = {**farmer["identity"], "pid": 999}
    elif failure == "stale":
        farmer["timestamp"] -= 10
    elif failure == "wrong_map":
        farmer["map_id"] = 1002
    with pytest.raises(ValueError):
        run_market_prep(tmp_path, monkeypatch, state, farmer, merchant)
    assert state["phase"] == "prepared" and "no_banking_required" not in state


def test_prepared_market_fast_path_rejects_corrupt_saved_origin(tmp_path, monkeypatch):
    arrow, selected = market_payload()
    farmer = account("Parasite", 1, [arrow, selected], map_id=1036)
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = market_prep_state(farmer, merchant)
    state["farmer"]["map_id"] = 1011
    with pytest.raises(ValueError, match="did not originate together"):
        run_market_prep(tmp_path, monkeypatch, state, farmer, merchant)
    assert state["phase"] == "prepared" and "no_banking_required" not in state


def test_prepared_town_origin_still_uses_banking(tmp_path, monkeypatch):
    arrow, selected = market_payload()
    farmer = account("Parasite", 1, [arrow, selected], map_id=1011)
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = market_prep_state(farmer, merchant)
    state["origin"] = 1011
    state["route"]["outbound"].update(source_map=1011, fare=100)
    fake = SimpleNamespace(refresh=lambda: None)
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    monkeypatch.setattr(prep, "PrepLoop", lambda ui, state: fake)
    monkeypatch.setattr(prep, "_reserve_window", lambda ui, key: None)
    monkeypatch.setattr(prep, "_release_window", lambda ui, key: None)
    monkeypatch.setattr(
        prep,
        "pair",
        lambda *args, **fields: (copy.deepcopy(farmer), copy.deepcopy(merchant)),
    )
    called = []

    def bank(*args, **fields):
        called.append(True)
        raise RuntimeError("bank path selected")

    monkeypatch.setattr(prep, "_bank_nonselected", bank)
    with pytest.raises(RuntimeError, match="bank path selected"):
        prep._run(object(), state)
    assert called == [True] and "no_banking_required" not in state


@pytest.mark.parametrize(
    "phase",
    ["warehouse_opening", "deposit_pending", "withdraw_pending", "withdraw_submitted"],
)
def test_market_origin_never_skips_an_unresolved_banking_phase(
    tmp_path, monkeypatch, phase
):
    arrow, selected = market_payload()
    farmer = account("Parasite", 1, [arrow, selected], map_id=1036)
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = market_prep_state(farmer, merchant, phase=phase)
    fake = SimpleNamespace(refresh=lambda: None)
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    monkeypatch.setattr(prep, "PrepLoop", lambda ui, state: fake)
    monkeypatch.setattr(prep, "_reserve_window", lambda ui, key: None)
    monkeypatch.setattr(prep, "_release_window", lambda ui, key: None)
    monkeypatch.setattr(
        prep,
        "pair",
        lambda *args, **fields: (copy.deepcopy(farmer), copy.deepcopy(merchant)),
    )
    called = []

    def unresolved(*args, **fields):
        called.append(phase)
        raise RuntimeError("unresolved banking path selected")

    if phase.startswith("withdraw"):
        monkeypatch.setattr("conquest.banking.open_warehouse", unresolved)
    else:
        monkeypatch.setattr(prep, "_bank_nonselected", unresolved)
    with pytest.raises(RuntimeError, match="unresolved banking path selected"):
        prep._run(object(), state)
    assert called == [phase] and "no_banking_required" not in state


@pytest.mark.parametrize("phase", ["banked", "market"])
def test_market_fast_path_resumes_from_durable_proof_without_banking(
    tmp_path, monkeypatch, phase
):
    arrow, selected = market_payload()
    farmer = account("Parasite", 1, [arrow, selected], map_id=1036)
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = market_prep_state(farmer, merchant)
    state["no_banking_required"] = prep._new_market_no_banking_proof(
        state, farmer, merchant
    )
    state["phase"] = phase
    run_market_prep(tmp_path, monkeypatch, state, farmer, merchant)
    assert state["phase"] == "completed"


@pytest.mark.parametrize("change", ["proof", "inventory"])
def test_market_fast_path_resume_rejects_malformed_or_changed_proof(
    tmp_path, monkeypatch, change
):
    arrow, selected = market_payload()
    farmer = account("Parasite", 1, [arrow, selected], map_id=1036)
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = market_prep_state(farmer, merchant)
    state["no_banking_required"] = prep._new_market_no_banking_proof(
        state, farmer, merchant
    )
    state["phase"] = "banked"
    if change == "proof":
        del state["no_banking_required"]["withdraw_absent"]
    else:
        farmer["inventory"][0]["quantity"] -= 1
    with pytest.raises(ValueError, match="proof"):
        run_market_prep(tmp_path, monkeypatch, state, farmer, merchant)
    assert state["phase"] == "banked"


@pytest.mark.parametrize(
    ("changed_read", "change"),
    [(4, "trade"), (4, "request"), (5, "map"), (5, "position")],
)
def test_market_fast_path_revalidates_after_approach_and_before_completion(
    tmp_path, monkeypatch, changed_read, change
):
    arrow, selected = market_payload()
    farmer = account("Parasite", 1, [arrow, selected], map_id=1036)
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = market_prep_state(farmer, merchant)
    calls = [0]

    def pair_read(*args, **fields):
        calls[0] += 1
        current_farmer = copy.deepcopy(farmer)
        current_merchant = copy.deepcopy(merchant)
        if calls[0] >= changed_read:
            if change == "trade":
                current_farmer["trade"] = {"participant": "Spiritual"}
            elif change == "request":
                current_merchant["request"] = {"participant": "Parasite"}
            elif change == "map":
                current_farmer["map_id"] = 1002
            else:
                current_farmer["position"] = [23, 10]
        return current_farmer, current_merchant

    with pytest.raises(
        ValueError, match="idle trade|remain in Market|distance changed"
    ):
        run_market_prep(
            tmp_path, monkeypatch, state, farmer, merchant, pair_read=pair_read
        )
    assert state["phase"] == "approaching" and "completed_at" not in state


def test_archive_publication_is_idempotent_nonoverwriting_and_resyncs(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    state = {"phase": "completed", "selected_uid": 7}
    prep._archive(state)
    prep._archive(state)
    audit = list((tmp_path / "trade-qualification-prep-audit").glob("*.json"))
    assert len(audit) == 1
    audit[0].write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        prep._archive(state)


def test_archive_failed_publication_never_leaves_partial_final(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    monkeypatch.setattr(
        os, "link", lambda source, target: (_ for _ in ()).throw(OSError("crash"))
    )
    with pytest.raises(OSError):
        prep._archive({"phase": "completed", "selected_uid": 8})
    directory = tmp_path / "trade-qualification-prep-audit"
    assert not list(directory.glob("*.json")) and not list(directory.glob("*.tmp"))


def test_actual_authenticated_bridge_start_does_not_self_call_deadlock(
    tmp_path, monkeypatch
):
    from conquest.merchants.bridge import MerchantBridge, request
    from conquest.merchants.ui import UnifiedUI

    farmer = account("Parasite", 1, [item(10)])
    merchant = account("Spiritual", 2, [], map_id=1036)
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    monkeypatch.setattr(
        prep,
        "pair",
        lambda *args, **fields: (copy.deepcopy(farmer), copy.deepcopy(merchant)),
    )
    monkeypatch.setattr(prep, "_run", lambda *args, **fields: None)
    monkeypatch.setattr(
        prep,
        "read_json",
        lambda path: {
            "origins": {
                "1011": {
                    "outbound": {
                        "verified": True,
                        "source_map": 1011,
                        "destination_map": 1036,
                        "fare": 100,
                    }
                }
            }
        },
    )
    monkeypatch.setattr(
        "conquest.merchants.farmer_preferences.permits_new_delivery", lambda *args: None
    )
    monkeypatch.setattr("conquest.protected_withdrawal.pending", lambda: [])
    monkeypatch.setattr("conquest.merchants.delivery_operation.pending", lambda: False)
    monkeypatch.setattr("conquest.merchants.delivery_route.pending", lambda: False)
    monkeypatch.setattr("conquest.merchants.delivery_journey.pending", lambda: False)
    monkeypatch.setattr("conquest.meteor_banking.pending", lambda: False)
    monkeypatch.setattr("conquest.storage_overflow.pending", lambda: False)
    monkeypatch.setattr(
        "conquest.merchants.delivery_probe.previous_probe", lambda: None
    )
    coordinator = SimpleNamespace(
        stopped=False, manual_session_blocked=lambda target: False
    )
    app = SimpleNamespace(
        control=SimpleNamespace(
            snapshot=lambda: {"enabled": False, "paused": False, "revision": 3}
        ),
        thread=None,
        selected_route=SimpleNamespace(id="bandit"),
    )
    journal = SimpleNamespace(pending=lambda name: [])
    ui = object.__new__(UnifiedUI)
    ui.coordinator = coordinator
    ui.app = app
    ui.runtime = SimpleNamespace(journal=journal, controllers={"Spiritual": object()})
    ui.safe_to_yield = lambda: True
    ui.calibrating = set()
    ui.delivery_workers = {}
    path = tmp_path / "bridge.json"
    bridge = MerchantBridge(ui.dispatch, path=path)
    try:
        result = request(
            {
                "action": "prepare-trade-qualification",
                "character": "Spiritual",
                "selected_uid": 10,
            },
            path=path,
        )
        assert result["started"] is True and result["selected_uid"] == 10
    finally:
        bridge.close()


def recovery_ui(warehouse, money=None):
    money = money or {"silver": 1000, "stored_silver": 900, "amount": 0}

    def town_trade(body):
        if body["action"] == "warehouse-money":
            return copy.deepcopy(money)
        if body == {"action": "warehouse-items", "rich": True}:
            return {"items": copy.deepcopy(warehouse), "capacity": 40}
        raise AssertionError(body)

    observer = SimpleNamespace(lock=threading.RLock(), town_trade=town_trade)
    return SimpleNamespace(
        trade_qualification_prep_thread=None,
        delivery_probe_thread=None,
        delivery_workers={},
        app=SimpleNamespace(observer=observer),
    )


def test_digest_bound_input_free_override_recovers_after_crash(tmp_path, monkeypatch):
    import conquest.recovery_override as recovery

    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    farmer = account("Parasite", 1, [item(10)])
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = {
        "phase": "outbound_pending",
        "character": "Spiritual",
        "selected_uid": 10,
        "selected_item": farmer["inventory"][0],
        "farmer": farmer,
        "merchant": merchant,
    }
    prep._durable(state)
    ui = recovery_ui([])
    monkeypatch.setattr(
        prep,
        "pair",
        lambda *args, **fields: (copy.deepcopy(farmer), copy.deepcopy(merchant)),
    )
    preview = prep.recheck(ui)
    original = recovery._complete
    monkeypatch.setattr(
        recovery,
        "_complete",
        lambda *args, **fields: (_ for _ in ()).throw(OSError("crash")),
    )
    with pytest.raises(OSError):
        prep.operator_override(
            ui,
            operator_confirmed=True,
            confirmation_reference=preview["incident_digest"],
            incident_digest=preview["incident_digest"],
        )
    assert (tmp_path / "prep.json.override-intent.json").exists()
    monkeypatch.setattr(recovery, "_complete", original)
    recovered = prep._read()
    assert (
        recovered["phase"] == "operator_overridden"
        and recovered["replan_required"] is True
    )
    assert prep.pending() is False


def test_override_rejects_changed_fresh_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    farmer = account("Parasite", 1, [item(10)])
    merchant = account("Spiritual", 2, [], map_id=1036)
    prep._durable(
        {
            "phase": "outbound_pending",
            "character": "Spiritual",
            "selected_uid": 10,
            "selected_item": farmer["inventory"][0],
            "farmer": farmer,
            "merchant": merchant,
        }
    )
    ui = recovery_ui([])
    current = [farmer]
    monkeypatch.setattr(
        prep,
        "pair",
        lambda *args, **fields: (copy.deepcopy(current[0]), copy.deepcopy(merchant)),
    )
    preview = prep.recheck(ui)
    current[0] = account(
        "Parasite", 1, [item(10), item(99, 1050002, plus=0, quantity=5)]
    )
    with pytest.raises(ValueError, match="ownership changed"):
        prep.operator_override(
            ui,
            operator_confirmed=True,
            confirmation_reference=preview["incident_digest"],
            incident_digest=preview["incident_digest"],
        )


def test_override_digest_includes_stored_warehouse_silver(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    farmer = account("Parasite", 1, [item(10)])
    merchant = account("Spiritual", 2, [], map_id=1036)
    prep._durable(
        {
            "phase": "withdraw_submitted",
            "character": "Spiritual",
            "selected_uid": 10,
            "selected_item": farmer["inventory"][0],
            "farmer": farmer,
            "merchant": merchant,
        }
    )
    money = {"silver": 0, "stored_silver": 900, "amount": 100}
    ui = recovery_ui([], money)
    monkeypatch.setattr(
        prep,
        "pair",
        lambda *args, **fields: (copy.deepcopy(farmer), copy.deepcopy(merchant)),
    )
    preview = prep.recheck(ui)
    assert preview["fresh_evidence"]["proof"]["warehouse"]["stored_silver"] == 900
    money["stored_silver"] = 800
    with pytest.raises(ValueError, match="ownership changed"):
        prep.operator_override(
            ui,
            operator_confirmed=True,
            confirmation_reference=preview["incident_digest"],
            incident_digest=preview["incident_digest"],
        )


def test_bridge_recovery_schemas_are_exact(monkeypatch):
    from conquest.merchants.ui import UnifiedUI

    ui = object.__new__(UnifiedUI)
    monkeypatch.setattr(prep, "recheck", lambda ui: {"ok": True})
    monkeypatch.setattr(prep, "operator_override", lambda ui, **fields: fields)
    assert ui.dispatch({"action": "trade-qualification-prep-recheck"}) == {"ok": True}
    with pytest.raises(ValueError, match="Unsupported"):
        ui.dispatch(
            {
                "action": "trade-qualification-prep-override",
                "operator_confirmed": True,
                "confirmation_reference": "x",
                "incident_digest": "x",
                "extra": True,
            }
        )


def test_guard_blocks_farming_while_prep_is_nonterminal(tmp_path, monkeypatch):
    from conquest.merchants import delivery_operation

    monkeypatch.setattr(prep, "JOURNAL", tmp_path / "prep.json")
    prep._durable({"phase": "outbound_pending"})
    monkeypatch.setattr("conquest.protected_withdrawal.pending", lambda: [])
    with pytest.raises(ValueError, match="Supervised trade preparation"):
        delivery_operation.guard_protected_assets()


def projection_candidate(build="build"):
    return {
        "at": time.time(),
        "client_sha256": build,
        "target_mode": {"rva": 0x699290, "value": 19},
        "recipient": {
            "vtable_rva": 20,
            "uid_offset": 120,
            "name_offset": 164,
            "position_offset": 232,
            "draw_offset": 248,
            "draw_format": "i32",
            "name_format": "inline_utf8",
            "name_capacity": 32,
        },
        "input_qualified": False,
        "evidence_reports": ["probe.json"],
        "remaining": ["live qualification"],
    }


def projection_rig(tmp_path, monkeypatch, *, phase="market", scroll=False):
    selected = item(10, 720027, plus=0) if scroll else item(10)
    farmer = account("Parasite", 1, [selected], map_id=1036)
    merchant = account("Spiritual", 2, [], map_id=1036)
    state = {
        "phase": phase,
        "character": "Spiritual",
        "selected_uid": 10,
        "selected_item": copy.deepcopy(selected),
        "farmer": copy.deepcopy(farmer),
        "merchant": copy.deepcopy(merchant),
    }
    journal = tmp_path / "prep.json"
    candidate = tmp_path / "candidate.json"
    monkeypatch.setattr(prep, "JOURNAL", journal)
    monkeypatch.setattr(prep, "CANDIDATE", candidate)
    prep._durable(state)
    candidate.write_text(
        __import__("json").dumps(projection_candidate()), encoding="utf-8"
    )
    observer = SimpleNamespace(
        adapter=SimpleNamespace(expected_sha256="build"),
        health_layout=object(),
        character="Parasite",
        operations=SimpleNamespace(
            target=SimpleNamespace(snapshot=lambda: {"client_size": [1024, 768]})
        ),
    )
    ui = SimpleNamespace(app=SimpleNamespace(observer=observer))
    monkeypatch.setattr(
        "conquest.merchants.memory.GuiReader",
        lambda adapter: SimpleNamespace(viewport_size=lambda: [1024, 768]),
    )
    monkeypatch.setattr(
        prep,
        "pair",
        lambda *args, **fields: (copy.deepcopy(farmer), copy.deepcopy(merchant)),
    )
    actionable = {
        "actionable": True,
        "reason": "actionable",
        "recipient": {
            "point": [500, 300],
            "occupied_tiles": [[20, 20]],
            "address": 123,
            "uid": merchant["character_uid"],
            "name": merchant["character"],
            "position": list(merchant["position"]),
        },
    }
    monkeypatch.setattr(
        prep,
        "_projection_observation",
        lambda *args: (copy.deepcopy(actionable), [[20, 20]], [512, 384]),
    )
    return ui, farmer, merchant, candidate, actionable


@pytest.mark.parametrize("scroll", [False, True])
def test_prep_target_projection_does_not_require_final_delivery_qualification(
    tmp_path, monkeypatch, scroll
):
    ui, farmer, merchant, candidate, actionable = projection_rig(
        tmp_path, monkeypatch, scroll=scroll
    )
    monkeypatch.setattr(
        "conquest.merchants.farmer_trade.delivery_target_status",
        lambda *args: pytest.fail(
            "Final farmer_delivery qualification must not be consulted"
        ),
    )
    result = prep.target_projection(ui, "Spiritual")
    assert result["ready"] is True and result["point"] == [500, 300]
    assert result["farmer_position"] == farmer["position"]


def test_prep_target_projection_requires_probe_range_with_inclusive_twelve(
    tmp_path, monkeypatch
):
    ui, farmer, merchant, candidate, actionable = projection_rig(tmp_path, monkeypatch)
    farmer["position"] = [226, 209]
    merchant["position"] = [239, 213]
    result = prep.target_projection(ui, "Spiritual")
    assert result["actionable"] is True and result["ready"] is False
    assert result["reason"] == "recipient_out_of_range"

    farmer["position"] = [227, 209]
    result = prep.target_projection(ui, "Spiritual")
    assert result["actionable"] is True and result["ready"] is True


@pytest.mark.parametrize("failure", ["corrupt", "wrong_build", "schema", "stale"])
def test_prep_target_projection_rejects_bad_candidate(tmp_path, monkeypatch, failure):
    ui, farmer, merchant, candidate, actionable = projection_rig(tmp_path, monkeypatch)
    if failure == "corrupt":
        candidate.write_text("{", encoding="utf-8")
    elif failure == "wrong_build":
        candidate.write_text(
            __import__("json").dumps(projection_candidate("other")), encoding="utf-8"
        )
    else:
        value = projection_candidate()
        if failure == "stale":
            value["at"] -= prep.CANDIDATE_MAX_AGE + 1
        else:
            value["unexpected"] = True
        candidate.write_text(__import__("json").dumps(value), encoding="utf-8")
    with pytest.raises(
        ValueError, match="unreadable|build changed|schema changed|stale"
    ):
        prep.target_projection(ui, "Spiritual")


def test_prep_target_projection_rejects_candidate_that_changes_midread(
    tmp_path, monkeypatch
):
    ui, farmer, merchant, candidate, actionable = projection_rig(tmp_path, monkeypatch)
    calls = [0]

    def observation(*args):
        calls[0] += 1
        if calls[0] == 2:
            value = projection_candidate()
            value["remaining"] = ["changed evidence"]
            candidate.write_text(__import__("json").dumps(value), encoding="utf-8")
        return copy.deepcopy(actionable), [[20, 20]], [512, 384]

    monkeypatch.setattr(prep, "_projection_observation", observation)
    with pytest.raises(ValueError, match="candidate changed"):
        prep.target_projection(ui, "Spiritual")


def test_prep_target_projection_rechecks_identity_position_and_actionability(
    tmp_path, monkeypatch
):
    ui, farmer, merchant, candidate, actionable = projection_rig(tmp_path, monkeypatch)
    changed = copy.deepcopy(farmer)
    changed["position"] = [11, 10]
    pairs = iter(
        [
            (copy.deepcopy(farmer), copy.deepcopy(merchant)),
            (changed, copy.deepcopy(merchant)),
        ]
    )
    monkeypatch.setattr(prep, "pair", lambda *args, **fields: next(pairs))
    with pytest.raises(ValueError, match="moved during target projection"):
        prep.target_projection(ui, "Spiritual")

    ui, farmer, merchant, candidate, actionable = projection_rig(tmp_path, monkeypatch)
    observations = iter(
        [
            (copy.deepcopy(actionable), [[20, 20]], [512, 384]),
            (
                {**copy.deepcopy(actionable), "reason": "covered"},
                [[20, 20]],
                [512, 384],
            ),
        ]
    )
    monkeypatch.setattr(
        prep, "_projection_observation", lambda *args: next(observations)
    )
    with pytest.raises(ValueError, match="actionability changed"):
        prep.target_projection(ui, "Spiritual")

    ui, farmer, merchant, candidate, actionable = projection_rig(tmp_path, monkeypatch)
    changed = copy.deepcopy(merchant)
    changed["identity"] = {**changed["identity"], "pid": 999}
    pairs = iter(
        [
            (copy.deepcopy(farmer), copy.deepcopy(merchant)),
            (copy.deepcopy(farmer), changed),
        ]
    )
    monkeypatch.setattr(prep, "pair", lambda *args, **fields: next(pairs))
    with pytest.raises(ValueError, match="identity changed"):
        prep.target_projection(ui, "Spiritual")


@pytest.mark.parametrize(
    "occupied",
    [
        [[21, 20], [30, 30]],
        [[30, 30], [20, 20]],
        [[20, 20], [30, 30], [40, 40]],
        [],
    ],
)
def test_prep_target_projection_accepts_crowd_changes_and_returns_fresh_occupancy(
    tmp_path, monkeypatch, occupied
):
    ui, farmer, merchant, candidate, actionable = projection_rig(
        tmp_path, monkeypatch, scroll=True
    )
    first = copy.deepcopy(actionable)
    first["recipient"]["occupied_tiles"] = [[20, 20], [30, 30]]
    second = copy.deepcopy(actionable)
    second["recipient"]["occupied_tiles"] = copy.deepcopy(occupied)
    observations = iter(
        [
            (first, first["recipient"]["occupied_tiles"], [512, 384]),
            (second, occupied, [512, 384]),
        ]
    )
    monkeypatch.setattr(
        prep, "_projection_observation", lambda *args: next(observations)
    )
    result = prep.target_projection(ui, "Spiritual")
    assert result["ready"] is True
    assert result["recipient"] == second["recipient"]
    assert result["occupied_tiles"] == [farmer["position"], *occupied]


def test_prep_target_projection_absent_receiver_uses_newest_occupancy(
    tmp_path, monkeypatch
):
    ui, farmer, merchant, candidate, actionable = projection_rig(tmp_path, monkeypatch)
    observations = iter(
        [(None, [[20, 20]], [512, 384]), (None, [[21, 20], [21, 20]], [512, 384])]
    )
    monkeypatch.setattr(
        prep, "_projection_observation", lambda *args: next(observations)
    )
    result = prep.target_projection(ui, "Spiritual")
    assert result["ready"] is False and result["reason"] == "recipient_absent"
    assert result["occupied_tiles"] == [farmer["position"], [21, 20]]


@pytest.mark.parametrize(
    "change",
    [
        "address",
        "uid",
        "name",
        "position",
        "point",
        "actionable",
        "reason",
        "blocking_panel",
        "anchor",
        "receiver_disappears",
        "receiver_appears",
    ],
)
def test_prep_target_projection_still_rejects_target_or_actionability_change(
    tmp_path, monkeypatch, change
):
    ui, farmer, merchant, candidate, actionable = projection_rig(tmp_path, monkeypatch)
    first = copy.deepcopy(actionable)
    second = copy.deepcopy(actionable)
    anchor = [512, 384]
    if change in ("address", "uid"):
        second["recipient"][change] += 1
    elif change == "name":
        second["recipient"]["name"] = "Dutch"
    elif change in ("position", "point"):
        second["recipient"][change][0] += 1
    elif change == "actionable":
        second["actionable"] = False
    elif change == "reason":
        second["reason"] = "panel_occlusion"
    elif change == "blocking_panel":
        second["blocking_panel"] = "Inventory"
    elif change == "anchor":
        anchor[0] += 1
    elif change == "receiver_disappears":
        second = None
    else:
        first = None
    observations = iter([(first, [[20, 20]], [512, 384]), (second, [[21, 20]], anchor)])
    monkeypatch.setattr(
        prep, "_projection_observation", lambda *args: next(observations)
    )
    with pytest.raises(ValueError, match="actionability changed"):
        prep.target_projection(ui, "Spiritual")


@pytest.mark.parametrize("change", ["identity", "selected_item", "trade", "request"])
def test_prep_target_projection_crowd_tolerance_preserves_fresh_participant_guards(
    tmp_path, monkeypatch, change
):
    ui, farmer, merchant, candidate, actionable = projection_rig(
        tmp_path, monkeypatch, scroll=True
    )
    changed = copy.deepcopy(farmer)
    if change == "identity":
        changed["identity"]["creation_time_100ns"] += 1
    elif change == "selected_item":
        changed["inventory"][0] = item(10)
    else:
        changed[change] = {"participant": "Spiritual"}
    pairs = iter(
        [
            (copy.deepcopy(farmer), copy.deepcopy(merchant)),
            (changed, copy.deepcopy(merchant)),
        ]
    )
    monkeypatch.setattr(prep, "pair", lambda *args, **fields: next(pairs))
    with pytest.raises(
        ValueError, match="identity changed|Selected item changed|trade state changed"
    ):
        prep.target_projection(ui, "Spiritual")


def test_prep_target_projection_requires_matching_market_phase(tmp_path, monkeypatch):
    ui, farmer, merchant, candidate, actionable = projection_rig(
        tmp_path, monkeypatch, phase="banked"
    )
    with pytest.raises(ValueError, match="No matching Market"):
        prep.target_projection(ui, "Spiritual")


def test_prep_target_bridge_schema_is_strict_and_normal_target_is_unchanged(
    tmp_path, monkeypatch
):
    from conquest.merchants.ui import UnifiedUI

    ui = object.__new__(UnifiedUI)
    monkeypatch.setattr(
        prep, "target_projection", lambda ui, character: {"prep": character}
    )
    monkeypatch.setattr(
        "conquest.merchants.farmer_trade.delivery_target_status",
        lambda ui, character: {"normal": character},
    )
    assert ui.dispatch(
        {"action": "trade-qualification-prep-target", "character": "Spiritual"}
    ) == {"prep": "Spiritual"}
    assert ui.dispatch({"action": "delivery-target", "character": "Spiritual"}) == {
        "normal": "Spiritual"
    }
    with pytest.raises(ValueError, match="Unsupported"):
        ui.dispatch(
            {
                "action": "trade-qualification-prep-target",
                "character": "Spiritual",
                "grant": True,
            }
        )


def test_authenticated_bridge_exposes_only_journal_gated_prep_projection(
    tmp_path, monkeypatch
):
    from conquest.merchants.bridge import MerchantBridge, request
    from conquest.merchants.ui import UnifiedUI

    ui = object.__new__(UnifiedUI)
    monkeypatch.setattr(
        prep,
        "target_projection",
        lambda ui, character: {"character": character, "read_only": True},
    )
    path = tmp_path / "bridge.json"
    bridge = MerchantBridge(ui.dispatch, path=path)
    try:
        assert request(
            {"action": "trade-qualification-prep-target", "character": "Spiritual"},
            path=path,
        ) == {"character": "Spiritual", "read_only": True}
    finally:
        bridge.close()
