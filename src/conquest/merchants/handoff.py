"""Durable farmer work windows; never accumulate missed merchant intervals."""

from conquest.character_context import state_path
import time
import math
from pathlib import Path
from conquest.discord_notify import read_json, write_json

INTERVAL = 900
WORK_SECONDS = 15
REFILL_CONTINUATION_SECONDS = 60
POLICY = Path("profiles/merchant-deliveries.json")
STATE = Path(state_path(".runtime/merchant-handoff.json"))
HOST_STATE = Path(state_path(".runtime/merchant-host-handoff.json"))


class WorkWindows:
    def __init__(self, path=STATE, *, clock=time.time):
        self.path, self.clock = Path(path), clock

    def state(self):
        return read_json(self.path)

    def due(self):
        return self.clock() >= self.state().get("next_check", 0)

    def reserve(
        self,
        request_id,
        *,
        town=False,
        urgent=False,
        visit=None,
        listing_progress=None,
        continuation=False,
    ):
        if not town and not urgent and not continuation and not self.due():
            return False
        now = self.clock()
        old = self.state()
        consumed = dict(old.get("consumed_listing_progress") or {})
        if continuation and (
            town
            or urgent
            or visit
            or not listing_progress
            or consumed.get(listing_progress["character"])
            == listing_progress["request_id"]
            or now < listing_progress["verified_at"] + REFILL_CONTINUATION_SECONDS
        ):
            return False
        if listing_progress:
            consumed[listing_progress["character"]] = listing_progress["request_id"]
        if visit and (not town or not now < visit["deadline"] <= now + 60):
            return False
        same_visit = visit and old.get("visit_id") == visit["visit_id"]
        # Reserve before parking/input: crashes or failed safe-spot searches
        # cannot generate repeated interruptions of the hunting loop.
        state = {
            "request_id": request_id,
            "last_check": old.get("last_check", now)
            if same_visit or continuation
            else now,
            "next_check": old["next_check"]
            if same_visit or continuation
            else now + INTERVAL,
            "phase": "preparing",
            "town": town,
            "last_attempt_at": old.get("last_attempt_at", now) if same_visit else now,
        }
        state.update(
            consumed_listing_progress=consumed, refill_continuation=continuation
        )
        if visit:
            state.update(
                visit_id=visit["visit_id"],
                deadline=visit["deadline"],
                farmer_profile_id=visit["farmer_profile_id"],
                scope="market_visit",
            )
        write_json(self.path, state)
        return True

    def started(self, *, listing=False):
        state = self.state()
        now = self.clock()
        state.update(
            phase="working",
            deadline=state.get("deadline", now + (45 if listing else WORK_SECONDS)),
        )
        if listing:
            state["scope"] = "listing_1078"
        write_json(self.path, state)
        return state["deadline"]

    def finish(self, phase, **extra):
        state = self.state()
        state.update(phase=phase, finished_at=self.clock(), **extra)
        write_json(self.path, state)
        return state


def resumable(health, proof, revision):
    """A manual control change or a different client always wins."""
    data = health.get("embedded_controls", {})
    control = data.get("control", {})
    return (
        health.get("target") == proof.get("target")
        and control.get("revision") == revision
        and not control.get("enabled")
        and not control.get("paused")
    )


def service_candidate(character):
    """Recovery needs an input window before it can produce a fresh snapshot."""
    return bool(
        character.get("connected")
        or (
            character.get("enabled")
            and character.get("credentials_saved")
            and (
                character.get("qualification", {}).get("login")
                or (character.get("recovery_safety") or {}).get("active")
            )
        )
    )


def qualified_listing_request(status):
    """Only the exact requesting merchant's earned receipt admits 45 seconds."""
    parts = str(status.get("handoff_requested") or "").split(":")
    if len(parts) != 3 or parts[0] != "merchant-refill" or not parts[2].isdigit():
        return None
    state = status.get("characters", {}).get(parts[1], {})
    if (
        state.get("connected")
        and state.get("refill", {}).get("enabled")
        and state.get("qualification", {}).get("foreground_open_booth_listing_1078")
        is True
    ):
        return parts[1]
    return None


