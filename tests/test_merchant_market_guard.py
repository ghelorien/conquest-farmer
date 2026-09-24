from types import SimpleNamespace as NS
import pytest
from conquest.merchants.market_guard import MarketGuard
from conquest.merchants.journal import Journal


def setup(tmp_path):
    journal = Journal(tmp_path / "journal.db")
    runtime = NS(
        journal=journal,
        connect_cancel={},
        enable=lambda c, v: journal.set(c, "enabled", v),
        set_refill_enabled=lambda c, v: journal.set(c, "refill_enabled", v),
    )
    observer = NS(
        adapter=NS(
            identity={"pid": 7, "creation_time_100ns": 99}, assert_identity=lambda: None
        ),
        health_layout=None,
    )
    return runtime, observer, MarketGuard(runtime)


@pytest.mark.parametrize(
    "map_id,dead,expected",
    [
        (1036, False, False),
        (1002, False, True),
        (1011, False, True),
        (1036, True, True),
    ],
)
def test_market_only_even_when_paused_and_pending(tmp_path, map_id, dead, expected):
    r, o, g = setup(tmp_path)
    calls = []
    r.journal.begin("pending", "Dutch", "delivery", {})
    g.check(
        "Dutch",
        o,
        read=lambda *a: NS(map_id=map_id, dead_candidate=dead),
        close=lambda i: calls.append(i) or True,
    )
    assert not calls
    assert bool(r.journal.get("Dutch", "market_safety")) == expected
    assert r.journal.pending("Dutch")
    if expected:
        assert r.journal.get("Dutch", "market_safety")["action"] == "pause_only"
        assert r.journal.get("Dutch", "connect_hold") is True
        assert r.journal.get("Dutch", "enabled") is False
        assert r.journal.get("Dutch", "refill_enabled") is False


def test_unreadable_location_expires_after_two_seconds(tmp_path):
    r, o, g = setup(tmp_path)
    calls = []

    def missing(*a):
        raise ValueError("unavailable")

    for t in (10, 11.9):
        g.check(
            "Dutch",
            o,
            clock=lambda: t,
            read=missing,
            close=lambda i: calls.append(i) or True,
        )
    assert not calls
    g.check(
        "Dutch",
        o,
        clock=lambda: 12,
        read=missing,
        close=lambda i: calls.append(i) or True,
    )
    assert not calls
    assert r.journal.get("Dutch", "market_safety")["action"] == "pause_only"


def test_emergency_never_calls_disconnect_even_if_available(tmp_path):
    r, o, g = setup(tmp_path)

    def forbidden(i):
        raise AssertionError("Never disconnect without user approval")

    g.check(
        "Dutch",
        o,
        read=lambda *a: NS(map_id=1002, dead_candidate=False),
        close=forbidden,
    )
    assert r.journal.get("Dutch", "market_safety")["disconnected"] is False
    assert "kept connected" in r.journal.get("Dutch", "attention")["note"]
