from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from conquest.character_context import state_path
from conquest.character_profiles import ProfileRegistry
from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.farmer_qualification import (
    LEGACY,
    qualification_path,
    promotion_destination,
)


@pytest.fixture(autouse=True)
def clean_context(monkeypatch):
    monkeypatch.delenv("CONQUEST_DATA_ROOT", raising=False)
    monkeypatch.delenv("CONQUEST_PROFILE_ID", raising=False)


def activate(monkeypatch, registry, profile):
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(registry.root))
    monkeypatch.setenv("CONQUEST_PROFILE_ID", profile.id)


def observer(name, build=CLIENT_SHA256):
    return NS(character=name, adapter=NS(expected_sha256=build))


def legacy_evidence(tmp_path, name="Parasite", uid=1):
    item = dict(
        uid=10, type_id=720027, plus=0, gem1=0, gem2=0, quantity=1, bound=False, slot=0
    )

    def account(character, character_uid, items):
        return dict(
            character=character,
            character_uid=character_uid,
            identity={"pid": character_uid},
            server="America",
            timestamp=100,
            map_id=1036,
            hp=100,
            silver=200,
            capacity=40,
            inventory=items,
            booth=[],
            trade=None,
            request=None,
            position=[100, 100],
        )

    before = account(name, uid, [item])
    peer = account("Spiritual", 99, [])
    after = deepcopy(before)
    after["inventory"] = []
    peer_after = deepcopy(peer)
    peer_after["inventory"] = [deepcopy(item)]
    receipt = {
        "phase": "delivery_verified",
        "verified_at": 100,
        "intent": {"farmer": before, "merchant": peer, "items": [item]},
        "farmer_after": after,
        "merchant_after": peer_after,
    }
    receipt_path = tmp_path / (name + "-receipt.json")
    receipt_path.write_text(json.dumps(receipt))
    return {
        "character": name,
        "server": "America",
        "client_sha256": CLIENT_SHA256,
        "capabilities": {"farmer_delivery": True},
        "evidence": str(receipt_path),
    }, receipt


def save_legacy(data):
    path = Path(state_path(LEGACY))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_standalone_path_stays_compatible_without_touching_files():
    assert qualification_path(observer("Parasite")) == Path(LEGACY)


def test_exact_legacy_proof_migrates_once_and_preserves_original(tmp_path, monkeypatch):
    registry = ProfileRegistry(tmp_path / "data")
    profile = registry.add("Parasite")
    registry.bind(profile.id, "Parasite", "America", 1)
    activate(monkeypatch, registry, profile)
    data, _ = legacy_evidence(tmp_path)
    old = save_legacy(data)
    original = old.read_bytes()
    path = qualification_path(observer("Parasite"))
    assert path.is_relative_to(tmp_path / "data" / "characters" / profile.id)
    migrated = json.loads(path.read_text())
    assert migrated["profile_id"] == profile.id and migrated["character_uid"] == 1
    assert migrated["evidence"] == data["evidence"] and old.read_bytes() == original
    before = path.read_bytes()
    assert (
        qualification_path(observer("Parasite")) == path and path.read_bytes() == before
    )


@pytest.mark.parametrize(
    "mismatch",
    [
        "name",
        "server",
        "build",
        "profile",
        "uid",
        "receipt_uid",
        "receipt_item",
        "receipt_phase",
        "unbound",
    ],
)
def test_legacy_migration_requires_exact_profile_build_and_asset_proof(
    tmp_path, monkeypatch, mismatch
):
    registry = ProfileRegistry(tmp_path / "data")
    profile = registry.add("Parasite")
    if mismatch != "unbound":
        registry.bind(profile.id, "Parasite", "America", 1)
    activate(monkeypatch, registry, profile)
    data, receipt = legacy_evidence(tmp_path)
    if mismatch == "name":
        data["character"] = "Other"
    if mismatch == "server":
        data["server"] = "Europe"
    if mismatch == "build":
        data["client_sha256"] = "a" * 64
    if mismatch == "profile":
        data["profile_id"] = "another-profile"
    if mismatch == "uid":
        data["character_uid"] = 2
    if mismatch == "receipt_uid":
        receipt["farmer_after"]["character_uid"] = 2
    if mismatch == "receipt_item":
        receipt["merchant_after"]["inventory"][0]["quantity"] = 2
    if mismatch == "receipt_phase":
        receipt["phase"] = "confirm_submitted"
    Path(data["evidence"]).write_text(json.dumps(receipt))
    old = save_legacy(data)
    original = old.read_bytes()
    path = qualification_path(observer("Parasite"))
    assert not path.exists() and old.read_bytes() == original


