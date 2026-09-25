"""A merchant profile without a UID records it on its first verified 1078 sight.

Failure modes this module must catch (written before the implementation):

1. A fresh PC's merchant (any name, no recorded UID) never gets one, so every
   1078 action gated on ``profile_uid_verified`` / a UID-pinned profile stays
   blocked forever.
2. Binding overwrites an existing, different UID (another character with the
   same name and server): it must fail closed and leave the registry alone.
3. A character whose in-memory name differs from the profile (including a
   case-only difference) or whose server differs is bound.
4. A login-screen client (no actor in memory, pinned-process rebind) records
   a UID.
5. An unverified snapshot records a UID: discovery and ownership disagree on
   the UID, the process identity changed, the ownership read fails, or the
   observer is not the read-only exact-build observer.
6. The UID is written but the live context is not reloaded: the attached
   observer keeps accepting any UID, or the observation still reports
   ``profile_uid_verified`` False.
7. A concurrent profile edit (another app bound a different UID between the
   observation and the write) is silently accepted or overwritten.
8. Each later verified sight rewrites the registry (revision churn) even though
   the UID is already recorded.
9. An already pinned profile changes behaviour: a matching UID must still be
   verified and a different UID must still be refused without any write.

The end-to-end test takes a fresh PC with arbitrary merchant names through
first sight (bridge observation and runtime attachment), a repeat sight and a
mismatching sight, and writes ``merchant-uid-first-sight.json``; the scenario
runs twice in separate roots and must produce byte-identical artifacts.
"""

from collections import defaultdict
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from conquest.character_profiles import ProfileRegistry

IDENTITY = {"pid": 4242, "creation_time_100ns": 99, "path": r"C:\CC\Conquer.exe"}


def fresh_pc(root, *, pinned=None):
    registry = ProfileRegistry(root)
    farmer = registry.add("Varric")
    registry.add("Kalhiam", role="Merchant", character_uid=pinned)
    registry.add("Brix", role="Merchant")
    return registry, farmer


def activate(monkeypatch, registry, profile):
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(registry.root))
    monkeypatch.setenv("CONQUEST_PROFILE_ID", profile.id)


def uid_of(registry, name):
    return registry.resolve(name, role="Merchant", server="America").character_uid


def revision(registry):
    return registry.read()["revision"]


def ownership(name, uid, *, identity=IDENTITY, server="America", capacity=40):
    return {
        "character": name,
        "character_uid": uid,
        "identity": dict(identity),
        "server": server,
        "timestamp": 1.0,
        "map_id": 1036,
        "position": [180, 190],
        "hp": 500,
        "silver": 10,
        "capacity": capacity,
        "inventory": [],
        "booth": [],
        "own_booth_uid": 77,
        "booth_open": True,
        "health": {"max_hp_candidate": 500},
        "trade": None,
        "request": None,
    }


class FakeSession:
    def __init__(self, pid, sha256):
        self.identity = dict(IDENTITY)
        self.expected_sha256 = sha256

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def assert_identity(self):
        return None

    def read(self, address, size):
        raise AssertionError("tests never read process memory")


class World:
    """One exact-1078 client as seen through memory: actor and ownership."""

    def __init__(self, name, uid, *, server=b"Classic_US", snapshot=None):
        self.actor = (name, uid, server)
        self.snapshot = snapshot or ownership(name, uid)
        self.before_ownership = None

    def install(self, monkeypatch):
        from conquest.merchants import observe_1078, reader_1078

        def read_manual_ownership():
            if self.before_ownership:
                self.before_ownership()
            return json.loads(json.dumps(self.snapshot))

        reader = SimpleNamespace(read_manual_ownership=read_manual_ownership)
        monkeypatch.setattr(observe_1078, "MemorySession", FakeSession)
        monkeypatch.setattr("conquest.memory.MemorySession", FakeSession)
        monkeypatch.setattr(observe_1078, "_actor_identity", lambda s: self.actor)
        monkeypatch.setattr(observe_1078, "open_read_only_1078", lambda s, n: reader)
        monkeypatch.setattr(reader_1078, "open_read_only_1078", lambda s, n: reader)
        monkeypatch.setattr(
            observe_1078,
            "inventory_reader_layouts",
            lambda s: (None, SimpleNamespace(capacity=self.snapshot["capacity"])),
        )


