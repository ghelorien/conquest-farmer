"""Exact-session native recovery; travel never grants trade or shop controls."""

import copy
import hashlib
import json
import threading
import time
import uuid
from types import SimpleNamespace

from conquest.capture import CaptureUnavailable
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants.return_driver import ReturnDriver

PURPOSE = "merchant_return_1078"
KEY = "native_return_1078"
BASELINE = "market_baseline_1078"
BASELINE_VERSION = 1
BASELINE_REFRESH_SECONDS = 60
TERMINAL = ("complete", "operator_overridden")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _profile(character):
    """The configured, UID-pinned local merchant profile, or ValueError."""
    from conquest.merchants.booth_listing_once_1078 import _profile as pinned

    return pinned(character)


def _identity_ok(identity):
    return (
        isinstance(identity, dict)
        and type(identity.get("pid")) is int
        and identity["pid"] > 0
        and type(identity.get("creation_time_100ns")) is int
        and identity["creation_time_100ns"] > 0
        and isinstance(identity.get("path"), str)
        and bool(identity["path"])
    )


def _booth_prices(booth):
    return [
        {
            key: item.get(key)
            for key in ("uid", "type_id", "name", "plus", "gem1", "gem2", "price")
        }
        for item in booth
    ]


def unresolved(runtime, character):
    """The durable non-terminal native recovery incident, if any."""
    state = runtime.journal.get(character, KEY)
    if isinstance(state, dict) and state and state.get("phase") not in TERMINAL:
        return state
    return None


def healthy_market(snapshot, profile):
    """A live owned Market booth with no modal, as read from exact memory."""
    return bool(
        isinstance(snapshot, dict)
        and snapshot.get("map_id") == 1036
        and snapshot.get("own_booth_uid")
        and snapshot.get("booth_open") is True
        and snapshot.get("trade") is None
        and snapshot.get("request") is None
        and type(snapshot.get("hp")) is int
        and snapshot["hp"] > 0
        and snapshot.get("character") == profile.name
        and snapshot.get("server") == profile.server
        and snapshot.get("character_uid") == profile.character_uid
        and _identity_ok(snapshot.get("identity"))
        and isinstance(snapshot.get("inventory"), list)
        and isinstance(snapshot.get("booth"), list)
        and isinstance(snapshot.get("position"), list)
    )


def record_baseline(runtime, character, snapshot, *, now=None):
    """Persist the last healthy Market snapshot; frozen while an incident exists.

    Only the exact-1078 ownership snapshot of the attached observer reaches this
    function. Non-Market, modal, pending or incident-time snapshots never
    replace the durable pre-loss evidence.
    """
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("map_id") != 1036
        or snapshot.get("booth_open") is not True
    ):
        return False
    try:
        profile = _profile(character)
    except ValueError:
        return False  # Recovery stays unavailable for an unpinned profile.
    if not healthy_market(snapshot, profile):
        return False
    if unresolved(runtime, character) is not None:
        return False
    if runtime.journal.pending(character):
        return False
    now = time.time() if now is None else now
    content = digest(
        {
            key: snapshot.get(key)
            for key in (
                "identity",
                "character_uid",
                "map_id",
                "position",
                "own_booth_uid",
                "booth_open",
                "inventory",
                "booth",
                "silver",
            )
        }
    )
    cache = runtime.__dict__.setdefault("_market_baseline_1078", {})
    previous = cache.get(character)
    if (
        previous
        and previous[0] == content
        and 0 <= now - previous[1] < BASELINE_REFRESH_SECONDS
    ):
        return False
    record = {
        "version": BASELINE_VERSION,
        "client_sha256": CLIENT_SHA256_1078,
        "profile_id": profile.id,
        "character": profile.name,
        "server": profile.server,
        "character_uid": snapshot["character_uid"],
        "identity": copy.deepcopy(snapshot["identity"]),
        "map_id": 1036,
        "position": list(snapshot["position"]),
        "own_booth_uid": snapshot["own_booth_uid"],
        "booth_prices": _booth_prices(snapshot["booth"]),
        "observed_at": snapshot.get("timestamp"),
        "recorded_at": now,
        "snapshot": copy.deepcopy(snapshot),
    }
    record["sha256"] = digest(record)
    runtime.journal.set(character, BASELINE, record)
    cache[character] = (content, now)
    return True


