"""Observe manual farmer trades using the existing pinned native memory reader."""

import json
from types import SimpleNamespace
import time

from conquest.capture import CaptureUnavailable
from conquest.character_context import farmer_name
from conquest.memory_build_layout import read_build_layout
from conquest.merchants.memory import GuiReader, string, unpack


CLOSED_SESSION = "Memory session is closed"
# Windows FILETIME (100 ns since 1601) to Unix seconds.
FILETIME_EPOCH = 11644473600


def release_hold_from_dead_process(runtime, snapshot, *, now=None):
    """Settle a reader hold whose game process no longer exists.

    A hold left because the reader's memory session closed (the client
    crashed or was relaunched) names a process that is gone, and no trade of
    that process can still be open. Live 2026-09-27 (Toxic): the client died
    in a GPU reset at 22:10, the reconnect logged a new one in at 22:11:41,
    and the stale hold fenced every town action until an operator override.

    Only that reason, only a fresh (2 s) snapshot of a process created after
    the hold, and only with no trade and no request. The ordinary override
    then starts the same rebaseline an operator's disposition would.
    """
    hold = runtime._manual_get("Farmer", "manual_reader_hold")
    if not hold or not str(hold.get("reason", "")).endswith(CLOSED_SESSION):
        return False
    now = time.time() if now is None else now
    identity = snapshot.get("identity") or {}
    created = identity.get("creation_time_100ns")
    if type(created) is not int:
        return False
    started = created / 1e7 - FILETIME_EPOCH
    if (
        started <= hold.get("created_at", float("inf"))
        or snapshot.get("trade") is not None
        or snapshot.get("request") is not None
        or not 0 <= now - snapshot.get("timestamp", 0) <= 2
    ):
        return False
    runtime.override_manual(
        hold["id"],
        confirmation_reference=f"restart:{identity.get('pid')}:{created}",
        operator="automatic (new game process)",
        reason=(
            f"Reader hold from a closed memory session at {hold.get('created_at')}; "
            f"process {identity.get('pid')} started after it and shows no trade or request"
        ),
        now=now,
    )
    runtime.manual_farmer_observation["stale_hold_released"] = hold["id"]
    return True


def presence(observer):
    """Map-independent modal presence; no inventory/participant inference."""
    observer.adapter.assert_identity()
    layout = read_build_layout(observer.adapter)
    gui = GuiReader.for_session(observer.adapter)
    session = gui.session
    trade = gui.model(14, layout.merchant_trade_vtable_rva)
    request = gui.model(15, layout.merchant_confirm_vtable_rva)
    flags = [unpack(session, address + 12, "<B")[0] for address in (trade, request)]
    if any(value not in (0, 1) for value in flags):
        raise ValueError("Trade modal flags are invalid")
    title = string(session, request + 0x48) if flags[1] else None
    if [
        unpack(session, address + 12, "<B")[0] for address in (trade, request)
    ] != flags:
        raise ValueError("Trade modal presence changed during observation")
    session.assert_identity()
    return bool(flags[0] or flags[1] and title == "Trade###Confirm")


class FarmerJournal:
    """Native decline journal adapter; never lends merchant delivery trust."""

    def __init__(self, runtime):
        self.runtime = runtime

    def get(self, character, name, default=None):
        return self.runtime._manual_get("Farmer", name, default)

    def set(self, character, name, value):
        self.runtime._manual_set("Farmer", name, value)

    def pending(self, character):
        return [{"kind": "farmer_delivery"}] if self.runtime.farmer_bot_owned() else []


ROUTE_PHASES = ("starting", "hunting", "restocking", "recovering_route")
ROUTE_STATUS_SECONDS = 30
# Farming-only servers have no visitor admission. An unapproved request is
# cancelled once it has stayed displayed this long (AGENTS.md: five seconds).
FARMING_ONLY_DECLINE_AFTER = 5
# A Cancel press that leaves the request displayed is retried at most this
# many times, this many seconds apart.
FARMING_ONLY_DECLINE_ATTEMPTS = 3
FARMING_ONLY_DECLINE_RETRY = 10