def bridge_observe(name):
    from conquest.merchants.observe_1078 import observe

    runtime = SimpleNamespace(
        catalog=SimpleNamespace(identities=lambda: [dict(IDENTITY)])
    )
    return observe(runtime, name)


class Observer:
    """The read-only exact-build observer shape, with its real ownership pin."""

    merchant_observation_only = True

    def __init__(self, client, character, context):
        from conquest.merchants.read_only_observer import ReadOnlyMerchantObserver

        self.lock = threading.RLock()
        self.character = character
        self.character_context = context
        self.hwnd = client.hwnd
        self.session = FakeSession(0, "")
        self.adapter = SimpleNamespace(identity=dict(client.identity))
        self.closed = False
        self._read = ReadOnlyMerchantObserver.read_ownership

    def read_ownership(self):
        return self._read(self)

    def close(self):
        self.closed = True


def attach(monkeypatch, name, *, login=False, observer_type=Observer):
    from conquest.character_context import merchant_context, resolve_merchant
    from conquest.client_attachment import AttachmentStatus
    from conquest.merchants.runtime import MerchantRuntime

    character = resolve_merchant(name)
    client = SimpleNamespace(hwnd=5150, identity=dict(IDENTITY))
    made, logins, controllers = [], [], []

    def factory(client, character):
        observer = observer_type(client, character, merchant_context(character))
        made.append(observer)
        return observer

    monkeypatch.setattr("conquest.reconnect.login_screen", lambda hwnd: login)
    monkeypatch.setattr(
        "conquest.merchants.return_1078.pinned_identity",
        lambda runtime, character: dict(IDENTITY),
    )
    monkeypatch.setattr(
        "conquest.input_probe.MessageTarget", lambda pid, hwnd: ("target", pid, hwnd)
    )
    fake = SimpleNamespace(
        attachments=defaultdict(AttachmentStatus),
        discovery_lock=threading.Lock(),
        lock=threading.RLock(),
        observers={},
        latest={},
        merchant_windows=lambda: [client],
        observer_factory=factory,
        _bind_controller_1078=lambda c, o: controllers.append(str(c)),
        _attach_login_1078=lambda *a: logins.append(a) or "login-rebind",
    )
    result = MerchantRuntime.attach_observation_1078(fake, character)
    return SimpleNamespace(
        runtime=fake,
        result=result,
        made=made,
        logins=logins,
        controllers=controllers,
        character=character,
    )


def test_bridge_observation_binds_a_fresh_merchant_once(tmp_path, monkeypatch):
    registry, farmer = fresh_pc(tmp_path)
    activate(monkeypatch, registry, farmer)
    World("Kalhiam", 7001).install(monkeypatch)
    assert uid_of(registry, "Kalhiam") is None
    before = revision(registry)
    observed = bridge_observe("Kalhiam")
    assert observed["profile_uid_verified"] is True
    assert observed["character_uid"] == 7001
    assert uid_of(registry, "Kalhiam") == 7001 and revision(registry) == before + 1
    # A repeat verified sight is a no-op for the registry.
    assert bridge_observe("Kalhiam")["profile_uid_verified"] is True
    assert revision(registry) == before + 1
    assert uid_of(registry, "Brix") is None


def test_bridge_observation_never_rebinds_a_different_uid(tmp_path, monkeypatch):
    registry, farmer = fresh_pc(tmp_path, pinned=7001)
    activate(monkeypatch, registry, farmer)
    World("Kalhiam", 7001).install(monkeypatch)
    assert bridge_observe("Kalhiam")["profile_uid_verified"] is True
    before = revision(registry)
    World("Kalhiam", 8002).install(monkeypatch)
    with pytest.raises(ValueError, match="differs"):
        bridge_observe("Kalhiam")
    assert uid_of(registry, "Kalhiam") == 7001 and revision(registry) == before


