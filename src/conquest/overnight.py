"""Reusable foreground hunt / town / sell / restock loop, independent of the AI."""

from conquest.character_context import installation_path, state_path
import ctypes
import json
import os
from pathlib import Path
import time

from conquest.navigation import read_terrain, native_waypoint
from conquest.route_input import BridgeJumpStepper
from conquest.routes import RouteLibrary
from conquest.travel_care import TravelCare, TravelStateChanged
from conquest.town_trade import junk_type, sale_candidate, TownObservationUnavailable
from conquest.potion_tiers import HEALING_POTIONS
from conquest.arrow_upgrades import REMNANT_ARROWS
from conquest.worker import request
from conquest.capture import CaptureUnavailable

RECOVERY_CHECKPOINT = Path(state_path(".runtime/death-return.json"))
# Approval timeout (5 s) + one decline + settlement (5 s) with margin; an
# uncertain decline is never replayed, so the route then needs attention.
MANUAL_REQUEST_WAIT_SECONDS = 60
# Unexpected route failures restart in-process this many times per rolling
# hour (after a short protected pause) before the route stops for attention.
AUTO_RESTARTS = 3
AUTO_RESTART_PAUSE_SECONDS = 20
# During a restart pause or failure cooldown outside town, a living monster
# this close calls for one escape jump, at most every FIELD_EVADE_SECONDS.
FIELD_EVADE_TILES = 4
FIELD_EVADE_SECONDS = 1.2
# A restock this far (Chebyshev) from its town's anchor reads the town's gate
# instead of walking: the GiantApe plain and the ThunderApes are 195-245 out
# of Ape City, the Macaques ~100.
GATE_HOME_TILES = 150
# The gate is read only from a quiet spot: no HP lost for GATE_QUIET_SECONDS,
# no living monster within GATE_CLEAR_TILES and every boss beyond its
# clearance + 4. Escape jumps look for one for up to GATE_SETTLE_SECONDS;
# without one the restock walks home.
GATE_QUIET_SECONDS = 3.0
GATE_CLEAR_TILES = 10
GATE_SETTLE_SECONDS = 20.0
# Once the hourly restart budget is spent: pause this long (escalating), then
# replan from fresh reads again instead of stopping for good.
FAILURE_COOLDOWNS = (120, 300, 600)


_TOWN_BOXES = {}


def town_box(map_id):
    """The saved town boundary (left, top, right, bottom) of `map_id`, or None."""
    if map_id not in _TOWN_BOXES:
        from conquest.city_travel import CITIES

        try:
            cities = json.loads(CITIES.read_text(encoding="utf-8"))["cities"]
        except (OSError, ValueError, KeyError):
            return None
        found = [c for c in cities if c.get("map_id") == map_id]
        _TOWN_BOXES[map_id] = (
            tuple(found[0]["town_boundary"]) if len(found) == 1 else None
        )
    return _TOWN_BOXES[map_id]


class OvernightStopped(Exception):
    pass


def read_status(path):
    # Windows may briefly deny a reader while the elevated app replaces its
    # status file. That does not mean the game or controller has stopped.
    for attempt in range(20):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (PermissionError, json.JSONDecodeError):
            if attempt == 19:
                raise
            time.sleep(0.025)


def select_app_route(loop):
    """Make the app's route selection the controller's route.

    The app builds the farm from its own selection (boundary, patrol, King
    clearance) and refuses targets from another route. A controller
    restarted across a route-hold change loads the plan's route while the
    app restores its saved one: Toxic, deployed at 15:53 on 2026-09-28 as
    its hold moved to Ratlings, stood in Phoenix with "Selected route and
    monster group differ" until the route was selected by hand.
    """
    app = state_path("reports/desktop-farming/app-state.json")
    try:
        state = read_status(app)
    except OSError:
        return False
    if "selected_route" not in state or state["selected_route"] == loop.route.id:
        return False
    loop.stop_farm()
    deadline = time.monotonic() + 10
    while True:
        try:
            request(loop.info, "controls", {"route_id": loop.route.id})
            break
        except ValueError:
            # Refused while the stopped farm thread winds down.
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)
    while read_status(app).get("selected_route") != loop.route.id:
        loop.check_stop()
        if time.monotonic() > deadline:
            raise ValueError("Route selection was not acknowledged")
        time.sleep(0.1)
    loop.record(
        "app_route_selected",
        route=loop.route.id,
        previous_route=state["selected_route"],
        activity=f"Selecting {loop.route.name} in the app",
    )
    return True


def manual_request_fence(loop, error):
    """The pre-input refusal is only an unapproved-request fence.

    Approved sessions, reader holds and other refusals keep the ordinary
    bounded retry/failure path. The app's projection grants no input.
    """
    from conquest.town_trade import MANUAL_TRADE_FENCE

    if not isinstance(error, TownObservationUnavailable) or (
        str(error) != MANUAL_TRADE_FENCE
    ):
        return False
    controls = loop.health()["embedded_controls"]
    return bool(
        controls.get("manual_input_fence") and controls.get("manual_request_pending")
    )


def supply_counts(snapshot, route):
    arrows = sum(
        i["amount"]
        for i in snapshot["items"]
        if i["type_id"] == route.supplies.arrow_type
    )
    ammo = snapshot.get("equipped_ammo")
    if ammo and ammo["type_id"] == route.supplies.arrow_type:
        arrows += ammo["amount"]
    from conquest import potion_tiers

    potions = potion_tiers.count(snapshot, route.supplies.healing_type)
    return {
        "arrows": arrows,
        "potions": potions,
        "free_slots": snapshot["capacity"] - len(snapshot["items"]),
        "silver": snapshot["silver"],
    }