def _clock():
    return time.monotonic()


def route_owns_farmer():
    """A live route process that the user has not stopped owns the farmer.

    The route turns the Farming control Off itself for every town action
    (stop_farm; town input requires it), so that flag alone is not the
    user's intent. Only the user's Off writes the overnight.stop marker.
    """
    from pathlib import Path

    from conquest.character_context import state_path
    from conquest.discord_notify import process_alive

    if Path(state_path(".runtime/overnight.stop")).exists():
        return False
    try:
        status = json.loads(
            Path(state_path("reports/overnight/status.json")).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return False
    updated = status.get("updated_at")
    return bool(
        isinstance(status, dict)
        and status.get("phase") in ROUTE_PHASES
        and type(updated) in (int, float)
        and 0 <= time.time() - updated <= ROUTE_STATUS_SECONDS
        and process_alive(status.get("pid"))
    )


def decline_permission(runtime):
    """(allowed, blocker) for the native decline of an unapproved request.

    Never changes saved intent. Global Stop and a user Off always win; a
    route-owned Off (town action, route start) does not block the decline.
    F11/F12 and the coordinator fence are still checked at input time.
    """
    from pathlib import Path

    from conquest.character_context import state_path

    intent = runtime.manual_farmer_control()
    if runtime.coordinator.stopped:
        return False, "Global Stop holds farmer decline input"
    if intent.get("paused"):
        return False, "Farmer is paused"
    if Path(state_path(".runtime/overnight.stop")).exists():
        return False, "Farming Off by user; the unapproved request awaits the operator"
    if intent.get("enabled") or route_owns_farmer():
        return True, None
    return False, "Farming is Off; the unapproved request awaits the operator"


def controller(runtime, observer, *, farming_only=False):
    from conquest.merchants.driver import MerchantDriver

    if farming_only:
        # Delivery qualification is America-only, so a farming-only server
        # has none. Its decline rests on the live native 1078 request proof
        # alone (unrelated_request); this path is never read.
        from pathlib import Path

        from conquest.character_context import state_path

        qualification = Path(
            state_path(".runtime/merchants/no-delivery-qualification.json")
        )
    else:
        from conquest.merchants.farmer_qualification import qualification_path

        qualification = qualification_path(observer, migrate=False)
    driver = MerchantDriver(observer, qualification, runtime.coordinator)
    driver.read = lambda: driver.memory.read(farmer_preflight=True)

    def active():
        return decline_permission(runtime)[0]

    def check():
        allowed, blocker = decline_permission(runtime)
        if not allowed:
            raise CaptureUnavailable(blocker)
        import ctypes

        if any(
            ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000 for key in (0x7A, 0x7B)
        ):
            raise CaptureUnavailable("Farmer manual decline stopped by F11/F12")
        runtime.coordinator.check()

    return SimpleNamespace(
        character="Farmer",
        manual_farmer=True,
        farming_only=farming_only,
        journal=FarmerJournal(runtime),
        driver=driver,
        coordinator=runtime.coordinator,
        active=active,
        check=check,
    )


def _project_farming_only_request(runtime, pending):
    """Tell the route's health read that only an unapproved request fences
    the farmer, so its town actions wait for the decline instead of failing."""
    runtime.coordinator.farming_only_request = bool(pending)


def decline_farming_only_request(runtime, observer, snapshot):
    """Cancel an unapproved request on a farming-only server.

    These servers never open a visitor session. Once the exact request has
    stayed displayed for FARMING_ONLY_DECLINE_AFTER seconds, the native 1078
    decline presses Cancel under its hover proof and once-only journal. A
    press that leaves the request displayed is retried a bounded number of
    times; a changed client identity is never pressed.
    """
    request = snapshot["request"]
    key = [
        request.get("participant_uid"),
        request.get("participant"),
        request.get("message"),
    ]
    now = _clock()
    track = getattr(runtime, "farming_only_request", None)
    if not track or track["key"] != key or now - track["seen"] > 2:
        track = {
            "key": key,
            "identity": snapshot.get("identity"),
            "since": now,
            "retry_at": now + FARMING_ONLY_DECLINE_AFTER,
            "presses": 0,
        }
    track["seen"] = now
    runtime.farming_only_request = track
    observation = runtime.manual_farmer_observation
    if snapshot.get("identity") != track["identity"]:
        observation["decline_blocker"] = (
            "Client identity changed under the displayed request; no decline input"
        )
        return False
    if track["presses"] >= FARMING_ONLY_DECLINE_ATTEMPTS:
        observation["decline_blocker"] = (
            "Cancel did not close the request; it stays fenced until it closes"
        )
        return False
    if now < track["retry_at"]:
        return False
    allowed, blocker = decline_permission(runtime)
    if not allowed:
        observation["decline_blocker"] = blocker
        return False
    from conquest.merchants.unrelated_request import decline_unrelated_request

    before = runtime._manual_get("Farmer", "unrelated_request_decline")
    declined = False
    try:
        current = runtime.manual_farmer_controller
        if (
            current is None
            or not getattr(current, "farming_only", False)
            or current.driver.observer is not observer
        ):
            current = controller(runtime, observer, farming_only=True)
            runtime.manual_farmer_controller = current
        declined = decline_unrelated_request(current, snapshot, operations_enabled=True)
    except (ValueError, OSError) as error:
        observation["decline_blocker"] = str(error)
    # Only a submitted press counts toward the bounded retries; refusals
    # before input (focus, F11/F12, changed dialog) just wait and retry.
    if runtime._manual_get("Farmer", "unrelated_request_decline") != before:
        track["presses"] += 1
    # The decline itself takes seconds; that is not an observation gap.
    track["seen"] = _clock()
    track["retry_at"] = track["seen"] + FARMING_ONLY_DECLINE_RETRY
    observation["farming_only_decline"] = {
        "presses": track["presses"],
        "declined": bool(declined),
    }
    return bool(declined)


def observe(runtime, observer=None):
    # Only the app's dedicated observer thread (run_manual_farmer) calls
    # without an observer; the combat and town boundaries pass theirs.
    observer_thread = observer is None
    configured = runtime.manual_farmer_provider()
    if observer is not None and observer is not configured:
        return False
    observer = configured
    if observer is None:
        runtime.manual_farmer_observation = {
            "available": False,
            "reason": "Farmer has no attached memory observer",
        }
        return runtime.manual_unavailable(
            "Farmer", "Farmer has no attached memory observer"
        )
    target = getattr(getattr(observer, "operations", None), "target", None)
    if target is not None and getattr(target, "hwnd", None) is not None:
        from conquest.reconnect import login_screen

        if login_screen(target.hwnd):
            runtime.manual_farmer_observation = {
                "available": False,
                "reason": "Farmer is disconnected; trade memory is unavailable",
            }
            return runtime.manual_unavailable(
                "Farmer", "Farmer disconnected during manual session"
            )
    if runtime.farmer_bot_owned():
        runtime.manual_farmer_observation = {
            "available": True,
            "bot_owned": True,
            "reason": "Automated farmer delivery has observation priority",
        }
        return runtime.coordinator.manual_session_blocked("Farmer")
    if not observer.lock.acquire(blocking=False):
        return runtime.coordinator.manual_session_blocked("Farmer")
    snapshot = None
    try:
        from conquest.character_context import registry, current

        context = current()
        if registry() is not None and (
            context is None or context.profile.role != "Farmer"
        ):
            raise ValueError(
                "Select an exact Farmer profile before observing manual trades"
            )
        if observer.character != farmer_name():
            raise ValueError("Attached farmer identity does not match selected profile")
        visible = presence(observer)
        held = (
            runtime.manual_status("Farmer") is not None
            or runtime.manual_handoff_status() is not None
        )
        if not visible and not held:
            _project_farming_only_request(runtime, False)
            runtime.manual_farmer_observation = {
                "available": True,
                "windows_absent": True,
                "observed_at": time.time(),
                "source": "read_only_memory",
                "qualified_full_snapshot_maps": [1002, 1011, 1036],
            }
            return False
        # read_build_layout (checked by presence above) qualifies only 1078.
        read_build_layout(observer.adapter)
        from conquest.merchants.trade_reader_1078 import manual_ownership

        snapshot = manual_ownership(observer.adapter, observer.character)
    except (ValueError, OSError) as error:
        reason = "Farmer manual memory unavailable: " + str(error)
        runtime.manual_farmer_observation = {
            "available": False,
            "reason": reason,
            "qualified_full_snapshot_maps": [1002, 1011, 1036],
        }
    finally:
        observer.lock.release()
    if snapshot is None:
        if runtime.manual_handoff_status() is not None:
            runtime.manual_handoff.unavailable(runtime.manual_target("Farmer"), reason)
            runtime._sync_manual_fence()
            return True
        if not runtime.manual_unavailable("Farmer", reason):
            # No exact first binding can be created without complete evidence.
            runtime.manual_reader_failure("Farmer", {"reader_error": reason}, reason)
        return True
    runtime.manual_farmer_observation = {
        "available": True,
        "observed_at": snapshot["timestamp"],
        "source": "read_only_memory",
        "snapshot": snapshot,
    }
    # Global operator handoff intentionally bypasses visitor admission and
    # decline routing.  It only observes the existing memory snapshot.
    if runtime.observe_manual_handoff("Farmer", snapshot):
        return True
    from conquest.client_attachment import FARMING_ONLY_SERVERS

    if snapshot.get("server") in FARMING_ONLY_SERVERS and (
        snapshot.get("request") is not None or snapshot.get("trade") is not None
    ):
        # Visitor admission is qualified on America only. A farming-only
        # server never opens a visitor session (which could not settle
        # there); it fences farmer input while the modal is open, and the
        # observer thread cancels an unapproved request after five seconds.
        runtime.manual_farmer_observation["farming_only_modal"] = True
        request_only = snapshot.get("trade") is None
        _project_farming_only_request(runtime, request_only)
        if observer_thread and request_only:
            decline_farming_only_request(runtime, observer, snapshot)
        return True
    _project_farming_only_request(runtime, False)
    release_hold_from_dead_process(runtime, snapshot)
    routed = runtime.process_probe_owned("Farmer", snapshot)
    if routed:
        from conquest.merchants.manual_runtime import OBSERVATION_DEFERRED

        if routed is OBSERVATION_DEFERRED:
            runtime.manual_farmer_observation.update(
                observation_deferred=True,
                reason="Trade observation waited for input; a fresh memory read is required",
            )
            return True  # Fence this caller's operation until its next fresh read.
        else:
            runtime.manual_farmer_observation.update(
                bot_owned=True,
                reason="Exact supervised farmer delivery has fresh bilateral observation priority",
            )
        return runtime.coordinator.manual_session_blocked("Farmer")
    if (
        runtime.manual_farmer_controller is None
        or runtime.manual_farmer_controller.driver.observer is not observer
    ):
        try:
            runtime.manual_farmer_controller = controller(runtime, observer)
        except (ValueError, OSError) as error:
            runtime.manual_farmer_observation["decline_blocker"] = str(error)
    allowed, blocker = decline_permission(runtime)
    if blocker and snapshot.get("request"):
        runtime.manual_farmer_observation["decline_blocker"] = blocker
    try:
        runtime.process_manual(
            "Farmer",
            snapshot,
            decline_enabled=bool(runtime.manual_farmer_controller and allowed),
        )
    except (ValueError, OSError, CaptureUnavailable) as error:
        runtime.manual_farmer_observation["decline_blocker"] = str(error)
    return runtime.coordinator.manual_session_blocked("Farmer")