@pytest.mark.parametrize(
    "case",
    [
        "case_only_name",
        "other_name",
        "other_server",
        "snapshot_uid_differs",
        "snapshot_name_differs",
        "snapshot_server_differs",
        "process_changed",
        "capacity_changed",
    ],
)
def test_bridge_observation_never_binds_unverified_evidence(
    tmp_path, monkeypatch, case
):
    registry, farmer = fresh_pc(tmp_path)
    activate(monkeypatch, registry, farmer)
    world = World("Kalhiam", 7001)
    if case == "case_only_name":
        world.actor = ("kalhiam", 7001, b"Classic_US")
    elif case == "other_name":
        world.actor = ("Brix", 7001, b"Classic_US")
    elif case == "other_server":
        world.actor = ("Kalhiam", 7001, b"Classic_EU")
    elif case == "snapshot_uid_differs":
        world.snapshot["character_uid"] = 7002
    elif case == "snapshot_name_differs":
        world.snapshot["character"] = "kalhiam"
    elif case == "snapshot_server_differs":
        world.snapshot["server"] = "Europe"
    elif case == "process_changed":
        world.snapshot["identity"] = {**IDENTITY, "pid": 1}
    else:
        world.snapshot["capacity"] = 41
        world.install(monkeypatch)
        monkeypatch.setattr(
            "conquest.merchants.observe_1078.inventory_reader_layouts",
            lambda s: (None, SimpleNamespace(capacity=40)),
        )
    if case != "capacity_changed":
        world.install(monkeypatch)
    before = revision(registry)
    with pytest.raises(ValueError):
        bridge_observe("Kalhiam")
    assert uid_of(registry, "Kalhiam") is None and revision(registry) == before


def test_concurrent_different_binding_fails_closed(tmp_path, monkeypatch):
    registry, farmer = fresh_pc(tmp_path)
    activate(monkeypatch, registry, farmer)
    world = World("Kalhiam", 7001)
    kalhiam = registry.resolve("Kalhiam")
    # Another app records a different UID while this observation is running.
    world.before_ownership = lambda: registry.bind(
        kalhiam.id, "Kalhiam", "America", 9999
    )
    world.install(monkeypatch)
    with pytest.raises(ValueError):
        bridge_observe("Kalhiam")
    assert uid_of(registry, "Kalhiam") == 9999


def test_runtime_attachment_binds_and_pins_the_live_observer(tmp_path, monkeypatch):
    registry, farmer = fresh_pc(tmp_path)
    activate(monkeypatch, registry, farmer)
    world = World("Brix", 7002)
    world.install(monkeypatch)
    attached = attach(monkeypatch, "Brix")
    assert uid_of(registry, "Brix") == 7002
    observer = attached.runtime.observers[attached.character]
    assert observer.character_context.profile.character_uid == 7002
    assert attached.controllers == ["Brix"] and not observer.closed
    # The reloaded context pins the UID for every later ownership read.
    world.snapshot["character_uid"] = 7003
    with pytest.raises(ValueError, match="differs from the configured profile"):
        observer.read_ownership()


def test_runtime_attachment_mismatch_fails_closed_without_writes(tmp_path, monkeypatch):
    registry, farmer = fresh_pc(tmp_path, pinned=7001)
    activate(monkeypatch, registry, farmer)
    world = World("Kalhiam", 7001, snapshot=ownership("Kalhiam", 8002))
    world.install(monkeypatch)
    before = revision(registry)
    with pytest.raises(ValueError, match="differs from the configured profile"):
        attach(monkeypatch, "Kalhiam")
    assert uid_of(registry, "Kalhiam") == 7001 and revision(registry) == before


@pytest.mark.parametrize("case", ["snapshot_uid_differs", "snapshot_name_differs"])
def test_runtime_attachment_never_binds_unverified_snapshot(
    tmp_path, monkeypatch, case
):
    registry, farmer = fresh_pc(tmp_path)
    activate(monkeypatch, registry, farmer)
    world = World("Brix", 7002)
    if case == "snapshot_uid_differs":
        world.snapshot["character_uid"] = 7003
    else:
        world.snapshot["character"] = "brix"
    world.install(monkeypatch)
    before = revision(registry)
    with pytest.raises(ValueError):
        attach(monkeypatch, "Brix")
    assert uid_of(registry, "Brix") is None and revision(registry) == before