def baseline(runtime, character):
    """Only the durable last healthy Market snapshot can identify a lost actor."""
    value = runtime.journal.get(character, BASELINE)
    if not isinstance(value, dict):
        return None
    try:
        profile = _profile(character)
    except ValueError:
        return None
    snapshot = value.get("snapshot")
    if (
        not isinstance(snapshot, dict)
        or value.get("version") != BASELINE_VERSION
        or value.get("client_sha256") != CLIENT_SHA256_1078
        or value.get("profile_id") != profile.id
        or value.get("character") != profile.name
        or value.get("server") != profile.server
        or value.get("character_uid") != profile.character_uid
        or value.get("map_id") != 1036
        or not value.get("own_booth_uid")
        or not _identity_ok(value.get("identity"))
        or not healthy_market(snapshot, profile)
        or snapshot.get("identity") != value["identity"]
        or snapshot.get("character_uid") != value["character_uid"]
        or snapshot.get("own_booth_uid") != value["own_booth_uid"]
        or snapshot.get("position") != value.get("position")
        or value.get("booth_prices") != _booth_prices(snapshot["booth"])
        or value.get("sha256")
        != digest({k: v for k, v in value.items() if k != "sha256"})
    ):
        return None
    return value


def begin_loss(runtime, character, before, identity):
    """Preserve a superseded historical incident, without declaring it resolved."""
    if before is None or before["identity"] != identity:
        raise ValueError(
            "Recovery needs a verified pre-loss baseline for this exact process"
        )
    state = runtime.journal.get(character, KEY)
    if state and state.get("phase") not in TERMINAL:
        if state.get("identity") != identity:
            raise ValueError("Another native recovery incident remains unresolved")
        return state
    if runtime.journal.pending(character):
        raise ValueError("Pending merchant transaction must reconcile before recovery")
    old = runtime.returns[character].state()
    state = {
        "id": uuid.uuid4().hex,
        "phase": "login",
        "identity": copy.deepcopy(identity),
        "before": copy.deepcopy(before),
        "before_sha256": digest(before),
        "started_at": time.time(),
        "login_attempted": False,
        "moves": 0,
        "intent": {
            "operations": runtime.journal.get(character, "enabled", False),
            "refill": runtime.journal.get(character, "refill_enabled", True),
        },
    }
    if old:
        state["previous_shop_return"] = {
            "sha256": digest(old),
            "state": copy.deepcopy(old),
        }
    runtime.journal.set(character, KEY, state)
    # The new loss is a separate incident with its own current stock. The old
    # unresolved state remains verbatim in the immutable lineage above.
    runtime.returns[character].save(
        {
            "phase": "returning",
            "started_at": state["started_at"],
            "native_return_id": state["id"],
            "before": copy.deepcopy(before),
            "moves": 0,
            "stalls": 0,
            "home": {"map_id": 1036, "position": before["position"]},
            "previous_incident": state.get("previous_shop_return"),
        }
    )
    from conquest.merchants.recovery_safety import arm

    arm(runtime, character)
    runtime.journal.event(
        character,
        "native_return_started",
        incident=state["id"],
        baseline_sha256=state["before_sha256"],
    )
    return state


