"""Observe manual farmer trades using the existing pinned native memory reader."""

import json
from types import SimpleNamespace
import time

from conquest.capture import CaptureUnavailable
from conquest.character_context import farmer_name
from conquest.memory_build_layout import read_build_layout
from conquest.merchants.memory import GuiReader, string, unpack


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


def controller(runtime, observer):
    from conquest.merchants.driver import MerchantDriver
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
        journal=FarmerJournal(runtime),
        driver=driver,
        coordinator=runtime.coordinator,
        active=active,
        check=check,
    )


def observe(runtime, observer=None):
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
        # Visitor admission and the native decline are qualified on America
        # only. A farming-only server never opens a visitor session (which
        # could not settle there); it only fences town input while the modal
        # is open, and the next closed read lifts the fence.
        runtime.manual_farmer_observation["farming_only_modal"] = True
        return True
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
