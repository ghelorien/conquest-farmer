"""Run the existing foreground combat loop with the hosted client's life reader."""

from conquest.character_context import state_path
from contextlib import contextmanager
from dataclasses import asdict, replace
import ctypes
import time

from conquest.capture import CaptureUnavailable
from conquest.viewport import size_for, clear_scene
from conquest.valuables import SPECIAL_LOOT_TYPES

# Seconds of recent damage that can still justify an escape jump.
DAMAGE_WINDOW = 1.25
# A hit taking more than this share of max HP is answered by a jump at once
# (Alex 2026-09-27: "As soon as you get attacked by damage that is over 1%
# of max hp jump away and start attacking back"). The former 10% bar let a
# typical 9% Apparition hit through: 43% of hits got no jump.
ESCAPE_DAMAGE_SHARE = 0.01
# How far the same exact monster (entity and object) may have moved between
# selection and input and still be re-aimed at its fresh position; the range
# check then applies. Apparitions drift 3-4 tiles in that time (live
# 2026-09-27: 77 attacks refused in 5 minutes at the old bound of 2).
MONSTER_DRIFT_TILES = 5
# Silver counts as ours when the client created it from this long before to
# this long after one of our verified kills (client ticks, ms), within this
# many tiles of the monster we were attacking (Scatter also kills its
# neighbours), or of the farmer when no target tile is remembered.
KILL_DROP_BEFORE_MS = 3000
KILL_DROP_AFTER_MS = 1500
KILL_DROP_RADIUS = 5
KILL_DROP_UNKNOWN_RADIUS = 10
KILL_SITE_KEEP_MS = 20000
# Scatter kills whatever its fan reaches, often not the monster used to aim:
# a kill this soon after a cast also counts silver within Scatter reach (8
# tiles on 1078) plus a step of where the archer cast from (live 2026-09-27
# 17:57-18:00: 17 jump-Scatter kills on Poltergeists, not one pickup).
KILL_DROP_SCATTER_SECONDS = 3
SCATTER_REACH = 8
KILL_DROP_SCATTER_RADIUS = SCATTER_REACH + 1
# An unconfirmed pickup of our own silver is retried this soon. Jump-Scatter
# escapes cut pickup walks short (live 2026-09-27 18:52: three of five silver
# clicks unconfirmed); the former minute outlived the drop's own-kill window.
SILVER_RETRY_SECONDS = 5
# The target's scene position this recent stands for where it died.
TARGET_SEEN_MS = 3000
# An escape jump that has not moved the farmer this long after the click
# failed (live 09-27 11:39: surrounded, the jump never happened, the loop
# attacked and Suicide died). Its landing is avoided for a few seconds and a
# few immediate retries go elsewhere before the normal escape cadence.
ESCAPE_VERIFY_SECONDS = 0.45
ESCAPE_BLOCK_SECONDS = 3
ESCAPE_QUICK_RETRIES = 3
ESCAPE_MIN_JUMP = 6
# The longest escape or Scatter jump (ranged_escape tries 12, 10 and 8
# tiles). Bosses are watched this much beyond their clearance and room: the
# ElfAide, 16 tiles off and unwatched, had two escapes land 7-8 tiles from
# it and took Toxic from 77% to 33% HP (FireSpirit field, 2026-09-28
# 16:53:25-16:53:31).
LANDING_REACH = 12
# Below this HP share a crowded escape takes the least crowded open landing
# even when none has fewer monsters than are attacking.
ESCAPE_LOW_HP = 0.5
# Jump-Scatter leaves before a monster is close enough to hit: Poltergeists
# hit from two tiles (Toxic 2026-09-27 16:30-16:50: in 61 of 100 hits no
# monster stood within one tile and the archer had not moved), so a one-tile
# trigger jumped only after the hit. Two tiles of reach plus one step.
JUMP_SCATTER_REACH = 3
# Valuables other than silver are clicked from at most this many tiles: of
# twelve Meteors seen on 2026-09-27 one was another player's, five were picked
# and six were left, three of them after a click from 10-16 tiles missed.
# Suicide 2026-09-27..29: 21 of 25 valuable clicks from 1-3 tiles were picked
# up, 33 of 60 from 4-6 tiles; each miss costs 4 s and usually the approach.
VALUABLE_CLICK_TILES = 3
# Unverified clicks on one valuable before it waits VALUABLE_MISS_COOLDOWN
# seconds, so a drop that cannot be picked up never holds the turn for good.
VALUABLE_CLICK_MISSES = 3
VALUABLE_MISS_COOLDOWN = 30
# Tiles past the hunting boundary a walk toward a valuable (never silver) may go.
VALUABLE_BOUNDARY_SLACK = 12
# Alex 2026-09-29: "There should be a 25 tile radius for valuables". A
# valuable (never silver) within this many tiles of the farmer is walked to
# wherever it lies: the loot audit showed Uniques and +1s at x 355-389, past
# the WingedSnake boundary's 352, that were never picked up (2026-09-28).
# The walk's last stretch counts from the drop too (approach_loot.allowed):
# 2026-10-01 a Meteor 38 tiles off at (546,377), 33 past the Bandit box, and
# two 29 tiles off at (475,323), just above its slack, were left on the
# ground, and Alex approved valuables pulling the farmer past the hunt's edge
# (2026-10-02). The 40-tile search still bounds every such walk.
VALUABLE_RADIUS = 25
# Such a walk holds the trial's boundary return this long after its last step
# or click, so leaving the box does not turn it straight back.
VALUABLE_CHASE_SECONDS = 3
# A valuable's approach that meets a moving farmer (projection or action
# changed before input) holds combat this long so the next try sees a settled
# frame, then not again for LOOT_SETTLE_COOLDOWN; never under LOOT_FIRST_HP.
# Suicide 2026-10-01 21:00-04:30: 4 of its 7 missed valuables (an Elite armor
# deferred 8 times, a Unique ring, a +1, a Meteor) were lost to "Player
# projection changed before loot input" while each retry's Scatter jump moved
# the farmer again.
LOOT_SETTLE_SECONDS = 1.5
LOOT_SETTLE_COOLDOWN = 3.0
# Ground drops audit_loot remembers (by uid and address) before starting over.
LOOT_AUDIT_MEMORY = 5000
# A boss seen this recently still shapes walks once out of view (Kings idle
# for hours; remember_bosses), unless the farmer comes within BOSS_SIGHT_TILES
# of its last tile and it is not there.
BOSS_MEMORY_SECONDS = 600
BOSS_SIGHT_TILES = 12
# Only remembered bosses this near the farmer shape its walk (each adds a
# clearance square to every patrol_step's zone).
BOSS_MEMORY_REACH = 120
# Fly, the archer's XP skill: this life status bit (xp_skill reads the same
# one), from a life read at most FLY_STATUS_SECONDS old. Alex 2026-10-01
# 05:5x: "When you are flying you can be super bold and go in huge packs as
# you cannot be attacked by melee monsters."
FLY_STATUS = 0x8000000
FLY_STATUS_SECONDS = 1.0
# An ordinary monster this many levels below the archer never calls for an
# escape jump by coming close; only its hits do (overnight.GATE_HARMLESS_LEVELS
# for gate reads). Alex 2026-10-01 06:3x sent Toxic (L62) to the Bandits (L32-36)
# with Suicide (L60, 104-135 kills a minute there) with the goal "minimum of
# 100 kpm for each over the span of an hour"; every jump from a Bandit is a
# lost cast.
HARMLESS_LEVELS = 20
# Alex 2026-10-01 07:2x, after a +1 lay 11 s beside a Bandit boss until he
# picked it up by hand (loot_step deferred it "boss_nearby", and escapes kept
# pulling Toxic off its approach): "Highest priority is always picking up
# valuable loot over anything else. The only exception is if you are about to
# die." Under this share of max HP survival (escapes, boss clearance) comes
# first again.
LOOT_FIRST_HP = 0.5

@contextmanager
def logical_coordinates():
    setter = ctypes.windll.user32.SetThreadDpiAwarenessContext
    setter.argtypes, setter.restype = [ctypes.c_void_p], ctypes.c_void_p
    previous = setter(ctypes.c_void_p(-1))
    if not previous:
        raise ctypes.WinError()
    try:
        yield
    finally:
        setter(previous)


UNVERIFIED_HEAL = "Healing consumption unverified; no repeat input issued"
# Post-click bag reads that may race an inventory refresh before giving up.
HEAL_EVIDENCE_READS = 3


def potion_carried(trade, uid):
    """(type_id, carried count) of the potion about to be used, or None."""
    inventory = getattr(trade, "inventory", None)
    if inventory is None:
        return None
    try:
        bag = inventory.read()
    except (ValueError, OSError, CaptureUnavailable):
        return None
    kind = next((i.type_id for i in bag.items if i.uid == uid), None)
    return None if kind is None else (kind, bag.count(kind))


def settle_unverified_heal(trade, uid, carried, error):
    """Settle a combat heal that used a potion without HP rising.

    A hit landing with the potion leaves HP no higher, so the consumption
    check reads unverified, and the error stopped the whole farm runner with
    monsters around: Toxic (level 37, Bandits) healed at 43% on 2026-09-28
    00:44:35, the runner stopped at 00:44:38 and it died where it stood. The
    carried count settles it without repeating any input: one fewer potion is
    a used potion (receipt), an unchanged count is an unused one (reobserve).
    Anything else keeps the original error.
    """
    kind, before = carried
    for attempt in range(HEAL_EVIDENCE_READS):
        try:
            after = trade.inventory.read().count(kind)
            break
        except (ValueError, OSError, CaptureUnavailable):
            if attempt == HEAL_EVIDENCE_READS - 1:
                return None
            time.sleep(0.05)
    if after == before - 1:
        return {
            "consumed": True,
            "uid": uid,
            "type_id": kind,
            "remaining": after,
            "hp_unconfirmed": True,
        }
    if after == before:
        raise CaptureUnavailable(
            "Healing: potion not used; reobserving: " + str(error)
        ) from error
    return None


