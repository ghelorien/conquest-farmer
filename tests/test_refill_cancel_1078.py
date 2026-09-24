"""Interrupted scheduled listing cleanup never replays listing or Cancel input."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from test_merchant_listing_capability_1078 import receipt, make_ui, item
from conquest.merchants import booth_listing_once_1078 as listing
from conquest.merchants import booth_listing_cancel_1078 as cancel
from conquest.merchants import refill_1078 as refill
from conquest.merchants import listing_handoff_1078 as handoff


@pytest.fixture
def pending(receipt, monkeypatch):
    x = receipt
    x.baseline["scheduled_foreground_refill"] = True
    with x.j.db() as db:
        db.execute("DELETE FROM transaction_steps WHERE stage='confirm_press'")
        db.execute("UPDATE transactions SET before_json=?", (json.dumps(x.baseline),))
    x.ui = make_ui(x.j, x.before)
    x.state = {
        **x.ui.runtime.refills["Dutch"].state(),
        "pending": True,
        "listing1078_engine": 1,
        "listing1078_request": x.request,
        "next_check": 12345,
        "last_checked": 11445,
        "listed": 2,
        "cursor": [91],
    }
    x.j.set("Dutch", "refill", x.state)
    monkeypatch.setattr(
        listing,
        "_profile",
        lambda c: SimpleNamespace(
            id="test-dutch", name="Dutch", server="America", character_uid=8
        ),
    )
    monkeypatch.setattr(refill, "require", lambda *args: None)
    x.snapshot = {
        **deepcopy(x.before),
        "closed_modal": True,
        "listing_preflight": {
            "price_modal": {
                "observed": True,
                "candidate_selected_item_uid": 91,
                "candidate_price_buffer_text": "",
                "selected_item_uid_verified": True,
                "price_buffer_binding_verified": True,
                "native_ok_cancel_handlers_verified": True,
            }
        },
    }
    return x


def test_exact_preconfirmation_modal_admission_is_read_only(pending):
    x = pending
    before_trace = x.j.trace(x.key)
    assert (
        refill.pending_cancel(x.ui.runtime, "Dutch", x.snapshot, preflight=True)
        == x.request
    )
    assert x.j.trace(x.key) == before_trace
    assert x.ui.runtime.refills["Dutch"].state() == x.state


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "uid",
        "type_id",
        "name",
        "plus",
        "gem1",
        "gem2",
        "bound",
        "quantity",
        "slot",
        "price",
        "unknown_field",
        "missing_field",
    ],
)
@pytest.mark.parametrize("collection", ["inventory", "booth"])
def test_derived_category_does_not_hide_any_native_stock_change(
    pending, collection, mutation
):
    x = pending
    x.baseline["snapshot"]["booth"] = [item(92, price=45000)]
    with x.j.db() as db:
        db.execute("UPDATE transactions SET before_json=?", (json.dumps(x.baseline),))
    x.snapshot["booth"] = deepcopy(x.baseline["snapshot"]["booth"])
    for rows in (x.snapshot["inventory"], x.snapshot["booth"]):
        for row in rows:
            row["category"] = "derived definition annotation"
    selected = x.snapshot[collection][0]
    if mutation == "unknown_field":
        selected["unknown_native_field"] = 1
    elif mutation == "missing_field":
        del selected["slot"]
    elif mutation:
        selected[mutation] = (
            "Changed"
            if mutation == "name"
            else True
            if mutation == "bound"
            else (selected[mutation] or 0) + 1
        )
    unchanged_input = deepcopy(x.snapshot)
    if mutation:
        with pytest.raises(ValueError):
            refill.pending_cancel(x.ui.runtime, "Dutch", x.snapshot)
    else:
        assert refill.pending_cancel(x.ui.runtime, "Dutch", x.snapshot) == x.request
    assert x.snapshot == unchanged_input
    assert x.ui.runtime.refills["Dutch"].state() == x.state


@pytest.mark.parametrize("closed_proof", [True, False])
def test_original_preflight_shape_requires_recorded_closed_windows(
    pending, closed_proof
):
    x = pending
    old = x.baseline["snapshot"]
    del old["trade"], old["request"]
    old.update(closed_modal=closed_proof, trade_open=False, request_open=False)
    with x.j.db() as db:
        db.execute("UPDATE transactions SET before_json=?", (json.dumps(x.baseline),))
    if closed_proof:
        assert (
            refill.pending_cancel(x.ui.runtime, "Dutch", x.snapshot, preflight=True)
            == x.request
        )
    else:
        with pytest.raises(ValueError):
            refill.pending_cancel(x.ui.runtime, "Dutch", x.snapshot, preflight=True)


@pytest.mark.parametrize(
    "mutation",
    [
        "confirm",
        "cancel",
        "worker",
        "identity",
        "stock",
        "silver",
        "price",
        "modal_closed",
        "different_uid",
        "missing_pin",
        "manual_receipt",
        "pause",
        "missing_ownership",
        "wrong_profile",
    ],
)
def test_changed_or_uncertain_admission_fails_closed(pending, mutation, monkeypatch):
    x = pending
    if mutation in ("confirm", "cancel"):
        x.j.step(x.key, mutation + "_press", "before_mouse_down", {})
    elif mutation == "worker":
        monkeypatch.setitem(
            listing.WORKERS, x.key, SimpleNamespace(is_alive=lambda: True)
        )
    elif mutation == "identity":
        x.snapshot["identity"]["creation_time_100ns"] += 1
    elif mutation == "stock":
        x.snapshot["inventory"][0]["quantity"] += 1
    elif mutation == "silver":
        x.snapshot["silver"] += 1
    elif mutation == "price":
        x.snapshot["listing_preflight"]["price_modal"][
            "candidate_price_buffer_text"
        ] = "99"
    elif mutation == "modal_closed":
        x.snapshot["listing_preflight"]["price_modal"]["observed"] = False
    elif mutation == "different_uid":
        x.snapshot["listing_preflight"]["price_modal"][
            "candidate_selected_item_uid"
        ] = 92
    elif mutation == "missing_pin":
        x.snapshot["listing_preflight"]["price_modal"][
            "native_ok_cancel_handlers_verified"
        ] = False
    elif mutation == "pause":
        x.j.set("Dutch", "refill_enabled", False)
    else:
        if mutation == "manual_receipt":
            x.baseline["scheduled_foreground_refill"] = False
        elif mutation == "wrong_profile":
            x.baseline["profile_id"] = "someone-else"
        else:
            del x.baseline["snapshot"]["silver"]
        with x.j.db() as db:
            db.execute(
                "UPDATE transactions SET before_json=?", (json.dumps(x.baseline),)
            )
    with pytest.raises(ValueError):
        refill.pending_cancel(x.ui.runtime, "Dutch", x.snapshot, preflight=True)
    assert x.ui.runtime.refills["Dutch"].state() == x.state


def test_farmer_off_and_safe_without_grant_dispatches_exact_cancel(
    pending, monkeypatch
):
    x = pending
    calls = []
    monkeypatch.setattr(
        handoff,
        "request_handoff",
        lambda *args: pytest.fail("Already-safe Farmer Off must not request a handoff"),
    )
    monkeypatch.setattr(
        cancel,
        "dispatch_cancel",
        lambda ui, key, character: (
            calls.append((ui, key, character)) or {"phase": "uncertain"}
        ),
    )
    result = refill._recover_pending(x.ui, "Dutch", x.snapshot)
    assert result == {
        "state": "listing_cancel_pending",
        "request_id": x.key,
        "phase": "uncertain",
    }
    assert calls == [(x.ui, x.key, "Dutch")]
    assert x.ui.runtime.refills["Dutch"].state() == x.state


def test_farmer_off_without_safe_yield_requests_handoff(pending, monkeypatch):
    x = pending
    x.ui.safe_to_yield = lambda: False
    monkeypatch.setattr(
        handoff, "request_handoff", lambda *args: "merchant-refill:Dutch:1"
    )
    monkeypatch.setattr(
        cancel,
        "dispatch_cancel",
        lambda *args: pytest.fail("Unsafe Farmer surface cannot dispatch Cancel"),
    )
    result = refill._recover_pending(x.ui, "Dutch", x.snapshot)
    assert result["blocker"] == "waiting_farmer_handoff"
    assert result["handoff_request_id"] == "merchant-refill:Dutch:1"
    assert x.ui.runtime.refills["Dutch"].state() == x.state


def test_refill_cancel_scope_authorizes_only_exact_cancel_and_respects_pause(pending):
    x = pending
    x.ui.grant = {
        "scope": handoff.SCOPE,
        "character": "Dutch",
        "listing_authority": {
            "mode": "refill_cancel",
            "profile_id": "test-dutch",
            "listing_request_id": x.key,
        },
    }
    assert handoff.scope_allows(x.ui, "Dutch", request_id=x.key, cleanup=True)
    assert not handoff.scope_allows(x.ui, "Dutch", request_id="other", cleanup=True)
    assert not handoff.scope_allows(x.ui, "Dutch", cleanup=True)
    assert not handoff.scope_allows(x.ui, "Dutch", request_id=x.key, scheduled=True)
    assert not handoff.scope_allows(x.ui, "Dutch", request_id=x.key)
    x.j.set("Dutch", "refill_enabled", False)
    assert not handoff.scope_allows(x.ui, "Dutch", request_id=x.key, cleanup=True)


def test_observer_reuses_cancel_dispatch_only_and_preserves_schedule(
    pending, monkeypatch
):
    x = pending
    x.ui.grant = {
        "scope": handoff.SCOPE,
        "character": "Dutch",
        "listing_authority": {
            "mode": "refill_cancel",
            "profile_id": "test-dutch",
            "listing_request_id": x.key,
        },
    }
    calls = []
    monkeypatch.setattr(
        cancel,
        "dispatch_cancel",
        lambda ui, key, character: (
            calls.append((key, character)) or {"phase": "uncertain"}
        ),
    )
    monkeypatch.setattr(
        listing, "dispatch", lambda *a, **kw: pytest.fail("No listing replay")
    )
    result = refill.step(x.ui, "Dutch", x.snapshot)
    assert result["state"] == "listing_cancel_pending"
    assert calls == [(x.key, "Dutch")]
    assert x.ui.runtime.refills["Dutch"].state() == x.state


def test_cancel_receipt_consumes_old_cursor_without_claiming_listing(pending):
    x = pending
    x.j.step(x.key, "cancel_press", "before_mouse_down", {})
    x.j.transition(
        x.key,
        "aborted",
        {
            "uid": 91,
            "cancel_verified": True,
            "stock_unchanged": True,
            "listing_submitted": False,
            "confirmation_attempted": False,
            "cancellation_attempted": True,
            "replay_allowed": False,
            "first": x.before,
            "second": deepcopy(x.before),
        },
    )
    refill._settle_cancelled(x.j, "Dutch", x.key)
    state = x.ui.runtime.refills["Dutch"].state()
    assert state == {**x.state, "listing1078_request": None}
    with pytest.raises(ValueError):
        cancel._pending(x.j, x.key, "Dutch")
