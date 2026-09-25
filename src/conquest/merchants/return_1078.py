"""Exact-session native recovery; travel never grants trade or shop controls."""

import copy
import hashlib
import json
import threading
import time
import uuid

from conquest.capture import CaptureUnavailable
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants.return_driver import ReturnDriver

PURPOSE = "merchant_return_1078"
KEY = "native_return_1078"
BASELINE = "market_baseline_1078"
BASELINE_VERSION = 1
BASELINE_REFRESH_SECONDS = 60
TERMINAL = ("complete", "operator_overridden", "restored_observed")
AUTHORIZATION = "automatic_1078_disconnect_recovery"
# Milestone 1 stops after a verified login. Travel, fare and stall claim are
# later separately admitted stages; nothing below the login stage may run.
STAGE_LIMIT = "login"
LOGIN_PHASES = ("login", "login_submitted")
TRAVEL_PHASES = ("returning", "fare_submitted")
# A login frame must persist this long after arming before any input.
LOGIN_SETTLE_SECONDS = 5
# Still at login this long after the single submission: stop for attention.
LOGIN_OUTCOME_SECONDS = 90
# Neither at login nor readable in world this long after submission.
LOADING_SECONDS = 180
# A failed login proof (error text, layout, credentials) this long escalates.
BLOCKED_ESCALATE_SECONDS = 120
# Farmer handoff requests per incident; a hunting farmer is never thrashed.
MAX_HANDOFF_REQUESTS = 3
# The submission itself may use its lease only this long after journaling.
SUBMISSION_SECONDS = 30
# Two in-world readings verify a login: at least 1 s and at most 30 s apart.
VERIFY_MIN_SECONDS = 1
VERIFY_MAX_SECONDS = 30

_clock = time.time


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


def status_view(state):
    """Bridge/status projection without bulky pre-loss stock evidence."""
    if not isinstance(state, dict):
        return None
    return {
        key: value
        for key, value in state.items()
        if key
        not in (
            "before",
            "previous_shop_return",
            "previous_native_return",
            "login_verified",
            "world_reading",
            "restore_reading",
            "restored_snapshot",
        )
    }


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
    now = _clock() if now is None else now
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


def pinned_identity(runtime, character):
    """Exact process identity recovery may rebind while no actor is readable."""
    state = unresolved(runtime, character)
    if state is not None:
        identity = state.get("identity")
        return dict(identity) if _identity_ok(identity) else None
    before = baseline(runtime, character)
    return dict(before["identity"]) if before else None


def _login_shell(hwnd):
    """Win32 class/title only; never sufficient on its own."""
    from conquest.reconnect import login_screen

    try:
        return bool(login_screen(hwnd))
    except Exception:  # pywintypes.error is not an OSError subclass.
        return False


def _login_memory(adapter):
    """The pinned 1078 renderer's native Login window is active this frame."""
    from conquest.login_1078 import assert_code
    from conquest.memory_shop import MemoryGui

    assert_code(adapter)
    window = MemoryGui.for_session(adapter).read("Login")
    if window.name != "Login":
        raise ValueError("1078 Login window is not the active login form")
    assert_code(adapter)
    return True


def at_login(observer):
    """Same pinned process, login shell window and native Login GUI proof."""
    observer.adapter.assert_identity()
    if not _login_shell(observer.hwnd):
        return False
    try:
        _login_memory(observer.adapter)
    except (ValueError, OSError):
        return False
    observer.adapter.assert_identity()
    return _login_shell(observer.hwnd)


def _login_statuses(runtime):
    return runtime.__dict__.setdefault("login1078_status", {})


def begin_loss(runtime, character, before, identity):
    """Arm one durable incident; an unresolved one is reused, never replaced."""
    if before is None or before["identity"] != identity:
        raise ValueError(
            "Recovery needs a verified pre-loss baseline for this exact process"
        )
    previous = runtime.journal.get(character, KEY)
    if previous and previous.get("phase") not in TERMINAL:
        if previous.get("identity") != identity:
            raise ValueError("Another native recovery incident remains unresolved")
        return previous
    old = runtime.returns[character].state()
    state = {
        "id": uuid.uuid4().hex,
        "phase": "login",
        "authorization": AUTHORIZATION,
        "stage_limit": STAGE_LIMIT,
        "identity": copy.deepcopy(identity),
        "before": copy.deepcopy(before),
        "before_sha256": digest(before),
        "started_at": _clock(),
        "login_attempted": False,
        "moves": 0,
        # Recorded only. Recovery runs regardless of trading/refill toggles;
        # it never enables trades, listing or refill.
        "intent": {
            "operations": runtime.journal.get(character, "enabled", False),
            "refill": runtime.journal.get(character, "refill_enabled", True),
        },
        "pending_at_loss": bool(runtime.journal.pending(character)),
    }
    if old:
        state["previous_shop_return"] = {
            "sha256": digest(old),
            "state": copy.deepcopy(old),
        }
    if previous:
        state["previous_native_return"] = {
            "id": previous.get("id"),
            "phase": previous.get("phase"),
            "sha256": digest(previous),
        }
    runtime.journal.set(character, KEY, state)
    # The five-second protective-disconnect watchdog is deliberately not armed
    # here: login and loading never count toward it (recovery_safety.observe).
    runtime.journal.event(
        character,
        "native_return_started",
        incident=state["id"],
        baseline_sha256=state["before_sha256"],
    )
    return state


