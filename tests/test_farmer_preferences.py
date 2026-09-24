import pytest
from conquest.merchants import farmer_preferences as p


def test_selected_profile_keeps_delivery_preferences_in_its_state_directory(
    tmp_path, monkeypatch
):
    from conquest.character_profiles import ProfileRegistry, context_for
    from conquest.merchants.farmer_identity import ui_character, route_character
    from types import SimpleNamespace as NS

    registry = ProfileRegistry(tmp_path)
    first = registry.add("Kilhiam")
    second = registry.add("FreshArcher")
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("CONQUEST_PROFILE_ID", first.id)
    assert ui_character(NS()) == route_character(NS()) == "Kilhiam"
    p.set_enabled("Kilhiam", False)
    assert not p.enabled()
    assert (
        context_for(first.id, tmp_path).state_dir
        / ".runtime/farmer-transfer-preferences.json"
    ).exists()
    monkeypatch.setenv("CONQUEST_PROFILE_ID", second.id)
    assert p.enabled() and ui_character(NS()) == "FreshArcher"
    p.set_enabled("FreshArcher", False)
    assert not p.enabled()
    monkeypatch.setenv("CONQUEST_PROFILE_ID", first.id)
    assert not p.enabled()


def test_preferences_are_per_farmer_and_survive_readback(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "PATH", tmp_path / "preferences.json")
    p.set_enabled("Parasite", False)
    p.set_enabled("AnotherFarmer", True)
    assert not p.enabled("Parasite") and p.enabled("AnotherFarmer")
    with pytest.raises(ValueError, match="Off"):
        p.permits_new_delivery("Parasite")
    p.set_enabled("Parasite", True)
    p.permits_new_delivery("Parasite")


def test_off_blocks_new_delivery_journey_without_contacting_merchants(
    tmp_path, monkeypatch
):
    from conquest.merchants import delivery_journey

    monkeypatch.setattr(p, "PATH", tmp_path / "preferences.json")
    p.set_enabled("Parasite", False)
    assert not delivery_journey.preflight(
        None, lambda _: pytest.fail("Off must not start work")
    )


def test_off_blocks_new_native_submission_even_with_rollout_enabled(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from conquest.merchants import delivery_operation as operation

    monkeypatch.setattr(p, "PATH", tmp_path / "preferences.json")
    p.set_enabled("Parasite", False)
    monkeypatch.setattr(operation, "JOURNAL", tmp_path / "source.sqlite3")
    monkeypatch.setattr(
        operation, "read_json", lambda _: dict(enabled=True, parity_verified=True)
    )
    monkeypatch.setattr(
        operation,
        "FarmerTradeDriver",
        lambda *_: pytest.fail("Off must prevent native input"),
    )
    with pytest.raises(ValueError, match="Off"):
        operation.dispatch(
            SimpleNamespace(),
            {
                "action": "delivery-start",
                "request_id": "new",
                "character": "Spiritual",
                "uids": [10],
            },
        )


def test_another_farmers_off_setting_gates_its_ui_and_route(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from conquest.merchants import delivery_operation as operation, delivery_journey

    monkeypatch.setattr(p, "PATH", tmp_path / "preferences.json")
    p.set_enabled("Parasite", True)
    p.set_enabled("AnotherFarmer", False)
    monkeypatch.setattr(operation, "JOURNAL", tmp_path / "source.sqlite3")
    monkeypatch.setattr(
        operation,
        "FarmerTradeDriver",
        lambda *_: pytest.fail("Wrong farmer preference"),
    )
    ui = NS(app=NS(transfer_character="AnotherFarmer"))
    with pytest.raises(ValueError, match="Off for AnotherFarmer"):
        operation.dispatch(
            ui,
            {
                "action": "delivery-start",
                "request_id": "new",
                "character": "Spiritual",
                "uids": [10],
            },
        )
    assert not delivery_journey.preflight(
        NS(character="AnotherFarmer"), lambda _: pytest.fail("Off must prevent travel")
    )
    assert p.enabled("Parasite")
