"""Bounded foreground patrol/combat with verified healing, supply and loot outcomes.

This is a supervised integration trial, not the fully qualified autonomous farmer.
"""
import json
import math
import sqlite3
import time
from typing import Literal
from pathlib import Path

import cv2
import yaml
from pydantic import BaseModel, ConfigDict, Field

from conquest.addressing import PlayerLayout, WorkerPointerSession, resolve_player
from conquest.capture import DesktopFrames, CaptureUnavailable
from conquest.vision import health_ratio, targets
from conquest.memory_inventory import InventoryLayout, MemoryInventoryReader
from conquest.inventory import InventoryReader
from conquest.healing import HealingAttempt, potion_point
from conquest.reloading import ReloadAttempt
from conquest.looting import PickupAttempt, nearby_drops
from conquest.recovery import RecoveryConfig, DeathRecovery, RecoveryPhase, revive_button


class TrialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_mode: Literal["memory_only", "legacy_visual"] = "memory_only"
    character: str
    player_profile: str
    inventory_profile: str
    template: str
    monster: Literal["Pheasant", "Turtledove"] = "Pheasant"
    client_size: tuple[int, int]
    boundary: tuple[int, int, int, int]
    minimum_health: float = Field(default=.4, gt=0, lt=1)
    interval: float = Field(default=.3, ge=.15, le=10)
    maximum_actions: int = Field(default=15, ge=1, le=1000)
    max_distance_pixels: int = Field(default=380, ge=50, le=450)
    ammo_type: int = Field(default=1050000, gt=0)
    potion_type: int = Field(default=1000000, gt=0)
    healing_enabled: bool = True
    heal_below: float = Field(default=.4, gt=0, lt=1)
    potion_cooldown: float = Field(default=1, ge=.5, le=10)
    inventory_templates: str = "profiles/templates"
    capture_output: int = Field(default=0, ge=0, le=8)
    capture_origin: tuple[int, int] = (0, 0)
    route: tuple[tuple[int, int], ...] = ()
    potion_key: int | None = Field(default=None, ge=112, le=122)
    ammo_key: int | None = Field(default=None, ge=112, le=122)
    loot_allowlist: tuple[str, ...] = ()
    loot_distance_pixels: int = Field(default=180, ge=30, le=250)
    loot_attempt_limit: int = Field(default=2, ge=1, le=3)
    expected_map: int = 1002
    leveling_plan: str = "profiles/leveling-plan.yaml"
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)


def supply_stop_reason(inventory, config, now):
    if not 0 <= now - inventory.started_at <= 1:
        return "stale_inventory"
    if len(inventory.items) >= inventory.capacity:
        return "inventory_full"
    ammo = inventory.equipped_ammo
    if ammo is None or ammo.type_id != config.ammo_type or ammo.amount <= 0:
        if not (getattr(config, "ammo_key", None) is not None
                and inventory.count(config.ammo_type) > 0
                and (ammo is None or ammo.type_id == config.ammo_type)):
            return "ammo_unavailable"
    if inventory.count(config.potion_type) <= 0:
        return "potions_exhausted"
    return None


def choose_target(observed, hp, position, config, age):
    if age > .35 or hp < config.minimum_health:
        return None
    left, top, right, bottom = config.boundary
    if not (left <= position[0] <= right and top <= position[1] <= bottom):
        return None
    candidates = []
    for target in observed:
        dx, dy = target.x - 792, target.y - 432
        map_x = position[0] + (dx / 32 + dy / 16) / 2
        map_y = position[1] + (dy / 16 - dx / 32) / 2
        distance = dx * dx + dy * dy
        if (target.name == config.monster and distance < config.max_distance_pixels ** 2
                and left <= map_x <= right and top <= map_y <= bottom):
            candidates.append((distance, target))
    return min(candidates, key=lambda pair: pair[0])[1] if candidates else None


def visible_movement_delta(dx, dy):
    """Shorten a jump to stay in the calibrated unobstructed play area."""
    scale = min(1, 280 / max(abs((dx-dy)*32), 1),
                110 / max(abs((dx+dy)*16), 1))
    return dx * scale, dy * scale


