import pytest

# The historical merchants of the original PC.  They are test data only: the
# application itself has no built-in merchant names (no registry => none).
LEGACY_TEST_MERCHANTS = ("Spiritual", "Dutch")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_merchant_roster: run without the explicit legacy test merchant roster",
    )


@pytest.fixture(scope="session")
def legacy_roster_registry(tmp_path_factory):
    """A real ProfileRegistry holding the legacy test merchants."""
    from conquest.character_profiles import ProfileRegistry
    from conquest.managed_security import provision_new

    root = tmp_path_factory.mktemp("legacy-merchant-roster")
    provision_new(root, directory=True)
    registry = ProfileRegistry(root)
    for name in LEGACY_TEST_MERCHANTS:
        registry.add(name, role="Merchant")
    return registry


@pytest.fixture(autouse=True)
def legacy_merchant_roster(request, monkeypatch):
    """Engines exercised without CONQUEST_DATA_ROOT see the registry roster above.

    Production returns no merchants when no profile registry is configured.
    Many older engine tests address merchants by name without a registry, so
    they receive this explicit roster (read from a real ProfileRegistry)
    instead of a hard-coded application fallback.  Tests that activate their
    own registry are unaffected: the roster only applies without one.
    """
    if request.node.get_closest_marker("no_merchant_roster"):
        return
    registry = request.getfixturevalue("legacy_roster_registry")
    names = tuple(p.name for p in registry.profiles() if p.role == "Merchant")
    from conquest import character_context

    monkeypatch.setattr(character_context, "_unregistered_merchants", lambda: names)


@pytest.fixture(autouse=True)
def isolate_live_session_plan(tmp_path, monkeypatch):
    monkeypatch.delenv("CONQUEST_APP_ROOT", raising=False)
    monkeypatch.delenv("CONQUEST_RELEASE_MANIFEST_SHA256", raising=False)
    # pytest's Windows tempfile ACL uses OWNER RIGHTS. Real managed roots now
    # explicitly grant TokenUser access, including to normal-token children.
    # This directory is fresh, test-owned, and contains no user state.
    from conquest.managed_security import provision_new

    provision_new(tmp_path, directory=True)
    from conquest.merchants import handoff

    monkeypatch.setattr(handoff, "POLICY", tmp_path / "merchant-deliveries.json")
    from conquest.merchants import delivery_route

    monkeypatch.setattr(delivery_route, "POLICY", tmp_path / "merchant-deliveries.json")
    monkeypatch.setattr(delivery_route, "STATE", tmp_path / "merchant-route.json")
    from conquest.merchants import delivery_journey

    monkeypatch.setattr(delivery_journey, "JOURNAL", tmp_path / "merchant-journey.json")
    from conquest.merchants import coordination

    monkeypatch.setattr(coordination, "INPUT_LOCK", tmp_path / "merchant-input.lock")
    # Tests must neither inherit nor clear the user's running overnight plan.
    from conquest import session_plan

    monkeypatch.setattr(session_plan, "PLAN", tmp_path / "session-plan.json")
    monkeypatch.setattr(session_plan, "CIRCUIT", tmp_path / "equipment-circuit.json")
    from conquest import safe_reload

    monkeypatch.setattr(safe_reload, "RESUME", tmp_path / "reload-resume.json")
    from conquest import banking
    from conquest import merchant_loop_acceptance

    monkeypatch.setattr(
        merchant_loop_acceptance, "STATE", tmp_path / "farmer-loop-acceptance.sqlite3"
    )
    monkeypatch.setattr(
        merchant_loop_acceptance, "PICKUPS", tmp_path / "acceptance-pickups.jsonl"
    )
    monkeypatch.setattr(banking, "CONFIG", tmp_path / "banking.json")
    monkeypatch.setattr(banking, "STATUS", tmp_path / "bank-status.json")
    monkeypatch.setattr(banking, "LEDGER", tmp_path / "bank-transfers.jsonl")
    from conquest import return_scroll
    from conquest import runback_monitor

    monkeypatch.setattr(runback_monitor, "OUTPUT", tmp_path / "runbacks")
    monkeypatch.setattr(return_scroll, "POLICY", tmp_path / "return-scroll.json")
    monkeypatch.setattr(return_scroll, "STATUS", tmp_path / "return-scroll-status.json")
    from conquest import storage_halt, storage_overflow, meteor_banking

    monkeypatch.setattr(storage_halt, "HALT", tmp_path / "storage-halt.json")
    monkeypatch.setattr(storage_overflow, "JOURNAL", tmp_path / "overflow.json")
    monkeypatch.setattr(storage_overflow, "POLICY", tmp_path / "meteor-banking.json")
    monkeypatch.setattr(meteor_banking, "POLICY", tmp_path / "meteor-banking.json")
    monkeypatch.setattr(
        meteor_banking, "JOURNAL", tmp_path / "meteor-consolidation.json"
    )