def test_runtime_attachment_never_binds_a_different_uid_seen_in_discovery(
    tmp_path, monkeypatch
):
    registry, farmer = fresh_pc(tmp_path, pinned=7001)
    activate(monkeypatch, registry, farmer)
    World("Kalhiam", 8002).install(monkeypatch)
    with pytest.raises(ValueError, match="found 0"):
        attach(monkeypatch, "Kalhiam")
    assert uid_of(registry, "Kalhiam") == 7001


def test_runtime_login_screen_rebind_never_binds(tmp_path, monkeypatch):
    registry, farmer = fresh_pc(tmp_path)
    activate(monkeypatch, registry, farmer)
    World("Brix", 7002).install(monkeypatch)
    before = revision(registry)
    attached = attach(monkeypatch, "Brix", login=True)
    assert attached.result == "login-rebind" and len(attached.logins) == 1
    assert uid_of(registry, "Brix") is None and revision(registry) == before


def test_runtime_rejects_non_read_only_observer_before_binding(tmp_path, monkeypatch):
    registry, farmer = fresh_pc(tmp_path)
    activate(monkeypatch, registry, farmer)
    World("Brix", 7002).install(monkeypatch)

    class Automation(Observer):
        merchant_observation_only = False

    with pytest.raises(ValueError, match="read-only observer"):
        attach(monkeypatch, "Brix", observer_type=Automation)
    assert uid_of(registry, "Brix") is None


def _first_sight_scenario(root, monkeypatch):
    registry, farmer = fresh_pc(root)
    activate(monkeypatch, registry, farmer)
    from conquest.merchants import booth_listing_once_1078 as listing

    def gate(name):
        try:
            return listing._profile(name).character_uid
        except ValueError:
            return "rejected"

    steps = {"before": {"listing_gate": gate("Kalhiam"), "uid": None}}
    World("Kalhiam", 7001).install(monkeypatch)
    start = revision(registry)
    first = bridge_observe("Kalhiam")
    steps["bridge_first_sight"] = {
        "profile_uid_verified": first["profile_uid_verified"],
        "uid": uid_of(registry, "Kalhiam"),
        "registry_writes": revision(registry) - start,
        "listing_gate": gate("Kalhiam"),
    }
    again = bridge_observe("Kalhiam")
    steps["bridge_repeat_sight"] = {
        "profile_uid_verified": again["profile_uid_verified"],
        "registry_writes": revision(registry) - start,
    }
    World("Kalhiam", 8002).install(monkeypatch)
    try:
        bridge_observe("Kalhiam")
        outcome = "accepted"
    except ValueError:
        outcome = "refused"
    steps["bridge_other_uid"] = {
        "outcome": outcome,
        "uid": uid_of(registry, "Kalhiam"),
        "registry_writes": revision(registry) - start,
    }
    World("Brix", 7002).install(monkeypatch)
    attached = attach(monkeypatch, "Brix")
    observer = attached.runtime.observers[attached.character]
    steps["runtime_first_sight"] = {
        "uid": uid_of(registry, "Brix"),
        "observer_pinned_uid": observer.character_context.profile.character_uid,
        "listing_gate": gate("Brix"),
        "registry_writes": revision(registry) - start,
    }
    path = Path(root) / "merchant-uid-first-sight.json"
    path.write_text(json.dumps(steps, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_fresh_pc_first_sight_uid_binding_e2e(tmp_path, monkeypatch):
    first = _first_sight_scenario(tmp_path / "pc-a", monkeypatch)
    second = _first_sight_scenario(tmp_path / "pc-b", monkeypatch)
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text(encoding="utf-8")) == {
        "before": {"listing_gate": "rejected", "uid": None},
        "bridge_first_sight": {
            "profile_uid_verified": True,
            "uid": 7001,
            "registry_writes": 1,
            "listing_gate": 7001,
        },
        "bridge_repeat_sight": {"profile_uid_verified": True, "registry_writes": 1},
        "bridge_other_uid": {"outcome": "refused", "uid": 7001, "registry_writes": 1},
        "runtime_first_sight": {
            "uid": 7002,
            "observer_pinned_uid": 7002,
            "listing_gate": 7002,
            "registry_writes": 2,
        },
    }