def run_trial(config_path, info_path, output, seconds, logger, observe_only=False):
    import win32api
    if not 1 <= seconds <= 1800:
        raise ValueError("Supervised run must last 1 to 1800 seconds")
    config = TrialConfig.model_validate(yaml.safe_load(Path(config_path).read_text()))
    if config.observation_mode == "memory_only":
        raise ValueError("Memory-only farming is not qualified: current HP, monster alive state, "
                         "ground loot and revival observations remain unresolved. No capture or input started.")
    if set(config.loot_allowlist) - {"Stancher"}:
        raise ValueError("Only the calibrated Stancher drop is supported")
    if any(not (config.boundary[0] <= x <= config.boundary[2] and config.boundary[1] <= y <= config.boundary[3]) for x,y in config.route):
        raise ValueError("Route waypoint is outside the farming boundary")
    layout = PlayerLayout.model_validate(yaml.safe_load(Path(config.player_profile).read_text()))
    template = cv2.imread(config.template, cv2.IMREAD_GRAYSCALE)
    expected_shape = {"Pheasant": (13,63), "Turtledove": (13,77)}[config.monster]
    if template is None or template.shape != expected_shape:
        raise ValueError("Calibrated monster template is missing or changed")
    loot_template = None
    if config.loot_allowlist:
        loot_template = cv2.imread(str(Path(config.inventory_templates) / "stancher-name.png"),0)
        if loot_template is None or loot_template.shape != (13,63):
            raise ValueError("Calibrated Stancher label template is missing")
    session = WorkerPointerSession(info_path, layout.expected_sha256)
    inventory_layout = InventoryLayout.model_validate(yaml.safe_load(Path(config.inventory_profile).read_text()))
    inventory_reader = MemoryInventoryReader(session, layout, inventory_layout)
    inventory_panel = InventoryReader(config.inventory_templates) if config.healing_enabled else None
    health = session.request("health")
    if not observe_only and health.get("input_revision", 0) < 5:
        raise ValueError("Worker needs input revision 5 for cursor and geometry checks")
    hwnd = health["window"]["hwnd"]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output / "trial.sqlite3")
    db.execute("CREATE TABLE IF NOT EXISTS events (time REAL, event TEXT, payload TEXT)")
    def event(name, **payload):
        db.execute("INSERT INTO events VALUES (?,?,?)", (time.time(), name, json.dumps(payload)))
        db.commit()
        logger.info(name, extra={"fields": payload})
    camera = DesktopFrames(hwnd, config.client_size, config.capture_output, config.capture_origin)
    started, last_action = time.monotonic(), 0
    attempts, paused, last_pause_key = 0, False, False
    healing, last_heal, verified_heals = None, -float("inf"), 0
    focus_paused, waypoint, moving, movement_failures = False, 0, None, 0
    pending_attack, confirmed_kills, attack_failures = None, 0, 0
    inventory_failures = 0
    stale_observations = 0
    reloading, verified_reloads = None, 0
    picking_up, verified_pickups, loot_attempts = None, 0, {}
    reason = "duration_limit"
    previous_level = None
    recovery, deaths, verified_revivals = None, 0, 0
    event("trial_started", observe_only=observe_only, seconds=seconds,
          monster=config.monster,
          confirmed_kills=None, confirmed_pickups=None)
    try:
        while time.monotonic() - started < seconds:
            if (output / "stop.request").exists():
                reason = "requested_stop"
                break
            if win32api.GetAsyncKeyState(0x7B) & 0x8000:  # F12
                reason = "emergency_stop"
                break
            pause_key = bool(win32api.GetAsyncKeyState(0x7A) & 0x8000)
            if pause_key and not last_pause_key:
                paused = not paused
                event("paused" if paused else "resumed")
            last_pause_key = pause_key
            if paused:
                time.sleep(.05)
                continue
            try:
                camera.geometry()
            except CaptureUnavailable as error:
                if not focus_paused:
                    event("paused", reason=str(error))
                    focus_paused = True
                time.sleep(.1)
                continue
            if focus_paused:
                event("resumed", reason="game_observation_available")
                focus_paused = False
            try:
                inventory = inventory_reader.read()
                inventory_failures = 0
            except ValueError as error:
                inventory_failures += 1
                event("inventory_retry", detail=str(error), failures=inventory_failures)
                if inventory_failures >= 3:
                    raise
                time.sleep(.03)
                continue
            supply_reason = supply_stop_reason(inventory, config, time.monotonic())
            addresses = resolve_player(session, layout)
            sample = session.request("sample", {"fields": [
                {"name": name, "address": hex(addresses[name]), "kind": kind}
                for name, kind in (("name", "utf8"), ("position", "xy_u32"), ("max_hp", "u32"),
                                   ("kill_counter", "u32"), ("level", "u32"), ("map", "u32"))]})
            fields = {field["name"]: field["value"] for field in sample["fields"]}
            if fields["name"] != config.character:
                raise ValueError("Character identity changed")
            if fields["map"][0] != config.expected_map:
                reason = "map_changed"
                break
            x, y = fields["position"]
            l, t, r, b = config.boundary
            if recovery is None and not (l <= x <= r and t <= y <= b):
                reason = "outside_trial_boundary"
                break
            try:
                frame = camera.read()
            except CaptureUnavailable:
                continue
            hp = health_ratio(frame.image)
            level = fields["level"][0]
            if not 1 <= level <= 140 or (previous_level is not None and level < previous_level):
                raise ValueError("Character level became invalid or decreased")
            if level != previous_level:
                from conquest.progression import review_plan
                plan = yaml.safe_load(Path(config.leveling_plan).read_text())
                event("level_observed" if previous_level is None else "level_changed",
                      previous_level=previous_level, character_level=level,
                      **review_plan(level, plan))
                previous_level = level
            event("health_observation", health_ratio=hp, max_hp=fields["max_hp"][0],
                  character_level=fields["level"][0], position=[x, y],
                  ammo=inventory.equipped_ammo.amount if inventory.equipped_ammo else 0,
                  potions=inventory.count(config.potion_type))
            if time.monotonic() - inventory.started_at > .85:
                stale_observations += 1
                event("state_retry", reason="observation_expired", failures=stale_observations)
                if stale_observations >= 3:
                    reason = "persistently_stale_state"
                    break
                continue
            stale_observations = 0
            if hp <= 0 and not config.recovery.enabled:
                reason = "death"
                break
            absolute_health = hp * fields["max_hp"][0]

            def dispatch(point, button="left", control=False):
                if (camera.geometry() != frame.origin or time.monotonic() - frame.timestamp > .35
                        or time.monotonic() - inventory.started_at > 1
                        or win32api.GetAsyncKeyState(0x7B) & 0x8000):
                    raise ValueError("Action expired or foreground changed")
                return session.request("foreground-click", {
                    "guard": {"name_address": hex(addresses["name"]), "name": config.character,
                              "hp_address": hex(addresses["max_hp"]), "max_hp": fields["max_hp"][0]},
                    "point": list(point), "button": button, "control": control, "expected_size": list(config.client_size),
                    "require_foreground": True, "expected_origin": list(frame.origin)})

            if hp <= 0 and (recovery is None or recovery.phase == RecoveryPhase.RETURNING):
                deaths += 1
                healing = reloading = picking_up = moving = pending_attack = None
                cv2.imwrite(str(output / "death.png"),frame.image)
                event("death_detected", deaths=deaths, position=[x,y], health_ratio=hp)
                if deaths > config.recovery.death_limit:
                    reason = "death_recovery_limit"
                    break
                recovery = DeathRecovery(config.recovery,(x,y),time.monotonic(),config.boundary)
                if not config.recovery.revive_template:
                    event("revive_calibration_required", image=str(output / "death.png"))

            def recover_step(button=None):
                nonlocal verified_revivals
                was_verified, old_phase = recovery.revival_verified, recovery.phase
                action = recovery.decide(health=hp,position=(x,y),map_id=fields["map"][0],
                                         timestamp=frame.timestamp,now=time.monotonic(),button=button)
                if recovery.revival_verified and not was_verified:
                    verified_revivals += 1
                    event("revival_verified", position=[x,y],health_ratio=hp,total=verified_revivals)
                if recovery.phase != old_phase:
                    event("recovery_state",state=str(recovery.phase),reason=recovery.reason)
                if action and not observe_only:
                    if action.kind == "return_walk" and (supply_reason or hp < config.heal_below):
                        return  # Resolve supplies/healing before the first return step too.
                    if time.monotonic() > action.expires_at:
                        raise ValueError("Recovery action expired")
                    dispatch(action.point)  # Walk across the raised gate; do not jump.
                    event("recovery_action",action=action.kind,point=action.point,
                          waypoint=recovery.waypoint,attempt=recovery.attempts)

            if recovery is not None and recovery.phase in (RecoveryPhase.WAITING,RecoveryPhase.REVIVING):
                button = None
                # First-death calibration may be supplied while the bot waits.
                # No coordinates or image are guessed when it is absent.
                if not config.recovery.revive_template:
                    updated = TrialConfig.model_validate(yaml.safe_load(Path(config_path).read_text())).recovery
                    if updated.enabled and updated.revive_template:
                        config.recovery = recovery.config = updated
                if hp <= 0 and config.recovery.revive_template:
                    template_image = cv2.imread(config.recovery.revive_template)
                    button = revive_button(frame.image,template_image,config.recovery.revive_region)
                recover_step(button)
                if recovery.phase == RecoveryPhase.FAILED:
                    reason = recovery.reason
                    break
                if recovery.phase == RecoveryPhase.COMPLETE:
                    event("recovery_complete",position=[x,y])
                    recovery = None
                time.sleep(.05)
                continue

            if supply_reason and not (healing and supply_reason == "potions_exhausted"):
                reason = supply_reason
                event("supply_stop", reason=reason)
                break

            if healing:
                outcome = healing.outcome(inventory, absolute_health, time.monotonic())
                if outcome == "waiting":
                    time.sleep(.05)
                    continue
                event("healing_outcome", outcome=outcome, item_uid=healing.uid, health_ratio=hp)
                if outcome != "verified":
                    reason = "healing_not_verified"
                    break
                verified_heals += 1
                healing = None
                if inventory.count(config.potion_type) <= 0:
                    reason = "potions_exhausted"
                    break
            if config.healing_enabled and hp < config.heal_below:
                if time.monotonic() - last_heal < config.potion_cooldown:
                    time.sleep(.05)
                    continue
                potion = next((i for i in inventory.items if i.type_id == config.potion_type and i.amount > 0), None)
                if potion is None:
                    reason = "potions_exhausted"
                    break
                point = None
                if config.potion_key is None:
                    panel = inventory_panel.read(frame.image)
                    point = potion_point(potion, panel.potions)
                if observe_only:
                    event("healing_needed", health_ratio=hp, item_uid=potion.uid)
                    time.sleep(.05)
                    continue
                issued = time.monotonic()
                if config.potion_key is None:
                    dispatch(point, "right")
                else:
                    if time.monotonic()-frame.timestamp > .35 or camera.geometry()!=frame.origin:
                        raise ValueError("Healing observation expired")
                    session.request("foreground-key", {
                        "guard": {"name_address":hex(addresses["name"]),"name":config.character,
                                  "hp_address":hex(addresses["max_hp"]),"max_hp":fields["max_hp"][0]},
                        "vk":config.potion_key,"expected_size":list(config.client_size),"require_foreground":True})
                healing = HealingAttempt(potion.uid, potion.amount, absolute_health, issued,
                                         config.potion_type, inventory.count(config.potion_type))
                last_heal = issued
                event("healing_attempt", item_uid=potion.uid, point=point, health_ratio=hp)
                continue
            if hp < config.minimum_health:
                reason = "low_health"
                cv2.imwrite(str(output / "stop.png"), frame.image)
                event("low_health", health_ratio=hp)
                break
            if recovery is not None:
                recover_step()
                if recovery.phase == RecoveryPhase.FAILED:
                    reason = recovery.reason
                    break
                if recovery.phase == RecoveryPhase.COMPLETE:
                    event("recovery_complete",position=[x,y])
                    recovery = None
                time.sleep(.05)
                continue
            if reloading:
                outcome = reloading.outcome(inventory, config.ammo_type, time.monotonic())
                if outcome == "waiting":
                    time.sleep(.05)
                    continue
                event("reload_outcome", outcome=outcome,
                      ammo=inventory.equipped_ammo.amount if inventory.equipped_ammo else 0)
                if outcome != "verified":
                    reason = "reload_not_verified"
                    break
                verified_reloads += 1
                reloading = None
                pending_attack = None
                last_action = 0
            if inventory.equipped_ammo is None or inventory.equipped_ammo.amount <= 0:
                reserve = tuple((i.uid,i.amount) for i in inventory.items
                                if i.type_id == config.ammo_type and i.amount > 0)
                if not reserve or config.ammo_key is None:
                    reason = "ammo_unavailable"
                    break
                if observe_only:
                    event("reload_needed")
                    time.sleep(.05)
                    continue
                if time.monotonic()-frame.timestamp > .35 or camera.geometry()!=frame.origin:
                    raise ValueError("Reload observation expired")
                issued = time.monotonic()
                session.request("foreground-key", {
                    "guard": {"name_address":hex(addresses["name"]),"name":config.character,
                              "hp_address":hex(addresses["max_hp"]),"max_hp":fields["max_hp"][0]},
                    "vk":config.ammo_key,"expected_size":list(config.client_size),"require_foreground":True})
                reloading = ReloadAttempt(inventory.equipped_ammo.uid if inventory.equipped_ammo else None,
                                          reserve, issued)
                event("reload_attempt", reserve_stacks=len(reserve))
                continue
            if moving:
                before_position, issued = moving
                if time.monotonic() - issued < 1.5:
                    time.sleep(.05)
                    continue
                changed = math.dist((x,y), before_position) > .5
                movement_failures = 0 if changed else movement_failures + 1
                event("movement_verified" if changed else "movement_stuck", position=[x,y])
                moving = None
                if movement_failures >= 3:
                    reason = "movement_failure_limit"
                    break
            if pending_attack:
                before_counter, issued, previous_ammo = pending_attack
                counter = fields["kill_counter"][0]
                if counter > before_counter:
                    increment=counter-before_counter
                    if increment > 10:
                        reason="kill_counter_discontinuity"
                        break
                    confirmed_kills += increment
                    event("kill_verified", count=increment, total=confirmed_kills, counter=counter,
                          character_level=fields["level"][0], ammo=inventory.equipped_ammo.amount)
                    pending_attack=None
                    attack_failures=0
                    last_action=0  # Retarget immediately after observed death.
                elif time.monotonic()-issued < 3:
                    time.sleep(.03)
                    continue
                elif inventory.equipped_ammo.amount < previous_ammo:
                    pending_attack=(before_counter,time.monotonic(),inventory.equipped_ammo.amount)
                    event("attack_progress", ammo=inventory.equipped_ammo.amount)
                    continue
                else:
                    pending_attack=None
                    attack_failures+=1
                    event("attack_progress_missing", failures=attack_failures)
                    if attack_failures>=3:
                        reason="attack_progress_failure_limit"
                        break
            if picking_up:
                outcome = picking_up.outcome(inventory,time.monotonic())
                if outcome == "waiting":
                    time.sleep(.05)
                    continue
                if outcome == "verified":
                    verified_pickups += 1
                event("pickup_outcome",outcome=outcome,item_name=picking_up.drop.name,total=verified_pickups,
                      position=picking_up.drop.position,potions=inventory.count(config.potion_type))
                picking_up = None
            if loot_template is not None and not observe_only:
                drops = nearby_drops(frame.image,loot_template,(x,y),config.boundary,config.loot_distance_pixels)
                for drop in drops:
                    key = (drop.name,drop.position)
                    attempts_here, last_seen = loot_attempts.get(key,(0,0))
                    if time.monotonic()-last_seen>60:
                        attempts_here = 0
                    if attempts_here >= config.loot_attempt_limit:
                        continue
                    issued=time.monotonic()
                    dispatch(drop.point)
                    picking_up = PickupAttempt(drop,frozenset(i.uid for i in inventory.items),
                                               inventory.count(drop.type_id),issued)
                    loot_attempts[key] = (attempts_here+1,issued)
                    event("pickup_attempt",item_name=drop.name,point=drop.point,position=drop.position)
                    break
                if picking_up:
                    continue
            observed = targets(frame.image, template, .94, config.monster)
            target = choose_target(observed, hp, (x, y), config, time.monotonic() - frame.timestamp)
            if time.monotonic() - last_action >= config.interval:
                event("observation", position=[x, y], health_ratio=hp, targets=len(observed),
                      ammo=inventory.equipped_ammo.amount, potions=inventory.count(config.potion_type),
                      occupied_slots=len(inventory.items), inventory_source="read_only_memory")
                if target and not observe_only:
                    # Geometry and physical emergency stop are checked again at
                    # dispatch. A fresh visual HP check is essential: max_hp is
                    # only an identity/range guard for this older worker revision.
                    if (camera.geometry() != frame.origin or
                            time.monotonic() - frame.timestamp > .35 or
                            supply_stop_reason(inventory, config, time.monotonic()) is not None or
                            win32api.GetAsyncKeyState(0x7B) & 0x8000):
                        raise ValueError("Action expired or foreground changed")
                    issued=time.monotonic()
                    dispatch((target.x, target.y))
                    pending_attack=(fields["kill_counter"][0],issued,inventory.equipped_ammo.amount)
                    attempts += 1
                    event("attack_attempt", number=attempts, point=[target.x, target.y],
                          confidence=target.confidence, outcome="awaiting_observation")
                    cv2.imwrite(str(output / f"attempt-{attempts:02d}.png"), frame.image)
                    if attempts >= config.maximum_actions:
                        reason = "action_limit"
                        break
                elif config.route and not observe_only:
                    if math.dist((x,y), config.route[waypoint]) <= 2:
                        waypoint = (waypoint + 1) % len(config.route)
                    dx,dy=config.route[waypoint][0]-x,config.route[waypoint][1]-y
                    scale=min(1,6/max(abs(dx),abs(dy),1))
                    dx,dy=dx*scale,dy*scale
                    if movement_failures == 1: dx+=1; dy-=1
                    if movement_failures == 2: dx-=1; dy+=1
                    dx,dy = visible_movement_delta(dx,dy)
                    if not (l <= x+dx <= r and t <= y+dy <= b):
                        reason="reposition_outside_boundary"
                        break
                    point=(round(792+(dx-dy)*32), round(432+(dx+dy)*16))
                    if not (100<point[0]<1100 and 140<point[1]<550):
                        reason="movement_point_obscured"
                        break
                    issued=time.monotonic()
                    dispatch(point, control=True)
                    moving=((x,y),issued)
                    event("movement_attempt", point=point, waypoint=config.route[waypoint])
                last_action = time.monotonic()
            time.sleep(.08)
    except (ValueError, OSError, RuntimeError) as error:
        reason = "observation_or_input_failure"
        event("trial_error", detail=str(error))
    except KeyboardInterrupt:
        reason = "interrupt"
    finally:
        camera.close()
        event("trial_stopped", reason=reason, duration=time.monotonic() - started,
              attack_attempts=attempts, verified_heals=verified_heals,
              verified_reloads=verified_reloads,
              deaths=deaths,verified_revivals=verified_revivals,
              confirmed_kills=confirmed_kills, confirmed_pickups=verified_pickups)
        db.close()
    return {"reason": reason, "attack_attempts": attempts, "verified_heals": verified_heals,
            "deaths":deaths,"verified_revivals":verified_revivals,
            "confirmed_kills":confirmed_kills,"confirmed_pickups":verified_pickups,"qualified_farmer": False}