def _release_handoff(runtime, state):
    """Forget only this incident's own ungranted farmer handoff request."""
    key = (state or {}).get("handoff_request")
    if not key:
        return
    fence = getattr(runtime.coordinator, "fence", None)
    with runtime.lock:
        active = getattr(fence, "active", None)
        if runtime.handoff == key and (
            active is None or getattr(active, "request_id", None) != key
        ):
            runtime.handoff = None


def needs_attention(runtime, character, state, note, *, now=None):
    """Stop automatic recovery input for this incident; #shops is notified."""
    if state.get("phase") == "needs_attention":
        return state
    save(
        runtime,
        character,
        state,
        "needs_attention",
        note=note,
        previous_phase=state.get("phase"),
        needs_attention_at=_clock() if now is None else now,
    )
    runtime.journal.event(
        character, "native_return_needs_attention", incident=state["id"], note=note
    )
    _release_handoff(runtime, state)
    return state


def _credentials(character):
    from conquest.merchants.recovery import credential_path

    return credential_path(character)


def _login_form_proof(adapter):
    """Recognised disconnect error (or none) and the pinned login form.

    LoginErrorReader rejects any error text other than the qualified
    "connection interrupted" message and any changed modal/button layout;
    form_points rejects a changed Login form. Both run before credentials are
    read from disk, and submit_login repeats them immediately before input.
    """
    from conquest import login_1078
    from conquest.memory_shop import MemoryGui
    from conquest.reconnect import LoginErrorReader

    error = LoginErrorReader(adapter).read()
    if error is None:
        login_1078.form_points(adapter, MemoryGui.for_session(adapter).read("Login"))
    return error


def _login_ready(runtime, character, observer, state, *, leased=False):
    """Every merchant-side gate for the single login; no input, no secrets."""
    coordinator = runtime.coordinator
    if coordinator.stopped or runtime.stop_event.is_set():
        raise CaptureUnavailable("Native recovery login waits: Stop is active")
    if coordinator.manual_active():
        raise CaptureUnavailable("Native recovery login waits: mouse control is yours")
    if (
        runtime.manual_handoff_status() is not None
        or coordinator.manual_session_blocked(character)
        or coordinator.manual_session_blocked("Farmer")
    ):
        raise CaptureUnavailable(
            "Native recovery login waits for manual ownership to end"
        )
    if runtime.journal.get(character, "connect_hold", False):
        raise CaptureUnavailable("Native recovery login is held by connect hold")
    if runtime.journal.pending(character):
        raise CaptureUnavailable(
            "Native recovery login waits for the pending merchant transaction"
        )
    if (
        getattr(runtime, "delivery_window", None)
        or getattr(runtime, "refill_window", None)
        or getattr(runtime, "connecting", None)
        or getattr(runtime, "refilling", None)
    ):
        raise CaptureUnavailable(
            "Native recovery login waits for the current merchant work window"
        )
    if not leased and coordinator.owner is not None:
        raise CaptureUnavailable("Waiting for input owner")
    if runtime.native1078_farmer_check is None:
        raise CaptureUnavailable(
            "Native recovery login waits for the farmer safety observation"
        )
    if (
        state.get("phase") != "login"
        or state.get("login_attempted") is not False
        or state.get("authorization") != AUTHORIZATION
        or state.get("stage_limit") != STAGE_LIMIT
    ):
        raise ValueError("Native recovery incident is not awaiting its single login")
    observer.adapter.assert_identity()
    if (
        observer.adapter.expected_sha256 != CLIENT_SHA256_1078
        or observer.adapter.identity != state.get("identity")
    ):
        raise ValueError(
            "Native recovery process identity changed; reconciliation required"
        )
    if not at_login(observer):
        raise CaptureUnavailable(
            "Merchant client is not at a memory-proven login screen"
        )
    _login_form_proof(observer.adapter)
    if not _credentials(character).exists():
        raise ValueError("Merchant encrypted credentials are unavailable")


