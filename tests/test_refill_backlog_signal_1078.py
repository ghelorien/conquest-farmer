"""Synthetic regression only; no game process, input or live journals."""

from copy import deepcopy
import time
from types import SimpleNamespace as NS

import pytest

from conquest.merchants import booth_listing_once_1078 as listing
from conquest.merchants import listing_capability_1078 as capability
from conquest.merchants import refill_1078
from test_merchant_listing_capability_1078 import item, make_ui, receipt  # noqa: F401


def _queue(priced, unknown):
    rows = [{"uid": 100 + i, "price": 90000 - i} for i in range(priced)]
    rows += [{"uid": 200 + i, "price": None} for i in range(unknown)]
    return rows


def _current(x, priced, unknown, booth):
    return {
        **deepcopy(x.second),
        "inventory": [item(100 + i) for i in range(priced)]
        + [item(200 + i) for i in range(unknown)],
        "booth": [item(300 + i, price=1000) for i in range(booth)],
        "timestamp": time.time(),
    }


@pytest.mark.parametrize(
    "priced,unknown,booth,expected",
    [(7, 3, 0, 7), (7, 3, 29, 3), (2, 9, 0, 2), (6, 0, 26, 6)],
)
def test_waiting_request_publishes_priced_backlog_within_free_booth_slots(
    receipt, monkeypatch, priced, unknown, booth, expected
):
    x = receipt
    capability.settle(x.j, x.key, x.first, x.second)
    current = _current(x, priced, unknown, booth)
    ui = make_ui(x.j, current)
    ui.app.control.snapshot = lambda: {"enabled": True}
    monkeypatch.setattr(
        "conquest.merchants.listing_plan_1078.plan",
        lambda *a: _queue(priced, unknown),
    )
    monkeypatch.setattr(
        "conquest.merchants.listing_handoff_1078.request_handoff",
        lambda *a: "merchant-refill:Dutch:1",
    )
    monkeypatch.setattr(
        listing, "dispatch", lambda *a, **k: pytest.fail("No input allowed")
    )
    result = refill_1078.step(ui, "Dutch", current)
    assert result["blocker"] == "waiting_farmer_handoff"
    # Unknown prices never count; only slots the booth can still accept do.
    assert result["eligible_backlog"] == expected
    assert 0 <= time.time() - result["backlog_observed_at"] < 5


def test_budget_wait_keeps_backlog_without_sending_input(receipt, monkeypatch):
    x = receipt
    capability.settle(x.j, x.key, x.first, x.second)
    current = _current(x, 2, 1, 0)
    ui = make_ui(x.j, current)
    ui.runtime.can_start_work = lambda seconds: False
    monkeypatch.setattr(
        "conquest.merchants.listing_plan_1078.plan", lambda *a: _queue(2, 1)
    )
    monkeypatch.setattr(listing, "_farmer_safe_market", lambda *a, **k: {})
    monkeypatch.setattr(listing, "_policy", lambda *a, **k: None)
    monkeypatch.setattr(listing, "_merchant_intent", lambda *a: {})
    monkeypatch.setattr(
        "conquest.input_probe.MessageTarget",
        lambda pid, hwnd: NS(snapshot=lambda: {"root_hwnd": 55}),
    )
    monkeypatch.setattr(
        listing, "dispatch", lambda *a, **k: pytest.fail("No input allowed")
    )
    result = refill_1078.step(ui, "Dutch", current)
    assert result["blocker"] == "listing_work_budget_insufficient"
    assert result["eligible_backlog"] == 2
