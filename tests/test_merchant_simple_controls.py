from types import SimpleNamespace
from conquest.merchants.simple_controls import toggle, summary


def test_pause_resume_retains_refill_only_permission():
    values = {("Dutch", "enabled"): False, ("Dutch", "refill_enabled"): True}
    j = SimpleNamespace(
        get=lambda c, k, d=None: values.get((c, k), d),
        set=lambda c, k, v: values.__setitem__((c, k), v),
    )
    r = SimpleNamespace(
        journal=j,
        enabled=lambda c: j.get(c, "enabled", False),
        refill_enabled=lambda c: j.get(c, "refill_enabled", False),
        set_refill_enabled=lambda c, v: j.set(c, "refill_enabled", v),
    )
    ui = SimpleNamespace(
        runtime=r,
        pause=lambda c: j.set(c, "enabled", False),
        resume=lambda c: j.set(c, "enabled", True),
        resume_refill=lambda c: j.set(c, "refill_enabled", True),
    )
    toggle(ui, "Dutch")
    assert not r.enabled("Dutch") and not r.refill_enabled("Dutch")
    toggle(ui, "Dutch")
    assert not r.enabled("Dutch") and r.refill_enabled("Dutch")


def test_refill_only_is_active_and_global_stop_is_clear():
    s = {
        "connected": True,
        "enabled": False,
        "refill": {"enabled": True, "next_check": 1000},
    }
    assert summary(s, now=100).startswith("ACTIVE")
    assert summary(s, now=100, global_stopped=True).startswith("STOPPED")
    s["refill"]["enabled"] = False
    assert summary(s, now=100).startswith("PAUSED")


def test_uncertain_transaction_is_not_presented_as_active():
    s = {"connected": True, "enabled": True, "pending": [{"phase": "uncertain"}]}
    assert summary(s, now=100).startswith("NEEDS ATTENTION")


def test_current_owned_peer_blocker_overrides_stale_completed_refill_summary():
    s = {
        "connected": True,
        "enabled": True,
        "snapshot": {"inventory": [{"uid": 1}], "booth": [], "capacity": 40},
        "refill": {
            "enabled": True,
            "status": "booth_full",
            "pending": False,
            "next_check": 80,
        },
        "foreground_refill_1078": {
            "state": "waiting",
            "blocker": "owned_peer_observation_unavailable",
            "unavailable_peer": "Spiritual",
        },
    }
    text = summary(s, now=100)
    assert text.startswith("WAITING") and "Spiritual owned booth memory" in text
    s["foreground_refill_1078"] = None
    assert summary(s, now=100).startswith("ACTIVE")
