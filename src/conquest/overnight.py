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
from conquest.worker import request
from conquest.capture import CaptureUnavailable

RECOVERY_CHECKPOINT = Path(state_path(".runtime/death-return.json"))


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


def supply_counts(snapshot, route):
    arrows = sum(
        i["amount"]
        for i in snapshot["items"]
        if i["type_id"] == route.supplies.arrow_type
    )
    ammo = snapshot.get("equipped_ammo")
    if ammo and ammo["type_id"] == route.supplies.arrow_type:
        arrows += ammo["amount"]
    potions = sum(
        i["amount"]
        for i in snapshot["items"]
        if i["type_id"] == route.supplies.healing_type
    )
    return {
        "arrows": arrows,
        "potions": potions,
        "free_slots": snapshot["capacity"] - len(snapshot["items"]),
        "silver": snapshot["silver"],
    }


def needs_town(counts, route):
    return counts["arrows"] < 3 or counts["potions"] <= 0 or counts["free_slots"] <= 0


def pharmacist_needed(snapshot, route, *, scroll_enabled=False):
    counts = supply_counts(snapshot, route)
    if counts["potions"] < route.supplies.healing_restock_to:
        return True
    if any(sale_candidate(item) for item in snapshot["items"]):
        return True
    return (
        scroll_enabled
        and route.restock_map_id == 1002
        and sum(i["amount"] for i in snapshot["items"] if i["type_id"] == 1060020) < 2
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
            if life and life["dead_candidate"] and not data["control"]["enabled"]:
                try:
                    self.care.check(h)
                except TravelStateChanged:
                    pass
            time.sleep(0.15)  # The wrapper reconnects; never use disconnected stats.

    def town(self, action, **fields):
        vendor = {3: "Pharmacist", 5: "Blacksmith"}.get(
            fields.get("vendor_type"), "shop"
        )
        activity = {
            "open": f"Opening {vendor} shop",
            "buy": f"Buying { {1000020: 'Painkiller', 1050000: 'LuckyArrow', 1050001: 'IronArrow', 1050002: 'SpeedArrow'}.get(fields.get('type_id'), 'supplies') } from {vendor}",
            "sell": f"Selling unwanted loot to {vendor}",
        }
        if action in activity:
            self.record("town_activity", activity=activity[action])
        for attempt in range(80):
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
                return request(self.info, "town", body)
            except ValueError as error:
                if action == "service-locate" and str(error).startswith(
                    "One memory-identified "
                ):
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
            3: "Pharmacist to sell loot and buy Painkiller",
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
        while time.monotonic() < deadline:
            waiting = time.monotonic()
            h = self.living()
            life = h["embedded_controls"]["life"]
            source = tuple(life["position"])
            occupied.discard(source)
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
            try:
                self.care.check(h)
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
                blocked = occupied | avoided
                target = (
                    travel_waypoint(
                        self.terrain, path, step_limit, avoid=blocked, viewport=viewport
                    )
                    if hasattr(self.terrain, "travel_path")
                    else native_waypoint(path, step_limit, viewport=viewport)
                )
                from types import SimpleNamespace
                from conquest.scene_input import (
                    memory_player_anchor,
                    visible_route_delta,
                    clear_route_point,
                )

                anchor = memory_player_anchor(
                    SimpleNamespace(adapter=self.care.session), SimpleNamespace(**life)
                )
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
                if not clear_route_point((px, py), bounds):
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
                from conquest.navigation import clear_segment

                if hasattr(self.terrain, "travel_path") and not clear_segment(
                    self.terrain, source, target, avoid=blocked
                ):
                    avoided.add(target)
                    continue
                result = self.stepper.step_to(target, expected_position=source)
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
            MAX_ARROW_PACKS,
            arrow_pack_count,
        )

        if type_id in NORMAL_ARROWS and arrow_pack_count(snapshot) >= MAX_ARROW_PACKS:
            self.record(
                "arrow_purchase_deferred",
                arrow_packs=arrow_pack_count(snapshot),
                activity="Keeping existing arrow packs; one equipped and one spare is enough",
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
        if (
            type_id in (1050001, 1050002)
            and before["arrows"] >= self.route.supplies.arrows_return_below
        ):
            shop = self.town("shop", vendor_type=vendor_type)
            products = [p for p in shop["products"] if p["type_id"] == type_id]
            if len(products) != 1:
                raise ValueError("Selected arrow price is not verified")
            if before["silver"] - products[0]["price"] < 3000:
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
        from conquest.savings import savings_plan, configure_route

        if savings_plan():
            self.route = configure_route(self.route, self.town("supplies")["silver"])
        from conquest.city_travel import city_for

        services = city_for(self.route.restock_map_id).get("services")
        if not services:
            raise ValueError("Restock vendors are not mapped in the destination city")
        from conquest.return_scroll import return_to_town

        return_to_town(self)
        from conquest.world_travel import travel_to_map

        travel_to_map(self, self.route.restock_map_id)
        # A restarted controller may inherit a shop left open by the failed run.
        self.town("close", window="Shop")
        self.town("close", window="Inventory")
        from conquest.banking import fund_restock

        fund_restock(self)
        from conquest.return_scroll import stock, POLICY as scroll_policy
        from conquest.discord_notify import read_json

        if pharmacist_needed(
            self.town("supplies"),
            self.route,
            scroll_enabled=read_json(scroll_policy).get("enabled", False),
        ):
            self.travel(self.route.restock_anchor)
            self.town("open", vendor_type=3)
            self.sell_junk(3)
            self.shopping_space(3, self.route.restock_anchor)
            for _ in range(30):
                counts = supply_counts(self.town("supplies"), self.route)
                if counts["potions"] >= self.route.supplies.healing_restock_to:
                    break
                reserve = self.route.supplies.minimum_free_slots + int(
                    counts["arrows"] < self.route.supplies.arrows_return_below
                )
                if (
                    counts["free_slots"] <= reserve
                    and counts["potions"] >= self.route.supplies.healing_return_below
                ):
                    break
                if not self.buy_supply(3, self.route.supplies.healing_type):
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
                if not self.buy_supply(5, self.route.supplies.arrow_type):
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
        from conquest.merchants.handoff import service_window

        service_window(self, town=True)
        if visits is not None:
            from conquest.town_visit import checkpoint_verified_tail

            checkpoint_verified_tail(self, "restock")
            visits.complete_town_work("restock")
        self.cycles += 1
        self.record("restock_complete", supplies=counts)

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
        if needs_town(supply_counts(bag, self.route), self.route):
            self.restock()
        else:
            # Valuables are already verified in storage. Use this required
            # safe town visit for the bounded refill window without shopping.
            from conquest.merchants.handoff import service_window

            service_window(self, town=True)
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
                ):
                    self.phase = "restocking"
                    self.record(
                        "return_required",
                        reason=reason,
                        activity="Returning to town to sell loot and restock",
                    )
                    self.stop_farm()
                    return
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
            if needs_town(supplies, self.route) or forced:
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
        from conquest.leveling_routes import read_level, desired_route

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
            selected, entry = desired_route(level)
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
                    update={"arrow_type": self.route.supplies.arrow_type}
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
            ARROW_REFILL_AMOUNTS,
        )

        state = state or self.town("gear")
        supplies = self.town("supplies")
        reserves = [i["type_id"] for i in supplies["items"] if i["amount"] >= 3]
        kind = current_arrow(
            state,
            self.route.supplies.arrow_type,
            reserves,
            equipped_ammo=supplies.get("equipped_ammo"),
        )
        target = ARROW_REFILL_AMOUNTS[kind]
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
        if needs_town(counts, self.route):
            self.restock()
        elif interrupted_circuit:
            from conquest.session_plan import upgrade_circuit

            upgrade_circuit(self)
        else:
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
        # Never leave town merely because a restarted worker sees stocked
        # supplies. The previous process may have stopped before banking or
        # may have submitted a transfer whose result needs reconciliation.
        self.town_visit.require_town_work_complete()
        if self.living()["embedded_controls"]["life"]["map_id"] != self.route.map_id:
            self.return_to_route_map()
        from conquest.city_travel import ensure_city_visit

        ensure_city_visit(self)
        self.prepare_supplies()
        self.select_level_route()
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
            if outcome != "route_changed":
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
        if needs_town(supply_counts(bag, self.route), self.route):
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

    def run(self):
        self.check_stop()
        self.refresh()
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        self.record("started")
        from conquest.travel_progress import TravelStalled

        try:
            # A pending acceptance recovery checks the exact live controller
            # identity before it reaches the ordinary route/life loop.
            # Establish it from fresh read-only health memory; never infer it
            # from the acceptance journal.
            self.health()
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
        except OvernightStopped as error:
            self.phase = "stopped"
            self.record("stopped", detail=str(error))
        except Exception as error:
            self.phase = "needs_attention"
            # Preserve the failing boundary without retaining locals or other
            # process data.  A generic message is not enough to distinguish a
            # pre-input acquisition denial from an uncertain submitted action.
            import traceback

            frames = traceback.extract_tb(error.__traceback__)[-8:]
            self.record(
                "failed",
                detail=str(error),
                error_type=type(error).__module__ + "." + type(error).__qualname__,
                failure_trace=[
                    {
                        "file": frame.filename,
                        "line": frame.lineno,
                        "function": frame.name,
                    }
                    for frame in frames
                ],
            )
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