def prepare(runtime, character, expected_identity):
    """Explicit one-time relog intent; never enables merchant operations."""
    from conquest.character_context import merchant_context, merchant_directory
    from conquest.merchants.journal import character_name
    from conquest.reconnect import login_screen
    from conquest.login_1078 import assert_code
    from conquest.memory import MemorySession
    from conquest.memory_life import MemoryLifeReader
    from conquest.memory_build_layout import health_reader_layout
    from conquest.input_probe import MessageTarget
    from conquest.merchants.driver import MerchantDriver
    from conquest.merchants.controller import MerchantController
    from conquest.merchants.memory import MerchantMemory

    character = character_name(character)
    with runtime.lock:
        if (
            runtime.coordinator.owner is not None
            or runtime.coordinator.stopped
            or runtime.manual_handoff_status() is not None
            or runtime.coordinator.manual_session_blocked(character)
            or runtime.coordinator.manual_session_blocked("Farmer")
            or runtime.journal.pending(character)
            or runtime.journal.get(character, "connect_hold", False)
            or runtime.connecting
            or runtime.refilling
            or getattr(runtime, "delivery_window", None)
            or getattr(runtime, "refill_window", None)
        ):
            raise ValueError(
                "One-time native recovery is held by another operation or Stop"
            )
        previous = runtime.journal.get(character, KEY)
        if previous and previous.get("phase") not in TERMINAL:
            if previous.get("identity") != expected_identity:
                raise ValueError("Existing native recovery identity differs")
            return previous
        before = baseline(runtime, character)
        if before is None or before["identity"] != expected_identity:
            raise ValueError(
                "One-time relog must match the exact last verified merchant process"
            )
        observer = runtime.observers.get(character)
        if observer is None:
            matches = [
                c
                for c in runtime.merchant_windows()
                if c.identity == expected_identity and login_screen(c.hwnd)
            ]
            if len(matches) != 1:
                raise ValueError(
                    "Expected one exact previous merchant process at login"
                )
            client = matches[0]
            session = MemorySession(
                client.identity["pid"], CLIENT_SHA256_1078
            ).__enter__()
            try:
                if session.identity != expected_identity:
                    raise ValueError("Login process identity changed")
                adapter = SimpleNamespace(
                    expected_sha256=session.expected_sha256,
                    identity=session.identity,
                    modules=session.modules,
                    read=session.read,
                    read_block=session.read,
                    assert_identity=session.assert_identity,
                )
                assert_code(adapter)
                observer = SimpleNamespace(
                    character=character,
                    character_context=merchant_context(character),
                    adapter=adapter,
                    session=session,
                    lock=threading.RLock(),
                    hwnd=client.hwnd,
                    merchant_observation_only=True,
                    read_only_build=True,
                    automation_ready_build=False,
                    health_layout=health_reader_layout(adapter),
                    close=session.close,
                    operations=SimpleNamespace(
                        target=MessageTarget(client.identity["pid"], client.hwnd)
                    ),
                )
                observer.read_life = lambda: MemoryLifeReader.for_session(
                    adapter, character
                ).read()
                observer.memory = MerchantMemory.for_observer(observer)
                observer.read_ownership = observer.memory.read_manual_ownership
                driver = MerchantDriver(
                    observer,
                    merchant_directory(character) / "qualification.json",
                    runtime.coordinator,
                )
                runtime.observers[character] = observer
                runtime.controllers[character] = MerchantController(
                    character, runtime.journal, driver, runtime.coordinator
                )
                runtime.coordinator.surface_blocks[character] = True
            except BaseException:
                session.close()
                raise
        if observer.adapter.identity != expected_identity or not login_screen(
            observer.hwnd
        ):
            raise ValueError(
                "One-time relog requires the exact identified client still at login"
            )
        assert_code(observer.adapter)
        state = begin_loss(runtime, character, before, expected_identity)
        state["authorization"] = "explicit_one_time_relog_to_market"
        save(runtime, character, state)
        return state


