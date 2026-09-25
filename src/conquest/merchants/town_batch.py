"""Town batch refill: list a large merchant backlog from the verified city.

Ordinary hunting refill parks the farmer at a quiet nearby field tile and
grants one exact-merchant 45-second listing window. A large backlog of
reliably priced items fills slowly that way. When any merchant's backlog
reaches the policy threshold, the farmer instead uses the qualified
``require_city`` parking mode (saved city terrain and anchor, checked native
travel, quiet stable living memory inside the city) and then grants the same
existing listing scope repeatedly. Every per-listing check stays in the
merchant listing engine. Before each grant the farmer rechecks its own safety;
while a merchant holds input the farmer only reads memory. If the city spot
cannot qualify, the caller falls back to the existing field behaviour.
"""

import math
import time

DEFAULTS = {
    "enabled": True,
    # Trigger: one merchant's reliably priced items that fit its free booth slots.
    "backlog_threshold": 5,
    # Keep granting until every requesting merchant's backlog is below this.
    "drain_below": 1,
    # Total batch budget, measured from departure (travel, parking, listings).
    "max_seconds": 600,
    # Budget for checked travel to the city anchor plus the quiet interval.
    "city_parking_seconds": 240,
    # Minimum hunting time after a batch before the next merchant window.
    "cooldown_seconds": 900,
    # Stop when consecutive grants produce no verified listing.
    "max_grants_without_listing": 2,
}
LIMITS = {
    "backlog_threshold": (1, 32),
    "drain_below": (1, 32),
    "max_seconds": (60, 1800),
    "city_parking_seconds": (30, 600),
    "cooldown_seconds": (0, 3600),
    "max_grants_without_listing": (1, 10),
}
POLICY_KEY = "town_batch_refill"
GRANT_SECONDS = 45  # The existing exact-merchant listing scope maximum.
MIN_GRANT_SECONDS = 40  # Listing admission requires 38 seconds remaining.
REQUEST_WAIT_SECONDS = 10
BACKLOG_FRESH_SECONDS = 10
STATUS_POLL_SECONDS = 2
RELEASE_SECONDS = 12
WAITING = ("waiting_farmer_handoff", "listing_work_budget_insufficient")
UNRECONCILED = (
    "listing_receipt_needs_reconciliation",
    "merchant_transaction_needs_reconciliation",
)
FALLBACK = "fallback_field"


class TownBatchAborted(Exception):
    """Merchant state changed while travelling.

    Deliberately not a ValueError: town travel retries some ValueErrors.
    """


class InputNotReleased(Exception):
    """A merchant grant could not be released; the farmer must stay stopped."""


def policy(raw):
    """Validated policy; any malformed value disables the batch (fail closed)."""
    row = (raw or {}).get(POLICY_KEY, {})
    if not isinstance(row, dict) or set(row) - set(DEFAULTS):
        return {**DEFAULTS, "enabled": False}
    result = {**DEFAULTS, **row}
    if type(result["enabled"]) is not bool:
        return {**DEFAULTS, "enabled": False}
    for key, (low, high) in LIMITS.items():
        value = result[key]
        if type(value) is not int or not low <= value <= high:
            return {**DEFAULTS, "enabled": False}
    return result


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def backlog(status, name, *, now=None):
    """Fresh planner count of priced items that fit free booth slots, or None.

    Only a merchant whose refill is enabled and due/pending, with its earned
    native listing capability and a current waiting planner result, counts.
    Unknown-price items never count; the merchant planner leaves them deferred.
    """
    now = time.time() if now is None else now
    character = (status.get("characters") or {}).get(name) or {}
    refill = character.get("refill") or {}
    work = character.get("foreground_refill_1078") or {}
    value = work.get("eligible_backlog")
    observed_at = work.get("backlog_observed_at")
    next_check = refill.get("next_check")
    due = refill.get("pending") is True or (_number(next_check) and next_check <= now)
    if (
        not character.get("connected")
        or refill.get("enabled") is not True
        or (character.get("qualification") or {}).get(
            "foreground_open_booth_listing_1078"
        )
        is not True
        or not due
        or work.get("blocker") not in WAITING
        or type(value) is not int
        or value < 0
        or not _number(observed_at)
        or not 0 <= now - observed_at <= BACKLOG_FRESH_SECONDS
    ):
        return None
    return value


def backlogs(status, *, now=None):
    return {
        name: backlog(status, name, now=now)
        for name in sorted(status.get("characters") or {})
    }