def test_switching_farmers_and_builds_resolves_distinct_paths_at_use_time(
    tmp_path, monkeypatch
):
    registry = ProfileRegistry(tmp_path / "data")
    one = registry.add("Parasite")
    two = registry.add("FreshArcher")
    registry.bind(one.id, one.name, "America", 1)
    registry.bind(two.id, two.name, "America", 2)
    activate(monkeypatch, registry, one)
    data, _ = legacy_evidence(tmp_path)
    save_legacy(data)
    first = qualification_path(observer(one.name))
    original = first.read_bytes()
    activate(monkeypatch, registry, two)
    missing = qualification_path(observer(two.name))
    assert not missing.exists()
    data, _ = legacy_evidence(tmp_path, two.name, 2)
    save_legacy(data)
    second = qualification_path(observer(two.name))
    assert first != second and first.read_bytes() == original and second.exists()
    other_build = qualification_path(observer(two.name, "a" * 64))
    assert other_build != second and not other_build.exists()


def test_existing_scoped_file_cannot_be_rebound_to_another_profile(
    tmp_path, monkeypatch
):
    registry = ProfileRegistry(tmp_path / "data")
    profile = registry.add("Parasite")
    registry.bind(profile.id, profile.name, "America", 1)
    activate(monkeypatch, registry, profile)
    data, _ = legacy_evidence(tmp_path)
    save_legacy(data)
    path = qualification_path(observer(profile.name))
    corrupted = json.loads(path.read_text())
    corrupted["profile_id"] = "wrong"
    path.write_text(json.dumps(corrupted))
    with pytest.raises(ValueError, match="different profile"):
        qualification_path(observer(profile.name))


def test_managed_context_requires_selected_matching_farmer(tmp_path, monkeypatch):
    registry = ProfileRegistry(tmp_path / "data")
    merchant = registry.add("Spiritual", role="Merchant")
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(registry.root))
    with pytest.raises(ValueError, match="Select a farmer"):
        qualification_path(observer("Parasite"))
    activate(monkeypatch, registry, merchant)
    with pytest.raises(ValueError, match="selected farmer"):
        qualification_path(observer("Spiritual"))


def test_promotion_binds_uid_and_does_not_write_machine_shared_destination(
    tmp_path, monkeypatch
):
    registry = ProfileRegistry(tmp_path / "data")
    profile = registry.add("Parasite")
    registry.bind(profile.id, profile.name, "America", 1)
    activate(monkeypatch, registry, profile)
    data, receipt = legacy_evidence(tmp_path)
    path, bound = promotion_destination(
        state_path(LEGACY), data, receipt["farmer_after"]
    )
    assert path == qualification_path(observer(profile.name), migrate=False)
    assert bound["profile_id"] == profile.id and bound["character_uid"] == 1
    assert path != Path(state_path(LEGACY)) and not path.exists()
    with pytest.raises(ValueError, match="exact farmer UID"):
        promotion_destination(
            state_path(LEGACY), data, {**receipt["farmer_after"], "character_uid": 2}
        )


def test_default_driver_path_is_resolved_after_profile_switch(tmp_path, monkeypatch):
    from conquest.merchants import farmer_trade

    registry = ProfileRegistry(tmp_path / "data")
    one = registry.add("Parasite")
    two = registry.add("FreshArcher")
    monkeypatch.setattr(
        farmer_trade, "MerchantDriver", lambda o, p, c: NS(qualification=p)
    )
    ui = NS(
        app=NS(
            observer=observer(one.name), control=NS(snapshot=lambda: {"revision": 1})
        ),
        coordinator=NS(),
    )
    activate(monkeypatch, registry, one)
    first = farmer_trade.FarmerTradeDriver(ui).driver.qualification
    activate(monkeypatch, registry, two)
    ui.app.observer = observer(two.name)
    second = farmer_trade.FarmerTradeDriver(ui).driver.qualification
    assert first != second
    assert first.is_relative_to(registry.root / "characters" / one.id)
    assert second.is_relative_to(registry.root / "characters" / two.id)


def test_short_build_filename_never_substitutes_for_full_hash(tmp_path, monkeypatch):
    registry = ProfileRegistry(tmp_path / "data")
    profile = registry.add("Parasite")
    registry.bind(profile.id, profile.name, "America", 1)
    activate(monkeypatch, registry, profile)
    data, _ = legacy_evidence(tmp_path)
    save_legacy(data)
    qualification_path(observer(profile.name))
    colliding_prefix = CLIENT_SHA256[:16] + "a" * 48
    with pytest.raises(ValueError, match="different profile or build"):
        qualification_path(observer(profile.name, colliding_prefix))
