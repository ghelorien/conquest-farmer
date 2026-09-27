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
KILL_DROP_SCATTER_RADIUS = 9
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
VALUABLE_CLICK_TILES = 6

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
        self.loot_cooldowns = {}
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
            if previous[0] - life.current_hp > ESCAPE_DAMAGE_SHARE * life.max_hp:
                self.last_damage_at = time.monotonic()
            if previous[1] == position:
                self.defend_until = time.monotonic() + 8
        self.last_health_position = (life.current_hp, position)

    def observe(self):
        from conquest.mouse_priority import require_idle
        from conquest.merchants.coordination import manual_session_blocked
        from conquest.merchants.coordination import observe_manual_farmer

        manual = bool(observe_manual_farmer(self.observer)) or manual_session_blocked(
            "Farmer"
        )
        if not manual:
            require_idle()
        with logical_coordinates(), self.observer.lock:
            life = self.read_life()
            if hasattr(life, "position"):
                self.position = tuple(life.position)
                self.map_id = life.map_id
                self.note_health(life)
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
            window = self.observer.operations.target.snapshot()
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
            if getattr(self, "runback_watch", None):
                self.runback_watch.observe(
                    {**asdict(life), "dead_candidate": life.dead_candidate},
                    paused=waiting,
                )
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
            self.note_target_position(monsters)
            intent = self.control.snapshot()
            accepted = []
            scatter_scene = []
            chase = []
            escape = []
            for monster in monsters:
                selected = (
                    monster.entity_id in intent["target_ids"]
                    or monster.type_id in intent["target_type_ids"]
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
                    not (selected or close or adjacent or threat)
                    or monster.alive is False
                    or monster.current_hp == 0
                ):
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

    def loot_step(
        self, inventory, position, dispatch, *, money_only=False, max_distance=12
    ):
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
        except (ValueError, OSError) as error:
            if str(error) != self.last_loot_error:
                self.notify("memory_loot_retry", {"detail": str(error)})
                self.last_loot_error = str(error)
            if self.pending_loot and now - self.pending_loot[2] >= 2:
                drop, _, _ = self.pending_loot
                delay = 60 if drop.silver else 1
                self.loot_cooldowns[(drop.uid, drop.object_address)] = now + delay
                if not drop.silver:
                    self.loot_wait_until = max(self.loot_wait_until, now + delay)
                    self.close_loot_retry = (drop.uid, drop.object_address)
                self.pending_loot = None
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
                if money_only:
                    return False
            elif now - issued < (1.5 if drop.silver else 3.0):
                return True
            else:
                self.notify(
                    "memory_pickup_unverified",
                    {"uid": drop.uid, "type_id": drop.type_id},
                )
                delay = 60 if drop.silver else 1
                self.loot_cooldowns[(drop.uid, drop.object_address)] = now + delay
                if not drop.silver:
                    self.loot_wait_until = max(self.loot_wait_until, now + delay)
                    self.close_loot_retry = (drop.uid, drop.object_address)
                self.pending_loot = None
        self.loot_cooldowns = {k: v for k, v in self.loot_cooldowns.items() if v > now}
        candidates = []
        approaches = []
        deferred = []
        anchor = None
        from conquest.discard_loot import ignored_drop, JOURNAL
        from conquest.discord_notify import read_json

        discarder = getattr(self, "discarder", None)
        ignored = discarder.records if discarder is not None else read_json(JOURNAL, [])
        for drop in drops:
            if (money_only and not drop.silver) or not wanted_drop(drop):
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
        return now < self.loot_wait_until

    def approach_loot(self, drop, position, dispatch):
        """Reposition toward a freshly observed valuable instead of skipping it."""
        now = time.monotonic()
        if now < getattr(self, "loot_approach_ready", 0):
            return True

        def deferred(reason):
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
            if any(
                not (
                    boundary[0] <= x <= boundary[2] and boundary[1] <= y <= boundary[3]
                )
                for x, y in path
            ):
                return deferred("Loot path leaves hunting boundary")
            destination = native_waypoint(path, viewport=size_for(self.observer))
            dx, dy = destination[0] - position[0], destination[1] - position[1]
            viewport = size_for(self.observer)
            anchor = self.player_anchor(position)
            from conquest.scene_input import visible_route_delta
            from conquest.viewport import scene_bounds

            delta = visible_route_delta((dx, dy), anchor, scene_bounds(viewport))
            if delta is None:
                return deferred("Loot approach has no visible step")
            dx, dy = delta
            destination = (position[0] + dx, position[1] + dy)
            point = (anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16)
            # Planning never authorizes a stale identity or stale player tile.
            with self.observer.lock:
                if drop not in self.ground_items():
                    return deferred("Ground item changed before approach")
                if tuple(self.read_life().position) != tuple(position):
                    return deferred("Player moved before loot approach")
            dispatch(point, control=max(abs(dx), abs(dy)) >= 8)
            self.last_loot_approach_deferred = None
            self.loot_approach_ready = time.monotonic() + 0.6
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
        except ValueError as error:
            return deferred("Valuable approach: " + str(error))

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
        for destination in dict.fromkeys(
            [*candidates[:4], tuple(fallback), *map(tuple, alternatives), *escapes]
        ):
            if destination == tuple(position):
                continue
            if not (x0 <= destination[0] <= x1 and y0 <= destination[1] <= y1):
                continue
            try:
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
                )
                cached = getattr(self, "travel_path_cache", None) if not chase else None
                from conquest.navigation import rejoin_path

                path = (
                    rejoin_path(terrain, cached[1], position, avoid=avoid)
                    if cached and cached[0] == key
                    else None
                )
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

                    path = planner(
                        position,
                        destination,
                        limit=FIELD_TRAVEL_LIMIT if not chase else 10000,
                        **({"avoid": avoid} if avoid else {}),
                    )
                if not chase:
                    self.travel_path_cache = (key, path)
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
                        avoid=avoid,
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
        self, position, boundary, anchor=None, adjacent_trigger=2, reach=1
    ):
        """A bounded clear jump away from memory-verified nearby living monsters.

        ``anchor`` is the player's screen position the jump is clicked from.
        ``adjacent_trigger`` monsters within ``reach`` tiles call for a jump
        without any damage; jump-Scatter uses one monster within
        JUMP_SCATTER_REACH (Alex 2026-09-27: "When doing jump scatter you
        can't let enemies ever attack you").
        """
        from conquest.navigation import native_movement_delta

        now = time.monotonic()
        if (
            now < getattr(self, "escape_ready_at", 0)
            or now - self.scene_timestamp > 0.5
        ):
            return None
        living = [m.position for m in self.escape_monsters]
        distances = [max(abs(a - b) for a, b in zip(p, position)) for p in living]
        adjacent = sum(d <= 1 for d in distances)
        within_reach = sum(d <= reach for d in distances)
        # Every hit over ESCAPE_DAMAGE_SHARE is reason to jump: tanking hits
        # only burns potions and town trips ("don't tank a few hits").
        damaged = (
            now - self.last_damage_at <= DAMAGE_WINDOW
            and self.last_damage_at > self.escape_damage_consumed_at
        )
        if within_reach < adjacent_trigger and not damaged:
            return None
        threats = [
            p for p, d in zip(living, distances) if d <= (12 if damaged else reach)
        ]
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

        def landings(threats, *, fewer=True, clearance=6):
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
                    separation = min(
                        max(abs(point[0] - mx), abs(point[1] - my))
                        for mx, my in threats
                    )
                    if separation < clearance:
                        continue
                    distances = [
                        max(abs(point[0] - mx), abs(point[1] - my)) for mx, my in living
                    ]
                    nearby = sum(d <= 4 for d in distances)
                    if fewer and nearby >= len(threats):
                        continue
                    # Prefer fewer nearby enemies, including those outside the
                    # original surround, then more clearance and longer jumps.
                    found.append((-nearby, min(distances), separation, distance, point))
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
        if not candidates:
            return None
        self.escape_context = {
            "adjacent_enemies": adjacent,
            "enemies_within_reach": within_reach,
            "nearest_enemy": min(distances) if distances else None,
            "recent_damage": damaged,
            "crowded": crowded,
            "reason": "recent_damage"
            if damaged
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