class NativeFarmSupervisor:
    def __init__(self, observer, control, recovery, notify):
        self.observer, self.control, self.recovery, self.notify = (
            observer,
            control,
            recovery,
            notify,
        )
        self.recovery.delegate_return = True
        self.last_state = None
        self.revision = control.snapshot()["revision"]
        from conquest.experience import ExperienceRate

        self.experience_rate = ExperienceRate()
        self.last_metrics = 0
        self.last_health_position = None
        self.defend_until = 0
        self.last_damage_at = -float("inf")
        self.escape_damage_consumed_at = -float("inf")
        self.escape_context = {}
        self.defending = False
        self.position = None
        self.map_id = 1002
        self.excluded_targets = {}
        self.last_target = None
        self.scene_monsters = ()
        self.chase_monsters = ()
        self.escape_monsters = ()
        self.targets_observation_available = False
        self.scene_timestamp = 0
        self.ground = None
        self.pending_loot = None
        # ((uid, object address), drop position, monotonic time) of the last
        # step or click toward a valuable; see valuable_chase_holds.
        self.valuable_chase = None
        self.loot_cooldowns = {}
        # Unverified clicks per valuable still on the ground (VALUABLE_CLICK_MISSES).
        self.loot_misses = {}
        self.loot_wait_until = 0
        self.pickups = 0
        self.last_loot_error = None
        self.discarder = None
        self.movement_obstructions = {}
        self.movement_run_until = 0
        self.recent_movement_progress = []
        self.patrol_chase = None
        self.recovery_death_seen = False

    def observe_inventory(self, inventory):
        """Record new valuable item identities independently of ground-read races."""
        if self.replan_after_manual(inventory):
            return  # Manual interval changes are not automated pickup receipts.
        from conquest.memory_ground import wanted_drop

        items = {i.uid: i for i in inventory.items}
        known = getattr(self, "inventory_seen", None)
        if known is None:
            self.inventory_seen = set(items)
            self.inventory_reported = set()
            return
        new = set(items) - known
        known.update(items)
        for uid in sorted(new):
            item = items[uid]
            if not wanted_drop(item):
                continue
            self.inventory_reported.add(uid)
            self.pickups += 1
            pending = self.pending_loot
            linked = (
                pending
                and pending[0].type_id == item.type_id
                and pending[0].plus == getattr(item, "plus", None)
            )
            self.notify(
                "memory_pickup_verified",
                {
                    "uid": pending[0].uid if linked else uid,
                    "inventory_uid": uid,
                    "type_id": item.type_id,
                    "plus": getattr(item, "plus", None),
                    "silver": False,
                    "increase": 1,
                    "total": self.pickups,
                    "position": pending[0].position if linked else None,
                    "map_id": self.map_id,
                    "timestamp": time.time(),
                    "source": "ground_pickup" if linked else "inventory_gain",
                    "timestamp_kind": "observed_at",
                },
            )

    def urgent_banking(self, inventory):
        from conquest.banking import urgent_valuables

        return bool(urgent_valuables(inventory.items))

    def replan_after_manual(self, inventory):
        from conquest.merchants.coordination import (
            manual_replan_journal,
            manual_session_blocked,
        )

        journal = manual_replan_journal()
        if journal is None or manual_session_blocked("Farmer"):
            return False
        # Inventory and map must come from this loop's new memory observation.
        if not 0 <= time.monotonic() - inventory.started_at <= 0.85:
            return False
        from conquest.merchants.manual_recovery import consume_farmer

        evidence = {
            "timestamp": time.time(),
            "map_id": self.map_id,
            "position": list(self.position) if self.position else None,
            "inventory": [
                {
                    "uid": i.uid,
                    "type_id": i.type_id,
                    "amount": i.amount,
                    "plus": getattr(i, "plus", None),
                }
                for i in inventory.items
            ],
            "urgent_banking": self.urgent_banking(inventory),
        }
        sessions = consume_farmer(journal, evidence)
        if not sessions:
            return False
        self.pending_loot = self.patrol_chase = None
        self.loot_wait_until = self.defend_until = self.movement_run_until = 0
        self.movement_obstructions.clear()
        self.excluded_targets.clear()
        self.inventory_seen = {i.uid for i in inventory.items}
        self.inventory_reported = set()
        self.finish_runback("manual_session_replan")
        self.notify(
            "manual_session_replan",
            {
                **evidence,
                "sessions": sessions,
                "activity": "Fresh memory replan after manual visitor session",
            },
        )
        return True

    def start_runback(self, destination):
        from conquest.runback_monitor import RunbackMonitor

        if getattr(self, "runback_watch", None):
            self.runback_watch.finish("replanned")
        self.runback_watch = RunbackMonitor(
            destination, self.map_id, "hunt", self.notify
        )

    def finish_runback(self, result):
        if getattr(self, "runback_watch", None):
            self.runback_watch.finish(result)
            self.runback_watch = None

    def reload_arrows(self, inventory, ammo_type):
        matches = [
            i for i in inventory.items if i.type_id == ammo_type and i.amount > 0
        ]
        if not matches:
            raise CaptureUnavailable("No reserve stack of the selected arrows")
        item = max(matches, key=lambda i: i.amount)

        def reload():
            # Inventory geometry is read in the embedded client's logical
            # coordinates; the combat thread otherwise uses physical pixels.
            with logical_coordinates():
                trade = self.observer.town_trade
                self.supply_panel_pending = True
                try:
                    return trade({"action": "equip-arrows", "uid": item.uid})
                except ValueError as error:
                    if str(error) in (
                        "Inventory opening unverified",
                        "Equipment changed before equip input",
                    ):
                        # Both guards fail before the equipment click. Never reuse
                        # the old slot: the next tick reads life, bag and reserve again.
                        raise CaptureUnavailable(
                            "Arrow reload: reobserve before equipment input: "
                            + str(error)
                        ) from error
                    raise  # An uncertain equipment receipt must not be replayed.
                finally:
                    # Reversible cleanup must not mask a receipt or the original
                    # uncertainty. The observation loop retries pending cleanup.
                    try:
                        trade({"action": "close", "window": "Inventory"})
                        self.supply_panel_pending = False
                    except (ValueError, OSError):
                        pass

        return self.dispatch(reload)

    def heal_potion(self, uid):
        def consume():
            from conquest.town_trade import TownObservationUnavailable

            with logical_coordinates():
                trade = self.observer.town_trade
                self.supply_panel_pending = True
                try:
                    carried = potion_carried(trade, uid)
                    try:
                        return trade({"action": "consume-healing", "uid": uid})
                    except TownObservationUnavailable as error:
                        raise CaptureUnavailable(
                            "Healing: reobserving before item use: " + str(error)
                        ) from error
                    except ValueError as error:
                        if str(error) == "Inventory opening unverified":
                            # The inventory-open guard fails before the potion
                            # click.  Drop this attempt and obtain a fresh life
                            # and bag observation; never infer consumption or
                            # replay a post-click uncertainty.
                            raise CaptureUnavailable(
                                "Healing: reobserving before item use: " + str(error)
                            ) from error
                        if str(error) == UNVERIFIED_HEAL and carried is not None:
                            receipt = settle_unverified_heal(trade, uid, carried, error)
                            if receipt is not None:
                                return receipt
                        raise
                finally:
                    # Cleanup is reversible and retried separately. Never mask
                    # a verified receipt or an uncertain consumption error.
                    try:
                        trade({"action": "close", "window": "Inventory"})
                        self.supply_panel_pending = False
                    except (ValueError, OSError):
                        pass

        return self.dispatch(consume)

    def emergency_return(self):
        """Read a carried TwinCityGate scroll: leave a fight potions cannot hold.

        The town trade's verified scroll use (outside town on Twin City only)
        proves the arrival in town; it never repeats an uncertain read.
        """

        def read_scroll():
            with logical_coordinates():
                trade = self.observer.town_trade
                self.supply_panel_pending = True
                try:
                    return trade({"action": "return-scroll"})
                finally:
                    try:
                        trade({"action": "close", "window": "Inventory"})
                        self.supply_panel_pending = False
                    except (ValueError, OSError):
                        pass

        return self.dispatch(read_scroll)

    def attack_strategy(self):
        from conquest.attack_strategy import AttackStrategy, equipment_context
        from conquest.equipment import read_equipment
        from conquest.combat_ranges import read_combat_ranges

        if not hasattr(self, "_attack_strategy"):
            self._attack_strategy = AttackStrategy(
                state_path(".runtime/attack-strategy.json"), self.notify
            )
            self._next_strategy_check = 0
        if time.monotonic() >= self._next_strategy_check:
            self._next_strategy_check = time.monotonic() + 5
            try:
                with self.observer.lock:
                    state = read_equipment(self.observer)
                    ranges = read_combat_ranges(self.observer)
                self._attack_strategy.set_context(
                    equipment_context(state, ranges["scatter"])
                )
            except (ValueError, OSError):
                pass  # A failed gear read must not reset an established decision.
        return self._attack_strategy

    def player_projection(self):
        """Pair fresh position and camera geometry from one verified life sample."""
        from conquest.scene_input import memory_player_anchor

        with self.observer.lock:
            life = self.read_life()
            if (
                life.dead_candidate
                or life.ghost_candidate
                or life.map_id != self.map_id
            ):
                raise CaptureUnavailable("Life or map changed before projection")
            try:
                anchor = memory_player_anchor(self.observer, life)
            except ValueError as error:
                raise CaptureUnavailable(str(error)) from error
            return tuple(life.position), anchor

    def player_anchor(self, position):
        from conquest.scene_input import memory_player_anchor

        with self.observer.lock:
            life = self.read_life()
            if tuple(life.position) != tuple(position):
                raise CaptureUnavailable("Player moved before projection")
            try:
                return memory_player_anchor(self.observer, life)
            except ValueError as error:
                raise CaptureUnavailable(str(error)) from error

    def xp_step(self, dispatch):
        from conquest.xp_skill import XpSkill

        if not hasattr(self, "_xp_skill"):
            self._xp_skill = XpSkill(self.observer, self.notify)
        with self.observer.lock:
            return self._xp_skill.step(dispatch)

    def flying(self, now=None):
        """Whether Fly is up in this loop's life read (status FLY_STATUS, the
        bit XpSkill verifies Fly by). An old read counts as not flying."""
        seen = getattr(self, "life_status", None)
        now = time.monotonic() if now is None else now
        return bool(seen) and now - seen[0] <= FLY_STATUS_SECONDS and bool(seen[1] & FLY_STATUS)

    def scatter_selection_step(self, dispatch):
        from conquest.scatter_selection import ScatterSelection

        if not hasattr(self, "_scatter_selection"):
            self._scatter_selection = ScatterSelection(self.observer, self.notify)
        with self.observer.lock:
            try:
                return self._scatter_selection.step(dispatch)
            except ValueError as error:
                raise CaptureUnavailable(str(error)) from error

    def read_life(self):
        from conquest.reconnect import login_screen

        hwnd = getattr(self.observer.operations.target, "hwnd", None)
        if hwnd is not None and login_screen(hwnd):
            raise CaptureUnavailable("Disconnected; waiting for automatic reconnection")
        try:
            attempts = getattr(
                getattr(self, "combat_speed", None), "torn_life_attempts", 1
            )
            for attempt in range(attempts):
                try:
                    return self.observer.read_life()
                except ValueError as error:
                    if (
                        str(error) != "Life state changed during observation"
                        or attempt == attempts - 1
                    ):
                        raise
                    time.sleep(0.005)
        except ValueError as error:
            if str(error) in (
                "Life state changed during observation",
                "Player pointer changed during life observation",
                "Life observation expired",
                "Health fields or pointer topology changed during sampling",
            ):
                raise CaptureUnavailable(str(error)) from error
            raise

    def experience_layout(self):
        """This client build's player layout for the XP fields, read once.

        Without it the XP reader used the retired build's offsets, which read
        level 0 on 1078 and never produced a sample (Back2Classic, 09-27).
        """
        if not hasattr(self, "_experience_layout"):
            from conquest.memory_build_layout import actual_player_layout

            try:
                self._experience_layout = actual_player_layout(self.observer.adapter)
            except (AttributeError, KeyError, ValueError, OSError):
                self._experience_layout = None
        return self._experience_layout

    def note_health(self, life):
        """Record a hit worth a jump, and damage taken while standing still."""
        previous = self.last_health_position
        position = tuple(life.position)
        if not life.dead_candidate and life.max_hp:
            self.health_share = life.current_hp / life.max_hp
        if previous and not life.dead_candidate and life.current_hp < previous[0]:
            from conquest.farm_mode import jump_worthy_hit

            if jump_worthy_hit(
                previous[0] - life.current_hp,
                life.current_hp,
                life.max_hp,
                ESCAPE_DAMAGE_SHARE,
            ):
                self.last_damage_at = time.monotonic()
            if previous[1] == position:
                self.defend_until = time.monotonic() + 8
        self.last_health_position = (life.current_hp, position)

    def observe(self):
        from conquest.mouse_priority import require_idle
        from conquest.merchants.coordination import manual_session_blocked
        from conquest.merchants.coordination import observe_manual_farmer

        # Per-part timing into the loop's native_loop_timing (observe_*): the
        # whole observe averaged ~190 ms in the app against ~25 ms for the same
        # reads from a separate process (2026-09-28), so the combat loop only
        # reacted about twice a second.
        timing = getattr(self, "loop_timing", None)
        mark = [time.perf_counter()]

        def lap(name):
            if timing is not None:
                now = time.perf_counter()
                timing.sample("observe_" + name, now - mark[0])
                mark[0] = now

        manual = bool(observe_manual_farmer(self.observer)) or manual_session_blocked(
            "Farmer"
        )
        if not manual:
            require_idle()
        lap("manual")
        with logical_coordinates(), self.observer.lock:
            lap("lock_wait")
            life = self.read_life()
            lap("life")
            if hasattr(life, "position"):
                self.position = tuple(life.position)
                self.map_id = life.map_id
                self.note_health(life)
                self.life_status = (time.monotonic(), getattr(life, "status", 0) or 0)
            if manual or manual_session_blocked("Farmer"):
                intent = self.control.snapshot()
                self.pending_loot = None
                return {
                    "waiting": True,
                    "manual_session": True,
                    "stop": intent["revision"] != self.revision,
                    "health_ratio": life.current_hp / life.max_hp,
                }
            self.defending = (
                not life.dead_candidate and time.monotonic() < self.defend_until
            )
            if time.monotonic() - self.last_metrics >= 2:
                from conquest.experience import read_experience

                try:
                    xp = read_experience(
                        self.observer.adapter,
                        life.object_address,
                        self.experience_layout(),
                    )
                    self.notify(
                        "experience_sample",
                        {
                            "level": xp.level,
                            "experience_candidate": xp.current,
                            "experience_required": xp.required,
                            "percent_candidate": xp.percent,
                            "xp_per_hour_candidate": self.experience_rate.add(xp),
                            "validated": False,
                        },
                    )
                except (AttributeError, ValueError, OSError):
                    pass  # XP telemetry must not delay combat or death recovery.
                self.last_metrics = time.monotonic()
                lap("experience")
            window = self.observer.operations.target.snapshot()
            lap("window")
            intent = self.control.snapshot()
            if intent["revision"] != self.revision:
                return {
                    "stop": True,
                    "waiting": True,
                    "health_ratio": life.current_hp / life.max_hp,
                }
            focused = (
                window["foreground"] == window["root_hwnd"] and not window["minimized"]
            )
            recovery_events = []
            if (
                life.dead_candidate or life.ghost_candidate
            ) and not self.recovery_death_seen:
                self.recovery_death_seen = True
                recovery_events.append(
                    {
                        "event": "death_detected",
                        "position": list(life.position),
                        "map_id": life.map_id,
                        "health_ratio": life.current_hp / life.max_hp,
                        "source": "native_memory",
                    }
                )
            status = self.recovery.step(
                {**asdict(life), "dead_candidate": life.dead_candidate}, focused
            )
            lap("recovery")
            phase = (getattr(self.recovery, "episode", None) or {}).get("phase")
            if (
                self.recovery_death_seen
                and not life.dead_candidate
                and not life.ghost_candidate
                and phase
                in ("returning_with_farmer", "returning_after_revive", "completed")
            ):
                # RouteRecovery has confirmed revival across fresh life samples.
                self.recovery_death_seen = False
                recovery_events.append(
                    {
                        "event": "revival_verified",
                        "position": list(life.position),
                        "map_id": life.map_id,
                        "health_ratio": life.current_hp / life.max_hp,
                        "source": "native_memory",
                    }
                )
            waiting = (
                not intent["enabled"]
                or not focused
                or life.dead_candidate
                or bool(status)
            )
            if not waiting and getattr(self, "supply_panel_pending", False):
                try:
                    self.dispatch(
                        lambda: self.observer.town_trade(
                            {"action": "close", "window": "Inventory"}
                        )
                    )
                    self.supply_panel_pending = False
                except (ValueError, OSError) as error:
                    raise CaptureUnavailable(
                        "Waiting to close healing inventory: " + str(error)
                    ) from error
            if not waiting and time.monotonic() >= getattr(self, "next_panel_check", 0):
                self.next_panel_check = time.monotonic() + 1
                from conquest.game_panels import close_one

                panel = self.dispatch(lambda: close_one(self.observer.town_trade))
                if panel:
                    self.notify(
                        "shop_panel_closed",
                        {
                            "panel": panel,
                            "activity": "Closed " + panel + " panel; resuming combat",
                        },
                    )
                    return {
                        "waiting": True,
                        "health_ratio": life.current_hp / life.max_hp,
                    }
            lap("panels")
            if getattr(self, "runback_watch", None):
                self.runback_watch.observe(
                    {**asdict(life), "dead_candidate": life.dead_candidate},
                    paused=waiting,
                )
            lap("runback")
            if waiting:
                self.pending_loot = None
                self.loot_wait_until = 0
            note = (
                status["note"]
                if status
                else "Waiting for game focus"
                if not focused
                else "Farming is off"
                if not intent["enabled"]
                else "Hunting"
            )
            state = status["state"] if status else "paused" if waiting else "farming"
            self.control.publish(intent["revision"], state, note)
            if (state, note) != self.last_state:
                self.notify("farm_state", {"state": state, "note": note})
                self.last_state = (state, note)
            result = {"waiting": waiting, "health_ratio": life.current_hp / life.max_hp}
            if recovery_events:
                result["recovery_events"] = recovery_events
            if (getattr(self.recovery, "episode", None) or {}).get(
                "phase"
            ) == "returning_with_farmer":
                result["returning_after_revive"] = True
            if self.defending:
                result["defending"] = True
            return result

    def match_targets(self, targets, *, refresh=False):
        with self.observer.lock:
            try:
                targeted = (
                    refresh
                    and len(targets) == 1
                    and getattr(
                        getattr(self, "combat_speed", None),
                        "selected_target_refresh",
                        False,
                    )
                    and targets[0].entity_id is not None
                    and targets[0].object_address is not None
                )
                options = (
                    {"packed": True}
                    if getattr(
                        getattr(self, "combat_speed", None),
                        "packed_monster_records",
                        False,
                    )
                    else {}
                )
                monsters = (
                    self.observer.entities.read(
                        **options,
                        selected=(targets[0].entity_id, targets[0].object_address),
                    ).monsters
                    if targeted
                    else self.observer.entities.read(**options).monsters
                )
            except ValueError:
                return []
            intent = self.control.snapshot()
            matched = []
            for target in targets:
                from conquest.routes import boss_name

                candidates = [
                    m
                    for m in monsters
                    if not boss_name(m.name)
                    and m.name == target.name
                    and (
                        target.entity_id is None
                        or (
                            m.entity_id == target.entity_id
                            and m.object_address == target.object_address
                        )
                    )
                    and (
                        m.entity_id in intent["target_ids"]
                        or m.type_id in intent["target_type_ids"]
                        or (
                            self.defending
                            and self.position is not None
                            and max(
                                abs(a - b) for a, b in zip(m.position, self.position)
                            )
                            <= 3
                        )
                    )
                    and (
                        (
                            refresh
                            and target.entity_id is not None
                            and target.object_address is not None
                            and target.world_position is not None
                            and max(
                                abs(a - b)
                                for a, b in zip(m.position, target.world_position)
                            )
                            <= MONSTER_DRIFT_TILES
                        )
                        or (
                            not refresh
                            and abs(m.draw_position[0] - target.x) <= 30
                            and abs(m.draw_position[1] - target.y) <= 35
                        )
                    )
                ]
                if len(candidates) == 1:
                    from conquest.monster_health import read_monster_health

                    try:
                        hp = read_monster_health(
                            self.observer.adapter,
                            self.observer.entities.layout,
                            candidates[0],
                        )
                        if hp <= 0:
                            continue
                    except (ValueError, OSError):
                        continue
                    monster = candidates[0]
                    matched.append(
                        replace(
                            target,
                            entity_id=monster.entity_id,
                            object_address=monster.object_address,
                            **(
                                {
                                    "x": monster.draw_position[0],
                                    "y": monster.draw_position[1],
                                    "world_position": tuple(monster.position),
                                    "current_hp": hp,
                                }
                                if refresh
                                else {}
                            ),
                        )
                    )
            return matched

    def remember_bosses(self, monsters):
        """Keep each boss's last seen tile, so walks plan round bosses out of view.

        2026-09-30 03:36: past King 404775, travel_path from (578, 336) to
        thunderape-nw ran straight west through a GiantApeAide at (480, 324),
        a Msgr at (482, 338) and Kings at (481, 309) and (454, 340); the walk
        round them north of y 300 was just as short (249 tiles). The walk only
        zoned bosses in view, met them 12-15 tiles off, took Msgr hits and went
        home heavy-damaged. An entry lapses after BOSS_MEMORY_SECONDS, or once
        its tile is within BOSS_SIGHT_TILES of the farmer without the boss.
        """
        from conquest.routes import boss_name

        now = time.monotonic()
        memory = getattr(self, "boss_memory", None)
        if memory is None:
            memory = self.boss_memory = {}
        seen = set()
        for m in monsters:
            if not boss_name(getattr(m, "name", "") or "") or not getattr(m, "position", None):
                continue
            key = (self.map_id, m.entity_id)
            if getattr(m, "alive", None) is False or getattr(m, "current_hp", None) == 0:
                memory.pop(key, None)
                continue
            memory[key] = (m.name, tuple(m.position), now)
            seen.add(key)
        position = getattr(self, "position", None)
        for key, (_, spot, at) in list(memory.items()):
            if key in seen or key[0] != self.map_id:
                continue
            if now - at > BOSS_MEMORY_SECONDS or (
                position
                and max(abs(a - b) for a, b in zip(spot, position)) <= BOSS_SIGHT_TILES
            ):
                del memory[key]

    def remembered_bosses(self, visible):
        """Bosses on this map seen in the last BOSS_MEMORY_SECONDS, not in
        ``visible`` and within BOSS_MEMORY_REACH of the farmer (their last
        tile; for boss_zone). Farther ones join as the walk nears them."""
        from types import SimpleNamespace

        now = time.monotonic()
        shown = {getattr(m, "entity_id", None) for m in visible}
        position = getattr(self, "position", None)
        return [
            SimpleNamespace(entity_id=key[1], name=name, position=spot)
            for key, (name, spot, at) in (getattr(self, "boss_memory", None) or {}).items()
            if key[0] == self.map_id
            and key[1] not in shown
            and now - at <= BOSS_MEMORY_SECONDS
            and (
                not position
                or max(abs(a - b) for a, b in zip(spot, position)) <= BOSS_MEMORY_REACH
            )
        ]

    def memory_targets(self, size=(1036, 793)):
        """Selected scene IDs and draw coordinates; no pixel observations.

        Scene presence does not prove life. Attack progress and the player's
        kill counter are checked separately; no per-ID kill claim is inferred.
        """
        from conquest.vision import Target

        with self.observer.lock:
            self.chase_monsters = ()
            self.escape_monsters = ()
            self.scatter_scene_targets = ()
            self.targets_observation_available = False
            try:
                options = (
                    {"packed": True}
                    if getattr(
                        getattr(self, "combat_speed", None),
                        "packed_monster_records",
                        False,
                    )
                    else {}
                )
                monsters = self.observer.entities.read(**options).monsters
            except ValueError:
                return []
            self.targets_observation_available = True
            self.scene_monsters = monsters
            self.scene_timestamp = time.monotonic()
            self.remember_bosses(monsters)
            self.note_target_position(monsters)
            intent = self.control.snapshot()
            accepted = []
            scatter_scene = []
            chase = []
            escape = []
            from conquest.routes import BOSS_CLEARANCE, BOSS_ROOM, boss_clearance, boss_name

            # Bosses stay visible to the escape out to their clearance plus
            # the landing room: with only the 12-tile threat radius a RatKing
            # (15-tile clearance) was ignored from 13-15 tiles. And a jump
            # lands up to LANDING_REACH away, so a boss that far beyond it
            # still counts against every landing.
            king_clearance = getattr(self, "king_clearance", BOSS_CLEARANCE)
            elite_clearance = getattr(self, "elite_clearance", BOSS_CLEARANCE)
            for monster in monsters:
                selected = (
                    monster.entity_id in intent["target_ids"]
                    or monster.type_id in intent["target_type_ids"]
                )
                watched_boss = (
                    self.position is not None
                    and boss_name(monster.name)
                    and max(abs(a - b) for a, b in zip(monster.position, self.position))
                    <= boss_clearance(monster.name, king_clearance, elite_clearance)
                    + BOSS_ROOM
                    - BOSS_CLEARANCE
                    + LANDING_REACH
                )
                close = (
                    self.defending
                    and self.position is not None
                    and max(abs(a - b) for a, b in zip(monster.position, self.position))
                    <= 3
                )
                adjacent = (
                    self.position is not None
                    and max(abs(a - b) for a, b in zip(monster.position, self.position))
                    <= 1
                )
                threat = (
                    self.position is not None
                    and max(abs(a - b) for a, b in zip(monster.position, self.position))
                    <= 12
                )
                if (
                    not (selected or close or adjacent or threat or watched_boss)
                    or monster.alive is False
                    or monster.current_hp == 0
                ):
                    continue
                if watched_boss and not (selected or close or adjacent or threat):
                    # Keeping clear of a distant boss needs only its position;
                    # its HP read must not gate this scene's targets.
                    escape.append(monster)
                    continue
                key = (monster.entity_id, monster.object_address)
                from conquest.monster_health import read_monster_health

                try:
                    monster_hp = read_monster_health(
                        self.observer.adapter, self.observer.entities.layout, monster
                    )
                    if monster_hp <= 0:
                        continue
                except (ValueError, OSError):
                    self.targets_observation_available = False
                    from conquest.routes import boss_name

                    if boss_name(monster.name):
                        # Keeping clear of a boss needs only its position. A
                        # roaming WingedSnakeKing failed this read whenever it
                        # moved, so boss clearance never saw it (Suicide died
                        # beside it at 07:19 and 07:22 on 2026-09-28).
                        escape.append(monster)
                    continue
                # Retain the qualified HP used for this target decision. Region
                # occupancy must not see the original scene record's unknown
                # HP and mistake offscreen but living targets for an empty area.
                monster = replace(monster, current_hp=monster_hp)
                escape.append(monster)
                from conquest.routes import boss_name

                if (
                    boss_name(monster.name)
                    or not (selected or close)
                    or self.excluded_targets.get(key, 0) > time.monotonic()
                ):
                    continue
                chase.append(monster)
                x, y = map(int, monster.draw_position)
                scatter_scene.append(
                    Target(
                        monster.name,
                        x,
                        y,
                        1.0,
                        monster.entity_id,
                        monster.object_address,
                        tuple(monster.position),
                        monster_hp,
                    )
                )
                if not (80 < x < size[0] - 80 and 140 < y < size[1] - 126):
                    continue
                if not clear_scene((x, y), size):
                    continue
                accepted.append(
                    Target(
                        monster.name,
                        x,
                        y,
                        1.0,
                        monster.entity_id,
                        monster.object_address,
                        tuple(monster.position),
                        monster_hp,
                    )
                )
            self.chase_monsters = tuple(chase)
            self.escape_monsters = tuple(escape)
            self.scatter_scene_targets = tuple(scatter_scene)
            return accepted

    def ground_items(self):
        from conquest.memory_ground import MemoryGroundReader

        if self.ground is None:
            self.ground = MemoryGroundReader(self.observer.entities)
        return self.ground.read()

    @property
    def discard_panel_pending(self):
        return bool(
            self.discarder and getattr(self.discarder, "cleanup_pending", False)
        )

    def discard_step(self, inventory):
        from conquest.discard_loot import DiscardLoot, discard_candidate

        if self.discard_panel_pending:

            def close():
                with logical_coordinates():
                    try:
                        self.discarder.close_inventory()
                    except (ValueError, OSError) as error:
                        # Keep the panel cleanup pending across fresh memory,
                        # focus and revival checks; never terminate the route.
                        raise CaptureUnavailable(
                            "Waiting to close Inventory: " + str(error)
                        ) from error

            self.dispatch(close)
            return True
        # Finish confirming the pickup before removing that inventory UID.
        if self.pending_loot or self.defending:
            return False
        # Optional housekeeping must not displace a live combat opportunity.
        # Unknown or stale scene memory is not evidence that opening the bag
        # is safe. Closing an already-open bag above remains unconditional.
        now = time.monotonic()
        if (
            not getattr(self, "targets_observation_available", False)
            or not 0 <= now - getattr(self, "scene_timestamp", 0) <= 0.5
            or getattr(self, "position", None) is None
        ):
            return False
        if any(
            monster.alive is not False
            and monster.current_hp != 0
            and max(abs(a - b) for a, b in zip(monster.position, self.position)) <= 16
            for monster in self.scene_monsters
        ):
            return False
        if not any(discard_candidate(i) for i in inventory.items):
            return False
        if self.discarder is None:
            self.discarder = DiscardLoot(self.observer.town_trade)
        if time.monotonic() < getattr(self.discarder, "next_attempt_at", 0):
            return False
        candidate = next(
            (
                i
                for i in inventory.items
                if discard_candidate(i) and i.uid not in self.discarder.attempted
            ),
            None,
        )
        if candidate is None:
            return False
        self.notify(
            "discarding_loot",
            {
                "uid": candidate.uid,
                "type_id": candidate.type_id,
                "activity": "Dropping unwanted +0 loot",
            },
        )

        def discard():
            # Direct input runs on the combat thread rather than the HTTP
            # bridge; use the same logical coordinate context as the GUI reader.
            with logical_coordinates():
                return self.discarder.discard(candidate.uid)

        result = self.dispatch(discard)
        event = {"verified": "loot_discarded", "deferred": "loot_discard_deferred"}.get(
            result["state"], "loot_discard_unverified"
        )
        self.notify(event, result)
        return True

    def ownership_guard(self):
        from conquest.loot_ownership import LootOwnership

        if not hasattr(self, "_loot_ownership"):
            self._loot_ownership = LootOwnership(self.observer.adapter)
        return self._loot_ownership

    def combat_loot_step(self, inventory, position, dispatch):
        # Valuable drops must not wait for a constantly respawning scene to
        # empty. The memory allowlist excludes currency; care runs first.
        return self.loot_step(inventory, position, dispatch, max_distance=12)

    def audit_loot(self, drops):
        """Record each notable gear drop seen once, with the pickup verdict.

        2,469 pickups to 2026-09-28 were silver and eight Meteors, not one
        piece of gear, and nothing recorded what lay on the ground and why it
        stayed: a missed +1 or Super item could not be told from no drop.
        Unique and higher, any plus, and an unreadable plus are notable;
        cross-check loot-audit.jsonl against pickups.jsonl by uid.
        """
        from conquest.memory_ground import wanted_drop
        from conquest.valuables import loot_gear

        seen = getattr(self, "loot_audited", None)
        if seen is None or len(seen) > LOOT_AUDIT_MEMORY:
            seen = self.loot_audited = set()
        rows = []
        for drop in drops:
            kind = drop.type_id
            if not loot_gear(kind):
                continue
            key = (drop.uid, drop.object_address)
            if key in seen or not (
                kind % 10 >= 7 or drop.plus is None or drop.plus >= 1
            ):
                continue
            seen.add(key)
            rows.append(
                {
                    "time": time.time(),
                    "uid": drop.uid,
                    "type_id": kind,
                    "plus": drop.plus,
                    "position": list(drop.position),
                    "map_id": getattr(self, "map_id", None),
                    "wanted": wanted_drop(drop),
                }
            )
        if rows:
            import json
            from pathlib import Path

            path = Path(state_path("reports/desktop-farming/loot-audit.jsonl"))
            try:
                with path.open("a", encoding="utf-8") as handle:
                    handle.writelines(json.dumps(row) + "\n" for row in rows)
            except OSError:
                pass  # an audit line is never worth a combat interruption

    def loot_step(
        self,
        inventory,
        position,
        dispatch,
        *,
        money_only=False,
        max_distance=12,
        valuables_only=False,
    ):
        """One loot turn. ``valuables_only`` (an overdue Scatter goes first)
        skips silver and the wait for kill drops but never a valuable: a Super
        MeteorEarring 23 tiles off was lost while every loot turn was skipped
        (Toxic 2026-09-28 14:51)."""
        from conquest.memory_ground import pickup_delta, wanted_drop

        now = time.monotonic()
        try:
            with self.observer.lock:
                ownership = self.ownership_guard()
                if (
                    self.pending_loot
                    and ownership is not None
                    and getattr(self, "pending_loot_feedback", None) is not None
                ):
                    from conquest.loot_ownership import ownership_rejected

                    if ownership_rejected(
                        self.pending_loot_feedback, ownership.snapshot()
                    ):
                        drop, _, _ = self.pending_loot
                        ownership.reject(drop, self.map_id)
                        self.end_valuable_chase(drop)
                        self.pending_loot = None
                        self.pending_loot_feedback = None
                        self.notify(
                            "memory_pickup_owned",
                            {
                                "uid": drop.uid,
                                "type_id": drop.type_id,
                                "position": drop.position,
                                "timestamp": time.time(),
                                "activity": "Skipping another player's loot",
                            },
                        )
                        return False
                drops = self.ground_items()
            chase = getattr(self, "valuable_chase", None)
            if chase is not None and not any(
                (d.uid, d.object_address) == chase[0] for d in drops
            ):
                self.valuable_chase = None  # picked, taken by someone, or gone
            self.audit_loot(drops)
        except (ValueError, OSError) as error:
            if str(error) != self.last_loot_error:
                self.notify("memory_loot_retry", {"detail": str(error)})
                self.last_loot_error = str(error)
            if self.pending_loot and now - self.pending_loot[2] >= 2:
                drop, _, _ = self.pending_loot
                delay = SILVER_RETRY_SECONDS if drop.silver else 1
                self.loot_cooldowns[(drop.uid, drop.object_address)] = now + delay
                if not drop.silver:
                    self.loot_wait_until = max(self.loot_wait_until, now + delay)
                    self.close_loot_retry = (drop.uid, drop.object_address)
                self.pending_loot = None
            if valuables_only:
                return bool(self.pending_loot) and not self.pending_loot[0].silver
            return bool(self.pending_loot) or now < self.loot_wait_until
        if self.last_loot_error is not None:
            self.notify("memory_loot_ready", {})
        self.last_loot_error = None
        if self.pending_loot:
            drop, before, issued = self.pending_loot
            exists = drop in drops
            delta = pickup_delta(drop, before, inventory)
            if not exists and delta > 0:
                previous = {i.uid for i in before.items}
                gained = [
                    i
                    for i in inventory.items
                    if i.uid not in previous and i.type_id == drop.type_id
                ]
                already_reported = gained and all(
                    i.uid in getattr(self, "inventory_reported", set()) for i in gained
                )
                if not already_reported:
                    self.pickups += 1
                    fields = {
                        "uid": drop.uid,
                        "type_id": drop.type_id,
                        "silver": drop.silver,
                        "plus": drop.plus,
                        "position": drop.position,
                        "map_id": self.map_id,
                        "increase": delta,
                        "total": self.pickups,
                        "timestamp": time.time(),
                    }
                    if len(gained) == 1:
                        fields["inventory_uid"] = gained[0].uid
                    self.notify("memory_pickup_verified", fields)
                self.pending_loot = None
                self.end_valuable_chase(drop)
                if money_only:
                    return False
            elif now - issued < (1.5 if drop.silver else 3.0):
                if not (valuables_only and drop.silver):
                    return True
            else:
                self.notify(
                    "memory_pickup_unverified",
                    {"uid": drop.uid, "type_id": drop.type_id},
                )
                key = (drop.uid, drop.object_address)
                delay = SILVER_RETRY_SECONDS if drop.silver else 1
                if not drop.silver:
                    self.close_loot_retry = key
                    misses = getattr(self, "loot_misses", {})
                    misses[key] = misses.get(key, 0) + 1
                    self.loot_misses = misses
                    if misses[key] >= VALUABLE_CLICK_MISSES:
                        # Not ours to pick up for now (another player's, or
                        # always covered): combat gets its turns meanwhile.
                        delay = VALUABLE_MISS_COOLDOWN
                        del misses[key]
                        self.end_valuable_chase(drop)
                    else:
                        self.loot_wait_until = max(self.loot_wait_until, now + delay)
                        if exists:
                            # The retry from beside it keeps the chase, so
                            # combat does not walk away during the cooldown.
                            self.chase_valuable(drop)
                self.loot_cooldowns[key] = now + delay
                self.pending_loot = None
        self.loot_cooldowns = {k: v for k, v in self.loot_cooldowns.items() if v > now}
        present = {(d.uid, d.object_address) for d in drops}
        self.loot_misses = {
            k: v for k, v in getattr(self, "loot_misses", {}).items() if k in present
        }
        candidates = []
        approaches = []
        deferred = []
        anchor = None
        from conquest.discard_loot import ignored_drop, JOURNAL
        from conquest.discord_notify import read_json
        from conquest.routes import BOSS_CLEARANCE, near_boss

        discarder = getattr(self, "discarder", None)
        ignored = discarder.records if discarder is not None else read_json(JOURNAL, [])
        for drop in drops:
            if (
                (money_only and not drop.silver)
                or (valuables_only and drop.silver)
                or not wanted_drop(drop)
            ):
                continue
            if drop.silver and not self.own_kill_drop(drop):
                continue  # another player's (or an old) drop: not worth the walk

            def defer(reason):
                deferred.append(
                    {
                        "uid": drop.uid,
                        "type_id": drop.type_id,
                        "plus": drop.plus,
                        "position": drop.position,
                        "action": "deferred",
                        "reason": reason,
                    }
                )

            if ownership is not None and ownership.blocked(drop, self.map_id):
                defer("ownership_rejected")
                continue
            if ignored_drop(drop, getattr(self, "map_id", 1002), ignored):
                defer("previously_discarded")
                continue
            if (drop.uid, drop.object_address) in self.loot_cooldowns:
                defer("pickup_cooldown")
                continue
            if not drop.silver and len(inventory.items) >= inventory.capacity:
                defer("inventory_full")
                continue
            dx, dy = drop.position[0] - position[0], drop.position[1] - position[1]
            distance = max(abs(dx), abs(dy))
            if distance > 40:
                defer("outside_40_tile_search")
                continue
            if (drop.silver or not self.loot_first()) and near_boss(
                drop.position,
                getattr(self, "escape_monsters", ()),
                king_clearance=getattr(self, "king_clearance", BOSS_CLEARANCE),
                elite_clearance=getattr(self, "elite_clearance", BOSS_CLEARANCE),
            ):
                # Both RatKings parked just outside the Ratling boundary
                # (2026-09-28): no silver is worth walking into a boss's
                # reach, and no valuable either once HP is under LOOT_FIRST_HP.
                defer("boss_nearby")
                continue
            viewport = size_for(self.observer)
            if anchor is None:
                anchor = self.player_anchor(position)
            point = (anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16)
            rank = (
                drop.type_id not in SPECIAL_LOOT_TYPES,
                not bool(drop.plus),
                dx * dx + dy * dy,
            )
            retry_close = getattr(self, "close_loot_retry", None) == (
                drop.uid,
                drop.object_address,
            )
            reach = max_distance if drop.silver else min(max_distance, VALUABLE_CLICK_TILES)
            if (
                distance > reach
                or retry_close
                and distance > 1
                # Under the farmer the click hits the farmer, not the item.
                or distance == 0
                and not drop.silver
                or not clear_scene(point, viewport)
            ):
                approaches.append((rank, drop))
                continue
            candidates.append((rank, drop, point))
        if (
            candidates
            or approaches
            or deferred
            or getattr(self, "last_valuable_summary", None)
        ):
            summary = [
                {
                    "uid": drop.uid,
                    "type_id": drop.type_id,
                    "plus": drop.plus,
                    "position": drop.position,
                    "action": "approach",
                }
                for _, drop in approaches
            ]
            summary += [
                {
                    "uid": drop.uid,
                    "type_id": drop.type_id,
                    "plus": drop.plus,
                    "position": drop.position,
                    "action": "pickup",
                }
                for _, drop, _ in candidates
            ]
            summary += deferred
            if summary != getattr(self, "last_valuable_summary", None):
                self.last_valuable_summary = summary
                self.notify(
                    "memory_loot_observed",
                    {
                        "valuable_drops": summary,
                        "timestamp": time.time(),
                        "player_position": position,
                        "map_id": self.map_id,
                    },
                )
        if approaches and (
            not candidates
            or min(row[0] for row in approaches) < min(row[0] for row in candidates)
        ):
            for _, drop in sorted(approaches, key=lambda row: row[0]):
                if self.approach_loot(drop, position, dispatch):
                    return True
        if candidates:
            _, drop, point = min(candidates, key=lambda row: row[0])
            try:
                with self.observer.lock:
                    baseline = ownership.snapshot() if ownership is not None else None
            except (ValueError, OSError) as error:
                self.loot_cooldowns[(drop.uid, drop.object_address)] = (
                    time.monotonic() + 2
                )
                self.notify(
                    "memory_loot_retry",
                    {"detail": "System pickup feedback unavailable: " + str(error)},
                )
                return False
            try:
                dispatch(point, drop=drop)
            except CaptureUnavailable as error:
                if str(error) not in (
                    "Ground item changed before pickup",
                    "Ground scene changed during sampling",
                ):
                    raise
                # No click was sent: give combat a turn instead of repeatedly
                # selecting the same changing ground record on every frame.
                self.loot_cooldowns[(drop.uid, drop.object_address)] = (
                    time.monotonic() + 1
                )
                self.notify(
                    "memory_pickup_deferred", {"uid": drop.uid, "detail": str(error)}
                )
                return False
            self.pending_loot = (drop, inventory, time.monotonic())
            self.pending_loot_feedback = baseline
            if not drop.silver:
                self.chase_valuable(drop)
            self.notify(
                "memory_pickup_attempt",
                {
                    "uid": drop.uid,
                    "type_id": drop.type_id,
                    "position": drop.position,
                    "silver": drop.silver,
                    "point": point,
                },
            )
            return True
        # A valuable chase keeps the turn through a moving farmer or a changing
        # ground record (never past VALUABLE_CHASE_SECONDS), so combat does not
        # jump back into the box between its steps.
        chasing = not money_only and self.valuable_chase_holds(position)
        if valuables_only:
            return chasing  # no wait for kill drops while a Scatter is due
        return chasing or now < self.loot_wait_until

    def loot_first(self):
        """Whether a valuable outranks every escape and boss clearance now:
        always, unless HP is under LOOT_FIRST_HP (Alex: "The only exception is
        if you are about to die")."""
        return getattr(self, "health_share", 1.0) >= LOOT_FIRST_HP

    def valuable_pending(self, position):
        """A valuable pickup is under way: its click awaits the receipt, a
        chase (an approach step, or a retry beside it) still holds, or its
        approach waits for a moving farmer to settle (loot_settling)."""
        pending = getattr(self, "pending_loot", None)
        return (
            bool(pending and not pending[0].silver)
            or self.valuable_chase_holds(position)
            or self.loot_settling()
        )

    def chase_valuable(self, drop):
        self.valuable_chase = (
            (drop.uid, drop.object_address),
            tuple(drop.position),
            time.monotonic(),
        )

    def end_valuable_chase(self, drop):
        chase = getattr(self, "valuable_chase", None)
        if chase is not None and chase[0] == (drop.uid, drop.object_address):
            self.valuable_chase = None

    def valuable_chase_holds(self, position):
        """Whether a walk toward a valuable within VALUABLE_RADIUS is under
        way, so the trial lets it leave the hunting boundary instead of
        walking straight back (Alex 2026-09-29: "There should be a 25 tile
        radius for valuables"). It ends once the drop leaves the ground, its
        pickup is refused, no way there is left, or no step or click came for
        VALUABLE_CHASE_SECONDS."""
        chase = getattr(self, "valuable_chase", None)
        if chase is None:
            return False
        _, target, stamp = chase
        return (
            time.monotonic() - stamp <= VALUABLE_CHASE_SECONDS
            and max(abs(target[0] - position[0]), abs(target[1] - position[1]))
            <= VALUABLE_RADIUS
        )

    def approach_loot(self, drop, position, dispatch):
        """Reposition toward a freshly observed valuable instead of skipping it."""
        now = time.monotonic()
        if now < getattr(self, "loot_approach_ready", 0):
            return True

        def deferred(reason, *, lasting=True):
            # A lasting reason (no way there) ends a valuable chase; a moving
            # farmer or a changing ground record only waits for the next turn.
            if lasting:
                self.end_valuable_chase(drop)
            signature = (drop, tuple(position), reason)
            if signature != getattr(self, "last_loot_approach_deferred", None):
                self.last_loot_approach_deferred = signature
                self.notify(
                    "memory_pickup_deferred",
                    {
                        "uid": drop.uid,
                        "type_id": drop.type_id,
                        "position": drop.position,
                        "player_position": position,
                        "detail": reason,
                    },
                )
            return False

        terrain = getattr(self.recovery, "terrain", None)
        if terrain is None or not hasattr(terrain, "path"):
            return deferred("Loot terrain unavailable")
        from conquest.navigation import native_waypoint

        boundary = getattr(
            self, "loot_boundary", (0, 0, terrain.width - 1, terrain.height - 1)
        )
        if not drop.silver:
            # A valuable may lie a little past the hunt's edge: 8 Uniques
            # dropped at x 363-384, beyond the WingedSnake boundary's 352, and
            # were all deferred here (Toxic 2026-09-28).
            slack = VALUABLE_BOUNDARY_SLACK
            boundary = (
                max(0, boundary[0] - slack),
                max(0, boundary[1] - slack),
                min(terrain.width - 1, boundary[2] + slack),
                min(terrain.height - 1, boundary[3] + slack),
            )
        try:
            if tuple(drop.position) == tuple(position):
                # Standing on it: step to a free tile beside it first.
                side = next(
                    (
                        (position[0] + dx, position[1] + dy)
                        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1))
                        if terrain.walkable((position[0] + dx, position[1] + dy))
                    ),
                    None,
                )
                if side is None:
                    return deferred("No free tile beside the loot")
                path = [tuple(position), side]
            else:
                # Stop beside the drop: an item under the farmer is covered by
                # the farmer (live 15:41, a Meteor stayed under Suicide).
                path = terrain.path(position, drop.position)[:-1]
                if len(path) < 2:
                    return False  # already beside it: the click comes next
            if len(path) < 2 or len(path) > 100:
                return deferred("Loot path outside bounded approach length")

            def allowed(x, y):
                inside = boundary[0] <= x <= boundary[2] and boundary[1] <= y <= boundary[3]
                return inside or (
                    not drop.silver
                    and min(
                        max(abs(x - position[0]), abs(y - position[1])),
                        max(abs(x - drop.position[0]), abs(y - drop.position[1])),
                    )
                    <= VALUABLE_RADIUS
                )

            if not all(allowed(x, y) for x, y in path):
                return deferred("Loot path leaves hunting boundary")
            from conquest.routes import BOSS_CLEARANCE, near_boss

            monsters = getattr(self, "escape_monsters", ())
            king = getattr(self, "king_clearance", BOSS_CLEARANCE)
            elite = getattr(self, "elite_clearance", BOSS_CLEARANCE)
            # A valuable's walk passes a boss unless HP is under LOOT_FIRST_HP.
            if (drop.silver or not self.loot_first()) and any(
                near_boss(tile, monsters, king_clearance=king, elite_clearance=elite)
                for tile in path
            ):
                return deferred("Loot path passes a boss")
            destination = native_waypoint(path, viewport=size_for(self.observer))
            dx, dy = destination[0] - position[0], destination[1] - position[1]
            viewport = size_for(self.observer)
            anchor = self.player_anchor(position)
            from conquest.scene_input import visible_route_delta
            from conquest.viewport import scene_bounds

            delta = visible_route_delta((dx, dy), anchor, scene_bounds(viewport))
            if delta is None:
                return deferred("Loot approach has no visible step", lasting=False)
            dx, dy = delta
            destination = (position[0] + dx, position[1] + dy)
            point = (anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16)
            # Planning never authorizes a stale identity or stale player tile.
            with self.observer.lock:
                if drop not in self.ground_items():
                    return deferred("Ground item changed before approach", lasting=False)
                if tuple(self.read_life().position) != tuple(position):
                    deferred("Player moved before loot approach", lasting=False)
                    return self.settle_for_valuable(drop)
            dispatch(point, control=max(abs(dx), abs(dy)) >= 8)
            self.last_loot_approach_deferred = None
            self.loot_approach_ready = time.monotonic() + 0.6
            if not drop.silver:
                self.chase_valuable(drop)
            self.notify(
                "memory_pickup_approach",
                {
                    "uid": drop.uid,
                    "type_id": drop.type_id,
                    "position": drop.position,
                    "destination": destination,
                    "timestamp": time.time(),
                    "activity": "Moving closer to valuable loot",
                },
            )
            return True
        except CaptureUnavailable as error:
            # The farmer or camera is still moving from the last step and no
            # input was sent: keep the chase, or combat takes the turn and
            # walks away. "Player projection changed before loot input" ended
            # nearly every chase; a Meteor 6 tiles off took 25 s while the
            # farmer drifted 12+ tiles away between steps (Suicide 2026-09-29).
            deferred("Valuable approach: " + str(error), lasting=False)
            # Combat waits a moment too: its next jump would move the frame again.
            return self.settle_for_valuable(drop)
        except ValueError as error:
            return deferred("Valuable approach: " + str(error))

    def settle_for_valuable(self, drop):
        """Hold the turn (True) up to LOOT_SETTLE_SECONDS for a valuable whose
        approach met a moving farmer, then not again for LOOT_SETTLE_COOLDOWN."""
        if drop.silver or not self.loot_first():
            return False
        now = time.monotonic()
        key = (drop.uid, drop.object_address)
        settle = getattr(self, "loot_settle", None)
        if (
            settle is None
            or settle[0] != key
            or now - settle[1] >= LOOT_SETTLE_SECONDS + LOOT_SETTLE_COOLDOWN
        ):
            settle = self.loot_settle = (key, now)
        return now - settle[1] < LOOT_SETTLE_SECONDS

    def loot_settling(self):
        settle = getattr(self, "loot_settle", None)
        return bool(
            settle is not None
            and time.monotonic() - settle[1] < LOOT_SETTLE_SECONDS
            and self.loot_first()
        )

    def movement_failed(self, position, destination):
        # A failed landing is dynamic evidence, not a permanent terrain edit.
        # Avoid its first step as well so A* chooses another departure direction.
        now = time.monotonic()
        dx, dy = destination[0] - position[0], destination[1] - position[1]
        first = (
            position[0] + (1 if dx > 0 else -1 if dx < 0 else 0),
            position[1] + (1 if dy > 0 else -1 if dy < 0 else 0),
        )
        for point in (first, tuple(destination)):
            if point != tuple(position):
                self.movement_obstructions[(self.map_id, point)] = now + 30
        self.movement_run_until = now + 6
        self.notify(
            "movement_recovery",
            {
                "position": list(position),
                "blocked_landing": list(destination),
                "activity": "Blocked movement; taking another path",
            },
        )

    def movement_succeeded(self, source, destination, *, arrived):
        # One verified detour clears slow walking mode; keep the failed tiles
        # excluded so returning to long jumps cannot replay the blocked edge.
        if max(abs(a - b) for a, b in zip(source, destination)) >= 3:
            self.movement_run_until = 0
            if arrived:
                now = time.monotonic()
                self.recent_movement_progress = [
                    entry
                    for entry in self.recent_movement_progress
                    if now - entry[0] <= 12
                ]
                self.recent_movement_progress.append(
                    (now, self.map_id, tuple(source), tuple(destination))
                )

    def patrol_step(self, position, fallback, boundary, *, chase=True, alternatives=()):
        self.patrol_destination = None
        from conquest.navigation import native_waypoint

        terrain = self.recovery.terrain
        intent = self.control.snapshot()
        x0, y0, x1, y1 = boundary
        now = time.monotonic()
        self.movement_obstructions = {
            key: until
            for key, until in self.movement_obstructions.items()
            if until > now
        }
        avoid = {
            point
            for (map_id, point) in self.movement_obstructions
            if map_id == self.map_id and point != tuple(position)
        }
        candidates = []
        fresh = {}
        if chase and now - self.scene_timestamp <= 0.5:
            for monster in self.chase_monsters:
                key = (monster.entity_id, monster.object_address)
                from conquest.routes import boss_name

                if (
                    boss_name(monster.name)
                    or monster.alive is False
                    or monster.current_hp == 0
                ):
                    continue
                if (
                    monster.entity_id not in intent["target_ids"]
                    and monster.type_id not in intent["target_type_ids"]
                ):
                    continue
                if self.excluded_targets.get(key, 0) > now:
                    continue
                mx, my = monster.position
                if (
                    x0 <= mx <= x1
                    and y0 <= my <= y1
                    and max(abs(mx - position[0]), abs(my - position[1])) > 1
                ):
                    fresh[key] = tuple(monster.position)
            candidates = sorted(
                fresh.values(),
                key=lambda p: abs(p[0] - position[0]) + abs(p[1] - position[1]),
            )
        lease = getattr(self, "patrol_chase", None)
        if chase and lease:
            key, point, until = lease
            if key in fresh:
                point = fresh[key]
            if (
                self.excluded_targets.get(key, 0) <= now
                and (key in fresh or until > now)
                and x0 <= point[0] <= x1
                and y0 <= point[1] <= y1
                and max(abs(a - b) for a, b in zip(point, position)) > 3
            ):
                # A briefly unavailable target read must not reverse travel back
                # to the patrol point. This is scouting a last-seen location;
                # attack dispatch still requires fresh identity and positive HP.
                candidates.insert(0, point)
            else:
                self.patrol_chase = None
        if not chase:
            self.patrol_chase = None
        # An obstructed approach waypoint must still permit a local detour.
        escapes = (
            [
                (position[0] + dx, position[1] + dy)
                for dx, dy in ((4, 0), (0, 4), (-4, 0), (0, -4))
            ]
            if avoid
            else []
        )
        # The approach and boundary returns plan round every visible boss's
        # clearance, as town travel does (boss_zone). travel_path's equally
        # short paths otherwise run straight through a King: from 2-4 tiles
        # off the road to the GiantApe west strip a fresh plan crossed
        # GiantApeKing 404775's idle spot (2026-09-29), and ranged_escape
        # alone held Suicide 11 tiles from him (18:27).
        from conquest.routes import BOSS_CLEARANCE, boss_name, boss_zone

        in_view = (
            [
                m
                for m in getattr(self, "scene_monsters", ())
                if boss_name(getattr(m, "name", "") or "")
            ]
            if now - getattr(self, "scene_timestamp", -float("inf")) <= 5
            else []
        )
        # Out of view but seen lately (remember_bosses).
        remembered = self.remembered_bosses(in_view)
        bosses = []
        if not chase:
            # Walks plan round them too.
            bosses = in_view + remembered
        from conquest.routes import boss_clearance

        king = getattr(self, "king_clearance", BOSS_CLEARANCE)
        elite = getattr(self, "elite_clearance", BOSS_CLEARANCE)
        # Spots a walk or the patrol must not head for: within a boss's
        # clearance. A King stood on giantape-far-west's anchor (404774 at
        # (478, 307), 2026-09-30 03:29) and boss_zone skips a boss whose zone
        # holds the destination, so the walk went straight at him until the
        # hold. On the Macaque field the patrol kept heading for points and
        # targets beside the MonkeyKing: 42-56 boss escapes in 5 minutes
        # (04:07-04:22). The patrol weighs remembered bosses as well: a boss
        # escape lands out of his view, and Toxic's patrol walked back to its
        # sweep point (244, 514) beside a SnakemanKing and jumped off it again
        # every ~5 s, nine times in 40 s with no kill, until boss_chase ended
        # the hunt (2026-10-01 05:42-05:43; at 05:30 too).
        held_by = bosses if not chase else in_view + remembered

        def held_by_boss(point):
            return any(
                max(abs(b.position[0] - point[0]), abs(b.position[1] - point[1]))
                <= boss_clearance(getattr(b, "name", "") or "", king, elite)
                for b in held_by
            )

        # The approach and returns take the nearest other patrol point when a
        # boss holds the spot (entering the hunting boundary ends the walk);
        # the patrol keeps its sweep order.
        others = list(map(tuple, alternatives))
        if not chase:
            others.sort(
                key=lambda p: max(abs(p[0] - position[0]), abs(p[1] - position[1]))
            )
        for destination in dict.fromkeys(
            [*candidates[:4], tuple(fallback), *others, *escapes]
        ):
            if destination == tuple(position):
                continue
            if not (x0 <= destination[0] <= x1 and y0 <= destination[1] <= y1):
                continue
            if held_by and held_by_boss(destination):
                continue
            try:
                from conquest.routes import BOSS_ROOM

                # Round each boss with BOSS_ROOM's margin, never closer than
                # we stand: the trial's boss hold (boss_step_ok) refuses any
                # step inside that margin that closes in, so a detour hugging
                # the clearance itself held Suicide 8 minutes beside a
                # GiantApeKing parked on the plain's gateway (619, 331),
                # 2026-09-30 10:37.
                zone = (
                    boss_zone(
                        tuple(position),
                        destination,
                        bosses,
                        king_clearance=getattr(self, "king_clearance", BOSS_CLEARANCE),
                        elite_clearance=getattr(self, "elite_clearance", BOSS_CLEARANCE),
                        margin=BOSS_ROOM - BOSS_CLEARANCE,
                    )
                    if bosses
                    else frozenset()
                )
                # Long inter-area travel can exceed the small local patrol budget.
                # Reuse a checked path while fresh memory stays on it or a few
                # clear tiles beside it (a shortened or escape jump) and the
                # destination, terrain, boundary and temporary obstructions agree:
                # replanning a 600-tile return after every off-line landing
                # stalled travel 2-5 s at a time (live 2026-09-27).
                key = (
                    id(terrain),
                    self.map_id,
                    destination,
                    tuple(boundary),
                    frozenset(avoid),
                    zone,
                )
                cached = getattr(self, "travel_path_cache", None) if not chase else None
                from conquest.navigation import rejoin_path

                hit = bool(cached) and cached[0] == key
                path = (
                    rejoin_path(terrain, cached[1], position, avoid=avoid | zone)
                    if hit
                    else None
                )
                detoured = hit and path is not None and cached[2:] == (True,)
                if path is None:
                    planner = (
                        getattr(
                            terrain,
                            "travel_path",
                            getattr(terrain, "straight_path", terrain.path),
                        )
                        if not chase
                        else terrain.path
                    )
                    from conquest.navigation import FIELD_TRAVEL_LIMIT

                    limit = FIELD_TRAVEL_LIMIT if not chase else 10000
                    # A walk plans inside its travel boundary, which refuses
                    # any path leaving it: from (788, 445) travel_path crossed
                    # the GiantApe plain at y 228-260 while the boundary began
                    # at y 255, so every destination was planned (~0.5 s each)
                    # and refused, each loop, and the frame always expired
                    # (Toxic stood 14 and 5 minutes, 2026-10-01 00:00, 00:39).
                    bounded = {} if chase else {"bounds": (x0, y0, x1, y1)}

                    def plan_walk(**kwargs):
                        try:
                            return planner(position, destination, limit=limit, **bounded, **kwargs)
                        except TypeError:
                            if not bounded:
                                raise
                            return planner(position, destination, limit=limit, **kwargs)

                    path = plan_walk(**({"avoid": avoid} if avoid else {}))
                    if zone and any(tuple(p) in zone for p in path):
                        try:
                            detour = plan_walk(avoid=avoid | zone)
                        except ValueError:
                            detour = None
                        # Only a detour inside the travel boundary: a landing
                        # beyond it stops the runner (reposition_outside_boundary),
                        # which a detour round King 404773 did at (582, 307)
                        # before Suicide died there (2026-09-30 01:20). With no
                        # way round inside it, keep the direct plan: the boss
                        # hold and ranged_escape still guard it.
                        if detour is not None and all(
                            x0 <= px <= x1 and y0 <= py <= y1 for px, py in detour
                        ):
                            path, detoured = detour, True
                if not chase:
                    self.travel_path_cache = (key, path, detoured)
                if any(not (x0 <= px <= x1 and y0 <= py <= y1) for px, py in path):
                    continue
                chosen = next(
                    (key for key, point in fresh.items() if point == destination), None
                )
                if chosen is not None:
                    self.patrol_chase = (chosen, destination, now + 2)
                    stand_off = getattr(self, "scatter_standoff", 0)
                    if stand_off:
                        # Approach until the group is inside Scatter reach;
                        # do not jump onto the target and trigger an escape.
                        monster = next(
                            m
                            for m in self.chase_monsters
                            if (m.entity_id, m.object_address) == chosen
                        )

                        def aim_visible(p):
                            # Being in world range is insufficient when the target
                            # is outside the clear input area. Do not creep one tile
                            # at a time toward a target that remains obscured.
                            dx, dy = p[0] - position[0], p[1] - position[1]
                            px = monster.draw_position[0] - (dx - dy) * 32
                            py = monster.draw_position[1] - (dx + dy) * 16
                            return clear_scene((px, py), size_for(self.observer))

                        stop = next(
                            (
                                i
                                for i, p in enumerate(path)
                                if i > 0
                                and max(abs(a - b) for a, b in zip(p, destination))
                                <= stand_off
                                and aim_visible(p)
                            ),
                            len(path) - 1,
                        )
                        path = path[: stop + 1]
                if not chase and hasattr(terrain, "travel_path"):
                    from conquest.navigation import travel_waypoint

                    step = travel_waypoint(
                        terrain,
                        path,
                        4 if now < self.movement_run_until else 12,
                        # A landing's straight jump must not cut into the
                        # clearance the detour walks round.
                        avoid=avoid | zone if detoured else avoid,
                        viewport=size_for(self.observer),
                    )
                else:
                    step = native_waypoint(
                        path,
                        4 if now < self.movement_run_until else 12,
                        viewport=size_for(self.observer),
                    )
                repeats = sum(
                    now - stamp <= 12
                    and map_id == self.map_id
                    and source == tuple(position)
                    and landing == tuple(step)
                    for stamp, map_id, source, landing in self.recent_movement_progress
                )
                if repeats >= 2:
                    # Reaching a landing is insufficient if fresh memory keeps
                    # returning to the same source before the next identical move.
                    # Use the existing bounded detour; do not guess whether this
                    # was server correction, auto-chasing, or a dynamic obstacle.
                    self.movement_failed(position, step)
                    self.recent_movement_progress = []
                    self.notify(
                        "movement_reversed",
                        {
                            "position": list(position),
                            "landing": list(step),
                            "repetitions": repeats,
                            "activity": "Repeated return to the same tile; taking another path",
                        },
                    )
                    raise CaptureUnavailable(
                        "Patrol progress reversed; taking another path"
                    )
                if destination not in candidates:
                    self.patrol_destination = destination
                return step
            except CaptureUnavailable:
                raise
            except ValueError:
                continue
        raise CaptureUnavailable("Waiting for a traversable patrol step")

    def ranged_escape(
        self,
        position,
        boundary,
        anchor=None,
        adjacent_trigger=2,
        reach=1,
        scatter_range=None,
        harmless_level=None,
    ):
        """A bounded clear jump away from memory-verified nearby living monsters.

        ``anchor`` is the player's screen position the jump is clicked from.
        ``adjacent_trigger`` monsters within ``reach`` tiles call for a jump
        without any damage; jump-Scatter uses one monster within
        JUMP_SCATTER_REACH (Alex 2026-09-27: "When doing jump scatter you
        can't let enemies ever attack you"). With ``scatter_range``, equally
        safe landings prefer the one with the most monsters left inside
        Scatter range: the farthest landing often left the pack out of range,
        and 40% of escapes went over 1.5 s without a cast (18:45-18:56).
        An ordinary monster of level ``harmless_level`` or lower calls for no
        jump by coming close (HARMLESS_LEVELS); its hits still do.
        """
        from conquest.navigation import native_movement_delta
        from conquest.routes import BOSS_CLEARANCE, BOSS_ROOM, boss_clearance, boss_name

        now = time.monotonic()
        if (
            now < getattr(self, "escape_ready_at", 0)
            or now - self.scene_timestamp > 0.5
        ):
            return None
        living = [m.position for m in self.escape_monsters]
        distances = [max(abs(a - b) for a, b in zip(p, position)) for p in living]

        def harmless(monster):
            return (
                harmless_level is not None
                and type(getattr(monster, "level", None)) is int
                and monster.level <= harmless_level
                and not boss_name(getattr(monster, "name", "") or "")
            )

        close = [d for m, d in zip(self.escape_monsters, distances) if not harmless(m)]
        adjacent = sum(d <= 1 for d in close)
        within_reach = sum(d <= reach for d in close)

        # Bosses hit from range: leave one within its clearance (the route's
        # king_clearance for the King tier, its elite_clearance for Aides and
        # Messengers) and never land inside another's.
        king_clearance = getattr(self, "king_clearance", BOSS_CLEARANCE)
        elite_clearance = getattr(self, "elite_clearance", BOSS_CLEARANCE)
        boss_reach = [
            (
                tuple(m.position),
                boss_clearance(
                    getattr(m, "name", "") or "", king_clearance, elite_clearance
                ),
            )
            for m in self.escape_monsters
            if boss_name(getattr(m, "name", "") or "")
        ]
        boss_near = [
            b
            for b, clearance in boss_reach
            if max(abs(a - c) for a, c in zip(b, position)) <= clearance
        ]
        # Every hit over ESCAPE_DAMAGE_SHARE is reason to jump: tanking hits
        # only burns potions and town trips ("don't tank a few hits").
        damaged = (
            now - self.last_damage_at <= DAMAGE_WINDOW
            and self.last_damage_at > self.escape_damage_consumed_at
        )
        if within_reach < adjacent_trigger and not damaged and not boss_near:
            return None
        threats = [
            p for p, d in zip(living, distances) if d <= (12 if damaged else reach)
        ]
        threats += [b for b in boss_near if b not in threats]
        if not threats:
            return None
        x, y = position
        left, top, right, bottom = boundary
        terrain = self.recovery.terrain
        blocked = {
            landing: until
            for landing, until in getattr(self, "escape_blocked", {}).items()
            if until > now
        }
        self.escape_blocked = blocked

        def landings(threats, *, fewer=True, clearance=6, clear_of_all=None):
            found = []
            for length in (12, 10, 8):
                for dx, dy in (
                    (length, 0),
                    (-length, 0),
                    (0, length),
                    (0, -length),
                    # Diagonals: a surround that walls off the straight lines.
                    (length, length),
                    (-length, -length),
                    (length, -length),
                    (-length, length),
                ):
                    # Judge the click from the player's own screen anchor,
                    # the point the dispatch clicks from.
                    dx, dy = native_movement_delta(
                        dx, dy, viewport=size_for(self.observer), anchor=anchor
                    )
                    distance = max(abs(dx), abs(dy))
                    if distance < ESCAPE_MIN_JUMP:
                        continue
                    point = (x + dx, y + dy)
                    if point in blocked:
                        continue  # that landing just failed to move the farmer
                    if not (left <= point[0] <= right and top <= point[1] <= bottom):
                        continue
                    sx, sy = dx // distance, dy // distance
                    if not all(
                        terrain.walkable((x + sx * i, y + sy * i))
                        for i in range(distance + 1)
                    ):
                        continue
                    if any(
                        max(abs(point[0] - bx), abs(point[1] - by)) <= reach_of_boss
                        for (bx, by), reach_of_boss in boss_reach
                    ):
                        continue  # inside a boss's reach
                    separation = min(
                        max(abs(point[0] - mx), abs(point[1] - my))
                        for mx, my in threats
                    )
                    if separation < clearance:
                        continue
                    distances = [
                        max(abs(point[0] - mx), abs(point[1] - my)) for mx, my in living
                    ]
                    if clear_of_all is not None and min(distances) <= clear_of_all:
                        continue  # a monster could hit us where we land
                    nearby = sum(d <= 4 for d in distances)
                    if fewer and nearby >= len(threats):
                        continue
                    # Prefer fewer nearby enemies, including those outside the
                    # original surround, then room from a roaming boss, then
                    # (jump-Scatter) more of the pack still in Scatter range,
                    # then more clearance and longer jumps.
                    roomy = all(
                        max(abs(point[0] - bx), abs(point[1] - by))
                        >= reach_of_boss + BOSS_ROOM - BOSS_CLEARANCE
                        for (bx, by), reach_of_boss in boss_reach
                    )
                    in_range = (
                        sum(reach < d <= scatter_range for d in distances)
                        if scatter_range
                        else 0
                    )
                    found.append(
                        (
                            -nearby,
                            roomy,
                            in_range,
                            min(distances),
                            separation,
                            distance,
                            point,
                        )
                    )
            return found

        candidates = landings(threats)
        crowded = False
        if not candidates and damaged:
            # A crowd within 12 tiles can rule out every landing; still get
            # 6+ tiles clear of the monsters close enough to be hitting us
            # rather than standing there and tanking.
            attackers = [
                p for p in threats if max(abs(a - b) for a, b in zip(p, position)) <= 3
            ]
            if attackers:
                candidates, crowded = landings(attackers), True
                if not candidates and getattr(self, "health_share", 1) < ESCAPE_LOW_HP:
                    # Low HP in a crowd (09-27 13:19: ~30 Apparitions, no
                    # landing with fewer monsters): still leave the attackers
                    # for the least crowded open landing instead of tanking.
                    candidates = landings(attackers, fewer=False) or landings(
                        attackers, fewer=False, clearance=4
                    )
        if not candidates and scatter_range:
            # Jump-Scatter never stands inside a pack. In a dense one no
            # landing has fewer monsters nearby, and above ESCAPE_LOW_HP the
            # archer kept shooting monsters 1-2 tiles away: Bandits took Toxic
            # from 81% to 33% (2026-09-28 00:09) and from 80% to 25% in 1.5 s
            # (00:19, 8 monsters within 3 tiles). Leave for the least crowded
            # landing clear of those monsters that nothing can hit.
            close = [p for p, d in zip(living, distances) if d <= JUMP_SCATTER_REACH]
            close += [b for b in boss_near if b not in close]
            if close:
                clear = JUMP_SCATTER_REACH
                candidates = landings(close, fewer=False, clear_of_all=clear) or landings(
                    close, fewer=False, clearance=4, clear_of_all=clear
                )
                crowded = bool(candidates)
        flight = False
        if not candidates and boss_near:
            # Last resort from a boss inside its clearance: take the walkable
            # landing that most increases the distance from the nearest boss,
            # off the hunting boundary and short of the full clearance if need
            # be. Toxic died on Ratlings (2026-09-28 14:27:18) at (532,508),
            # outside the boundary, where every landing inside it lay within
            # the RatKing's 15 tiles: no escape was tried for 14 s while the
            # King closed from 8 to 2 tiles and hit ~250 HP/s.
            here = min(
                max(abs(a - b) for a, b in zip(boss, position)) for boss, _ in boss_reach
            )
            width = getattr(terrain, "width", None)
            height = getattr(terrain, "height", None)
            for length in (12, 10, 8):
                for dx, dy in (
                    (length, 0),
                    (-length, 0),
                    (0, length),
                    (0, -length),
                    (length, length),
                    (-length, -length),
                    (length, -length),
                    (-length, length),
                ):
                    dx, dy = native_movement_delta(
                        dx, dy, viewport=size_for(self.observer), anchor=anchor
                    )
                    distance = max(abs(dx), abs(dy))
                    if distance < ESCAPE_MIN_JUMP:
                        continue
                    point = (x + dx, y + dy)
                    if point in blocked or (
                        width is not None
                        and height is not None
                        and not (0 <= point[0] < width and 0 <= point[1] < height)
                    ):
                        continue
                    # The whole line, also for an uneven delta from
                    # native_movement_delta (12/11 walks both axes).
                    if not all(
                        terrain.walkable(
                            (x + round(dx * i / distance), y + round(dy * i / distance))
                        )
                        for i in range(distance + 1)
                    ):
                        continue
                    room = min(
                        max(abs(point[0] - bx), abs(point[1] - by))
                        for (bx, by), _ in boss_reach
                    )
                    if room <= here:
                        continue  # no further from the boss than now
                    nearby = sum(
                        max(abs(point[0] - mx), abs(point[1] - my)) <= 4
                        for mx, my in living
                    )
                    candidates.append((room, -nearby, distance, point))
            flight = bool(candidates)
        damage_flight = False
        if not candidates and damaged:
            # Last resort under fire, as the boss flight is for a boss inside
            # its clearance: no landing qualified (the boundary, the crowd
            # rules or a boss's reach ruled each out), so take any walkable
            # landing off every boss's reach, past the boundary if need be,
            # clear of the monsters hitting us. Suicide stood 14 s at
            # (299, 312), 1 tile outside thunderape-nw's west edge, with
            # ThunderApes at 1-3 tiles and a King near: landings west lay
            # outside the box, those north-east in the King's reach, and it
            # died there with no escape tried (2026-09-30 14:32:16).
            hitters = [p for p, d in zip(living, distances) if d <= 3] or [
                p for p in threats if p not in boss_near
            ]
            # Clear of every monster, not only the hitters: a landing beside
            # another is no escape (Alex 2026-09-27: "you can't let enemies
            # ever attack you").
            everyone = list(dict.fromkeys([*living, *hitters]))
            width = getattr(terrain, "width", None)
            height = getattr(terrain, "height", None)
            for length in (12, 10, 8):
                for dx, dy in (
                    (length, 0),
                    (-length, 0),
                    (0, length),
                    (0, -length),
                    (length, length),
                    (-length, -length),
                    (length, -length),
                    (-length, length),
                ):
                    dx, dy = native_movement_delta(
                        dx, dy, viewport=size_for(self.observer), anchor=anchor
                    )
                    distance = max(abs(dx), abs(dy))
                    if distance < ESCAPE_MIN_JUMP:
                        continue
                    point = (x + dx, y + dy)
                    if point in blocked or (
                        width is not None
                        and height is not None
                        and not (0 <= point[0] < width and 0 <= point[1] < height)
                    ):
                        continue
                    if not all(
                        terrain.walkable(
                            (x + round(dx * i / distance), y + round(dy * i / distance))
                        )
                        for i in range(distance + 1)
                    ):
                        continue
                    if any(
                        max(abs(point[0] - bx), abs(point[1] - by)) <= reach_of_boss
                        for (bx, by), reach_of_boss in boss_reach
                    ):
                        continue  # never into a boss's reach
                    separation = min(
                        (max(abs(point[0] - mx), abs(point[1] - my)) for mx, my in everyone),
                        default=99,
                    )
                    if separation <= JUMP_SCATTER_REACH:
                        continue  # still within a monster's reach
                    nearby = sum(
                        max(abs(point[0] - mx), abs(point[1] - my)) <= 4
                        for mx, my in living
                    )
                    room = min(
                        (
                            max(abs(point[0] - bx), abs(point[1] - by))
                            for (bx, by), _ in boss_reach
                        ),
                        default=99,
                    )
                    candidates.append((-nearby, separation, room, distance, point))
            damage_flight = bool(candidates)
        if not candidates:
            return None
        self.escape_context = {
            "adjacent_enemies": adjacent,
            "enemies_within_reach": within_reach,
            "nearest_enemy": min(distances) if distances else None,
            "recent_damage": damaged,
            "crowded": crowded,
            "boss_nearby": bool(boss_near),
            "reason": "boss_flight"
            if flight
            else "damage_flight"
            if damage_flight
            else "recent_damage"
            if damaged
            else "boss_nearby"
            if boss_near and within_reach < adjacent_trigger
            else "enemies_within_one_tile"
            if reach == 1
            else "enemies_within_reach",
        }
        return max(candidates)[-1]

    def escape_sent(self, source, destination, *, consumed_before=None):
        """Remember a dispatched escape jump until its movement is verified."""
        self.escape_pending = (
            time.monotonic(),
            tuple(source),
            tuple(destination),
            consumed_before,
        )

    def escape_result(self, position):
        """'moved', 'failed', or None while the jump may still be landing."""
        pending = getattr(self, "escape_pending", None)
        if not pending:
            return None
        at, source, destination, consumed_before = pending
        now = time.monotonic()
        if max(abs(a - b) for a, b in zip(position, source)) >= 3:
            self.escape_pending = None
            self.escape_failures = 0
            return "moved"
        if now - at < ESCAPE_VERIFY_SECONDS:
            return None
        self.escape_pending = None
        blocked = getattr(self, "escape_blocked", {})
        blocked[destination] = now + ESCAPE_BLOCK_SECONDS
        self.escape_blocked = blocked
        self.escape_failures = getattr(self, "escape_failures", 0) + 1
        if consumed_before is not None:
            # The damage that called for the jump still stands.
            self.escape_damage_consumed_at = consumed_before
        return "failed"

    def escape_quick_retry(self):
        """Whether a failed jump may be retried at once, not after the cadence."""
        return 0 < getattr(self, "escape_failures", 0) <= ESCAPE_QUICK_RETRIES

    def note_target_position(self, monsters):
        """Where the monster we are shooting was last seen in the scene.

        Melee monsters walk at the archer while it shoots and the archer jumps
        away from hits, so a monster dies well away from where it was targeted.
        """
        target = self.last_target
        if target is None:
            return
        key = (target.entity_id, target.object_address)
        for monster in monsters:
            if (monster.entity_id, monster.object_address) == key:
                from conquest.memory_ground import client_tick_ms

                self.last_target_seen = (key, client_tick_ms(), tuple(monster.position))
                return

    def remember_kill_site(self):
        """Where and when our verified kill happened; its drops appear there.

        The monster's last seen tile, not where it was targeted: judged from
        the targeting tile our own silver was often more than KILL_DROP_RADIUS
        away and left behind (Toxic 2026-09-27: 0.96 silver pickups per kill
        before the rule, 0.2-0.3 after, and the bank drained to 94 silver).
        """
        from conquest.memory_ground import client_tick_ms

        now = client_tick_ms()
        target = self.last_target
        site = getattr(target, "world_position", None) if target is not None else None
        seen = getattr(self, "last_target_seen", None)
        if (
            target is not None
            and seen
            and seen[0] == (target.entity_id, target.object_address)
            and now - seen[1] <= TARGET_SEEN_MS
        ):
            site = seen[2]
        radius = KILL_DROP_RADIUS
        if site is None and self.position is not None:
            site, radius = tuple(self.position), KILL_DROP_UNKNOWN_RADIUS
        sites = [
            row for row in getattr(self, "kill_sites", ()) if now - row[0] <= KILL_SITE_KEEP_MS
        ]
        if site is not None:
            sites.append((now, tuple(site), radius))
        cast_at, _ = getattr(self, "last_scatter_group", (-float("inf"), {}))
        cast_from = getattr(self, "last_scatter_position", None)
        if (
            cast_from is not None
            and 0 <= time.monotonic() - cast_at <= KILL_DROP_SCATTER_SECONDS
        ):
            sites.append((now, tuple(cast_from), KILL_DROP_SCATTER_RADIUS))
        self.kill_sites = sites

    def own_kill_drop(self, drop):
        """Alex: only pick up the silver from the monsters we kill."""
        return any(
            at - KILL_DROP_BEFORE_MS <= drop.spawn_tick <= at + KILL_DROP_AFTER_MS
            and max(abs(a - b) for a, b in zip(drop.position, site)) <= radius
            for at, site, radius in getattr(self, "kill_sites", ())
        )

    def finish_target(self, reason):
        self.patrol_chase = None
        if reason == "kill_counter_increased":
            self.loot_wait_until = time.monotonic() + 0.45
            self.remember_kill_site()
        # Scatter can kill a different monster from the one used to aim.
        # A player-counter increase never proves that the aimed target died.
        if self.last_target is not None and reason != "kill_counter_increased":
            target = self.last_target
            self.excluded_targets[(target.entity_id, target.object_address)] = (
                time.monotonic() + 8
            )
            self.notify(
                "target_cooldown",
                {"entity_id": target.entity_id, "reason": reason, "seconds": 8},
            )
        self.last_target = None
        now = time.monotonic()
        self.excluded_targets = {
            key: expiry for key, expiry in self.excluded_targets.items() if expiry > now
        }

    def dispatch(
        self,
        callback,
        *,
        target=None,
        drop=None,
        expected_position=None,
        retarget=None,
        attack_range=None,
    ):
        with self.observer.lock, self.control.lock:
            with logical_coordinates():
                life = self.read_life()
            if (
                not self.control.enabled
                or self.control.revision != self.revision
                or life.dead_candidate
                or life.ghost_candidate
            ):
                raise CaptureUnavailable(
                    "Farming paused or character died before input"
                )
            if expected_position is not None and tuple(life.position) != tuple(
                expected_position
            ):
                raise CaptureUnavailable(
                    "Player moved before movement input; reobserving"
                )
            if target is not None:
                matched = (
                    self.match_targets([target], refresh=True)
                    if retarget
                    else self.match_targets([target])
                )
                if not matched:
                    raise CaptureUnavailable("Selected monster moved before input")
                if retarget:
                    target = matched[0]
                    if (
                        not isinstance(attack_range, (int, float))
                        or not 0 < attack_range <= 20
                        or max(
                            abs(a - b)
                            for a, b in zip(life.position, target.world_position)
                        )
                        > attack_range
                        or not clear_scene(
                            (target.x, target.y), size_for(self.observer)
                        )
                    ):
                        raise CaptureUnavailable(
                            "Refreshed monster aim is outside attack range or clear scene"
                        )
                    retarget(target)
            if drop is not None:
                try:
                    if drop not in self.ground_items():
                        raise CaptureUnavailable("Ground item changed before pickup")
                except ValueError as error:
                    raise CaptureUnavailable(str(error)) from error
            result = callback()
            if target is not None:
                self.last_target = target
            return result