def pending_listing_progress(status):
    """One exact verified listing can earn one more bounded backlog window."""
    name = qualified_listing_request(status)
    if not name:
        return None
    state = status["characters"][name]
    refill = state.get("refill") or {}
    snapshot = state.get("snapshot") or {}
    proof = refill.get("last_verified_listing") or {}
    now = time.time()
    observed_at = snapshot.get("timestamp")
    verified_at = proof.get("verified_at")
    blocker = (state.get("foreground_refill_1078") or {}).get("blocker")
    if (
        not refill.get("pending")
        or not refill.get("cursor")
        or len(refill["cursor"]) <= refill.get("deferred", 0)
        or refill.get("listing1078_engine") != 1
        or refill.get("listing1078_request")
        or state.get("pending")
        or state.get("needs_attention")
        or state.get("manual_input_fence")
        or blocker not in ("waiting_farmer_handoff", "listing_work_budget_insufficient")
        or not snapshot.get("inventory")
        or len(snapshot.get("booth", [])) >= 32
        or not snapshot.get("booth_open")
        or snapshot.get("map_id") != 1036
        or type(observed_at) not in (int, float)
        or not math.isfinite(observed_at)
        or not 0 <= now - observed_at <= 2
        or snapshot.get("trade") is not None
        or snapshot.get("request") is not None
        or not isinstance(proof.get("request_id"), str)
        or not proof["request_id"].startswith("booth-list1078-refill-")
        or type(verified_at) not in (int, float)
        or not math.isfinite(verified_at)
        or verified_at > now
        or proof["verified_at"] < (refill.get("attempt_started_at") or float("inf"))
        or proof.get("character") != name
        or not proof.get("profile_id")
        or proof["profile_id"] != state.get("profile_id")
        or proof.get("character_uid") != snapshot.get("character_uid")
        or proof.get("identity") != snapshot.get("identity")
    ):
        return None
    return {**proof, "character": name}


def native_host_request(status):
    """App-owned display intent; never depends on trading/refill preferences."""
    row = status.get("host_request") or {}
    key = row.get("request_id")
    identities = row.get("identities")
    if (
        not isinstance(key, str)
        or not key.startswith("merchant-host:")
        or not isinstance(identities, dict)
        or not identities
    ):
        return None
    for name, identity in identities.items():
        state = status.get("characters", {}).get(name) or {}
        if (
            not state.get("connected")
            or (state.get("snapshot") or {}).get("identity") != identity
        ):
            return None
    return key


def host_restored_before_grant(status, identities):
    """Only fresh exact attached hosts and released input explain this race."""
    if (
        not identities
        or status.get("handoff_active") is not False
        or status.get("input_owner") is not None
        or status.get("host_request") is not None
        or status.get("manual_handoff") is not None
        or status.get("manual_sessions") != []
        or (status.get("manual_farmer") or {}).get("input_fenced") is not False
    ):
        return False
    for name, identity in identities.items():
        state = status.get("characters", {}).get(name) or {}
        if (
            not state.get("connected")
            or (state.get("snapshot") or {}).get("identity") != identity
            or not 0
            <= time.time() - (state.get("snapshot") or {}).get("timestamp", 0)
            <= 2
            or (status.get("layout", {}).get(name) or {}).get("attached") is not True
        ):
            return False
    return True