def potion_reserve(route, departed=None):
    """Potions kept for the walk back: while the level goal runs, and always
    on Back2Classic.

    The walk from the low-level fields to Twin City takes minutes; leaving
    with none meant arriving (or dying) on an empty bar (09-27). A trip that
    left town with few potions keeps at most half of them, so a poor restock
    hunts before it walks back instead of turning straight round.

    The quarter is of the larger of the route's fill and what the hunt left
    with: a restarted controller reads the route file's fill until the next
    planned restock (bandit.yaml: 5, so a reserve of 1), and Suicide, gone out
    with 18, turned home on its last potion and died on the way (2026-09-28
    08:01-08:02).
    """
    from conquest import level_goal

    if not level_goal.protections():
        return 0
    fill = max(route.supplies.healing_restock_to, departed or 0)
    reserve = min(TRIP_POTION_RESERVE, fill // 4)
    if departed is not None:
        reserve = min(reserve, departed // 2)
    return reserve


TRIP_POTION_RESERVE = 5
# A town-travel iteration slower than this is logged with its phase timings.
SLOW_TRAVEL_STEP_SECONDS = 4


def needs_town(counts, route, *, departed=None, reserve=False):
    """Whether supplies send the farmer to town.

    The trip reserve applies only where a caller decides to walk to town or
    top up: a finished restock with at least one potion, three arrows and a
    free slot can hunt.
    """
    kept = potion_reserve(route, departed) if reserve else 0
    return (
        counts["arrows"] < 3
        or counts["potions"] <= kept
        or counts["free_slots"] <= 0
    )


def last_verified_price(type_id, path=None):
    """Price of the newest verified purchase of this type, or None.

    Read from this character's own event log, so it is a price the shop
    actually charged, never a guess.
    """
    path = Path(path or state_path("reports/overnight/events.jsonl"))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"purchase"' not in line:
            continue
        try:
            receipt = json.loads(line).get("receipt") or {}
        except ValueError:
            continue
        price = receipt.get("price")
        if receipt.get("bought") == type_id and type(price) is int and price > 0:
            return price
    return None


# Potions a farmer carries before arrow silver may cap further potion buys.
# Below this it keeps buying potions; if that leaves no arrow money, the
# arrow purchase refuses and the farmer stays in town. Ten stranded Toxic
# twice on 2026-09-27: at 18:18 nine Resolutives (18 each) left 143 silver
# for a 200-silver pack and 2 arrows, and no hunt can earn it back. Five
# potions and a full quiver hunt; nine potions and no arrows cannot.
SAFE_HUNT_POTIONS = 5
# In-place restarts of a combat runner stopped by an observation or input
# error, per window, before the route falls back to its own full restart.
RUNNER_RESTARTS = 3
RUNNER_RESTART_WINDOW = 120
# Potions used this fast send the farmer to town while some are left: busy
# Bandit fights used 2.5-3.3 in two minutes; at 01:00-01:03 on 2026-09-28
# Toxic used 16 in three, turned for town on its last one and died on the way.
HEAVY_BURN_POTIONS = 6
HEAVY_BURN_WINDOW = 120
# An IronArrow or SpeedArrow top-up while the quiver can still shoot is
# optional: buy_supply takes it only if this much silver stays after it.
OPTIONAL_ARROW_FLOOR = 3000
# Carried arrows lasting less than this at the route's learned rate make the
# top-up required: the hunt would come straight back for arrows (live
# 2026-09-28 00:25, Toxic: 184 IronArrows, about four minutes, and 6,082
# silver banked).
MIN_TOPUP_MINUTES = 10


# Route travel walks around a boss in the scene (routes.boss_zone). With no
# way around it waits this long for the boss to move, then walks on; a
# detour re-baselines the no-progress watchdog at most BOSS_DETOUR_RESETS
# times a trip. Toxic's WingedSnake walk ran through the King's roaming box
# and runbacks crossed the Bandit field past its Kings (2026-09-28).
BOSS_WAIT_SECONDS = 20
BOSS_DETOUR_RESETS = 3


# A bag stack of REMNANT_ARROWS or fewer may be sold when it holds one of a
# tier's pack slots while less than a pack is carried. Real partial packs
# still count against the limit and prevent top-ups (one equipped pack and
# one spare; test_purchase_cap_counts_partial_packs_and_prevents_topups).
# REMNANT_ARROWS lives in arrow_upgrades: an equipped remnant is not a pack.


def blocking_remnant(snapshot, kind):
    """The smallest bag stack of REMNANT_ARROWS or fewer counted against
    buying ``kind``, when all such arrows carried add up to less than one
    ``kind`` pack; else None.

    Partial packs count against the pack limit on purpose (one equipped pack
    and one spare), but a scrap is no spare. Live 2026-09-28 03:59 (Toxic):
    the equipped IronArrow stack (140) and a 3-arrow stack filled the
    two-pack limit, the planned pack was refused and it left town with 143
    arrows and 10,664 silver banked.
    """
    from conquest.arrow_upgrades import (
        ARROW_REFILL_AMOUNTS,
        MAX_ARROW_PACKS,
        counted_tiers,
    )

    tiers = counted_tiers(kind)
    items = [
        i for i in snapshot["items"] if i["type_id"] in tiers and i["amount"] > 0
    ]
    ammo = snapshot.get("equipped_ammo") or {}
    carried = sum(i["amount"] for i in items)
    if ammo.get("type_id") in tiers and not any(
        i.get("uid") == ammo.get("uid") for i in items
    ):
        carried += ammo.get("amount") or 0
    if carried >= ARROW_REFILL_AMOUNTS.get(kind, 0) // MAX_ARROW_PACKS:
        return None
    small = [
        i
        for i in items
        if i["amount"] <= REMNANT_ARROWS and i.get("uid") != ammo.get("uid")
    ]
    return min(small, key=lambda i: i["amount"], default=None)


def optional_top_up(counts, route):
    """Whether an IronArrow or SpeedArrow pack may wait for OPTIONAL_ARROW_FLOOR:
    only while the carried arrows still hunt MIN_TOPUP_MINUTES at the route's
    learned rate (supply_plan), or, unmeasured, from the return threshold."""
    if route.supplies.arrow_type not in (1050001, 1050002):
        return False
    if counts["arrows"] < route.supplies.arrows_return_below:
        return False
    from conquest.discord_notify import read_json
    from conquest.supply_plan import RATES

    rates = read_json(RATES).get("routes", {}).get(getattr(route, "id", None)) or {}
    rate = rates.get("arrows_per_min")
    measured = type(rate) in (int, float) and rate > 0
    return not (measured and counts["arrows"] < rate * MIN_TOPUP_MINUTES)


def arrow_reserve(counts, route, arrow_price):
    """Silver a restock keeps for arrows: one verified pack's price while less
    than a pack is carried, else nothing. Unknown prices keep nothing.

    At 15:20 on 2026-09-27 Suicide came in with 53 arrows (about a minute of
    shooting), spent 192 of its 385 silver on potions and left 7 short of a
    200-silver pack.

    IronArrows and SpeedArrows come after the planned potions (supply_plan
    already splits the budget between them): an optional top-up keeps
    nothing, and a required refill keeps one LuckyArrow pack, the tier it
    falls back to when the wallet cannot pay (buy_refill_arrows). At 00:26 on
    2026-09-28 Suicide came in with 380 IronArrows and 4,262 silver; potions
    stopped at 5 of the planned 23 to keep 4,800, the pack was then deferred
    for its floor, and it left with 5 potions and banked 4,002.
    """
    from conquest.arrow_upgrades import ARROW_REFILL_AMOUNTS, MAX_ARROW_PACKS

    kind = route.supplies.arrow_type
    pack = ARROW_REFILL_AMOUNTS.get(kind, 0) // MAX_ARROW_PACKS
    short = counts["arrows"] < max(route.supplies.arrows_return_below, pack)
    if not (arrow_price and short):
        return 0
    if kind == 1050000:
        return arrow_price
    if optional_top_up(counts, route):
        return 0
    return last_verified_price(1050000) or 200


def potion_budget_reached(counts, route, potion_price, arrow_price):
    """Stop potions while the next one would leave too little for arrows.

    A short-of-arrows restock spent every coin on Painkillers and then could
    not buy a single arrow pack, stranding the farmer in town (live
    2026-09-27). Once a safe hunt's potions are carried, keep arrow_reserve.
    """
    keep = arrow_reserve(counts, route, arrow_price)
    return bool(
        potion_price
        and keep
        and counts["potions"]
        >= max(route.supplies.healing_return_below, SAFE_HUNT_POTIONS)
        and counts["silver"] - potion_price < keep
    )


def pharmacist_needed(snapshot, route, *, scroll_enabled=False):
    counts = supply_counts(snapshot, route)
    if counts["potions"] < route.supplies.healing_restock_to:
        return True
    if any(sale_candidate(item) for item in snapshot["items"]):
        return True
    from conquest.return_scroll import GATES

    gate = GATES.get(route.restock_map_id)
    return (
        scroll_enabled
        and gate is not None
        and sum(i["amount"] for i in snapshot["items"] if i["type_id"] == gate) < 2
    )


class OvernightLoop:
    def __init__(self, route_id="turtledove", hours=None, *, first_hunt_seconds=None):
        if hours is not None and not 0 < hours <= 10:
            raise ValueError("Overnight duration must be at most ten hours")
        from conquest.session_plan import active_plan

        plan = active_plan()
        route_id = plan["route_id"] if plan else route_id
        self.route = RouteLibrary().load(route_id)
        from conquest.savings import configure_route

        self.route = configure_route(self.route)
        from conquest import potion_tiers

        if potion_tiers.adaptive() and potion_tiers.TIER.exists():
            # Resume with the tier bought on the last Pharmacist visit.
            self.route = self.route.model_copy(
                update={
                    "supplies": self.route.supplies.model_copy(
                        update={"healing_type": potion_tiers.active_type()}
                    )
                }
            )
        self.queue_route_optimization()
        self.terrain = read_terrain(
            installation_path(r"C:\Program Files\Classic Conquer 2.0"),
            self.route.map_id,
        )
        self.deadline = None if hours is None else time.monotonic() + hours * 3600
        self.first_hunt_seconds = first_hunt_seconds
        self.info = self.care = self.stepper = None
        self.identity = None
        self.auto_level = True
        self.next_level_check = 0
        self.last_level = 0
        self.phase = "starting"
        self.cycles = 0
        self.output = Path(state_path("reports/overnight"))
        self.output.mkdir(parents=True, exist_ok=True)
        self.stop_path = Path(state_path(".runtime/overnight.stop"))
        self.state = {
            "pid": os.getpid(),
            "route": route_id,
            "cycles": 0,
            "started_at": time.time(),
            "ends_at": None if hours is None else time.time() + hours * 3600,
        }
        from conquest.town_visit import TownVisit

        self.town_visit = TownVisit()

    def queue_route_optimization(self):
        from conquest.route_optimization import queue_area

        try:
            queue_area(self.route)
        except (OSError, ValueError, KeyError):
            # Benchmark bookkeeping cannot interrupt healing or farming.
            pass

    def record(self, event, **fields):
        visits = getattr(self, "town_visit", None)
        if visits is not None:
            fields.setdefault("town_visit_id", visits.active_id())
        self.state.update(
            phase=self.phase,
            cycles=self.cycles,
            updated_at=time.time(),
            event=event,
            **fields,
        )
        temporary = self.output / "status.tmp"
        temporary.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        for attempt in range(20):
            try:
                temporary.replace(self.output / "status.json")
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.025)
        if event == "heartbeat":
            return
        if event == "level_route_pending":
            # Keep status fresh, but an unchanged unavailable route is not a
            # new incident every five-second level check.
            fingerprint = json.dumps(
                {
                    "route": getattr(getattr(self, "route", None), "id", None),
                    "phase": self.phase,
                    **fields,
                },
                sort_keys=True,
            )
            now = time.monotonic()
            previous = getattr(self, "pending_route_event", None)
            if previous and previous[0] == fingerprint and 0 <= now - previous[1] < 60:
                return
            self.pending_route_event = (fingerprint, now)
        with (self.output / "events.jsonl").open("a", encoding="utf-8") as out:
            out.write(
                json.dumps(
                    {"time": time.time(), "event": event, "phase": self.phase, **fields}
                )
                + "\n"
            )

    def check_stop(self):
        from conquest.storage_halt import active, HALT, REASON

        if active():
            from conquest.discord_notify import read_json

            raise OvernightStopped(read_json(HALT).get("reason", REASON))
        if (
            self.stop_path.exists()
            or ctypes.windll.user32.GetAsyncKeyState(0x7B) & 0x8000
        ):
            raise OvernightStopped("Stopped by user")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise OvernightStopped("Overnight duration finished")

    def refresh(self):
        state = read_status(state_path("reports/desktop-farming/app-state.json"))
        info = state.get("worker_info_path")
        if not info:
            raise ValueError("Embed the client before starting the overnight route")
        if info != self.info:
            self.info = info
            self.care = TravelCare(
                info, lambda event: self.record(event.pop("event"), **event)
            )
            self.stepper = BridgeJumpStepper(info, on_life=self.care.check)

    def health(self):
        self.check_stop()
        self.refresh()
        result = request(self.info, "health")
        if getattr(self, "runback_watch", None):
            self.runback_watch.observe_health(result)
        if time.time() - self.state.get("updated_at", 0) > 2:
            self.record("heartbeat")
        if self.identity is None:
            self.identity = result["target"]
        elif result["target"] != self.identity:
            raise OvernightStopped(
                "Client process changed; restart the route for that client"
            )
        return result

    def focus(self, health):
        if health.get("embedded_controls", {}).get("manual_mouse"):
            return False
        from conquest.focus_recovery import activate_client
        import pywintypes

        window = health["window"]
        if window["foreground"] == window["root_hwnd"] and not window["minimized"]:
            return True
        now = time.monotonic()
        if now < getattr(self, "next_focus_attempt", 0):
            return False
        self.next_focus_attempt = now + 1
        self.check_stop()
        try:
            return activate_client(window["hwnd"], health["target"])
        except (OSError, ValueError, pywintypes.error):
            return False

    def living(self):
        while True:
            service_deadline = getattr(self, "market_service_deadline", None)
            if service_deadline is not None and time.time() >= service_deadline:
                from conquest.travel_progress import TravelStalled

                raise TravelStalled(
                    "Market merchant-service deadline expired; defer further input",
                    code="service_deadline",
                )
            h = self.health()
            data = h["embedded_controls"]
            life = data.get("life")
            if not self.focus(h):
                time.sleep(0.25)
                continue
            if (
                life
                and not life["dead_candidate"]
                and 0 <= time.time() - data.get("observed_at", 0) <= 1
            ):
                return h
            if life and life["dead_candidate"]:
                from conquest import buff_trip

                # Alex: "if you die, you lose the double damage buff".
                if buff_trip.lost("death"):
                    self.record("buff_lost", buff="stigma", reason="death",
                                activity="Died: MrBuffer's double damage is gone")
            if life and life["dead_candidate"] and not data["control"]["enabled"]:
                try:
                    self.care.check(h)
                except TravelStateChanged:
                    pass
            elif life and life["dead_candidate"]:
                # Travel care revives only with Farming Off, and the app cannot
                # start farming on a dead archer: this wait was silent for 40
                # minutes on 2026-09-29 (11:51-12:31). Say so, once a minute.
                now = time.monotonic()
                if now - getattr(self, "dead_farming_on_logged", -60) >= 60:
                    self.dead_farming_on_logged = now
                    self.record(
                        "living_wait_dead_farming_on",
                        death_position=life.get("position"),
                        activity="Dead with Farming On; travel care revives only with Farming Off",
                    )
            time.sleep(0.15)  # The wrapper reconnects; never use disconnected stats.

    def revive_before_route(self):
        """Revive a farmer found dead at route (re)start, with Farming Off.

        A deploy relaunched the controller on a dead Toxic (2026-09-29
        11:51): living() waits forever with Farming On, and the app cannot
        start farming on a dead archer, so nothing revived it until an
        operator toggled Farming Off. Same order as restock_restart.resume:
        Farming Off, then living() lets travel care press Revive.
        """
        life = self.health()["embedded_controls"].get("life")
        if not life or not life.get("dead_candidate"):
            return False
        self.record(
            "revive_before_route",
            death_position=life.get("position"),
            activity="Dead at route start; reviving with Farming Off",
        )
        self.stop_farm()
        self.living()
        return True

    def town(self, action, **fields):
        vendor = {3: "Pharmacist", 5: "Blacksmith"}.get(
            fields.get("vendor_type"), "shop"
        )
        activity = {
            "open": f"Opening {vendor} shop",
            "buy": f"Buying { {**{k: v[0] for k, v in HEALING_POTIONS.items()}, 1050000: 'LuckyArrow', 1050001: 'IronArrow', 1050002: 'SpeedArrow'}.get(fields.get('type_id'), 'supplies') } from {vendor}",
            "sell": f"Selling unwanted loot to {vendor}",
            "sell-scroll": f"Selling a TwinCityGate to {vendor} for arrows",
        }
        if action in activity:
            self.record("town_activity", activity=activity[action])
        attempt, waiting_since = -1, None
        while attempt < 79:
            attempt += 1
            self.living()
            body = {"action": action, **fields}
            if action not in (
                "supplies",
                "shop",
                "gear",
                "vendor-status",
                "service-locate",
                "service-dialog",
                "warehouse-items",
            ):
                body["expires_at"] = time.time() + 4
            try:
                result = request(self.info, "town", body)
                if waiting_since is not None:
                    self.record("manual_request_cleared", action=action)
                return result
            except ValueError as error:
                if action == "service-locate" and str(error).startswith(
                    "One memory-identified "
                ):
                    raise
                if manual_request_fence(self, error):
                    # An unapproved incoming request is declined by the app's
                    # exact-request path about five seconds after it appears.
                    # Hold position (living() keeps survival reads) and retry
                    # the refused pre-input action without spending attempts.
                    now = time.monotonic()
                    if waiting_since is None:
                        waiting_since = now
                        self.record(
                            "manual_request_wait",
                            action=action,
                            activity="Holding while an unapproved trade request is declined",
                        )
                    if now - waiting_since < MANUAL_REQUEST_WAIT_SECONDS:
                        attempt -= 1
                        time.sleep(0.25)
                        continue
                    # Pre-input refusal only: never a town_action_failed that
                    # restart reconciliation must treat as possibly transacted.
                    self.record(
                        "manual_request_wait_expired",
                        action=action,
                        detail=str(error),
                        waited_seconds=round(now - waiting_since, 2),
                    )
                    raise
                # Typed pre-input failures are safe to retry even for trades.
                # An uncertain purchase/sale must never be blindly repeated.
                from conquest.merchants.coordination import InputAcquisitionBusy

                deposit_busy = action == "warehouse-deposit" and isinstance(
                    error, InputAcquisitionBusy
                )
                retryable = (
                    action
                    in (
                        "supplies",
                        "shop",
                        "gear",
                        "vendor-status",
                        "service-locate",
                        "service-dialog",
                        "warehouse-items",
                    )
                    or isinstance(error, TownObservationUnavailable)
                    or deposit_busy
                )
                if attempt == 79 or (deposit_busy and attempt >= 19) or not retryable:
                    self.record("town_action_failed", action=action, detail=str(error))
                    raise
                if attempt in (0, 19, 39, 59):
                    self.record(
                        "town_observation_retry", action=action, detail=str(error)
                    )
                time.sleep(0.25)

    def heavy_burn(self, potions, now=None):
        """Whether this hunt used HEAVY_BURN_POTIONS within HEAVY_BURN_WINDOW.

        Only decreases count: potions picked up or bought never offset use.
        """
        now = time.monotonic() if now is None else now
        samples = [
            (t, p)
            for t, p in getattr(self, "potion_samples", ())
            if now - t <= HEAVY_BURN_WINDOW
        ]
        samples.append((now, potions))
        self.potion_samples = samples
        used = sum(max(0, a - b) for (_, a), (_, b) in zip(samples, samples[1:]))
        return used >= HEAVY_BURN_POTIONS

    def restart_runner(self, data):
        """Restart a combat runner stopped by an observation or input error.

        The route's own restart takes 20-40 s while the archer stands among
        the monsters: Toxic died that way on 2026-09-28 at 00:44 (an
        unverified heal) and 02:03 (a stale reload frame). Restart combat in
        place instead, the way every hunt starts, while the character is
        alive and at most RUNNER_RESTARTS times per RUNNER_RESTART_WINDOW;
        otherwise the route restarts as before.
        """
        life = data.get("life")
        if not life or life.get("dead_candidate"):
            return False
        now = time.monotonic()
        recent = [
            t
            for t in getattr(self, "runner_restarts", ())
            if now - t < RUNNER_RESTART_WINDOW
        ]
        if len(recent) >= RUNNER_RESTARTS:
            return False
        self.runner_restarts = [*recent, now]
        self.record(
            "runner_restarted",
            detail=data["control"].get("note"),
            activity="Restarting combat after a runner error",
        )
        self.stop_farm()
        request(
            self.info,
            "controls",
            {
                "enabled": True,
                "target_type_ids": list(self.route.monster_type_ids),
                "target_ids": [],
            },
        )
        return True

    def stop_farm(self):
        request(self.info, "controls", {"enabled": False})
        deadline = time.monotonic() + 10
        h = self.health()
        while h["embedded_controls"].get("external_execution"):
            if time.monotonic() > deadline:
                raise ValueError("Farm did not release input for town travel")
            time.sleep(0.1)
            h = self.health()
        from conquest.discord_notify import read_json

        saved = read_json(RECOVERY_CHECKPOINT)
        if (
            saved
            and saved.get("identity") == h.get("target")
            and saved.get("phase") not in ("completed", "cancelled")
        ):
            # Town travel owns the next position. An earlier recovery waypoint
            # must not be replayed after shopping or a different town revival.
            request(self.info, "controls", {"route_id": self.route.id})
            deadline = time.monotonic() + 5
            while read_json(RECOVERY_CHECKPOINT).get("phase") not in (
                "completed",
                "cancelled",
            ):
                if time.monotonic() > deadline:
                    raise ValueError("Previous death return did not release the route")
                time.sleep(0.1)

    def travel(
        self,
        destination,
        *,
        activity=None,
        vendor_type=None,
        service_name=None,
        arrival_radius=0,
        avoid=(),
    ):
        from conquest.runback_monitor import RunbackMonitor

        life = self.living()["embedded_controls"]["life"]
        self.runback_watch = watch = RunbackMonitor(
            destination,
            life.get("map_id", self.terrain.map_id),
            "town",
            lambda event, row: self.record(event, runback=row),
        )
        watch.observe(life)
        stepper = getattr(self, "stepper", None)
        previous = getattr(stepper, "on_life", None)

        def observe(health):
            watch.observe_health(health)
            if previous:
                previous(health)

        if stepper is not None:
            stepper.on_life = observe
        outcome = "interrupted"
        try:
            result = self._travel(
                destination,
                activity=activity,
                vendor_type=vendor_type,
                service_name=service_name,
                arrival_radius=arrival_radius,
                avoid=avoid,
            )
            outcome = "arrived"
            return result
        finally:
            if stepper is not None:
                stepper.on_life = previous
            watch.finish(outcome)
            self.runback_watch = None

    def _travel(
        self,
        destination,
        *,
        activity=None,
        vendor_type=None,
        service_name=None,
        arrival_radius=0,
        avoid=(),
    ):
        if type(arrival_radius) is not int or not 0 <= arrival_radius <= 2:
            raise ValueError("Intermediate arrival radius must be zero to two tiles")
        from conquest.city_travel import service_role

        vendor_role = service_role(self.terrain.map_id, destination)
        from conquest.arrow_upgrades import NORMAL_ARROWS

        purpose = {
            3: "Pharmacist to sell loot and buy potions",
            5: f"Blacksmith to buy {NORMAL_ARROWS.get(self.route.supplies.arrow_type, 'arrows')}",
            4: "Armorer to check armor and headgear",
            1: "Shopkeeper to check ring, boots and necklace",
        }.get(vendor_role, str(destination))
        self.record(
            "travel",
            destination=destination,
            activity=activity or "Heading to " + purpose,
        )
        # Read-only memory occupancy is a hard constraint for this trip.  Keep
        # it distinct from transient failed movement edges, which may be reset
        # after verified progress.
        occupied = set(map(tuple, avoid))
        avoided = set()
        recovery_run_until = 0
        deadline = time.monotonic() + 90
        service_deadline = getattr(self, "market_service_deadline", None)
        if service_deadline is not None:
            deadline = min(
                deadline, time.monotonic() + max(0, service_deadline - time.time())
            )
        from conquest.travel_progress import ProgressDeadline, TravelStalled

        progress_deadline = ProgressDeadline(clock=time.monotonic)
        last_progress_position = None
        cached_path = None
        cached_avoid = None
        market_failures = 0
        market_landings = set()
        market_failed = set()
        obstruction_origin = None
        blocked_jump_origin = None
        boss_wait_until = None
        boss_detouring = False
        zone_crossed = False
        boss_resets = 0
        detour_cache = None
        laps = {}
        lap_at = [time.monotonic()]
        route_chat = []  # viewport.ChatPassThrough, read once when first needed

        def chat_passes(point):
            # The chat's message area passes route clicks through. At Phoenix's
            # west gate the clamped camera draws the (8,376) approach at
            # (256,144) inside it; refusing it shortened every step to (9,377)
            # and the detour stepped back to (10,377) (Suicide 2026-09-29).
            if not route_chat:
                from conquest.viewport import read_route_chat

                route_chat.append(read_route_chat(self.care.session))
            return route_chat[0] is not None and route_chat[0].passes(point)

        def lap(name):
            # Where each travel iteration spends its time; reported when slow.
            now = time.monotonic()
            laps[name] = round(laps.get(name, 0) + now - lap_at[0], 2)
            lap_at[0] = now

        while time.monotonic() < deadline:
            if laps and sum(laps.values()) >= SLOW_TRAVEL_STEP_SECONDS:
                self.record("travel_slow_step", timings=dict(laps))
            laps.clear()
            lap_at[0] = time.monotonic()
            waiting = time.monotonic()
            h = self.living()
            lap("living")
            life = h["embedded_controls"]["life"]
            source = tuple(life["position"])
            occupied.discard(source)
            from conquest.routes import boss_name, boss_zone

            scene = h["embedded_controls"].get("monsters") or ()
            zone = boss_zone(
                source,
                tuple(destination),
                scene,
                king_clearance=getattr(self.route, "king_clearance", 9),
                elite_clearance=getattr(self.route, "elite_clearance", 9),
            )
            from conquest.viewport import scene_bounds, clear_scene

            viewport = tuple(h.get("window", {}).get("client_size", (1036, 793)))
            bounds = scene_bounds(viewport)
            if getattr(self, "walk_after_obstruction", False):
                blocked_jump_origin = source
                self.walk_after_obstruction = False
            if (
                blocked_jump_origin is not None
                and max(abs(a - b) for a, b in zip(source, blocked_jump_origin)) >= 12
            ):
                blocked_jump_origin = None
            if obstruction_origin is None:
                obstruction_origin = source
            if source != last_progress_position:
                last_progress_position = source
                avoided.discard(source)
            if max(abs(a - b) for a, b in zip(source, destination)) <= arrival_radius:
                return
            if service_name:
                try:
                    service = self.town("service-locate", name=service_name)["npc"]
                except ValueError as error:
                    if not str(error).startswith("One memory-identified "):
                        raise
                    service = None
                if (
                    service
                    and max(abs(a - b) for a, b in zip(source, service["position"]))
                    <= 12
                ):
                    x, y = service["draw_position"]
                    if clear_scene((x, y - 32), viewport):
                        return
            # Stop as soon as the live vendor can be interacted with, including
            # Phoenix aliases and armor shops; a service anchor is only a fallback.
            if vendor_type is not None:
                if self.town("vendor-status", vendor_type=vendor_type).get("reachable"):
                    return
            elif vendor_role is not None:
                from conquest.memory_npcs import vendor_identity

                vendor = vendor_identity(self.terrain.map_id, vendor_role)
                if max(abs(a - b) for a, b in zip(source, vendor.position)) <= 18:
                    if self.town("vendor-status", vendor_type=vendor_role).get(
                        "reachable"
                    ):
                        return
            lap("vendor_checks")
            try:
                self.care.check(h)
                lap("care")
                try:
                    planner = getattr(
                        self.terrain, "travel_path", self.terrain.straight_path
                    )
                    blocked = occupied | avoided
                    if (
                        cached_path
                        and cached_avoid == frozenset(blocked)
                        and source in cached_path
                    ):
                        path = cached_path[cached_path.index(source) :]
                    else:
                        path = planner(source, tuple(destination), avoid=blocked)
                    cached_path = path
                    cached_avoid = frozenset(blocked)
                except TravelStalled:
                    raise
                except ValueError:
                    if not avoided and not occupied:
                        from conquest.town_corner import recover_corner

                        if recover_corner(self, destination):
                            cached_path = None
                            cached_avoid = None
                            continue
                        raise
                    # Temporary failed steps can cut the only town corridor.
                    # Revalidate the actual terrain and retry with short runs.
                    path = planner(source, tuple(destination), avoid=occupied)
                    avoided.clear()
                    cached_path = path
                    cached_avoid = frozenset(occupied)
                    recovery_run_until = time.monotonic() + 6
                    self.record(
                        "town_path_retry",
                        activity="Retrying the town corridor with running steps",
                    )
                if zone and any(tuple(p) in zone for p in path):
                    key = frozenset(occupied | avoided | zone)
                    if detour_cache and detour_cache[0] == key and source in detour_cache[1]:
                        detour = detour_cache[1][detour_cache[1].index(source) :]
                    else:
                        try:
                            detour = planner(
                                source, tuple(destination), avoid=occupied | avoided | zone
                            )
                        except ValueError:
                            detour = None
                        detour_cache = (key, detour) if detour else None
                    bosses = [
                        [m["name"], list(m["position"])]
                        for m in scene
                        if boss_name(m.get("name") or "") and m.get("position")
                    ]
                    if detour is not None:
                        path = detour
                        if not boss_detouring:
                            boss_detouring = True
                            self.record(
                                "travel_boss_detour",
                                bosses=bosses,
                                source=list(source),
                                destination=list(destination),
                                activity="Walking around a boss near the route",
                            )
                            if boss_resets < BOSS_DETOUR_RESETS:
                                # The longer way round is not a stall.
                                boss_resets += 1
                                progress_deadline.best = None
                    else:
                        urgent = getattr(
                            getattr(self, "runback_watch", None), "urgent", False
                        )
                        if boss_wait_until is None and not urgent:
                            boss_wait_until = time.monotonic() + BOSS_WAIT_SECONDS
                            self.record(
                                "travel_boss_wait",
                                bosses=bosses,
                                source=list(source),
                                activity="A boss blocks the only way on; waiting for it to move",
                            )
                        if (
                            not urgent
                            and boss_wait_until is not None
                            and time.monotonic() < boss_wait_until
                        ):
                            time.sleep(1)
                            continue
                        zone = frozenset()  # Waited long enough (or hit): walk on.
                else:
                    boss_detouring = False
                    boss_wait_until = None
                lap("planner")
                remaining = sum(
                    max(abs(a[0] - b[0]), abs(a[1] - b[1]))
                    for a, b in zip(path, path[1:])
                )
                if max(
                    abs(a - b) for a, b in zip(source, obstruction_origin)
                ) >= 8 and (
                    progress_deadline.best is None or remaining < progress_deadline.best
                ):
                    # A retreat can cover many tiles without passing the
                    # obstruction. Keep its failed edges until the checked
                    # route beats our best remaining distance; otherwise the
                    # retreat erases the evidence and selects the same failure.
                    avoided.clear()
                    cached_path = None
                    cached_avoid = None
                    market_failures = 0
                    market_landings.clear()
                    market_failed.clear()
                    obstruction_origin = source
                if service_deadline is None and (
                    progress_deadline.best is None or remaining < progress_deadline.best
                ):
                    deadline = time.monotonic() + 90
                if progress_deadline.observe(remaining):
                    cached_path = None
                    cached_avoid = None
                    if not h["embedded_controls"].get("manual_mouse"):
                        self.focus(h)
                    self.record(
                        "travel_progress_recovery",
                        attempt=progress_deadline.attempts,
                        activity="Rechecking route and focus after five seconds without progress",
                    )
                    continue
                from conquest.navigation import travel_waypoint

                step_limit = (
                    4
                    if blocked_jump_origin is not None
                    or time.monotonic() < recovery_run_until
                    else 12
                )
                # Landings and the runback's evasion keep off a boss zone too.
                blocked = occupied | avoided | zone
                if hasattr(self.terrain, "travel_path"):
                    try:
                        target = travel_waypoint(
                            self.terrain, path, step_limit, avoid=blocked, viewport=viewport
                        )
                    except ValueError:
                        if not zone:
                            raise
                        # Walk on past the bosses rather than stand still: three
                        # BanditKings' 15-tile zones left no visible landing at
                        # (324,329), every restart failed there with the farm off
                        # while Bandits hit Toxic, and it died (2026-09-28 17:46).
                        if not zone_crossed:
                            zone_crossed = True
                            self.record(
                                "travel_boss_zone_crossed",
                                source=list(source),
                                activity="No landing clears the bosses; walking on past them",
                            )
                        zone = frozenset()
                        target = travel_waypoint(
                            self.terrain,
                            path,
                            step_limit,
                            avoid=occupied | avoided,
                            viewport=viewport,
                        )
                else:
                    target = native_waypoint(path, step_limit, viewport=viewport)
                from types import SimpleNamespace
                from conquest.scene_input import (
                    memory_player_anchor,
                    visible_route_delta,
                    clear_route_point,
                )

                lap("waypoint")
                anchor = memory_player_anchor(
                    SimpleNamespace(adapter=self.care.session), SimpleNamespace(**life)
                )
                lap("anchor")
                # Generic Market recovery has no occupancy input.  Retain a
                # fresh merchant probe's hard exclusions instead of bypassing
                # them with an alternate landing guessed from terrain alone.
                if (
                    self.terrain.map_id in (1036, 1011)
                    and not occupied
                    and market_failures >= 2
                    and len(market_landings) < 3
                ):
                    from conquest.market_navigation import recovery_landing

                    alternate = recovery_landing(
                        self.terrain,
                        source,
                        tuple(destination),
                        anchor,
                        failed=market_failed,
                        used=market_landings,
                        viewport=viewport,
                    )
                    if alternate is not None:
                        target = alternate
                        market_landings.add(alternate)
                        # These were guesses about the first path edge, not
                        # confirmed terrain obstacles. The alternate is checked
                        # independently against the actual collision map.
                        avoided.clear()
                        cached_path = None
                        cached_avoid = None
                        place = "Market" if self.terrain.map_id == 1036 else "Phoenix"
                        self.record(
                            "market_movement_recovery"
                            if self.terrain.map_id == 1036
                            else "town_movement_recovery",
                            source=source,
                            destination=target,
                            attempt=len(market_landings),
                            activity=place + " path blocked; taking an alternate jump",
                        )
                if getattr(self, "runback_watch", None) and self.runback_watch.urgent:
                    from conquest.runback_monitor import escape_step

                    escape = escape_step(
                        self.terrain,
                        source,
                        tuple(destination),
                        anchor,
                        h["embedded_controls"].get("monsters", []),
                        avoid=blocked,
                        viewport=viewport,
                    )
                    if escape is not None:
                        target = escape
                        self.runback_watch.recovery()
                        self.record(
                            "runback_evading",
                            activity="Under attack during runback; healing and moving away",
                        )
                dx, dy = target[0] - source[0], target[1] - source[1]
                px, py = anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16
                if not clear_route_point((px, py), bounds) and not chat_passes((px, py)):
                    shorter = visible_route_delta((dx, dy), anchor, bounds)
                    if shorter is None:
                        # At a clamped camera edge, a corner's endpoint can be
                        # hidden even though an earlier walking tile is clear.
                        visible = [
                            p
                            for p in path[1:5]
                            if clear_route_point(
                                (
                                    anchor[0]
                                    + (p[0] - source[0] - p[1] + source[1]) * 32,
                                    anchor[1]
                                    + (p[0] - source[0] + p[1] - source[1]) * 16,
                                ),
                                bounds,
                            )
                        ]
                        if not visible:
                            from conquest.navigation import visible_cardinal_step

                            target = visible_cardinal_step(
                                self.terrain,
                                source,
                                tuple(destination),
                                anchor,
                                avoid=avoided,
                                allow_detour=True,
                                bounds=bounds,
                            )
                            if target is None:
                                avoided.add(tuple(path[1]))
                                continue
                        else:
                            target = visible[-1]
                    else:
                        target = (source[0] + shorter[0], source[1] + shorter[1])
                from conquest.route_crowd import Crowd

                # A click on a player, booth or NPC does not move the farmer.
                # The destination itself stays the click target: service and
                # exit tiles stand beside their NPC (Market Controller, 09-26).
                lap("visible_point")
                crowd = Crowd.observe(self.care.session, anchor)
                lap("crowd")
                dx, dy = target[0] - source[0], target[1] - source[1]
                if tuple(target) != tuple(destination) and crowd.covers(
                    (anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16)
                ):
                    landing = crowd.open_landing(
                        self.terrain,
                        source,
                        tuple(destination),
                        anchor,
                        avoid=blocked | market_failed,
                        bounds=bounds,
                    )
                    if landing is not None:
                        self.record(
                            "route_click_rerouted",
                            source=source,
                            covered=target,
                            destination=landing,
                            activity="Route click was on a player or NPC; clicking open ground",
                        )
                        target = landing
                from conquest.navigation import clear_segment

                if hasattr(self.terrain, "travel_path") and not clear_segment(
                    self.terrain, source, target, avoid=blocked
                ):
                    avoided.add(target)
                    continue
                lap("reroute")
                result = self.stepper.step_to(target, expected_position=source)
                lap("step")
            except TravelStateChanged:
                continue
            except CaptureUnavailable:
                # Movement may have partially happened. Reobserve and replan;
                # never replay a stale destination or bypass manual Stop.
                self.check_stop()
                time.sleep(0.1)
                continue
            except ValueError as error:
                transient = (
                    "changed",
                    "moved",
                    "left the planned",
                    "observation",
                    "sampling",
                    "mouse control is yours",
                    "waiting for game focus",
                )
                if any(term in str(error).lower() for term in transient):
                    time.sleep(0.1)
                    continue
                raise
            if result["reached"]:
                recovery_run_until = 0
                market_failures = 0
            else:
                if getattr(self, "runback_watch", None):
                    self.runback_watch.recovery()
                latest = tuple(self.living()["embedded_controls"]["life"]["position"])
                recovery_run_until = time.monotonic() + 1.5 if latest == source else 0
                self.record(
                    "town_movement_stalled",
                    source=source,
                    destination=target,
                    position=latest,
                    detail=result.get("error"),
                    activity="Movement stalled; taking short running steps toward town",
                )
                # A walking step can time out before its final tile while still
                # advancing. Do not mark that traversed corridor as obstructed.
                if latest != source:
                    market_failures = 0
                    continue
                if (
                    self.terrain.map_id in (1011, 1036)
                    and max(abs(a - b) for a, b in zip(source, target)) >= 8
                ):
                    blocked_jump_origin = source
                if self.terrain.map_id in (1036, 1011):
                    market_failures += 1
                    market_failed.add(target)
                    if market_failures >= 6:
                        raise ValueError("Town route remains obstructed")
                delta = tuple(b - a for a, b in zip(source, target))
                tile = tuple(
                    v + (1 if d > 0 else -1 if d < 0 else 0)
                    for v, d in zip(latest, delta)
                )
                avoided.add(tile)
                # Corner runs can bypass the sign-diagonal tile entirely.
                # Exclude the actual first path edge too, so replanning cannot
                # submit the same failed corner endpoint until the deadline.
                avoided.add(tuple(path[1]))
                if len(avoided) > 8:
                    raise ValueError("Town route remains obstructed")
        if service_deadline is not None and time.time() >= service_deadline:
            raise TravelStalled(
                "Market merchant-service deadline expired; defer further input",
                code="service_deadline",
            )
        raise ValueError("Town travel has made no position progress for 90 seconds")

    def sell_junk(self, vendor_type):
        for _ in range(40):
            items = self.town("supplies")["items"]
            junk = next((item for item in items if sale_candidate(item)), None)
            if junk is None:
                return
            self.record(
                "sale",
                receipt=self.town("sell", vendor_type=vendor_type, uid=junk["uid"]),
            )
        raise ValueError("Unexpected inventory turnover while selling")

    def shopping_space(self, vendor_type, position):
        """Free a completely full bag before a shop can reject an essential buy."""
        bag = self.town("supplies")
        if len(bag["items"]) < bag["capacity"]:
            return False
        from conquest.banking import open_warehouse, close_warehouse, stash_valuables

        self.record(
            "shopping_storage_required",
            activity="Freeing inventory space in the warehouse before shopping",
        )
        self.town("close", window="Shop")
        self.town("close", window="Inventory")
        open_warehouse(self)
        try:
            # This emergency space recovery uses verified storage only. Routine
            # merchant delivery remains after shopping, with transport cash held.
            stash_valuables(self)
            after = self.town("supplies")
            if len(after["items"]) >= after["capacity"]:
                raise ValueError(
                    "Inventory remains full after verified storage; no purchase issued"
                )
        finally:
            from conquest.storage_halt import active

            if not active():
                close_warehouse(self)
        self.travel(tuple(position))
        self.town("open", vendor_type=vendor_type)
        return True

    def recycle_small_arrows(self):
        for _ in range(40):
            snapshot = self.town("supplies")
            counts = supply_counts(snapshot, self.route)
            from conquest.arrow_upgrades import NORMAL_ARROWS

            spent = [
                item
                for item in snapshot["items"]
                if item["type_id"] in NORMAL_ARROWS and 0 < item["amount"] < 3
            ]
            if spent:
                self.record(
                    "town_activity",
                    activity="Recycling arrow remnants that cannot fire Scatter",
                )
                self.record(
                    "sale",
                    receipt=self.town(
                        "sell_partial_arrow", vendor_type=5, uid=spent[0]["uid"]
                    ),
                )
                continue
            if counts["free_slots"] >= self.route.supplies.minimum_free_slots:
                return
            candidates = sorted(
                (
                    item
                    for item in snapshot["items"]
                    if item["type_id"] == self.route.supplies.arrow_type
                    and 0 < item["amount"] <= 25
                    and counts["arrows"] - item["amount"] >= 600
                ),
                key=lambda item: item["amount"],
            )
            if not candidates:
                return
            self.record(
                "town_activity",
                activity="Selling small arrow bundles to free loot slots",
            )
            self.record(
                "sale",
                receipt=self.town(
                    "sell_partial_arrow", vendor_type=5, uid=candidates[0]["uid"]
                ),
            )
        raise ValueError("Unexpected inventory turnover while recycling arrows")

    def buy_supply(self, vendor_type, type_id):
        snapshot = self.town("supplies")
        before = supply_counts(snapshot, self.route)
        from conquest.arrow_upgrades import (
            NORMAL_ARROWS,
            arrow_pack_count,
            max_arrow_packs,
        )

        if type_id in NORMAL_ARROWS and arrow_pack_count(
            snapshot, type_id
        ) >= max_arrow_packs(type_id):
            remnant = blocking_remnant(snapshot, type_id)
            if remnant is not None:
                self.record(
                    "town_activity",
                    activity="Recycling an arrow remnant that holds a pack slot",
                )
                self.record(
                    "sale",
                    receipt=self.town(
                        "sell_partial_arrow", vendor_type=5, uid=remnant["uid"]
                    ),
                )
                return self.buy_supply(vendor_type, type_id)
            self.record(
                "arrow_purchase_deferred",
                arrow_packs=arrow_pack_count(snapshot, type_id),
                activity="Keeping existing arrow packs; the pack limit is reached",
            )
            return False
        from conquest.savings import savings_plan, affordable_supply

        if savings_plan():
            products = self.town("shop", vendor_type=vendor_type)["products"]
            product = next((p for p in products if p["type_id"] == type_id), None)
            if product is None:
                raise ValueError("Essential supply is absent from the live shop")
            if not affordable_supply(type_id, product["price"], before, self.route):
                self.record(
                    "savings_purchase_deferred",
                    type_id=type_id,
                    supplies=before,
                    activity="Preserving silver; buying only affordable essential supplies",
                )
                return False
        if type_id in (1050001, 1050002) and optional_top_up(before, self.route):
            shop = self.town("shop", vendor_type=vendor_type)
            products = [p for p in shop["products"] if p["type_id"] == type_id]
            if len(products) != 1:
                raise ValueError("Selected arrow price is not verified")
            if before["silver"] - products[0]["price"] < OPTIONAL_ARROW_FLOOR:
                self.record(
                    "optional_purchase_deferred",
                    type_id=type_id,
                    supplies=before,
                    activity="Enough arrows to hunt; keeping silver for supplies",
                )
                return False
        try:
            receipt = self.town("buy", vendor_type=vendor_type, type_id=type_id)
        except ValueError as error:
            if str(error) != "Purchase was not verified; no repeat purchase issued":
                raise
            # Do not repeat an uncertain transaction. If nothing has changed
            # and supplies remain sufficient, defer this optional top-up.
            # Any debit, inventory change or supply shortage still needs care.
            for _ in range(6):
                time.sleep(0.5)
                current = supply_counts(self.town("supplies"), self.route)
                if current != before or needs_town(current, self.route):
                    raise error
            self.record(
                "optional_purchase_deferred",
                type_id=type_id,
                supplies=current,
                activity="Supplies sufficient — returning to hunting",
                detail="Purchase unconfirmed; inventory and silver unchanged. No repeat input issued.",
            )
            return False
        self.record("purchase", receipt=receipt)
        return True

    def buy_refill_arrows(self):
        """Buy the selected tier, or the next affordable lower tier.

        The worker refuses an unaffordable purchase before any input. A
        required refill must not strand in town because the level-best tier
        (SpeedArrow at 73+) costs more than the wallet holds after funding,
        which already withdrew every stored silver it could.
        """
        kind = self.route.supplies.arrow_type
        try:
            return self.buy_supply(5, kind)
        except ValueError as error:
            if str(error) != "Insufficient funds or inventory room to restock":
                raise
            from conquest.arrow_upgrades import (
                NORMAL_ARROWS,
                fallback_arrow,
                refill_target,
            )

            bag = self.town("supplies")
            products = self.town("shop", vendor_type=5).get("products") or []
            quote = next((p for p in products if p["type_id"] == kind), None)
            if (
                len(bag["items"]) >= bag["capacity"]
                or quote is None
                or quote["price"] <= bag["silver"]
            ):
                raise  # Bag room, or not a verified shortfall of silver.
            level = self.town("gear").get("level")
            product = (
                fallback_arrow(products, level, kind, bag["silver"])
                if type(level) is int
                else None
            )
            if product is None:
                raise
            fallback = product["type_id"]
            self.route = self.route.model_copy(
                update={
                    "supplies": self.route.supplies.model_copy(
                        update={
                            "arrow_type": fallback,
                            "arrows_restock_to": refill_target(fallback),
                        }
                    )
                }
            )
            self.record(
                "arrow_tier_fallback",
                arrow_type=fallback,
                previous_arrow_type=kind,
                price=product["price"],
                unaffordable_price=quote["price"],
                silver=bag["silver"],
                activity=(
                    f"{NORMAL_ARROWS[kind]} costs {quote['price']:,} with "
                    f"{bag['silver']:,} silver available; buying "
                    f"{NORMAL_ARROWS[fallback]}"
                ),
            )
            return self.buy_supply(5, fallback)

    def scroll_to_restock_town(self):
        """Read a TwinCityGate home when the restock town is Twin City, and
        another restock town's own gate only from far away.

        The TwinCityGate always lands in Twin City. Since it may be read on
        Phoenix Castle (the way back from there), a WingedSnake restock would
        scroll to Twin City and pay a Conductress fare back to the Phoenix
        shops a 40-second walk reaches.
        """
        if self.route.restock_map_id == 1002:
            from conquest.return_scroll import return_to_town

            return return_to_town(self)
        return self.gate_home_from_afar()

    def gate_home_from_afar(self):
        """Read the restock town's own gate when the walk home is long.

        Ape City's GiantApe and ThunderApe fields are 600-860 tiles from its
        vendors, and the walk crosses the bosses around the GiantApe plain:
        Suicide died on the ThunderApe runback at (563, 432) on 2026-09-29
        22:39, after three boss detours (3 GiantApeKings, 5 Aides) and a 14 s
        stall among the pack. A gate (ApeCityGate, 200 silver at Ape City's
        Pharmacist, stocked two at a time) lands in the town instead. The
        Macaque field, ~100 tiles out, keeps walking.
        """
        from conquest.return_scroll import GATES, read_gate

        home = self.route.restock_map_id
        if home not in GATES:
            return False
        life = self.living()["embedded_controls"]["life"]
        if life.get("map_id") == home:
            from conquest.city_travel import city_for

            anchor = city_for(home)["town_anchor"]
            if (
                max(abs(a - b) for a, b in zip(life["position"], anchor))
                < GATE_HOME_TILES
            ):
                return False
        if not self.quiet_for_gate():
            self.record(
                "gate_deferred_under_fire",
                activity="No quiet spot to read the gate; walking home",
            )
            return False
        return read_gate(self, home)

    def quiet_for_gate(self):
        """Whether a spot turned up where the gate can be read without being hit.

        Toxic died at (619, 281) on 2026-09-30 02:23: a heavy-damage return
        read its ApeCityGate standing still beside the GiantApe pack and two
        Aides, and was dead 2 s later ("Town action requires a living
        character"). Each pass runs life care; while HP fell in the last
        GATE_QUIET_SECONDS, a living monster stands within GATE_CLEAR_TILES
        or a boss has no room (boss_room), escape jumps (evade_in_field)
        look for a quiet spot, for up to GATE_SETTLE_SECONDS.
        """
        from types import SimpleNamespace

        from conquest.routes import BOSS_CLEARANCE, boss_name, boss_room

        king = getattr(self.route, "king_clearance", BOSS_CLEARANCE)
        elite = getattr(self.route, "elite_clearance", BOSS_CLEARANCE)
        started = time.monotonic()
        hit_at, last_hp = started, None
        while True:
            health = self.living()
            try:
                self.care.check(health)
            except OvernightStopped:
                raise
            except Exception:
                pass
            controls = health["embedded_controls"]
            life = controls.get("life") or {}
            now = time.monotonic()
            hp = life.get("current_hp")
            if last_hp is not None and hp is not None and hp < last_hp:
                hit_at = now
            last_hp = hp
            position = tuple(life.get("position") or ())
            living = [
                SimpleNamespace(**m)
                for m in controls.get("monsters") or []
                if m.get("position") and m.get("alive") is not False
            ]
            crowded = not position or any(
                not boss_name(m.name or "")
                and max(abs(a - b) for a, b in zip(m.position, position))
                <= GATE_CLEAR_TILES
                for m in living
            )
            if (
                not crowded
                and boss_room(position, living, king_clearance=king, elite_clearance=elite)
                and now - hit_at >= GATE_QUIET_SECONDS
            ):
                return True
            if now - started >= GATE_SETTLE_SECONDS:
                return False
            try:
                self.evade_in_field(health, tiles=GATE_CLEAR_TILES)
            except OvernightStopped:
                raise
            except Exception:
                pass
            time.sleep(0.3)

    def sell_scroll_for_arrows(self):
        """Sell one carried TwinCityGate for the pack an empty quiver needs,
        once funding has drawn the bank (True once one is sold).

        Live 2026-09-27 17:19 (Toxic, level 27, Phoenix City): 128 silver, an
        empty bank and one arrow left; a pack costs 200 and every restock
        retry failed in town. A TwinCityGate bought for 200 sells back for a
        third: two pay for the pack that earns the next scrolls.
        """
        from conquest.return_scroll import TYPE

        bag = self.town("supplies")
        scroll = next((i for i in bag["items"] if i["type_id"] == TYPE), None)
        if scroll is None:
            return False
        receipt = self.town("sell-scroll", vendor_type=5, uid=scroll["uid"])
        self.record(
            "scroll_sold_for_arrows",
            receipt=receipt,
            silver=bag["silver"] + receipt["silver_gained"],
            activity=f"Sold a TwinCityGate for {receipt['silver_gained']} silver toward arrows",
        )
        return True

    def open_arrow_refill(self):
        before = self.town("supplies")
        try:
            self.town("open", vendor_type=5)
            return True
        except ValueError as error:
            if str(error) != "Shop opening was not verified; no repeat input issued":
                raise
            # Opening a shop does not submit a purchase. A stocked character
            # can defer the spare, but never conceal an inventory/currency
            # change or use this path for an uncertain transaction.
            keys = ("items", "equipped_ammo", "silver", "capacity")
            baseline = {key: before.get(key) for key in keys}
            for _ in range(3):
                current = self.town("supplies")
                if {key: current.get(key) for key in keys} != baseline or needs_town(
                    supply_counts(current, self.route), self.route
                ):
                    raise error
                time.sleep(0.1)
            self.record(
                "optional_arrow_refill_deferred",
                supplies=supply_counts(current, self.route),
                detail=str(error),
                activity="Spare arrows deferred; finishing storage before returning to hunt",
            )
            return False

    def restock(self, *, review_both_cities=True):
        visits = getattr(self, "town_visit", None)
        if visits is not None:
            visits.begin(
                "restock",
                hunt_map_id=self.route.map_id,
                route_id=self.route.id,
                target=self.identity,
            )
        self.phase = "restocking"
        from conquest.session_plan import rotate_hold

        # A hold's field rotation: the restock's end departs to the rested
        # field (select_level_route follows the hold's new route id).
        rotated = rotate_hold(visits.active_id() if visits is not None else None)
        if rotated:
            self.record(
                "field_rotated",
                previous_route=self.route.id,
                route=rotated,
                activity=f"Next hunt on {rotated}: the other field has rested",
            )
        from conquest.savings import savings_plan, configure_route

        if savings_plan():
            self.route = configure_route(self.route, self.town("supplies")["silver"])
        from conquest.city_travel import city_for

        services = city_for(self.route.restock_map_id).get("services")
        if not services:
            raise ValueError("Restock vendors are not mapped in the destination city")
        from conquest import buff_trip

        # MrBuffer's double damage first (Alex 2026-09-30): TwinCityGate out,
        # ApeCityGate back, and the restock carries on at home.
        if buff_trip.enabled() and buff_trip.refresh_due(self.town("supplies")["items"]):
            buff_trip.trip(self)
        self.scroll_to_restock_town()
        from conquest.world_travel import travel_to_map

        travel_to_map(self, self.route.restock_map_id)
        # A restarted controller may inherit a shop left open by the failed run.
        self.town("close", window="Shop")
        self.town("close", window="Inventory")
        from conquest.supply_plan import balance

        # Learn the finished hunt and size potions against arrow packs before
        # the withdrawal budget is computed from those targets.
        balance(self)
        from conquest.banking import fund_restock

        fund_restock(self)
        from conquest.return_scroll import stock, secure_one, POLICY as scroll_policy
        from conquest.discord_notify import read_json

        if pharmacist_needed(
            self.town("supplies"),
            self.route,
            scroll_enabled=read_json(scroll_policy).get("enabled", False),
        ):
            self.travel(self.route.restock_anchor)
            self.town("open", vendor_type=3)
            from conquest.level_goal import healing_type

            # Choose the tier before selling: lower tiers become junk.
            healing_type(self)
            self.sell_junk(3)
            self.shopping_space(3, self.route.restock_anchor)
            arrow_price = last_verified_price(self.route.supplies.arrow_type)
            # The way home comes before potions when silver is short, but not
            # before the pack a short quiver needs: a scroll bought with the
            # last 200 left a minute of arrows, so every trip ended on a scroll.
            secure_one(
                self,
                keep=arrow_reserve(
                    supply_counts(self.town("supplies"), self.route),
                    self.route,
                    arrow_price,
                ),
            )
            potion_quote = None
            if arrow_price:
                try:
                    products = (self.town("shop", vendor_type=3) or {}).get(
                        "products"
                    ) or []
                except ValueError:
                    products = []  # No verified quote: keep the old behaviour.
                potion_quote = next(
                    (
                        p["price"]
                        for p in products
                        if p.get("type_id") == self.route.supplies.healing_type
                    ),
                    None,
                )
            for _ in range(30):
                counts = supply_counts(self.town("supplies"), self.route)
                if counts["potions"] >= self.route.supplies.healing_restock_to:
                    break
                if potion_budget_reached(
                    counts, self.route, potion_quote, arrow_price
                ):
                    self.record(
                        "potion_purchase_capped",
                        supplies=counts,
                        arrow_price=arrow_price,
                        potion_price=potion_quote,
                        activity="Keeping silver for arrows before more potions",
                    )
                    break
                reserve = self.route.supplies.minimum_free_slots + int(
                    counts["arrows"] < self.route.supplies.arrows_return_below
                )
                if (
                    counts["free_slots"] <= reserve
                    and counts["potions"] >= self.route.supplies.healing_return_below
                ):
                    break
                try:
                    bought = self.buy_supply(3, self.route.supplies.healing_type)
                except ValueError as error:
                    # The worker refuses an unaffordable buy before any input.
                    # Leave with what the wallet bought; the final supply check
                    # decides whether the farmer can hunt (live 09-27 01:50: a
                    # raise here idled the farmer in town for seven hours).
                    if str(error) != "Insufficient funds or inventory room to restock":
                        raise
                    self.record(
                        "potion_purchase_short",
                        supplies=counts,
                        activity=f"Out of silver or bag room at {counts['potions']} potions",
                    )
                    break
                if not bought:
                    break
            stock(self)
            self.town("close", window="Shop")
            self.town("close", window="Inventory")
        self.travel(tuple(services["blacksmith"]))
        from conquest.equipment import EquipmentReview

        review = EquipmentReview(self)
        if self.open_arrow_refill():
            self.sell_junk(5)
            self.recycle_small_arrows()
            self.shopping_space(5, services["blacksmith"])
            review.visit(5)
            for _ in range(16):
                counts = supply_counts(self.town("supplies"), self.route)
                if counts["arrows"] >= self.route.supplies.arrows_restock_to:
                    break
                if (
                    counts["free_slots"] <= self.route.supplies.minimum_free_slots
                    and counts["arrows"] >= self.route.supplies.arrows_return_below
                ):
                    break
                # Equipping upgraded arrows closes Shop. An optional equipment
                # review may then defer before reopening it; verify the vendor
                # again before the required refill's price read or purchase.
                if not self.open_arrow_refill():
                    break
                try:
                    bought = self.buy_refill_arrows()
                except ValueError as error:
                    short = str(error) == "Insufficient funds or inventory room to restock"
                    empty = counts["arrows"] < self.route.supplies.arrows_return_below
                    if short and empty and self.sell_scroll_for_arrows():
                        continue
                    # A spare pack the wallet or bag cannot take is no reason
                    # to stop the route while enough arrows are carried to
                    # hunt (live 09-27 14:26: the eight-pack refill after a
                    # resumed restock idled Suicide in a failure cooldown).
                    if not short or empty:
                        raise
                    self.record(
                        "arrow_purchase_short",
                        supplies=counts,
                        activity=f"Out of silver or bag room at {counts['arrows']} arrows",
                    )
                    break
                if not bought:
                    break
        self.town("close", window="Shop")
        self.town("close", window="Inventory")
        from conquest.savings import savings_plan

        for vendor, point in [] if savings_plan() else services["equipment"]:
            try:
                self.travel(point)
                self.town("open", vendor_type=vendor)
                review.visit(vendor)
            except ValueError as error:
                self.record(
                    "equipment_review_deferred",
                    vendor=vendor,
                    detail=str(error),
                    activity="Equipment shop unavailable; continuing supplied route",
                )
            finally:
                # Do not hunt through an unclosed shop or inventory panel.
                self.town("close", window="Shop")
                self.town("close", window="Inventory")
        from conquest.session_plan import upgrade_circuit

        toured = upgrade_circuit(self) if review_both_cities else False
        from conquest import buff_trip

        # No TwinCityGate yet (Ape City sells none): ride to Twin City once for
        # MrBuffer's buff and the gates that make later trips a scroll each way.
        if (
            not toured
            and buff_trip.enabled()
            and buff_trip.bootstrap_due(self, self.town("supplies")["items"])
        ):
            toured = buff_trip.trip(self, ride=True)
        self.town("close", window="Shop")
        self.town("close", window="Inventory")
        from conquest.banking import after_shopping

        after_shopping(self)
        # A bag full of protected loot is the reason for this visit. Storage
        # must get its turn before the final free-space check can reject it.
        counts = supply_counts(self.town("supplies"), self.route)
        if needs_town(counts, self.route):
            if toured:
                self.restock(review_both_cities=False)
                return
            raise ValueError(
                "Supplies or inventory room remain insufficient after restocking and storage"
            )
        from conquest.level_goal import mark_reviewed

        mark_reviewed(getattr(self, "last_level", 0))
        from conquest import scatter_training

        if scatter_training.due(self):
            scatter_training.attempt(self)
        self.optional_town_service()
        if visits is not None:
            from conquest.town_visit import checkpoint_verified_tail

            checkpoint_verified_tail(self, "restock")
            visits.complete_town_work("restock")
        self.cycles += 1
        from conquest.supply_plan import begin_hunt

        begin_hunt(self.route.id, counts)
        self.record("restock_complete", supplies=counts)

    def resume_settled_town_work(self):
        """Re-run unfinished town work when no transaction is left open.

        Every town step re-reads memory before acting (buy up to the target,
        deposit what is still carried, move silver to a target balance), so a
        fresh run cannot double anything. Open journals (merchant trade,
        delivery journey, Meteor, overflow, withdrawal, storage halt) still
        block: their own reconciliation paths decide. Town work bound to a
        game process that no longer exists (client restart) is superseded.
        """
        visits = getattr(self, "town_visit", None)
        if visits is None:
            return False
        row = visits.state()
        if row.get("phase") != "town_work" or row.get("town_work_completed_at"):
            return False
        from conquest.urgent_town_recovery import transaction_holds

        if transaction_holds():
            return False
        target = self.identity
        stale = [
            field
            for field in (
                "restock_target",
                "restock_recovery_target",
                "urgent_target",
                "return_target",
            )
            if row.get(field) is not None and row[field] != target
        ]
        if stale:
            visits.supersede("game_process_restarted", target=target)
        self.record(
            "town_work_resumed",
            resumed_visit_id=row.get("town_visit_id"),
            reasons=row.get("reasons"),
            superseded=bool(stale),
            activity="Finishing interrupted town work from fresh memory reads",
        )
        self.stop_farm()
        self.living()
        from conquest.banking import urgent_valuables

        if urgent_valuables(self.town("supplies")["items"]):
            self.bank_urgent_valuables()
            if visits.state().get("town_work_completed_at"):
                return True
        self.restock()
        return True

    def optional_town_service(self):
        """Merchant refill at a town visit is optional work: a failure after the
        merchant has provably released input is skipped, never a route stop."""
        from conquest.merchants.handoff import service_window

        try:
            service_window(self, town=True)
        except OvernightStopped:
            raise
        except Exception as error:
            if "did not release" in str(error):
                raise
            from conquest.merchants.bridge import request as merchant

            try:
                status = merchant({"action": "status"})
            except Exception:
                raise error from None
            if (
                status.get("input_owner") is not None
                or status.get("handoff_active")
                or status.get("handoff_granted")
            ):
                raise
            self.record(
                "merchant_refill_skipped",
                detail=str(error)[:300],
                error_type=type(error).__module__ + "." + type(error).__qualname__,
                activity="Merchant refill skipped at this town visit; continuing",
            )

    def bank_urgent_valuables(self):
        from conquest.banking import urgent_valuables, after_shopping

        items = urgent_valuables(self.town("supplies")["items"])
        if not items:
            return
        visits = getattr(self, "town_visit", None)
        if visits is not None:
            visits.begin(
                "urgent_banking",
                hunt_map_id=self.route.map_id,
                route_id=self.route.id,
                target=self.identity,
                urgent_items=items,
            )
        self.phase = "restocking"
        self.record(
            "urgent_banking_started",
            uids=[i["uid"] for i in items],
            activity="Heading directly to the warehouse to protect carried valuables",
        )
        from conquest.return_scroll import return_to_town
        from conquest.world_travel import travel_to_map

        return_to_town(self)
        travel_to_map(self, self.route.restock_map_id)
        self.town("close", window="Shop")
        self.town("close", window="Inventory")
        if not after_shopping(self, urgent=True):
            raise ValueError("Urgent valuable banking is disabled")
        if visits is not None:
            visits.record_urgent_tail("banking", target=self.identity)
        bag = self.town("supplies")
        if urgent_valuables(bag["items"]):
            raise ValueError("Urgent valuables remain carried; farming will not resume")
        self.record(
            "urgent_banking_complete",
            activity="Valuables banked; returning to monsters",
        )
        if needs_town(supply_counts(bag, self.route), self.route, reserve=True):
            self.restock()
        else:
            # Valuables are already verified in storage. Use this required
            # safe town visit for the bounded refill window without shopping.
            self.optional_town_service()
        if visits is not None:
            visits.record_urgent_tail("followup", target=self.identity)
            from conquest.town_visit import checkpoint_verified_tail

            checkpoint_verified_tail(self, "urgent_banking")
            visits.complete_town_work("urgent_banking")

    def hunt(self):
        self.phase = "hunting"
        self.living()
        from conquest.world_travel import travel_to_map

        travel_to_map(self, self.route.map_id)
        from conquest.city_travel import ensure_city_visit

        ensure_city_visit(self)
        from conquest.conductress_shortcut import ride

        # Alex: the Conductress to Ape City is the fast way to the far fields.
        ride(self)
        select_app_route(self)
        request(
            self.info,
            "controls",
            {
                "enabled": True,
                "target_type_ids": list(self.route.monster_type_ids),
                "target_ids": [],
            },
        )
        visits = getattr(self, "town_visit", None)
        if visits is not None:
            visits.returning(self.route.map_id, target=self.identity)
        self.record("hunt_started", activity="Heading back to the hunting area")
        reached = None
        last_report = 0
        departed = None  # potions carried at the first supply read of this hunt
        self.potion_samples = []
        while True:
            h = self.health()
            data = h["embedded_controls"]
            if not data["control"]["enabled"]:
                raise OvernightStopped("Farming was switched Off")
            if data["control"].get("execution_state") == "runner_stopped":
                note = data["control"]["note"]
                if (
                    note.removeprefix("Farm runner stopped: ")
                    == "valuable_banking_required"
                ):
                    self.stop_farm()
                    return "urgent_banking"
            from conquest.merchant_loop_acceptance import observe_hunting

            if observe_hunting(self, h):
                self.stop_farm()
                return "merchant_acceptance"
            if data["control"].get("execution_state") == "runner_stopped":
                note = data["control"]["note"]
                reason = note.removeprefix("Farm runner stopped: ")
                if reason == "map_changed":
                    self.stop_farm()
                    self.return_to_route_map()
                    return "route_changed"
                if reason in (
                    "inventory_full",
                    "ammo_unavailable",
                    "potions_exhausted",
                    # The combat loop already read a TwinCityGate out of a
                    # losing fight; restocking in town needs no second scroll.
                    "emergency_return",
                ):
                    self.phase = "restocking"
                    self.record(
                        "return_required",
                        reason=reason,
                        activity="Returning to town to sell loot and restock",
                    )
                    self.stop_farm()
                    return
                if reason.startswith("observation_or_input_failure") and self.restart_runner(
                    data
                ):
                    continue
                raise ValueError(note)
            self.focus(h)
            life = data.get("life")
            if not life or life["dead_candidate"]:
                time.sleep(0.2)
                continue
            if life["map_id"] != self.route.map_id:
                self.stop_farm()
                self.return_to_route_map()
                return "route_changed"
            if visits is not None:
                completed = visits.observe_hunting(h)
                if completed:
                    self.record(
                        "town_visit_completed",
                        town_visit_id=completed["town_visit_id"],
                        elapsed_seconds=completed["elapsed_seconds"],
                        verified_resume_kill=completed["first_verified_resume_kill"],
                        activity="Required town visit complete; resumed hunting is verified",
                    )
                    observe_hunting(self, h)
            left, top, right, bottom = self.route.hunting_boundary
            margin = (
                self.route.patrol_search.expansion_tiles
                * self.route.patrol_search.maximum_expansions
            )
            if (
                left - margin <= life["position"][0] <= right + margin
                and top - margin <= life["position"][1] <= bottom + margin
            ):
                reached = reached or time.monotonic()
            if self.select_level_route(h):
                return "route_changed"
            bag = self.town("supplies")
            from conquest.banking import urgent_valuables

            if urgent_valuables(bag["items"]):
                self.stop_farm()
                return "urgent_banking"
            supplies = supply_counts(bag, self.route)
            from conquest.savings import progress

            if progress(self, supplies["silver"]):
                self.stop_farm()
                return "savings_target"
            from conquest import level_goal
            from conquest.banking import STATUS as BANK_STATUS
            from conquest.discord_notify import read_json

            step = level_goal.due(
                getattr(self, "last_level", 0),
                gear=lambda: self.town("gear"),
                city=self.route.restock_map_id,
                silver=supplies["silver"]
                + read_json(BANK_STATUS).get("stored_silver", 0),
            )
            if step == "reached":
                self.stop_farm()
                return "level_goal"
            if step == "gear":
                self.record(
                    "return_required",
                    reason="gear_review",
                    level=self.last_level,
                    supplies=supplies,
                    activity=f"Level {self.last_level}: returning to town to check gear upgrades",
                )
                self.stop_farm()
                return
            from conquest import buff_trip

            if buff_trip.hunt_should_end(bag["items"]):
                self.record(
                    "return_required",
                    reason="buff_refresh",
                    supplies=supplies,
                    activity="MrBuffer's double damage is running out; refreshing it in Twin City",
                )
                self.stop_farm()
                return
            if time.monotonic() - last_report > 10:
                app = read_status(state_path("reports/desktop-farming/app-state.json"))
                self.record(
                    "hunting",
                    position=life["position"],
                    supplies=supplies,
                    kills=app.get("kills"),
                    pickups=app.get("pickups"),
                    experience=app.get("experience"),
                )
                last_report = time.monotonic()
            forced = (
                self.cycles == 0
                and self.first_hunt_seconds
                and reached
                and time.monotonic() - reached >= self.first_hunt_seconds
            )
            if departed is None:
                departed = supplies["potions"]
            if self.heavy_burn(supplies["potions"]):
                self.phase = "restocking"
                self.record(
                    "return_required",
                    reason="heavy_damage",
                    supplies=supplies,
                    activity=f"{HEAVY_BURN_POTIONS}+ potions used in {HEAVY_BURN_WINDOW // 60} minutes; returning to town while some are left",
                )
                self.stop_farm()
                return
            if (
                needs_town(supplies, self.route, departed=departed, reserve=True)
                or forced
            ):
                self.record(
                    "return_required", supplies=supplies, validation_cycle=bool(forced)
                )
                self.stop_farm()
                return
            if not data.get("external_execution"):
                # Preserve On through transient memory/reconnect interruptions.
                request(self.info, "controls", {"enabled": True})
            from conquest.merchants.handoff import service_window

            service_window(self)
            time.sleep(1)

    def select_level_route(self, health=None):
        if not getattr(self, "auto_level", False):
            return False
        from conquest.leveling_routes import desired_route, hunting_level, read_level

        now = time.monotonic()
        if now < self.next_level_check:
            return False
        self.next_level_check = now + 5
        try:
            level = read_level(self.info, health or self.health())
            if level < self.last_level:
                raise ValueError("Level decreased during progression check")
            self.last_level = level
            from conquest.session_plan import active_plan

            plan = active_plan()
            selected, entry = desired_route(hunting_level(level))
            if not plan and selected is not None:
                from conquest.leveling_economy import economy_route

                # A wallet that cannot fund the next bracket's potions hunts
                # the cheaper one until it can (Toxic ran to 94 silver).
                selected, entry = economy_route(self, level, selected, entry)
            if plan:
                selected = RouteLibrary().load(plan["route_id"])
                if getattr(self, "reported_hold", None) != plan["started_at"]:
                    self.reported_hold = plan["started_at"]
                    self.record(
                        "route_hold_active",
                        route_hold=plan,
                        activity=(
                            f"Staying on {selected.name}; automatic route changes paused"
                            if plan.get("mode") == "hold_route"
                            else "Continuous Poltergeist farming; no silver limit"
                            if plan.get("mode") == "save_silver"
                            and plan.get("silver_target") is None
                            else "Saving 50,000 silver at Poltergeists; affordable IronArrows allowed"
                            if plan.get("mode") == "save_silver"
                            and plan.get("allow_iron_arrows")
                            else "Saving 50,000 silver at Poltergeists; upgrades disabled"
                            if plan.get("mode") == "save_silver"
                            else "Overnight hold: Bandits, with Phoenix shops only"
                            if plan["upgrade_maps"] == [1011]
                            else "Overnight hold: Bandits, with both-city equipment checks"
                        ),
                    )

            if getattr(self, "checked_bracket", None) != entry["id"]:
                self.checked_bracket = entry["id"]
                self.record(
                    "level_bracket_checked",
                    level=level,
                    level_bracket=entry["id"],
                    level_range=entry["levels"],
                    next_route=entry["name"],
                )
        except (ValueError, OSError):
            return False
        if selected is None:
            self.record(
                "level_route_pending",
                level=level,
                next_route=entry["name"],
                activity="Next level zone needs a travel connection; current hunt continues",
            )
            return False
        if selected.id == self.route.id:
            return False
        from conquest.city_travel import city_for

        try:
            city_for(selected.map_id)
        except ValueError as error:
            self.record(
                "level_route_pending",
                level=level,
                next_route=selected.name,
                detail=str(error),
                activity="Next level route needs a saved destination town",
            )
            return False
        life = (health or self.health())["embedded_controls"]["life"]
        from conquest.world_travel import connection_path, travel_to_map

        if selected.map_id != life["map_id"]:
            try:
                connection_path(life["map_id"], selected.map_id)
                connection_path(selected.map_id, selected.restock_map_id)
            except ValueError as error:
                self.record(
                    "level_route_pending",
                    level=level,
                    next_route=selected.name,
                    activity="Level route is waiting for a verified return connection",
                    detail=str(error),
                )
                return False
        self.stop_farm()
        self.phase = "changing_route"
        self.record(
            "level_route_departing",
            level=level,
            next_route=selected.name,
            activity=f"Level {level}: moving to {selected.name}",
        )
        if selected.map_id != life["map_id"]:
            # Another map starts from town: its Conductress trip walks to her
            # from where the farmer stands, ~950 tiles from the Poltergeists
            # and past town travel's 90 s limit.
            from conquest.return_scroll import return_to_town

            return_to_town(self)
        travel_to_map(self, selected.map_id)
        request(self.info, "controls", {"route_id": selected.id})
        deadline = time.monotonic() + 10
        while (
            read_status(state_path("reports/desktop-farming/app-state.json")).get(
                "selected_route"
            )
            != selected.id
        ):
            self.check_stop()
            if time.monotonic() > deadline:
                raise ValueError("Route selection was not acknowledged")
            time.sleep(0.1)
        previous = self.route.id
        self.route = selected.model_copy(
            update={
                "supplies": selected.supplies.model_copy(
                    update={
                        "arrow_type": self.route.supplies.arrow_type,
                        # Keep the potion tier chosen for this character's HP.
                        "healing_type": self.route.supplies.healing_type,
                    }
                )
            }
        )
        self.queue_route_optimization()
        self.terrain = read_terrain(
            installation_path(r"C:\Program Files\Classic Conquer 2.0"), selected.map_id
        )
        self.record(
            "level_route_changed",
            route=selected.id,
            previous_route=previous,
            level=level,
            activity=f"Level {level}: heading to {selected.name}",
        )
        return True

    def resume_on_route_map(self):
        """Start on the route's map: the level route when the character
        already stands on its map, else travel back to the current route's."""
        if self.living()["embedded_controls"]["life"]["map_id"] != self.route.map_id:
            # A level route change interrupted after the crossing continues
            # on the new route's map instead of walking back (live 2026-09-27
            # 17:46: through Phoenix's west gate into Twin City, the restarted
            # WingedSnake route turned round for Phoenix).
            self.select_level_route()
        if self.living()["embedded_controls"]["life"]["map_id"] != self.route.map_id:
            self.return_to_route_map()

    def return_to_route_map(self):
        from conquest.world_travel import travel_to_map

        self.stop_farm()
        self.phase = "recovering_route"
        self.record(
            "returning_to_route_map",
            activity=f"Returning to {self.route.name} after map change",
        )
        travel_to_map(self, self.route.map_id)
        # Route selection clears an obsolete recovery checkpoint after a cross-map revive.
        request(self.info, "controls", {"route_id": self.route.id})
        time.sleep(0.2)

    def adopt_ammunition(self, state=None):
        from conquest.arrow_upgrades import (
            current_arrow,
            NORMAL_ARROWS,
            refill_target,
        )

        from conquest.savings import savings_plan

        state = state or self.town("gear")
        supplies = self.town("supplies")
        reserves = [i["type_id"] for i in supplies["items"] if i["amount"] >= 3]
        # With nothing usable, refill the level-best tier. Only savings mode
        # keeps its configured tier (Lucky, or user-authorized Iron), and a
        # leveling archer the best tier its wallet sustains.
        default = self.route.supplies.arrow_type if savings_plan() else None
        from conquest.equipment import leveling_archer

        if default is None and leveling_archer() and type(state.get("level")) is int:
            from conquest.arrow_upgrades import leveling_tier
            from conquest.banking import STATUS
            from conquest.discord_notify import read_json

            default = leveling_tier(
                state["level"],
                supplies.get("silver", 0) + read_json(STATUS).get("stored_silver", 0),
            )
        kind = current_arrow(
            state,
            default,
            reserves,
            equipped_ammo=supplies.get("equipped_ammo"),
        )
        target = refill_target(kind)
        # A measured supply plan may carry fewer packs so more potions fit.
        target = min(target, getattr(self, "planned_arrows", {}).get(kind, target))
        if (
            kind != self.route.supplies.arrow_type
            or target != self.route.supplies.arrows_restock_to
        ):
            supplies = self.route.supplies.model_copy(
                update={"arrow_type": kind, "arrows_restock_to": target}
            )
            self.route = self.route.model_copy(update={"supplies": supplies})
            self.record(
                "ammunition_selected",
                arrow_type=kind,
                activity=f"Using {NORMAL_ARROWS[kind]} for combat and restocking",
            )

    def carried_arrow_fallback(self):
        """Hunt on with another carried tier once the route's runs out.

        A leveling archer that moved up to IronArrows keeps the LuckyArrows it
        carried as a fallback; the Farmer starts combat on the best carried
        tier. Only a spent route tier on the hunting map qualifies: every other
        shortage, or a farmer already scrolled home, still restocks.
        """
        counts = supply_counts(self.town("supplies"), self.route)
        if counts["arrows"] >= self.route.supplies.arrows_return_below:
            return False
        life = self.health()["embedded_controls"].get("life")
        if not life or life["map_id"] != self.route.map_id:
            return False
        from conquest.arrow_upgrades import NORMAL_ARROWS

        spent = self.route.supplies.arrow_type
        self.adopt_ammunition()
        kind = self.route.supplies.arrow_type
        counts = supply_counts(self.town("supplies"), self.route)
        if kind == spent or needs_town(counts, self.route, reserve=True):
            return False
        self.record(
            "arrow_fallback",
            arrow_type=kind,
            previous_arrow_type=spent,
            supplies=counts,
            activity=f"{NORMAL_ARROWS[spent]} spent; hunting on with the carried "
            f"{NORMAL_ARROWS[kind]}",
        )
        return True

    def prepare_supplies(self):
        self.living()
        self.stop_farm()
        self.town("close", window="Shop")
        self.town("close", window="Inventory")
        self.adopt_ammunition()
        counts = supply_counts(self.town("supplies"), self.route)
        from conquest.session_plan import active_plan, CIRCUIT
        from conquest.discord_notify import read_json

        plan = active_plan()
        circuit = read_json(CIRCUIT)
        interrupted_circuit = bool(
            plan
            and circuit.get("plan_started_at") == plan["started_at"]
            and not circuit.get("finished_at")
            and not circuit.get("completed")
        )
        if needs_town(counts, self.route, reserve=True):
            self.restock()
        elif interrupted_circuit:
            from conquest.session_plan import upgrade_circuit

            upgrade_circuit(self)
        else:
            from types import SimpleNamespace

            from conquest import buff_trip
            from conquest.return_scroll import in_town

            # Starting in town (a deploy, a restart): fetch MrBuffer's buff
            # before walking out, by gate or, with none carried, the ride.
            if buff_trip.enabled():
                life = self.living()["embedded_controls"]["life"]
                items = self.town("supplies")["items"]
                if in_town(SimpleNamespace(**life), self.route.restock_map_id):
                    if buff_trip.refresh_due(items):
                        buff_trip.trip(self)
                    elif buff_trip.bootstrap_due(self, items):
                        buff_trip.trip(self, ride=True)
            self.record("supplies_ready", supplies=counts)

    def _run_route(self):
        from conquest.merchants.delivery_operation import guard_protected_assets
        from conquest.merchants import delivery_journey

        # A journal-matching scroll operation gets one read-only reconciliation
        # before generic asset guards. No farming stop, focus or movement input
        # is allowed until that read has released the durable ownership hold.
        delivery_journey.reconcile_pending_scroll(self)
        guard_protected_assets()
        from conquest.manual_storage_recovery import resume as resume_manual_storage

        resume_manual_storage(self)
        if delivery_journey.pending():
            self.stop_farm()
            delivery_journey.resume(self)
            from conquest.banking import after_shopping

            after_shopping(self)
        from conquest import meteor_banking

        if meteor_banking.pending():
            self.stop_farm()
            from conquest.restock_town_recovery import capture_pre_admission_tail

            capture_pre_admission_tail(self)
            from conquest.restock_town_recovery import (
                require_claimed_identity_before_meteor,
            )

            require_claimed_identity_before_meteor(self)
            meteor_banking.resume(self)
            from conquest.banking import close_warehouse

            close_warehouse(self)
        from conquest.storage_overflow import pending, resume

        if pending():
            self.stop_farm()
            resume(self)
            from conquest.banking import close_warehouse

            close_warehouse(self)
        from conquest.merchant_loop_acceptance import cycle_pending

        if cycle_pending():
            self.bank_acceptance_delivery()
        from conquest.town_visit import resume_verified_tail

        resume_verified_tail(self)
        self.resume_settled_town_work()
        from conquest.urgent_town_recovery import resume_claimed

        resume_claimed(self)
        from conquest.restock_town_recovery import (
            resume_claimed as resume_restock_claimed,
        )

        resume_restock_claimed(self)
        from conquest.restock_town_recovery import resume_pre_admission_tail

        resume_pre_admission_tail(self)
        from conquest.restock_cash_tail import resume as resume_restock_cash_tail

        # Only a proved restock whose Meteor batch completed on restart, with no
        # recorded or possible transfer since it began; claimed once, never replayed.
        resume_restock_cash_tail(self)
        from conquest.restock_restart import resume as restart_zero_transaction_restock

        # Only a restock whose route failed walking to the Warehouseman before
        # any transaction, proved from its journals; revives a dead farmer via
        # living() first, marks it once, then runs restock() again; never replayed.
        restart_zero_transaction_restock(self)
        # A controller (re)started on a dead farmer revives it first, with
        # Farming Off (see revive_before_route).
        self.revive_before_route()
        # Never leave town merely because a restarted worker sees stocked
        # supplies. The previous process may have stopped before banking or
        # may have submitted a transfer whose result needs reconciliation.
        self.town_visit.require_town_work_complete()
        self.resume_on_route_map()
        from conquest.city_travel import ensure_city_visit

        ensure_city_visit(self)
        self.prepare_supplies()
        self.select_level_route()
        from conquest import scatter_training

        # A trainer visit that came due while the route was down (a deploy at
        # the Scatter level) is made now, not a restock later.
        if scatter_training.due(self):
            scatter_training.attempt(self)
        while True:
            outcome = self.hunt()
            if outcome == "merchant_acceptance":
                self.bank_acceptance_delivery()
                continue
            if outcome == "urgent_banking":
                self.bank_urgent_valuables()
                continue
            if outcome == "savings_target":
                from conquest.savings import finish_in_town

                if finish_in_town(self):
                    return
                continue
            if outcome == "level_goal":
                from conquest import level_goal

                if level_goal.finish_in_town(self):
                    return
                continue
            if outcome != "route_changed":
                if self.carried_arrow_fallback():
                    continue
                self.restock()

    def bank_acceptance_delivery(self):
        """Temporary early town obligation; reuse only native bank/trade paths."""
        from conquest import merchant_loop_acceptance as acceptance

        if not acceptance.cycle_pending():
            return
        self.check_stop()
        acceptance.reconcile_route_receipts()
        from conquest.merchants.bridge import request as merchant

        source = merchant({"action": "delivery-source"})["farmer"]
        carried = acceptance.verify_carried_or_delivered(source)
        visit = self.town_visit.begin(
            "merchant_acceptance", hunt_map_id=self.route.map_id, route_id=self.route.id
        )
        acceptance.town_started(self, visit, source)
        self.phase = "restocking"
        if carried:
            self.record(
                "merchant_acceptance_return",
                activity="Acceptance: newly looted deliverable; returning for native merchant delivery",
            )
            from conquest.return_scroll import return_to_town
            from conquest.world_travel import travel_to_map
            from conquest.banking import after_shopping

            acceptance.town_input_boundary()
            return_to_town(self)
            travel_to_map(self, self.route.restock_map_id)
            self.town("close", window="Shop")
            self.town("close", window="Inventory")
            if not after_shopping(self):
                raise ValueError("Acceptance requires the normal native banking policy")
        from conquest.merchants.handoff import service_window

        acceptance.refill_observed(merchant({"action": "status"}).get("characters", {}))
        if not acceptance.refill_complete():
            service_window(self, town=True)
        acceptance.finish_town(self, send=merchant)
        bag = self.town("supplies")
        if needs_town(supply_counts(bag, self.route), self.route, reserve=True):
            self.restock()
        self.town_visit.complete_town_work("merchant_acceptance")

    def protect_during_movement_retry(self):
        """Retain the controller and life care instead of abandoning a runback."""
        self.phase = "recovering_route"
        self.walk_after_obstruction = True
        self.record(
            "route_movement_retry",
            activity="Route blocked; healing and revival active while replanning",
        )
        until = time.monotonic() + 2
        while time.monotonic() < until:
            self.check_stop()
            health = self.living()
            try:
                self.care.check(health)
            except TravelStateChanged:
                pass
            time.sleep(0.1)

    def recover_travel_stall(self, error):
        """Keep survival active off-Market, without bypassing trade settlement."""
        if getattr(error, "code", "no_progress") != "no_progress":
            raise error
        from conquest.merchants.delivery_route import pending as delivery_pending

        if delivery_pending():
            # A submitted/ambiguous trade owns the next action. Route recovery
            # must never carry that state into hunting or another town action.
            raise error
        health = self.living()
        life = health["embedded_controls"].get("life")
        if not life or life.get("map_id") == 1036:
            # Market is the bounded merchant fallback. Its work deadline must
            # not be renewed by the ordinary exposed-runback retry path.
            raise error
        self.protect_during_movement_retry()

    def _run_route_once(self):
        from conquest.travel_progress import TravelStalled

        while True:
            try:
                self._run_route()
                return
            except CaptureUnavailable as error:
                # This loop runs in its own process. Read the app's fresh
                # authenticated control projection rather than its empty
                # local coordinator singleton.
                try:
                    fenced = bool(
                        self.health()["embedded_controls"].get("manual_input_fence")
                    )
                except (
                    CaptureUnavailable,
                    ValueError,
                    OSError,
                    KeyError,
                    TypeError,
                ):
                    fenced = False
                if not fenced and str(error) not in (
                    "Manual visitor session holds farmer input",
                    "Manual visitor session holds automation input",
                ):
                    raise
                # A user-owned global manual handoff is a normal wait,
                # not a failed town/route action.  The next fresh memory
                # loop replans after the durable settlement signal.
                self.record(
                    "manual_handoff_wait",
                    activity="Waiting for operator manual handoff to settle",
                )
                time.sleep(0.2)
            except TravelStalled as error:
                self.recover_travel_stall(error)
            except ValueError as error:
                if str(error) not in (
                    "Town route remains obstructed",
                    "Town travel has made no position progress for 90 seconds",
                ):
                    raise
                self.protect_during_movement_retry()

    def failure_record(self, error):
        # Preserve the failing boundary without retaining locals or other
        # process data.  A generic message is not enough to distinguish a
        # pre-input acquisition denial from an uncertain submitted action.
        import traceback

        frames = traceback.extract_tb(error.__traceback__)[-8:]
        return {
            "detail": str(error),
            "error_type": type(error).__module__ + "." + type(error).__qualname__,
            "failure_trace": [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in frames
            ],
        }

    def auto_restart(self, error):
        """Bounded self-restart after an unexpected route failure.

        At most AUTO_RESTARTS per rolling hour. The failure is recorded as
        'recovered_failure' (so history stays visible), the farmer is kept
        alive for a short pause, then the route replans from fresh reads.
        Returns False when the budget is spent, so the caller stops as before.
        """
        now = time.monotonic()
        recent = [t for t in getattr(self, "auto_restarts", []) if now - t < 3600]
        if len(recent) >= AUTO_RESTARTS:
            return False
        recent.append(now)
        self.auto_restarts = recent
        self.record(
            "recovered_failure",
            attempt=len(recent),
            activity="Unexpected stop; restarting the route automatically",
            **self.failure_record(error),
        )
        self.phase = "recovering_route"
        self.release_farm_for_care()
        until = time.monotonic() + AUTO_RESTART_PAUSE_SECONDS
        while time.monotonic() < until:
            self.check_stop()
            self.protect_during_pause()
            time.sleep(0.5)
        self.refresh()
        return True

    def release_farm_for_care(self):
        """Turn the app's farming control off before a restart pause.

        A runner stopped for a reason hunt() does not handle (for example
        "reposition_outside_boundary") left the control on. TravelCare.check
        then refused every pass ("Travel care cannot share input with
        farming"): no heal, and the evasion after it never ran. Suicide stood
        at (582, 307) through a 20 s pause among GiantApes and died on
        2026-09-30 01:20 (912 -> 231 HP in 9 s). The runner has already
        stopped, so nothing is interrupted; hunt() turns farming on again.
        """
        try:
            health = self.health()
            if health["embedded_controls"]["control"].get("enabled"):
                self.stop_farm()
                self.record(
                    "restart_farm_released",
                    activity="Farming control released so healing and evasion can act during the restart",
                )
        except OvernightStopped:
            raise
        except Exception:
            pass

    def protect_during_pause(self):
        """Life care, then field evasion, each on its own: a refused care
        pass must not skip the jump away from monsters."""
        health = None
        try:
            health = self.living()
            self.care.check(health)
        except OvernightStopped:
            raise
        except Exception:
            pass
        if health is None:
            return
        try:
            self.evade_in_field(health)
        except OvernightStopped:
            raise
        except Exception:
            pass

    def evade_in_field(self, health, tiles=FIELD_EVADE_TILES):
        """One escape jump from monsters closing in while a pause runs outside town.

        Restart pauses and failure cooldowns only ran life care, so a failure
        in the field left the farmer standing among monsters. On 2026-09-29
        15:58 a failed restock travel left Suicide among four GiantApes on the
        plain north of Ape City: it drank its last potion and died 8 s into
        the 20 s pause (the 11:17 death on the Macaque field was the same
        gap). Uses the runback's escape_step: a clear visible landing with
        less danger, nearer the town on ties. A boss inside the route's
        clearance for it also calls for the jump; no landing or path enters
        another boss's clearance. ``tiles``: how close a living monster calls
        for the jump. Returns whether it jumped.
        """
        now = time.monotonic()
        if now - getattr(self, "field_evaded_at", -FIELD_EVADE_SECONDS) < FIELD_EVADE_SECONDS:
            return False
        controls = health["embedded_controls"]
        life = controls.get("life") or {}
        position = life.get("position")
        if (
            not position
            or life.get("dead_candidate")
            or life.get("map_id") != getattr(self.terrain, "map_id", None)
            or self.stepper is None
        ):
            return False
        source = tuple(position)
        box = town_box(life["map_id"])
        if box and box[0] <= source[0] <= box[2] and box[1] <= source[1] <= box[3]:
            return False
        from conquest.routes import BOSS_CLEARANCE, boss_clearance, boss_name

        king = getattr(self.route, "king_clearance", BOSS_CLEARANCE)
        elite = getattr(self.route, "elite_clearance", BOSS_CLEARANCE)
        living = [
            m for m in controls.get("monsters") or []
            if m.get("position") and m.get("alive") is not False
        ]
        distance = lambda m: max(abs(a - b) for a, b in zip(m["position"], source))
        # A boss inside its clearance weighs as five monsters, so the landing
        # leaves it; every other boss's clearance is no landing and no path.
        threats, avoid, close = list(living), set(), False
        for m in living:
            name = m.get("name") or ""
            if not boss_name(name):
                close = close or distance(m) <= tiles
                continue
            reach = boss_clearance(name, king, elite)
            if distance(m) <= reach:
                threats += [m] * 4
                close = True
                continue
            bx, by = m["position"]
            avoid.update(
                (x, y)
                for x in range(max(bx - reach, source[0] - 13), min(bx + reach, source[0] + 13) + 1)
                for y in range(max(by - reach, source[1] - 13), min(by + reach, source[1] + 13) + 1)
            )
        if not close:
            return False
        from types import SimpleNamespace
        from conquest.runback_monitor import escape_step
        from conquest.scene_input import memory_player_anchor

        anchor = memory_player_anchor(
            SimpleNamespace(adapter=self.care.session), SimpleNamespace(**life)
        )
        escape = escape_step(
            self.terrain,
            source,
            tuple(getattr(self.route, "town_anchor", None) or source),
            anchor,
            threats,
            avoid=avoid,
            viewport=tuple(health.get("window", {}).get("client_size", (1036, 793))),
        )
        if escape is None:
            return False
        self.field_evaded_at = now
        self.record(
            "restart_evading",
            source=source,
            destination=escape,
            activity="Monsters close during a route restart; jumping clear",
        )
        self.stepper.step_to(escape, expected_position=source)
        return True

    def failure_cooldown(self, error):
        """Wait out a failure that keeps recurring, then replan from fresh reads.

        Stopping for good after the hourly restart budget left the farmer
        standing in town for seven hours (live 2026-09-27 01:50). The pause
        grows with each consecutive cooldown; life care keeps running, the
        health reads keep the status heartbeat fresh and manual Stop wins.
        """
        streak = getattr(self, "cooldowns", 0)
        seconds = FAILURE_COOLDOWNS[min(streak, len(FAILURE_COOLDOWNS) - 1)]
        self.cooldowns = streak + 1
        self.record(
            "failure_cooldown",
            attempt=self.cooldowns,
            seconds=seconds,
            activity=f"Repeated failures; retrying in {seconds // 60} minutes",
            **self.failure_record(error),
        )
        self.phase = "recovering_route"
        self.release_farm_for_care()
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.check_stop()
            self.protect_during_pause()
            time.sleep(1)
        self.auto_restarts = []
        self.refresh()

    def run(self):
        self.check_stop()
        self.refresh()
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        self.record("started")
        try:
            # A pending acceptance recovery checks the exact live controller
            # identity before it reaches the ordinary route/life loop.
            # Establish it from fresh read-only health memory; never infer it
            # from the acceptance journal.
            self.health()
            while True:
                try:
                    self._run_route_once()
                    return
                except OvernightStopped:
                    raise
                except Exception as error:
                    # Open journals keep their own reconciliation gates, so a
                    # restart can never replay an uncertain transaction.
                    if not self.auto_restart(error):
                        self.failure_cooldown(error)
        except OvernightStopped as error:
            self.phase = "stopped"
            self.record("stopped", detail=str(error))
        except Exception as error:
            self.phase = "needs_attention"
            self.record("failed", **self.failure_record(error))
        finally:
            try:
                # The desktop app removes the authenticated bridge receipt as
                # it exits.  A missing receipt during controller teardown is
                # already a stopped input surface, not a new route failure.
                try:
                    request(self.info, "controls", {"enabled": False})
                except FileNotFoundError:
                    pass
            finally:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
