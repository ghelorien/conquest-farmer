"""Back2Classic (Classic_B2C) is qualified for farming only.

Failure modes this module must catch (written before the implementation):

1. A Back2Classic farmer profile is refused at attach ("America client only"),
   so a new Back2Classic character can never be farmed.
2. A profile attaches to a client on another server: an America profile to a
   Classic_B2C client, or a Back2Classic profile to a Classic_US client.
3. A Back2Classic merchant attaches. Merchant work (market prices, listing,
   sales, deliveries) is built on America market data.
4. An unqualified server (Classic_EU, Classic_Murica, Test, blank) attaches.
5. A Back2Classic farmer's first sight binds its UID under the wrong server
   (for example "America"), or a later sight with another UID rebinds it.
6. America farmers and merchants change behaviour: they must still attach
   only to Classic_US and bind under "America".
7. A Back2Classic farmer qualifies for merchant delivery.
8. The profile editor stores a label the attach check never matches (a
   case-only variant such as "back2classic"), or creates a Back2Classic
   merchant.

The end-to-end test takes a fresh PC holding an America pair and a new
Back2Classic farmer through every attach outcome above and writes
``back2classic-farming.json``; the scenario runs twice in separate roots and
must produce byte-identical artifacts.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conquest.character_profiles import ProfileRegistry, context_for

IDENTITY = {"pid": 4242, "creation_time_100ns": 99, "path": r"C:\CC\ImConquer.exe"}
BASE = 0x400000
SERVER_RVA = 0x6B7FC0
LIFE_OBJECT = 0x1234


class Client:
    """One exact-1078 client as seen through memory: server string and UID."""

    def __init__(self, server, uid):
        self.server, self.uid = server, uid
        self.identity = dict(IDENTITY)

    def read_block(self, address, size):
        assert (address, size) == (BASE + SERVER_RVA, 64)
        return self.server.ljust(64, b"\0")

    def assert_identity(self):
        return None


def install(monkeypatch):
    class Life:
        @classmethod
        def for_session(cls, session, character):
            return SimpleNamespace(
                read=lambda: SimpleNamespace(object_address=LIFE_OBJECT)
            )

    monkeypatch.setattr(
        "conquest.memory_build_layout.read_build_layout",
        lambda session: SimpleNamespace(merchant_server_rva=SERVER_RVA),
    )
    monkeypatch.setattr("conquest.memory_life.MemoryLifeReader", Life)
    monkeypatch.setattr(
        "conquest.merchants.memory.GuiReader",
        SimpleNamespace(for_session=lambda session: SimpleNamespace(base=BASE)),
    )

    def character_uid(session, base, address, *, layout=None):
        assert (base, address) == (BASE, LIFE_OBJECT)
        return session.uid

    monkeypatch.setattr("conquest.merchants.memory.character_uid", character_uid)


def attach(registry, name, server, uid):
    from conquest.client_attachment import verify_observer

    profile = registry.resolve(name)
    observer = SimpleNamespace(adapter=Client(server, uid), read_only_build=True)
    return verify_observer(context_for(profile.id, registry.root), observer)


def uid_of(registry, name):
    return registry.resolve(name).character_uid


def revision(registry):
    return registry.read()["revision"]


def fresh_pc(root):
    registry = ProfileRegistry(root)
    registry.add("Kilhiam")
    registry.add("Kalhiam", role="Merchant")
    registry.add("Suicide", "Back2Classic", "Farmer")
    return registry


def test_back2classic_farmer_attaches_and_binds_its_uid(tmp_path, monkeypatch):
    install(monkeypatch)
    registry = fresh_pc(tmp_path)
    before = revision(registry)
    evidence = attach(registry, "Suicide", b"Classic_B2C", 7101)
    assert evidence == {
        "character": "Suicide",
        "server": "Back2Classic",
        "character_uid": 7101,
    }
    assert uid_of(registry, "Suicide") == 7101 and revision(registry) == before + 1
    # A repeat sight through a reloaded context does not write again.
    assert attach(registry, "Suicide", b"Classic_B2C", 7101)["server"] == "Back2Classic"
    assert revision(registry) == before + 1


@pytest.mark.parametrize(
    "name, server, match",
    [
        ("Kilhiam", b"Classic_B2C", "does not match"),
        ("Kalhiam", b"Classic_B2C", "does not match"),
        ("Suicide", b"Classic_US", "does not match"),
        ("Suicide", b"Classic_EU", "qualified America and Back2Classic"),
        ("Suicide", b"Classic_Murica", "qualified America and Back2Classic"),
        ("Suicide", b"Test", "qualified America and Back2Classic"),
        ("Suicide", b"", "qualified America and Back2Classic"),
    ],
)
def test_cross_and_unqualified_servers_fail_closed(
    tmp_path, monkeypatch, name, server, match
):
    install(monkeypatch)
    registry = fresh_pc(tmp_path)
    before = revision(registry)
    with pytest.raises(ValueError, match=match):
        attach(registry, name, server, 7101)
    assert uid_of(registry, name) is None and revision(registry) == before


def test_back2classic_merchant_is_refused(tmp_path, monkeypatch):
    install(monkeypatch)
    registry = fresh_pc(tmp_path)
    registry.add("Mule", "Back2Classic", "Merchant")
    before = revision(registry)
    with pytest.raises(ValueError, match="farming only"):
        attach(registry, "Mule", b"Classic_B2C", 7201)
    assert uid_of(registry, "Mule") is None and revision(registry) == before


def test_back2classic_farmer_never_rebinds_a_different_uid(tmp_path, monkeypatch):
    install(monkeypatch)
    registry = fresh_pc(tmp_path)
    attach(registry, "Suicide", b"Classic_B2C", 7101)
    before = revision(registry)
    with pytest.raises(ValueError, match="UID does not match"):
        attach(registry, "Suicide", b"Classic_B2C", 7102)
    assert uid_of(registry, "Suicide") == 7101 and revision(registry) == before


def test_a_profile_gone_from_the_saved_registry_fails_closed(tmp_path, monkeypatch):
    # Removed in Manage profiles (or the data root switched) while the app
    # kept its context: a ValueError the attach handlers report, not a bare
    # StopIteration escaping them.
    install(monkeypatch)
    registry = fresh_pc(tmp_path)
    context = context_for(registry.resolve("Suicide").id, registry.root)
    monkeypatch.setattr(ProfileRegistry, "profiles", lambda self: [])
    from conquest.client_attachment import verify_observer

    observer = SimpleNamespace(adapter=Client(b"Classic_B2C", 7101), read_only_build=True)
    with pytest.raises(ValueError, match="no longer in the saved profiles"):
        verify_observer(context, observer)


@pytest.mark.parametrize("name", ["Kilhiam", "Kalhiam"])
def test_america_profiles_are_unchanged(tmp_path, monkeypatch, name):
    install(monkeypatch)
    registry = fresh_pc(tmp_path)
    evidence = attach(registry, name, b"Classic_US", 1173940)
    assert evidence == {
        "character": name,
        "server": "America",
        "character_uid": 1173940,
    }
    assert uid_of(registry, name) == 1173940


def test_back2classic_farmer_never_qualifies_for_delivery(tmp_path, monkeypatch):
    registry = fresh_pc(tmp_path)
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(registry.root))
    monkeypatch.setenv("CONQUEST_PROFILE_ID", registry.resolve("Suicide").id)
    from conquest.merchants.farmer_qualification import _context

    with pytest.raises(ValueError, match="does not belong to the selected farmer"):
        _context("Suicide", "a" * 64)


@pytest.mark.parametrize(
    "typed, role, stored",
    [
        ("Back2Classic", "Farmer", "Back2Classic"),
        (" back2classic ", "Farmer", "Back2Classic"),
        ("america", "Merchant", "America"),
        ("America", "Farmer", "America"),
        ("Somewhere Else", "Farmer", "Somewhere Else"),
    ],
)
def test_editor_stores_the_exact_qualified_label(typed, role, stored):
    from conquest.profile_editor import new_character_server

    assert new_character_server(typed, role) == stored


@pytest.mark.parametrize("typed", ["Back2Classic", "back2classic"])
def test_editor_refuses_a_back2classic_merchant(typed):
    from conquest.profile_editor import new_character_server

    with pytest.raises(ValueError, match="farming only"):
        new_character_server(typed, "Merchant")


def _outcome(registry, name, server, uid):
    start = revision(registry)
    try:
        result = {
            "outcome": "accepted",
            "evidence": attach(registry, name, server, uid),
        }
    except ValueError as error:
        result = {"outcome": "refused", "error": str(error)}
    result["uid"] = uid_of(registry, name)
    result["registry_writes"] = revision(registry) - start
    return result


def _scenario(root, monkeypatch):
    install(monkeypatch)
    registry = fresh_pc(root)
    registry.add("Mule", "Back2Classic", "Merchant")
    steps = {
        "suicide_first_sight": _outcome(registry, "Suicide", b"Classic_B2C", 7101),
        "suicide_repeat_sight": _outcome(registry, "Suicide", b"Classic_B2C", 7101),
        "suicide_other_uid": _outcome(registry, "Suicide", b"Classic_B2C", 7102),
        "suicide_on_classic_us": _outcome(registry, "Suicide", b"Classic_US", 7101),
        "suicide_on_classic_eu": _outcome(registry, "Suicide", b"Classic_EU", 7101),
        "kilhiam_on_back2classic": _outcome(
            registry, "Kilhiam", b"Classic_B2C", 1173940
        ),
        "kilhiam_on_classic_us": _outcome(registry, "Kilhiam", b"Classic_US", 1173940),
        "kalhiam_on_back2classic": _outcome(
            registry, "Kalhiam", b"Classic_B2C", 1174300
        ),
        "back2classic_merchant": _outcome(registry, "Mule", b"Classic_B2C", 7201),
    }
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(registry.root))
    monkeypatch.setenv("CONQUEST_PROFILE_ID", registry.resolve("Suicide").id)
    from conquest.merchants.farmer_qualification import _context

    try:
        _context("Suicide", "a" * 64)
        steps["suicide_delivery_qualification"] = "accepted"
    except ValueError:
        steps["suicide_delivery_qualification"] = "refused"
    path = Path(root) / "back2classic-farming.json"
    path.write_text(json.dumps(steps, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_back2classic_farming_e2e(tmp_path, monkeypatch):
    first = _scenario(tmp_path / "pc-a", monkeypatch)
    second = _scenario(tmp_path / "pc-b", monkeypatch)
    assert first.read_bytes() == second.read_bytes()
    mismatch = "Connected client does not match the selected character/server"
    unqualified = (
        "This engine supports the qualified America and Back2Classic clients only"
    )
    assert json.loads(first.read_text(encoding="utf-8")) == {
        "suicide_first_sight": {
            "outcome": "accepted",
            "evidence": {
                "character": "Suicide",
                "server": "Back2Classic",
                "character_uid": 7101,
            },
            "uid": 7101,
            "registry_writes": 1,
        },
        "suicide_repeat_sight": {
            "outcome": "accepted",
            "evidence": {
                "character": "Suicide",
                "server": "Back2Classic",
                "character_uid": 7101,
            },
            "uid": 7101,
            "registry_writes": 0,
        },
        "suicide_other_uid": {
            "outcome": "refused",
            "error": "Connected character UID does not match the profile",
            "uid": 7101,
            "registry_writes": 0,
        },
        "suicide_on_classic_us": {
            "outcome": "refused",
            "error": mismatch,
            "uid": 7101,
            "registry_writes": 0,
        },
        "suicide_on_classic_eu": {
            "outcome": "refused",
            "error": unqualified,
            "uid": 7101,
            "registry_writes": 0,
        },
        "kilhiam_on_back2classic": {
            "outcome": "refused",
            "error": mismatch,
            "uid": None,
            "registry_writes": 0,
        },
        "kilhiam_on_classic_us": {
            "outcome": "accepted",
            "evidence": {
                "character": "Kilhiam",
                "server": "America",
                "character_uid": 1173940,
            },
            "uid": 1173940,
            "registry_writes": 1,
        },
        "kalhiam_on_back2classic": {
            "outcome": "refused",
            "error": mismatch,
            "uid": None,
            "registry_writes": 0,
        },
        "back2classic_merchant": {
            "outcome": "refused",
            "error": "Back2Classic is qualified for farming only",
            "uid": None,
            "registry_writes": 0,
        },
        "suicide_delivery_qualification": "refused",
    }