def service_window(loop, *, town=False):
    """Run on the existing route controller, retaining its exclusive ownership."""
    policy = read_json(POLICY)
    from conquest.merchant_loop_acceptance import trial_permitted
    from conquest.merchants.farmer_preferences import rollout_enabled
    from conquest.merchants.farmer_identity import route_character

    # This window serves refill/recovery, not delivery admission. Preserve
    # its existing authority when the separate delivery preference is Off.
    permitted = policy.get("parity_verified") or (
        town and rollout_enabled(route_character(loop), policy=policy)
    )
    from conquest.merchants.bridge import request as merchant, MerchantRejected
    from conquest.worker import request
    from conquest.safe_reload import park, clear_observation
    from conquest.overnight import OvernightStopped

    windows = WorkWindows()
    try:
        status = merchant({"action": "status"})
    except (OSError, ValueError):
        return False
    host_request = (
        None if town or urgent_recovery(status) else native_host_request(status)
    )
    host_identities = (status.get("host_request") or {}).get("identities", {})
    if host_request and not WorkWindows(HOST_STATE).due():
        host_request = None
    if host_request:
        # Independent bookkeeping must not advance, reset or discard a queued
        # fifteen-minute capacity check just to restore native window hosts.
        windows = WorkWindows(HOST_STATE)
    # The 1078 listing engine earns its own narrow capability from a real
    # listing receipt. An old build's farming-parity rollout flag must not
    # permanently block its independent fifteen-minute refill. This only
    # admits refill requests; the existing park/grant/revision checks still
    # decide whether the farmer can actually yield input.
    listing_character = qualified_listing_request(status)
    native_refill = (
        bool(listing_character)
        or town
        and any(
            c.get("connected")
            and c.get("refill", {}).get("enabled")
            and c.get("qualification", {}).get("foreground_open_booth_listing_1078")
            is True
            for c in status.get("characters", {}).values()
        )
    )
    if (
        not host_request
        and not native_refill
        and (
            (not permitted and not (town and trial_permitted(loop)))
            or (not town and not policy.get("hunting_handoffs_enabled"))
        )
    ):
        return False
    urgent = False if host_request else urgent_recovery(status)
    if host_request:
        listing_character = None
    progress = (
        pending_listing_progress(status) if not host_request and not urgent else None
    )
    continuation = bool(
        not town
        and progress
        and not windows.due()
        and windows.state()
        .get("consumed_listing_progress", {})
        .get(progress["character"])
        != progress["request_id"]
        and time.time() >= progress["verified_at"] + REFILL_CONTINUATION_SECONDS
    )
    if not town and not urgent and not windows.due() and not continuation:
        return False
    before = loop.health()
    control = before["embedded_controls"]["control"]
    if before["embedded_controls"].get("manual_mouse"):
        return False
    visit = None
    if (
        not host_request
        and town
        and (before["embedded_controls"].get("life") or {}).get("map_id") == 1036
    ):
        from conquest.merchants.service_visit import MarketVisit, parent_visit

        visit = MarketVisit().begin(parent=parent_visit())
        if time.time() >= visit["deadline"]:
            return False  # A refill-only entry cannot renew a used delivery visit.
    if town and (visit or urgent):
        listing_character = (
            None  # Preserve Market/recovery scope and its existing budget.
        )
    if host_request:
        request_id = host_request
    elif town and any(
        c.get("connected") for c in status.get("characters", {}).values()
    ):
        request_id = status.get("handoff_requested") if listing_character else None
        if not visit and not urgent and not listing_character:
            for name, character in status.get("characters", {}).items():
                snapshot = character.get("snapshot") or {}
                candidate = f"merchant-refill:{name}:{time.time_ns()}"
                if (
                    snapshot.get("inventory")
                    and len(snapshot.get("booth", [])) < 32
                    and qualified_listing_request(
                        {**status, "handoff_requested": candidate}
                    )
                    == name
                ):
                    listing_character, request_id = name, candidate
                    break
        # Non-Market town grants must use the same exact-recipient native
        # listing scope as hunting grants. A generic 15-second grant can
        # never satisfy the listing engine's 20-second admission requirement.
        request_id = request_id or "restock-refill:" + str(time.time_ns())
        merchant({"action": "refill-check", "request_id": request_id})
    else:
        request_id = status.get("handoff_requested")
    if not request_id or not any(
        service_candidate(c) for c in status.get("characters", {}).values()
    ):
        return False
    if not windows.reserve(
        request_id,
        town=town and not host_request,
        urgent=urgent,
        visit=visit,
        listing_progress=progress,
        continuation=continuation,
    ):
        return False
    was_enabled, phase = control["enabled"], loop.phase
    loop.stop_farm()
    stopped = loop.health()
    revision = stopped["embedded_controls"]["control"]["revision"]
    proof = {"target": stopped["target"]}
    granted = False
    released = True
    manually_cancelled = False
    original_check = loop.check_stop
    previous_deadline = getattr(loop, "market_service_deadline", None)
    if visit:
        loop.market_service_deadline = visit["deadline"]

    def check():
        nonlocal manually_cancelled
        original_check()
        import ctypes

        if ctypes.windll.user32.GetAsyncKeyState(0x7A) & 0x8000:
            manually_cancelled = True
            raise OvernightStopped("Merchant work paused with F11")
        current = request(loop.info, "health")
        if not resumable(current, proof, revision):
            raise OvernightStopped("Manual control changed during merchant work")

    class Cancellation:
        def is_set(self):
            check()
            return False

    loop.check_stop = check
    try:
        loop.phase = "merchant_handoff"
        loop.record(
            "merchant_safe_spot", activity="Finding a safe spot for merchant refill"
        )
        parking_started = time.monotonic()
        parking = {"outcome": "interrupted"}
        try:
            parking_budget = 30 if listing_character or host_request else 12
            seconds = (
                min(parking_budget, max(0, visit["deadline"] - time.time()))
                if visit
                else parking_budget
            )
            if seconds <= 0:
                parking.update(
                    outcome="deferred", reason="Market visit budget exhausted"
                )
                windows.finish("paused_budget")
                return False
            parked = park(
                loop,
                Cancellation(),
                lambda _: None,
                seconds=seconds,
                allow_town_retreat=False,
                diagnostic=parking,
            )
            parking.update(outcome="safe", reason="Three quiet stable seconds verified")
        except ValueError as error:
            parking.update(outcome="deferred", reason=str(error)[:180])
            windows.finish("unsafe_deferred")
            return False
        finally:
            loop.record(
                "merchant_parking_finished",
                parking={
                    **parking,
                    "elapsed_seconds": round(time.monotonic() - parking_started, 3),
                },
            )
        check()
        if not clear_observation(loop.health()):
            windows.finish("unsafe_deferred")
            return False
        deadline = windows.started(listing=bool(listing_character))
        if deadline <= time.time():
            windows.finish("paused_budget")
            return False
        command = {
            "action": "handoff-grant",
            "request_id": request_id,
            "revision": revision,
            "expires_at": deadline,
            "safe": True,
        }
        if host_request:
            command.update(scope="merchant_host")
        elif visit:
            command.update(scope="market_visit", visit_id=visit["visit_id"])
        elif listing_character:
            command.update(scope="listing_1078", character=listing_character)
        # A lost acknowledgement may still have granted input. Revoke in finally.
        granted = True
        try:
            merchant(command)
        except MerchantRejected as error:
            if not host_request or str(error) != "Handoff request changed":
                # A fresh pre-input admission rejection (for example a booth
                # changed while parking) is deferred work, not a farmer route
                # failure. Only an acknowledged empty owner/grant permits
                # skipping revoke; transport loss retains the uncertain grant.
                fresh_status = merchant({"action": "status"})
                check()
                if (
                    fresh_status.get("input_owner") is None
                    and fresh_status.get("handoff_active") is False
                    and fresh_status.get("handoff_granted") is False
                ):
                    granted = False
                    windows.finish("admission_deferred")
                    loop.record(
                        "merchant_admission_deferred",
                        reason=str(error)[:180],
                        activity="Merchant state changed; refill deferred safely",
                    )
                    return False
                raise
            # This exact rejection precedes fence activation. Safe-Off polling
            # may already have attached both hosts while the route parked.
            # Never release a different request or infer this from transport loss.
            granted = False
            manually_cancelled = True
            fresh_status = merchant({"action": "status"})
            check()
            if not host_restored_before_grant(fresh_status, host_identities):
                raise
            manually_cancelled = False
            windows.finish("host_restored_before_grant")
            return True
        loop.record(
            "merchant_work_started",
            activity=(
                "Restoring merchant client tabs safely"
                if host_request
                else "Safe merchant refill within this Market visit"
                if visit
                else "Safe merchant listing · up to 45 seconds"
                if listing_character
                else "Safe merchant refill · up to 15 seconds"
            ),
            deadline=deadline,
        )
        while time.time() < deadline:
            check()
            health = loop.health()
            if not clear_observation(health):
                break
            status = merchant({"action": "status"})
            if host_request and native_host_request(status) != host_request:
                break
            if not status.get("handoff_requested"):
                break
            time.sleep(0.2)
        windows.finish("paused_budget" if time.time() >= deadline else "released")
        return True
    finally:
        loop.check_stop = original_check
        loop.market_service_deadline = previous_deadline
        if granted:
            # Revocation blocks further input. Read-only reconciliation can
            # finish before the input lease is released; never race the farmer.
            released = False
            until = time.monotonic() + 12
            while time.monotonic() < until:
                result = merchant(
                    {"action": "handoff-release", "request_id": request_id}
                )
                if result.get("released"):
                    released = True
                    break
                original_check()
                time.sleep(0.1)
        loop.phase = phase
        original_check()
        current = loop.health()
        if not released:
            raise ValueError(
                "Merchant input did not release; farmer remains protected and stopped"
            )
        if (
            was_enabled
            and not manually_cancelled
            and resumable(current, proof, revision)
        ):
            loop.focus(current)
            request(loop.info, "controls", {"enabled": True})
            loop.record(
                "merchant_work_finished", activity="Hunting resumed after merchant work"
            )


def urgent_recovery(status):
    request = str(status.get("handoff_requested", ""))
    if not request.startswith(("merchant-recovery:", "merchant-return:")):
        return False
    parts = request.split(":")
    state = status.get("characters", {}).get(parts[1] if len(parts) > 1 else "", {})
    if (state.get("recovery_safety") or {}).get("active"):
        return True
    returning = state.get("shop_return") or {}
    return bool(
        returning.get("phase")
        not in (None, "complete", "operator_overridden", "needs_attention")
        and (state.get("snapshot") or {}).get("map_id") != 1036
    )