def merchant_blockers(status):
    """Merchant-side reasons that forbid starting or continuing a batch."""
    from conquest.merchants.handoff import urgent_recovery

    reasons = []
    if status.get("manual_handoff") is not None:
        reasons.append("manual_handoff")
    if status.get("manual_sessions"):
        reasons.append("manual_session")
    if (status.get("manual_farmer") or {}).get("input_fenced"):
        reasons.append("manual_farmer_input")
    if status.get("host_request") is not None:
        reasons.append("merchant_host_request")
    if urgent_recovery(status):
        reasons.append("merchant_recovery")
    characters = status.get("characters")
    if not isinstance(characters, dict) or not characters:
        return reasons + ["merchant_status_unavailable"]
    refill_enabled = False
    for name in sorted(characters):
        character = characters[name] or {}
        refill = character.get("refill") or {}
        work = character.get("foreground_refill_1078") or {}
        shop_return = character.get("shop_return") or {}
        refill_enabled = refill_enabled or refill.get("enabled") is True
        if character.get("pending"):
            reasons.append(f"{name}:pending_transaction")
        if character.get("needs_attention"):
            reasons.append(f"{name}:needs_attention")
        if character.get("manual_input_fence"):
            reasons.append(f"{name}:manual_input")
        if (character.get("recovery_safety") or {}).get("active"):
            reasons.append(f"{name}:recovery")
        if shop_return.get("phase") not in (None, "complete", "operator_overridden"):
            reasons.append(f"{name}:shop_return")
        if refill.get("listing1078_request") or work.get("blocker") in UNRECONCILED:
            # An in-flight or uncertain listing is reconciled by the merchant
            # engine; a batch never grants past it or replays it.
            reasons.append(f"{name}:listing_unreconciled")
    if not refill_enabled:
        reasons.append("refill_paused_or_global_stop")
    return reasons


def farmer_blockers(loop, health):
    """Farmer-side obligations that outrank a merchant batch."""
    data = health.get("embedded_controls") or {}
    life = data.get("life") or {}
    control = data.get("control") or {}
    route = getattr(loop, "route", None)
    reasons = []
    if getattr(loop, "phase", None) != "hunting":
        reasons.append("farmer_not_hunting")
    if data.get("manual_mouse") or data.get("manual_input_fence"):
        reasons.append("manual_input")
    if control.get("paused"):
        reasons.append("farmer_paused")
    if not life or life.get("dead_candidate"):
        reasons.append("farmer_not_alive")
    if route is None or life.get("map_id") != getattr(route, "restock_map_id", None):
        reasons.append("not_on_city_map")

    def town_visit():
        visits = getattr(loop, "town_visit", None)
        return visits is not None and visits.active_id() is not None

    def death_return():
        from conquest.discord_notify import read_json
        from conquest.overnight import RECOVERY_CHECKPOINT

        saved = read_json(RECOVERY_CHECKPOINT)
        return bool(saved) and saved.get("phase") not in ("completed", "cancelled")

    def delivery_journey():
        from conquest.merchants import delivery_journey

        return delivery_journey.pending()

    def delivery_operation():
        from conquest.merchants import delivery_operation

        return delivery_operation.pending()

    def delivery_route():
        from conquest.merchants import delivery_route

        return delivery_route.pending()

    def meteor_banking():
        from conquest import meteor_banking

        return meteor_banking.pending()

    def storage_overflow():
        from conquest import storage_overflow

        return storage_overflow.pending()

    def manual_storage():
        from conquest import manual_storage_recovery

        return manual_storage_recovery.pending()

    def acceptance():
        from conquest import merchant_loop_acceptance

        return merchant_loop_acceptance.cycle_pending()

    for name, active in (
        ("town_visit", town_visit),
        ("death_return", death_return),
        ("delivery_journey", delivery_journey),
        ("farmer_delivery", delivery_operation),
        ("merchant_delivery", delivery_route),
        ("meteor_banking", meteor_banking),
        ("storage_overflow", storage_overflow),
        ("manual_storage_recovery", manual_storage),
        ("merchant_acceptance", acceptance),
    ):
        try:
            if active():
                reasons.append(name + "_in_progress")
        except Exception:
            reasons.append(name + "_unreadable")
    return reasons