def _blocked(runtime, character, state, error, now):
    """Record why login waits; a persistent failed proof stops for attention."""
    note = str(error)[:200]
    values = {}
    if state.get("blocker") != note:
        values["blocker"] = note
    if not isinstance(error, CaptureUnavailable):
        since = state.get("proof_blocked_since")
        if since is None:
            values["proof_blocked_since"] = since = now
        if now - since >= BLOCKED_ESCALATE_SECONDS:
            return needs_attention(
                runtime,
                character,
                state,
                "Automatic login cannot proceed safely: " + note,
                now=now,
            )
    if values:
        save(runtime, character, state, **values)
    raise error


def _request_handoff(runtime, character, state, now):
    """Ask the farmer to park once per request; bounded per incident."""
    key = state.get("handoff_request")
    count = int(state.get("handoff_requests") or 0)
    with runtime.lock:
        current = runtime.handoff
        if key and current == key:
            outcome = "pending"
        elif current is not None:
            outcome = "other"
        elif count >= MAX_HANDOFF_REQUESTS:
            outcome = "exhausted"
        else:
            key = f"merchant-recovery:{character}:{time.time_ns()}"
            runtime.handoff = key
            outcome = "requested"
    if outcome == "exhausted":
        return needs_attention(
            runtime,
            character,
            state,
            "The farmer did not grant a safe login handoff after "
            f"{MAX_HANDOFF_REQUESTS} requests; no automatic retry",
            now=now,
        )
    if outcome == "requested":
        save(
            runtime,
            character,
            state,
            handoff_request=key,
            handoff_requests=count + 1,
            handoff_requested_at=now,
        )
        runtime.journal.event(
            character,
            "native_return_handoff_requested",
            incident=state["id"],
            request_id=key,
        )
    if outcome == "other":
        raise CaptureUnavailable(
            "Native recovery login waits for the current farmer handoff"
        )
    raise CaptureUnavailable("Native recovery login waits for a safe farmer handoff")


def _submit_once(runtime, character, observer, state):
    """The single journaled login under a thread-bound native input capability."""
    coordinator = runtime.coordinator
    workers = runtime.__dict__.setdefault("native_return_workers", {})
    workers[character] = (threading.get_ident(), state["id"])
    try:
        with coordinator.native_return1078_scope(
            character, lambda: policy(runtime, character)
        ):
            with coordinator.lease(character, purpose=PURPOSE):

                def check():
                    coordinator.check()
                    if not policy(runtime, character):
                        raise CaptureUnavailable("Native recovery authority changed")

                check()
                current = runtime.journal.get(character, KEY)
                if current != state:
                    raise CaptureUnavailable(
                        "Native recovery incident changed before login"
                    )
                _login_ready(runtime, character, observer, current, leased=True)
                from conquest.focus_recovery import activate_client

                # Focus is not game input. Failing here consumes no attempt.
                if not activate_client(observer.hwnd, observer.adapter.identity):
                    raise CaptureUnavailable(
                        "Login client did not receive verified focus; nothing was submitted"
                    )
                check()
                # Durable before any input: an interrupted or crashed
                # submission is uncertain and is never replayed.
                save(
                    runtime,
                    character,
                    current,
                    "login_submitted",
                    login_attempted=True,
                    submitted_at=_clock(),
                    blocker=None,
                )
                runtime.journal.event(
                    character, "native_return_login_submitted", incident=current["id"]
                )
                from conquest.reconnect import submit_login

                try:
                    submit_login(
                        observer.operations.target,
                        _credentials(character),
                        session=observer.adapter,
                    )
                except Exception as error:
                    # Never persist exception text: login code can hold secrets.
                    save(
                        runtime,
                        character,
                        current,
                        login_submit_outcome="interrupted_uncertain",
                        login_submit_error=type(error).__name__,
                    )
                    raise CaptureUnavailable(
                        "Login submission was interrupted; its outcome is uncertain "
                        "and it will not be replayed"
                    ) from None
                save(runtime, character, current, login_submit_outcome="returned")
                return current
    finally:
        workers.pop(character, None)
        _release_handoff(runtime, runtime.journal.get(character, KEY) or state)


def _attempt_login(runtime, character, observer, state, now):
    """Milestone 1 login stage: at most one journaled submission per incident."""
    if now - state.get("started_at", now) < LOGIN_SETTLE_SECONDS:
        return state
    try:
        _login_ready(runtime, character, observer, state)
    except ValueError as error:  # Includes CaptureUnavailable waits.
        return _blocked(runtime, character, state, error, now)
    if state.get("proof_blocked_since") is not None or state.get("blocker"):
        save(runtime, character, state, proof_blocked_since=None, blocker=None)
    if not runtime.coordinator.safe_to_yield():
        return _request_handoff(runtime, character, state, now)
    return _submit_once(runtime, character, observer, state)


