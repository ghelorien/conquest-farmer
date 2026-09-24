from types import SimpleNamespace as NS
import pytest
from conquest.merchants import delivery_readiness as module


@pytest.mark.parametrize(
    "missing", ["none", "farmer", "booth_panel", "credentials", "paused", "rollout"]
)
def test_diagnostics_identify_prerequisites_without_input_or_permission_changes(
    monkeypatch, missing
):
    calls = []

    def qualified(cap):
        calls.append(cap)
        if cap == missing:
            raise ValueError("Private diagnostic must not escape")

    runtime = NS(
        controllers={
            c: NS(driver=NS(require_qualified=qualified)) for c in module.CHARACTERS
        },
        enabled=lambda c: missing != "paused",
    )
    ui = NS(runtime=runtime)

    def farmer(ui):
        if missing == "farmer":
            raise ValueError("Private credential text")
        return NS(require_qualified=lambda: None)

    monkeypatch.setattr(
        module,
        "read_json",
        lambda path: {
            "enabled": missing != "rollout",
            "parity_verified": True,
            "hunting_handoffs_enabled": False,
        },
    )
    monkeypatch.setattr(
        module,
        "credential_path",
        lambda c: NS(is_file=lambda: missing != "credentials"),
    )
    result = module.describe(ui, farmer)
    assert result["qualified"] is (missing != "farmer")
    assert result["configured_prerequisites_met"] is (missing == "none")
    assert result["live_submission_checks_required"] is True
    assert result["hunting_handoffs_enabled"] is False
    assert len(calls) == len(module.RECEIVER_CAPABILITIES) * 2
    assert "Private" not in str(result)
    expected = {
        "farmer": "farmer_trade_controls_unqualified",
        "booth_panel": "Spiritual:booth_panel",
        "credentials": "Dutch:credentials_missing",
        "paused": "Dutch:trading_paused",
        "rollout": "delivery_rollout_disabled",
    }
    if missing != "none":
        assert expected[missing] in result["blockers"]


def test_disconnected_merchants_are_reported_without_creating_observers(monkeypatch):
    monkeypatch.setattr(module, "read_json", lambda p: {})
    monkeypatch.setattr(module, "credential_path", lambda c: NS(is_file=lambda: False))
    result = module.describe(
        NS(), lambda ui: (_ for _ in ()).throw(AttributeError("not attached"))
    )
    assert not result["configured_prerequisites_met"]
    for state in result["merchants"].values():
        assert not state["attached"]
        assert state["missing_qualifications"] == list(module.RECEIVER_CAPABILITIES)