def admission(loop, status, health, raw_policy):
    """Return a batch plan, or None to keep the existing field behaviour."""
    from conquest.merchants.handoff import qualified_listing_request

    rules = policy(raw_policy)
    if not rules["enabled"]:
        return None
    now = time.time()
    counts = backlogs(status, now=now)
    ready = {
        name: value
        for name, value in counts.items()
        if value is not None and value >= rules["backlog_threshold"]
    }
    if not ready:
        return None
    reasons = merchant_blockers(status) + farmer_blockers(loop, health)
    if status.get("input_owner") is not None or status.get("handoff_active"):
        reasons.append("merchant_input_active")
    character = qualified_listing_request(status)
    if character is None:
        reasons.append("no_qualified_listing_request")
    if reasons:
        fingerprint = (tuple(reasons), tuple(sorted(ready)))
        previous = getattr(loop, "town_batch_refusal", None)
        if not previous or previous[0] != fingerprint or now - previous[1] >= 60:
            loop.town_batch_refusal = (fingerprint, now)
            loop.record(
                "merchant_town_batch_refused",
                town_batch_reasons=reasons[:12],
                backlogs=counts,
                backlog_threshold=rules["backlog_threshold"],
            )
        return None
    return {
        "character": character,
        "backlogs": counts,
        "trigger": max(sorted(ready), key=ready.get),
        "policy": rules,
    }


def farmer_unsafe(health, proof, city, last_hp):
    """Reason the parked farmer cannot yield input now, or None."""
    from conquest.safe_reload import clear_observation, _in_city_parking_spot

    data = health.get("embedded_controls") or {}
    life = data.get("life")
    if health.get("target") != proof["target"]:
        return "farmer_process_changed"
    if data.get("manual_mouse") or data.get("manual_input_fence"):
        return "manual_input"
    if data.get("external_execution"):
        return "farmer_execution_active"
    if not life:
        return "observation_gap"
    if life.get("dead_candidate"):
        return "farmer_dead"
    if life.get("map_id") != proof["map_id"] or not _in_city_parking_spot(
        life, city, tuple(proof["city_anchor"])
    ):
        return "farmer_left_city_spot"
    if life["current_hp"] < last_hp:
        return "farmer_damaged"
    if not clear_observation(health):
        observed_at = data.get("observed_at")
        if not data.get("observations_available") or not (
            _number(observed_at) and 0 <= time.time() - observed_at <= 1
        ):
            return "observation_gap"
        return "threat_or_low_health"
    return None


def _listing_proof(status, name):
    character = (status.get("characters") or {}).get(name) or {}
    proof = (character.get("refill") or {}).get("last_verified_listing") or {}
    request_id, verified_at = proof.get("request_id"), proof.get("verified_at")
    if (
        isinstance(request_id, str)
        and request_id.startswith("booth-list1078-refill-")
        and _number(verified_at)
        and proof.get("character") == name
    ):
        return request_id, verified_at
    return None


def _grant_finished(status, name, grant_started):
    """The merchant verified a listing and has no more work in this grant.

    Either it cannot start another listing with the time left, or its refill
    check completed (booth full, no stock, or only unknown prices remain).
    """
    character = (status.get("characters") or {}).get(name) or {}
    refill = character.get("refill") or {}
    work = character.get("foreground_refill_1078") or {}
    proof = _listing_proof(status, name)
    return bool(
        proof
        and proof[1] >= grant_started
        and not refill.get("listing1078_request")
        and (
            work.get("blocker") == "listing_work_budget_insufficient"
            or work.get("state") in ("capacity_checked", "unknown_prices_deferred")
        )
        and status.get("input_owner") is None
    )


def _release(merchant, key):
    """Revoke and release one grant; never resume the farmer on uncertainty."""
    until = time.monotonic() + RELEASE_SECONDS
    while time.monotonic() < until:
        try:
            result = merchant({"action": "handoff-release", "request_id": key})
        except (OSError, ValueError):
            result = {}
        if result.get("released") is True:
            return
        time.sleep(0.1)
    raise InputNotReleased(
        "Merchant input did not release; farmer remains protected and stopped"
    )