def observe_gap(runtime, character, *, now=None):
    """Neither at login nor readable in world: bounded wait after submission."""
    now = _clock() if now is None else now
    state = unresolved(runtime, character)
    if (
        state is not None
        and state.get("phase") == "login_submitted"
        and now - state.get("submitted_at", now) >= LOADING_SECONDS
    ):
        return needs_attention(
            runtime,
            character,
            state,
            "The single automatic login was submitted but the merchant is not "
            "readable in world; no automatic retry",
            now=now,
        )
    return state


def on_login(runtime, character, observer, *, now=None):
    """Called only after at_login(): arm once, then run the bounded login stage."""
    now = _clock() if now is None else now
    identity = dict(observer.adapter.identity)
    status = {"at_login": True, "identity": identity, "observed_at": now}
    _login_statuses(runtime)[character] = status
    state = unresolved(runtime, character)
    if state is None:
        before = baseline(runtime, character)
        if before is None:
            status["reason"] = (
                "No verified Market baseline; automatic login is unavailable"
            )
            return None
        if before["identity"] != identity:
            # A different process at login is never identified by title alone.
            status["reason"] = (
                "Login process is not the last verified Market process; "
                "automatic login is unavailable"
            )
            return None
        state = begin_loss(runtime, character, before, identity)
    status["incident"] = state["id"]
    if state.get("identity") != identity:
        return needs_attention(
            runtime,
            character,
            state,
            "Merchant process identity changed during recovery; reconciliation required",
            now=now,
        )
    if state.get("world_reading") is not None:
        save(runtime, character, state, world_reading=None)
    phase = state.get("phase")
    if phase == "login":
        return _attempt_login(runtime, character, observer, state, now)
    if phase == "needs_attention":
        _release_handoff(runtime, state)
        return state
    if phase == "login_submitted":
        if now - state.get("submitted_at", now) >= LOGIN_OUTCOME_SECONDS:
            return needs_attention(
                runtime,
                character,
                state,
                "The single automatic login did not leave the login screen; "
                "no automatic retry",
                now=now,
            )
        return state
    return needs_attention(
        runtime,
        character,
        state,
        "Merchant returned to the login screen after a verified login; "
        "automatic login is not repeated",
        now=now,
    )


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
    """A purpose string alone cannot authorize a native recovery lease.

    Milestone 1 authorizes only the single login stage: the incident must be
    awaiting its one login, or inside the bounded window of that journaled
    submission, on this exact thread and process. Trading/refill toggles are
    deliberately not consulted: recovery always runs, and never enables them.
    """
    coordinator = runtime.coordinator
    state = runtime.journal.get(character, KEY) or {}
    observer = runtime.observers.get(character)
    binding = getattr(runtime, "native_return_workers", {}).get(character)
    phase = state.get("phase")
    login_stage = (
        phase == "login"
        and state.get("login_attempted") is False
        or phase == "login_submitted"
        and state.get("login_attempted") is True
        and 0 <= _clock() - state.get("submitted_at", 0) <= SUBMISSION_SECONDS
    )
    if (
        not binding
        or binding != (threading.get_ident(), state.get("id"))
        or coordinator.purpose != PURPOSE
        or not coordinator.native_return1078_bound(character)
        or observer is None
        or observer.adapter.expected_sha256 != CLIENT_SHA256_1078
        or observer.adapter.identity != state.get("identity")
        or not login_stage
        or state.get("authorization") != AUTHORIZATION
        or state.get("stage_limit") != STAGE_LIMIT
        or coordinator.stopped
        or runtime.stop_event.is_set()
        or coordinator.manual_active()
        or runtime.manual_handoff_status() is not None
        or coordinator.manual_session_blocked(character)
        or coordinator.manual_session_blocked("Farmer")
        or runtime.journal.get(character, "connect_hold", False)
        or runtime.journal.pending(character)
        or getattr(runtime, "delivery_window", None)
        or getattr(runtime, "refill_window", None)
        or runtime.native1078_farmer_check is None
        or not coordinator.safe_to_yield()
    ):
        return False
    observer.adapter.assert_identity()
    runtime.native1078_farmer_check()
    return True


def save(runtime, character, state, phase=None, **values):
    if phase:
        state["phase"] = phase
    state.update(values, updated_at=_clock())
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
                # The single journaled login belongs to on_login(); never here.
                return False
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
                elif _clock() - state["submitted_at"] <= 10:
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
                    submitted_at=_clock(),
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
                completed_at=_clock(),
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