class ReturnDriver1078(ReturnDriver):
    """Reuse checked terrain and native Conductress records on the exact actor."""

    def __init__(self, driver):
        super().__init__(driver, travel_only=True)
        from conquest.memory_entities import MemoryEntityReader
        from conquest.memory_build_layout import entity_reader_layout

        if self.observer.adapter.expected_sha256 != CLIENT_SHA256_1078:
            raise ValueError("Native return requires exact1078")
        self.observer.entities = MemoryEntityReader(
            self.observer.adapter, entity_reader_layout(self.observer.adapter)
        )

    def read(self):
        return self.driver.memory.read(recovery=True)

    def life(self):
        from conquest.memory_life import MemoryLifeReader

        return MemoryLifeReader.for_session(
            self.observer.adapter, self.observer.character
        ).read()

    def qualify_movement(self):
        from conquest.scene_input import memory_player_anchor
        from conquest.desktop_runtime import physical_coordinates

        self.observer.adapter.assert_identity()
        life = self.life()
        if (
            life.dead_candidate
            or life.current_hp <= 0
            or life.map_id not in (1002, 1036)
        ):
            raise ValueError(
                "Native return requires a living identified merchant in Twin City or Market"
            )
        anchor = memory_player_anchor(self.observer, life)
        gui = self.driver.memory.gui.viewport_size()
        with physical_coordinates():
            native = self.driver.target.snapshot()
        size = list(native["client_size"])
        if (
            native["root_hwnd"] != self.driver.target.hwnd
            or min(*gui, *size) < 300
            or memory_player_anchor(self.observer, life) != anchor
            or self.driver.memory.gui.viewport_size() != gui
        ):
            raise ValueError("Native return projection or viewport changed")
        return {"gui_size": gui, "client_size": size}

    def click(self, point, check, *, before_press, jump=False):
        # Base foreground path rechecks the current qualified projection,
        # identity, geometry and exact control immediately before mouse-down.
        return super().click(point, check, before_press=before_press, jump=jump)


def policy(runtime, character):
    """A purpose string alone cannot authorize a native recovery lease."""
    coordinator = runtime.coordinator
    state = runtime.journal.get(character, KEY) or {}
    observer = runtime.observers.get(character)
    binding = getattr(runtime, "native_return_workers", {}).get(character)
    if (
        not binding
        or binding != (threading.get_ident(), state.get("id"))
        or coordinator.purpose != PURPOSE
        or observer is None
        or observer.adapter.expected_sha256 != CLIENT_SHA256_1078
        or observer.adapter.identity != state.get("identity")
        or state.get("phase") in TERMINAL + ("needs_attention",)
        or state.get("authorization") != "explicit_one_time_relog_to_market"
        or coordinator.stopped
        or runtime.stop_event.is_set()
        or runtime.manual_handoff_status() is not None
        or coordinator.manual_session_blocked(character)
        or coordinator.manual_session_blocked("Farmer")
        or runtime.journal.get(character, "connect_hold", False)
        or runtime.journal.pending(character)
        or getattr(runtime, "delivery_window", None)
        or getattr(runtime, "refill_window", None)
        or not coordinator.safe_to_yield()
    ):
        return False
    if state.get("intent") != {
        "operations": runtime.journal.get(character, "enabled", False),
        "refill": runtime.journal.get(character, "refill_enabled", True),
    }:
        return False
    observer.adapter.assert_identity()
    runtime.native1078_farmer_check()
    return True


def save(runtime, character, state, phase=None, **values):
    if phase:
        state["phase"] = phase
    state.update(values, updated_at=time.time())
    runtime.journal.set(character, KEY, state)