def run(loop, plan, *, windows, merchant, check, revision):
    """Park in the verified city and run successive exact listing grants.

    Returns FALLBACK when city parking cannot qualify (the caller continues
    with the existing field parking), True after a parked batch, and False
    when merchant state changed before any grant.
    """
    from conquest.merchants.bridge import MerchantRejected
    from conquest.merchants.handoff import qualified_listing_request
    from conquest.safe_reload import park

    rules = plan["policy"]
    started_wall, started = time.time(), time.monotonic()
    deadline = started + rules["max_seconds"]
    listings = {}
    latest = {}
    summary = {
        "outcome": "interrupted",
        "grants": 0,
        "backlogs_before": plan["backlogs"],
        "backlogs_after": None,
        "parking_seconds": None,
        "trigger": plan["trigger"],
        "max_seconds": rules["max_seconds"],
    }
    fallback = False

    def observe(status):
        for name in status.get("characters") or {}:
            proof = _listing_proof(status, name)
            if proof and proof[1] >= started_wall:
                listings.setdefault(name, set()).add(proof[0])
                latest[name] = proof[0]
        summary["backlogs_after"] = backlogs(status)

    def listed():
        return sum(len(rows) for rows in listings.values())

    loop.record(
        "merchant_town_batch_started",
        backlogs=plan["backlogs"],
        backlog_threshold=rules["backlog_threshold"],
        max_seconds=rules["max_seconds"],
        activity="Merchant backlog: walking into town to list a batch safely",
    )
    try:
        parking = {"outcome": "interrupted", "mode": "city"}
        parking_started = time.monotonic()
        polled = [0.0]
        missing_since = [None]

        def watched():
            check()
            if time.monotonic() - polled[0] < STATUS_POLL_SECONDS:
                return
            polled[0] = time.monotonic()
            try:
                status = merchant({"action": "status"})
            except (OSError, ValueError) as error:
                raise TownBatchAborted("merchant_status_unavailable") from error
            reasons = merchant_blockers(status)
            if reasons:
                raise TownBatchAborted(", ".join(reasons))
            # A merchant re-requests within a tick or two; only a persistent
            # absence means there is no refill work left to walk for.
            if qualified_listing_request(status) is not None:
                missing_since[0] = None
            elif missing_since[0] is None:
                missing_since[0] = time.monotonic()
            elif time.monotonic() - missing_since[0] >= REQUEST_WAIT_SECONDS:
                raise TownBatchAborted("no_qualified_listing_request")

        class Cancellation:
            def is_set(self):
                watched()
                return False

        previous_check = loop.check_stop
        loop.check_stop = watched
        try:
            proof = park(
                loop,
                Cancellation(),
                lambda _: None,
                seconds=min(rules["city_parking_seconds"], rules["max_seconds"]),
                allow_town_retreat=False,
                diagnostic=parking,
                require_city=True,
                travel_activity="Walking into town for a safe merchant listing batch",
            )
            parking.update(outcome="safe", reason="Quiet stable city spot verified")
        except TownBatchAborted as error:
            parking.update(outcome="aborted", reason=str(error)[:180])
            summary["outcome"] = "merchant_state_changed"
            return False
        except ValueError as error:
            parking.update(outcome="deferred", reason=str(error)[:180])
            summary["outcome"] = "city_parking_failed"
            fallback = True
            return FALLBACK
        finally:
            loop.check_stop = previous_check
            summary["parking_seconds"] = round(time.monotonic() - parking_started, 3)
            loop.record(
                "merchant_parking_finished",
                parking={**parking, "elapsed_seconds": summary["parking_seconds"]},
            )
        from conquest.city_travel import city_for

        try:
            city = city_for(proof["map_id"])
            if city["terrain_sha256"] != proof["city_terrain_sha256"]:
                raise ValueError("City terrain changed after parking")
        except (KeyError, TypeError, ValueError, OSError):
            summary["outcome"] = "city_proof_unavailable"
            return False
        last_hp = proof["hp"]
        idle_since = None
        rejections = 0
        without_listing = 0

        def verified_safe():
            nonlocal last_hp
            reason = None
            for _ in range(4):
                check()
                health = loop.health()
                reason = farmer_unsafe(health, proof, city, last_hp)
                if reason is None:
                    last_hp = health["embedded_controls"]["life"]["current_hp"]
                    return None
                if reason != "observation_gap":
                    return reason
                time.sleep(0.5)
            return reason

        while True:
            check()
            if deadline - time.monotonic() < MIN_GRANT_SECONDS:
                summary["outcome"] = "cap_reached"
                break
            try:
                status = merchant({"action": "status"})
            except (OSError, ValueError):
                summary["outcome"] = "merchant_status_unavailable"
                break
            observe(status)
            reasons = merchant_blockers(status)
            if status.get("input_owner") is not None or status.get("handoff_active"):
                reasons.append("merchant_input_active")
            if reasons:
                summary.update(outcome="merchant_blocked", reasons=reasons[:12])
                break
            character = qualified_listing_request(status)
            key = status.get("handoff_requested")
            if character is None:
                if key is not None and not str(key).startswith("merchant-refill:"):
                    summary["outcome"] = "other_handoff_requested"
                    break
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since >= REQUEST_WAIT_SECONDS:
                    summary["outcome"] = "backlog_cleared"
                    break
                time.sleep(0.5)
                continue
            idle_since = None
            known = [v for v in backlogs(status).values() if v is not None]
            if known and all(v < rules["drain_below"] for v in known):
                summary["outcome"] = "backlog_below_threshold"
                break
            unsafe = verified_safe()
            if unsafe:
                summary["outcome"] = "farmer_unsafe:" + unsafe
                break
            remaining = deadline - time.monotonic()
            grant_started = time.time()
            expires_at = grant_started + min(GRANT_SECONDS, remaining)
            before = listed()
            granted = True
            ended = None
            try:
                try:
                    merchant(
                        {
                            "action": "handoff-grant",
                            "request_id": key,
                            "revision": revision,
                            "expires_at": expires_at,
                            "safe": True,
                            "scope": "listing_1078",
                            "character": character,
                        }
                    )
                except MerchantRejected as error:
                    # Only an acknowledged empty owner/grant permits skipping
                    # revoke; transport loss keeps the uncertain grant revoked.
                    fresh = merchant({"action": "status"})
                    check()
                    if not (
                        fresh.get("input_owner") is None
                        and fresh.get("handoff_active") is False
                        and fresh.get("handoff_granted") is False
                    ):
                        raise
                    granted = False
                    rejections += 1
                    loop.record(
                        "merchant_admission_deferred",
                        reason=str(error)[:180],
                        scope="town_batch",
                    )
                    if rejections >= 3:
                        summary["outcome"] = "admission_rejected"
                        break
                    time.sleep(1)
                    continue
                rejections = 0
                summary["grants"] += 1
                loop.record(
                    "merchant_work_started",
                    activity=f"Town merchant batch · {character} listing · "
                    f"up to {round(expires_at - grant_started)} seconds",
                    deadline=expires_at,
                    town_batch_grant=summary["grants"],
                    character=character,
                )
                while time.time() < expires_at:
                    check()
                    health = loop.health()
                    ended = farmer_unsafe(health, proof, city, last_hp)
                    if ended:
                        break
                    last_hp = health["embedded_controls"]["life"]["current_hp"]
                    status = merchant({"action": "status"})
                    observe(status)
                    if status.get("handoff_requested") != key:
                        ended = "merchant_released"
                        break
                    if _grant_finished(status, character, grant_started):
                        ended = "listing_verified"
                        break
                    time.sleep(0.2)
                else:
                    ended = "grant_expired"
            finally:
                if granted:
                    _release(merchant, key)
            # A listing verified at the deadline settles on the next merchant
            # tick. An in-flight or uncertain request stays with its engine.
            settle_until = time.monotonic() + 5
            while True:
                status = merchant({"action": "status"})
                observe(status)
                pending = (
                    ((status.get("characters") or {}).get(character) or {}).get(
                        "refill"
                    )
                    or {}
                ).get("listing1078_request")
                if not pending or time.monotonic() >= settle_until:
                    break
                check()
                time.sleep(0.25)
            summary["last_grant_end"] = ended
            if pending:
                summary["outcome"] = "listing_needs_reconciliation"
                break
            # Farmer safety is rechecked before the next grant (verified_safe).
            if listed() > before:
                without_listing = 0
            else:
                without_listing += 1
                if without_listing >= rules["max_grants_without_listing"]:
                    summary["outcome"] = "no_listing_progress"
                    break
        return True
    finally:
        summary.update(
            elapsed_seconds=round(time.monotonic() - started, 3),
            listings={name: len(rows) for name, rows in sorted(listings.items())},
            listed=listed(),
        )
        loop.record(
            "merchant_town_batch_finished",
            town_batch=summary,
            activity=(
                "Town parking unavailable; using a quiet nearby spot instead"
                if fallback
                else "Merchant batch finished; heading back to the hunting area"
            ),
        )
        if not fallback:
            # Hunt a full cooldown before the next merchant window. Receipts
            # earned in town are consumed so they cannot immediately earn a
            # field continuation window either.
            state = windows.state()
            windows.finish(
                "town_batch_finished",
                town_batch_outcome=summary["outcome"],
                next_check=max(
                    state.get("next_check", 0), time.time() + rules["cooldown_seconds"]
                ),
                consumed_listing_progress={
                    **(state.get("consumed_listing_progress") or {}),
                    **latest,
                },
            )