def step(runtime, character):
    """Run one bounded native recovery stage before normal merchant work."""
    from conquest.reconnect import login_screen

    observer = runtime.observers.get(character)
    state = runtime.journal.get(character, KEY) or {}
    if observer is None:
        return False
    observer.adapter.assert_identity()
    at_login = login_screen(observer.operations.target.hwnd)
    if not state or state.get("phase") in TERMINAL:
        return False
    if state.get("identity") != observer.adapter.identity:
        raise ValueError(
            "Native recovery process identity changed; reconciliation required"
        )
    if state["phase"] == "needs_attention":
        raise ValueError(state["note"])
    if (
        runtime.coordinator.stopped
        or runtime.manual_handoff_status() is not None
        or runtime.coordinator.manual_session_blocked(character)
        or runtime.coordinator.manual_session_blocked("Farmer")
        or runtime.journal.pending(character)
        or runtime.journal.get(character, "connect_hold", False)
    ):
        raise CaptureUnavailable(
            "Native recovery waits for Stop, manual ownership or transaction reconciliation"
        )
    if not runtime.coordinator.safe_to_yield():
        with runtime.lock:
            if runtime.handoff is None:
                runtime.handoff = f"merchant-recovery:{character}:{time.time_ns()}"
        return True
    if getattr(runtime, "refill_window", None) or getattr(
        runtime, "delivery_window", None
    ):
        return True
    workers = getattr(runtime, "native_return_workers", None)
    if workers is None:
        runtime.native_return_workers = {}
        workers = runtime.native_return_workers
    workers[character] = (threading.get_ident(), state["id"])
    try:
        with runtime.coordinator.lease(character, purpose=PURPOSE):

            def check():
                runtime.coordinator.check()
                if not policy(runtime, character):
                    raise CaptureUnavailable("Native recovery authority changed")

            check()
            if at_login:
                if state.get("login_attempted"):
                    raise ValueError(
                        "Submitted native login requires observation; no credential replay"
                    )
                from conquest.login_1078 import assert_code
                from conquest.reconnect import submit_login
                from conquest.merchants.recovery import credential_path
                from conquest.merchants.recovery_safety import submitted

                assert_code(observer.adapter)
                if not credential_path(character).exists():
                    raise ValueError("Merchant encrypted credentials are unavailable")
                save(runtime, character, state, login_attempted=True)
                submitted(runtime, character)
                submit_login(
                    observer.operations.target,
                    credential_path(character),
                    session=observer.adapter,
                )
                return True
            travel = ReturnDriver1078(runtime.controllers[character].driver)
            current = travel.read()
            from conquest.merchants.controller import identities

            if (
                identities(current["inventory"] + current["booth"])
                != identities(state["before"]["inventory"] + state["before"]["booth"])
                or current.get("trade")
                or current.get("request")
            ):
                raise ValueError("Native recovery ownership or trade changed")
            runtime.latest[character] = current
            if state.get("phase") == "fare_submitted":
                if (
                    current["map_id"] == 1036
                    and state["silver_before"] - current["silver"] == 100
                ):
                    save(runtime, character, state, "market", fare_verified=current)
                elif time.time() - state["submitted_at"] <= 10:
                    return True
                else:
                    raise ValueError(
                        "Native Market fare remains uncertain; no repeat payment"
                    )
            if current["map_id"] == 1002:
                if max(abs(a - b) for a, b in zip(current["position"], (438, 444))) > 2:
                    after = travel.move(current, (438, 444), check)
                    save(
                        runtime,
                        character,
                        state,
                        "returning",
                        moves=state["moves"] + 1,
                        last_movement={"before": current, "after": after},
                    )
                    return True
                records = travel.prepare_transfer(current, check)
                check()
                before = travel.read()
                if (
                    before["identity"] != current["identity"]
                    or before["position"] != current["position"]
                    or before["silver"] != current["silver"]
                ):
                    raise ValueError("Native fare baseline changed")
                save(
                    runtime,
                    character,
                    state,
                    "fare_submitted",
                    silver_before=before["silver"],
                    submitted_at=time.time(),
                    fare_before=before,
                    fare_records=records,
                )
                travel.transfer(records, check)
                return True
            if current["map_id"] != 1036:
                raise ValueError("Native recovery arrived in an unsupported map")
            save(
                runtime,
                character,
                state,
                "complete",
                market_snapshot=current,
                completed_at=time.time(),
                market_arrival_verified=True,
                shop_restored=False,
            )
            # Market arrival ends the unsafe-transit watchdog, not recovery.
            # Occupancy/claim and immutable-price restoration remain separately
            # admitted stages; they must never borrow movement qualification.
            from conquest.merchants.recovery_safety import observe

            observe(runtime, character, current["identity"], travel.life())
            runtime.journal.event(
                character,
                "native_return_market_verified",
                incident=state["id"],
                identity=current["identity"],
                shop_restored=False,
            )
            return True
    finally:
        workers.pop(character, None)
