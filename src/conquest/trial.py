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
from conquest.patrol_search import PatrolSearchConfig, AdaptivePatrol

from pydantic import BaseModel, ConfigDict, Field

from conquest.addressing import PlayerLayout, WorkerPointerSession, resolve_player
from conquest.farmer_profile import CombatSpeed,load_combat_speed
from conquest.capture import DesktopFrames, CaptureUnavailable, Frame
from conquest.vision import health_ratio, targets
from conquest.memory_inventory import InventoryLayout, MemoryInventoryReader
from conquest.inventory import InventoryReader
from conquest.healing import HealingAttempt, potion_point
from conquest.reloading import ReloadAttempt
from conquest.looting import PickupAttempt, nearby_drops
from conquest.recovery import RecoveryConfig, DeathRecovery, RecoveryPhase, revive_button


def scatter_receipt_ready(elapsed, previous_ammo, current_ammo, minimum_seconds=.2, minimum_arrows=3):
    """A profile-qualified consumption permits movement; recast/minimum stock stay separate."""
    return elapsed>=minimum_seconds and minimum_arrows<=previous_ammo-current_ammo


class TrialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_mode: Literal["memory_only", "legacy_visual"] = "memory_only"
    character: str
    combat_speed: CombatSpeed | None = None
    player_profile: str
    inventory_profile: str
    template: str
    kite_when_surrounded: bool = False
    attack_button: Literal["left", "right"] = "left"
    adaptive_scatter: bool = False
    jump_scatter: bool = False
    single_isolated_targets: bool = False
    single_attack_range_tiles: int = Field(default=12,ge=1,le=20)
    monster: Literal["Pheasant", "Turtledove", "Robin", "Apparition", "Poltergeist", "WingedSnake", "Bandit", "Ratling", "FireSpirit"] = "Pheasant"
    monster_variants: tuple[str,...] = ()
    client_size: tuple[int, int]
    boundary: tuple[int, int, int, int]
    patrol_search: PatrolSearchConfig = Field(default_factory=PatrolSearchConfig)
    minimum_health: float = Field(default=.4, gt=0, lt=1)
    interval: float = Field(default=.3, ge=.15, le=10)
    target_threshold: float = Field(default=.94,ge=.8,le=1)
    attack_progress_timeout: float = Field(default=3,ge=.8,le=5)
    maximum_actions: int = Field(default=15, ge=1, le=1000)
    attack_range_tiles: int = Field(default=16,ge=1,le=20)
    max_distance_pixels: int = Field(default=380, ge=50, le=450)
    ammo_type: int = Field(default=1050000, gt=0)
    potion_type: int = Field(default=1000000, gt=0)
    healing_enabled: bool = True
    heal_below: float = Field(default=.4, gt=0, lt=1)
    potion_cooldown: float = Field(default=1, ge=.5, le=10)
    inventory_templates: str = "profiles/templates"
    capture_output: int = Field(default=0, ge=0, le=8)
    capture_origin: tuple[int, int] = (0, 0)
    player_anchor: tuple[int,int] = (792,432)
    approach_route: tuple[tuple[int,int], ...] = ()
    approach_boundary: tuple[int,int,int,int] | None = None
    hunting_anchor: tuple[int,int] | None = None
    route: tuple[tuple[int, int], ...] = ()
    potion_key: int | None = Field(default=None, ge=112, le=122)
    ammo_key: int | None = Field(default=None, ge=112, le=122)
    loot_allowlist: tuple[str, ...] = ()
    loot_distance_pixels: int = Field(default=180, ge=30, le=250)
    loot_attempt_limit: int = Field(default=2, ge=1, le=3)
    expected_map: int = 1002
    leveling_plan: str = "profiles/leveling-plan.yaml"
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)


def ammunition_per_attack(config):
    # User-configured Scatter minimum; single shots can still use one arrow.
    return 3 if (getattr(config,'jump_scatter',False) or getattr(config,'attack_button',None)=='right') else 1


def ammunition_reload_needed(inventory, config, *, proactive=False):
    ammo=inventory.equipped_ammo
    # Do not leave 23–25 arrows behind in every pack when patrolling.
    return ammo is None or ammo.type_id != config.ammo_type or ammo.amount < ammunition_per_attack(config)


def supply_stop_reason(inventory, config, now):
    if not 0 <= now - inventory.started_at <= 1:
        return "stale_inventory"
    if len(inventory.items) >= inventory.capacity:
        return "inventory_full"
    ammo = inventory.equipped_ammo
    if ammo is None or ammo.type_id != config.ammo_type or ammo.amount < ammunition_per_attack(config):
        if not (getattr(config, "ammo_key", None) is not None
                and any(i.type_id==config.ammo_type and i.amount>=ammunition_per_attack(config) for i in inventory.items)):
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
        anchor = getattr(config, 'player_anchor', (792,432))
        dx, dy = target.x - anchor[0], target.y - anchor[1]
        map_x = position[0] + (dx / 32 + dy / 16) / 2
        map_y = position[1] + (dy / 16 - dx / 32) / 2
        distance = dx * dx + dy * dy
        in_range=distance < config.max_distance_pixels ** 2
        if target.world_position is not None:
            map_x,map_y=target.world_position
            in_range=max(abs(map_x-position[0]),abs(map_y-position[1]))<=config.attack_range_tiles
        if (target.name in (config.monster,*getattr(config,'monster_variants',())) and in_range
                and left <= map_x <= right and top <= map_y <= bottom):
            candidates.append((distance, target))
    return min(candidates, key=lambda pair: pair[0])[1] if candidates else None


def visible_movement_delta(dx, dy, *, horizontal_limit=280, vertical_limit=110):
    """Shorten a jump to stay in the calibrated unobstructed play area."""
    scale = min(1, horizontal_limit / max(abs((dx-dy)*32), 1),
                vertical_limit / max(abs((dx+dy)*16), 1))
    return dx * scale, dy * scale


def run_trial(config_path, info_path, output, seconds, logger, observe_only=False,
              *, session_override=None, camera_factory=None, config_override=None, supervisor=None):
    import win32api
    if not 1 <= seconds <= 1800:
        raise ValueError("Supervised run must last 1 to 1800 seconds")
    config = config_override or TrialConfig.model_validate(yaml.safe_load(Path(config_path).read_text()))
    speed=config.combat_speed or load_combat_speed(config.character)
    if supervisor:supervisor.combat_speed=speed
    if supervisor and config.attack_button=="right":
        supervisor.scatter_standoff=max(2,config.attack_range_tiles-2)
    if config.observation_mode == "memory_only" and supervisor is None:
        raise ValueError("Memory-only farming is not qualified: current HP, monster alive state, "
                         "ground loot and revival observations remain unresolved. No capture or input started.")
    if set(config.loot_allowlist) - {"Stancher"}:
        raise ValueError("Only the calibrated Stancher drop is supported")
    if any(not (config.boundary[0] <= x <= config.boundary[2] and config.boundary[1] <= y <= config.boundary[3]) for x,y in config.route):
        raise ValueError("Route waypoint is outside the farming boundary")
    if config.approach_route:
        if config.approach_boundary is None or any(not (
                config.approach_boundary[0] <= x <= config.approach_boundary[2]
                and config.approach_boundary[1] <= y <= config.approach_boundary[3])
                for x, y in config.approach_route):
            raise ValueError("Approach waypoints need a containing boundary")
        ax, ay = config.approach_route[-1]
        if not (config.boundary[0] <= ax <= config.boundary[2] and config.boundary[1] <= ay <= config.boundary[3]):
            raise ValueError("Approach must end inside the farming boundary")
    layout = PlayerLayout.model_validate(yaml.safe_load(Path(config.player_profile).read_text()))
    template = loot_template = None
    if supervisor is None:
        if config.monster not in ('Pheasant','Turtledove'):
            raise ValueError('This leveling target requires the memory-only native runner')
        template = cv2.imread(config.template, cv2.IMREAD_GRAYSCALE)
        expected_shape = {"Pheasant": (13,63), "Turtledove": (13,77)}[config.monster]
        if template is None or template.shape != expected_shape:
            raise ValueError("Calibrated monster template is missing or changed")
        if config.loot_allowlist:
            loot_template = cv2.imread(str(Path(config.inventory_templates) / "stancher-name.png"),0)
            if loot_template is None or loot_template.shape != (13,63):
                raise ValueError("Calibrated Stancher label template is missing")
    elif config.healing_enabled and config.potion_key is None:
        raise ValueError("Memory farming requires a configured healing key")
    session = session_override or WorkerPointerSession(info_path, layout.expected_sha256)
    inventory_layout = InventoryLayout.model_validate(yaml.safe_load(Path(config.inventory_profile).read_text()))
    inventory_reader = MemoryInventoryReader(session, layout, inventory_layout)
    inventory_panel = InventoryReader(config.inventory_templates) if config.healing_enabled and supervisor is None else None
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
    camera = (camera_factory or DesktopFrames)(hwnd, config.client_size, config.capture_output, config.capture_origin)
    started, last_action = time.monotonic(), 0
    search=(AdaptivePatrol(config.boundary,config.patrol_search,started,
            (supervisor.recovery.terrain.width,supervisor.recovery.terrain.height)) if supervisor else None)
    attempts, paused, last_pause_key = 0, False, False
    healing, last_heal, verified_heals = None, -float("inf"), 0
    focus_paused, waypoint, moving, movement_failures = False, 0, None, 0
    from conquest.region_rotation import RegionRotation
    rotation=RegionRotation(config.patrol_search.regions) if supervisor and config.patrol_search.regions else None
    if rotation:config=config.model_copy(update={'route':rotation.region.patrol})
    navigation_waiting = False
    escape_settle_until = 0
    attack_interrupted = False
    last_kill_counter = None
    last_scatter_cast = -float("inf")
    # Start with a cast if a living selected target is already in range.
    # Only a successful cast earns the next ordinary hunting jump.
    scatter_jump_due = False
    pending_attack, confirmed_kills, attack_failures = None, 0, 0
    pending_attack_button=config.attack_button
    inventory_failures = 0
    stale_observations = 0
    reloading, verified_reloads = None, 0
    picking_up, verified_pickups, loot_attempts = None, 0, {}
    reason = "duration_limit"
    previous_level = None
    # Hosted sessions restart internally at duration/action limits. Their saved
    # approach belongs to an earlier departure; use the first fresh position
    # below to plan a return if outside the hunting area instead of replaying it.
    approaching = bool(config.approach_route) and not (supervisor and config.hunting_anchor)
    approach_waypoint = 0
    boundary_return_target = None
    recovery, deaths, verified_revivals = None, 0, 0
    event("trial_started", observe_only=observe_only, seconds=seconds,
          farmer=config.character,combat_speed=speed.model_dump(),
          monster=config.monster,
          rotation_regions=[region.name for region in config.patrol_search.regions],
          confirmed_kills=None, confirmed_pickups=None)
    try:
        while time.monotonic() - started < seconds:
            try:
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
                    # Manual play during F11 pause is not bot performance.
                    last_kill_counter = None
                    pending_attack = None
                    time.sleep(.05)
                    continue
                supervised = supervisor.observe() if supervisor else None
                for recovery_event in (supervised or {}).get('recovery_events',()):
                    payload=dict(recovery_event)
                    name=payload.pop('event')
                    if name=='death_detected':
                        deaths+=1
                        payload['deaths']=deaths
                    elif name=='revival_verified':
                        verified_revivals+=1
                        payload['total']=verified_revivals
                    event(name,**payload)
                if supervised and supervised.get('stop'):
                    reason='control_changed'
                    break
                if supervised and supervised['waiting']:
                    healing = reloading = picking_up = moving = pending_attack = None
                    time.sleep(.08)
                    continue
                defending=bool(supervised and supervised.get('defending'))
                if defending:
                    moving=picking_up=None
                try:
                    camera.geometry()
                except CaptureUnavailable as error:
                    if 'Mouse control is yours' in str(error):
                        # The user may kill a whole group before yielding input.
                        # Keep session totals and elapsed time, but start a fresh
                        # counter baseline once memory observation resumes.
                        last_kill_counter = None
                        pending_attack = None
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
                if supervisor and hasattr(supervisor,'observe_inventory') and not observe_only:
                    supervisor.observe_inventory(inventory)
                if (supervisor and not observe_only and hasattr(supervisor,'urgent_banking')
                        and supervisor.urgent_banking(inventory)):
                    reason='valuable_banking_required'
                    break
                supply_reason = supply_stop_reason(inventory, config, time.monotonic())
                if supervisor and supply_reason=='potions_exhausted':
                    # Do not strand a living character under attack without
                    # defending just because the last potion was consumed.
                    supply_reason=None
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
                if supervisor and speed.coherent_projection and hasattr(supervisor,'player_projection'):
                    (x,y),anchor=supervisor.player_projection()
                    fields['position']=[x,y]
                    config=config.model_copy(update={'player_anchor':anchor})
                elif supervisor and hasattr(supervisor,"player_anchor"):
                    config=config.model_copy(update={"player_anchor":supervisor.player_anchor((x,y))})
                l, t, r, b = config.boundary
                if approaching and l <= x <= r and t <= y <= b:
                    if supervisor and hasattr(supervisor,'finish_runback'):supervisor.finish_runback('arrived')
                    approaching, moving = False, None
                    if boundary_return_target is not None:
                        event("boundary_return_completed",position=[x,y])
                        boundary_return_target=None
                    event("farming_area_reached", position=[x, y])
                if approaching:
                    l, t, r, b = config.approach_boundary
                if recovery is None and not (l <= x <= r and t <= y <= b):
                    if supervisor is None or observe_only or config.hunting_anchor is None:
                        reason = "outside_trial_boundary"
                        break
                    from conquest.navigation import plan_hunting_return
                    approach,travel_boundary=plan_hunting_return(supervisor.recovery.terrain,
                        (x,y),config.hunting_anchor,config.boundary)
                    config=config.model_copy(update={'approach_route':approach,
                        'approach_boundary':travel_boundary})
                    approaching,approach_waypoint=True,0
                    boundary_return_target=tuple(config.hunting_anchor)
                    moving=picking_up=pending_attack=None
                    movement_failures=attack_failures=0
                    l,t,r,b=travel_boundary
                    event('boundary_return_started',position=[x,y],destination=list(boundary_return_target),
                        travel_boundary=travel_boundary)
                    if hasattr(supervisor,'start_runback'):supervisor.start_runback(boundary_return_target)
                    # Resample after path planning before healing or movement input.
                    continue
                try:
                    frame = Frame(time.monotonic(),None,camera.geometry()) if supervisor else camera.read()
                except CaptureUnavailable:
                    continue
                hp = supervised['health_ratio'] if supervised else health_ratio(frame.image, config.client_size)
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
                if not 0<=time.monotonic() - inventory.started_at<=.85:
                    stale_observations += 1
                    event("state_retry", reason="observation_expired", failures=stale_observations)
                    if stale_observations >= 3 and supervisor is None:
                        reason = "persistently_stale_state"
                        break
                    # The hosted observer checks life/revival at the top of
                    # every iteration. A slow read must not shut that owner
                    # down and leave the character unattended. No combat,
                    # movement or healing input uses this expired snapshot.
                    if supervisor and stale_observations==3:
                        event('state_recovery_wait',reason='observation_expired',
                              activity='Waiting for fresh memory; life recovery remains active')
                    time.sleep(.03)
                    continue
                if stale_observations>=3 and supervisor:
                    event('state_recovery_resumed',failures=stale_observations)
                stale_observations = 0
                if hp <= 0 and not config.recovery.enabled:
                    reason = "death"
                    break
                absolute_health = hp * fields["max_hp"][0]

                def dispatch(point, button="left", control=False, target=None, drop=None, *, ui=False):
                    if supervisor and not ui:
                        from conquest.viewport import require_world_point
                        require_world_point(point,config.client_size)
                    if (camera.geometry() != frame.origin or time.monotonic() - frame.timestamp > .35
                            or time.monotonic() - inventory.started_at > 1
                            or win32api.GetAsyncKeyState(0x7B) & 0x8000):
                        if supervisor:
                            raise CaptureUnavailable("Action expired or foreground changed; reobserving")
                        raise ValueError("Action expired or foreground changed")
                    body = {
                        "guard": {"name_address": hex(addresses["name"]), "name": config.character,
                                  "hp_address": hex(addresses["max_hp"]), "max_hp": fields["max_hp"][0]},
                        "point": list(point), "button": button, "control": control, "expected_size": list(config.client_size),
                        "require_foreground": True, "expected_origin": list(frame.origin)}
                    return (supervisor.dispatch(lambda:session.request('foreground-click',body),target=target,drop=drop,
                                                expected_position=(x,y) if target is None and drop is None else None,
                                                retarget=(lambda fresh:body.update(point=[fresh.x,fresh.y])) if target else None,
                                                attack_range=config.attack_range_tiles if button=='right' else config.single_attack_range_tiles)
                            if supervisor else session.request('foreground-click',body))

                def dispatch_key(body):
                    return (supervisor.dispatch(lambda:session.request('foreground-key',body))
                            if supervisor else session.request('foreground-key',body))

                counter=fields["kill_counter"][0]
                if last_kill_counter is None or counter<last_kill_counter:
                    last_kill_counter=counter
                elif counter>last_kill_counter:
                    increment=counter-last_kill_counter
                    if increment>(32 if config.attack_button=="right" else 10):
                        if not (supervisor and speed.counter_gap_recovery):
                            reason='kill_counter_discontinuity'
                            break
                        check=session.request('sample',{'fields':[
                            {'name':'name','address':hex(addresses['name']),'kind':'utf8'},
                            {'name':'kill_counter','address':hex(addresses['kill_counter']),'kind':'u32'}]})
                        checked={field['name']:field['value'] for field in check['fields']}
                        if checked['name']!=config.character:
                            raise ValueError('Character identity changed during counter verification')
                        if checked['kill_counter']!=[counter]:
                            raise CaptureUnavailable('Kill counter changed during gap verification; reobserving')
                        event('kill_counter_gap',previous=last_kill_counter,counter=counter,
                              unverified_increment=increment,verified_total=confirmed_kills,
                              action='Excluded from verified totals; continuing with stable counter')
                        last_kill_counter=counter
                        # Do not treat an unqualified count as a kill receipt.
                        # Pending combat still uses its normal ammunition/HP feedback.
                    else:
                        confirmed_kills+=increment
                        last_kill_counter=counter
                        event('kill_verified',count=increment,total=confirmed_kills,counter=counter,
                              character_level=fields['level'][0],
                              ammo=inventory.equipped_ammo.amount if inventory.equipped_ammo else 0,
                              kills_per_hour=confirmed_kills*3600/max(time.monotonic()-started,.001))
                        if supervisor:supervisor.finish_target('kill_counter_increased')
                        pending_attack=None
                        attack_interrupted=False
                        attack_failures=0
                        last_action=0

                escape_observation=None
                escape_observed_at=None
                if (supervisor and config.kite_when_surrounded and not observe_only
                        and time.monotonic()>=escape_settle_until
                        and time.monotonic()>=getattr(supervisor,'escape_ready_at',0)):
                    escape_observed_at=time.monotonic()
                    escape_observation=supervisor.memory_targets(config.client_size)
                    escape=supervisor.ranged_escape((x,y),(l,t,r,b))
                    if escape is not None:
                        dx,dy=escape[0]-x,escape[1]-y
                        dispatch((round(config.player_anchor[0]+(dx-dy)*32),
                                  round(config.player_anchor[1]+(dx+dy)*16)),control=True)
                        # Retain the counter checkpoint for an in-flight kill,
                        # but don't wait for the interrupted auto-attack to finish.
                        attack_interrupted=pending_attack is not None
                        moving=picking_up=None
                        scatter_jump_due=False
                        supervisor.patrol_chase=None
                        supervisor.escape_ready_at=time.monotonic()+.9
                        supervisor.escape_damage_consumed_at=getattr(supervisor,'last_damage_at',-float('inf'))
                        escape_settle_until=time.monotonic()+.55
                        last_action=time.monotonic()
                        event('ranged_escape',source=[x,y],destination=escape,
                              interrupted_attack=attack_interrupted,**getattr(supervisor,'escape_context',{}))
                        continue

                if supervisor is None and hp <= 0 and (recovery is None or recovery.phase == RecoveryPhase.RETURNING):
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

                if (supervisor and hasattr(supervisor,'discard_step') and not observe_only
                        and (getattr(supervisor,'discard_panel_pending',False) or
                             (hp>=max(.8,config.heal_below) and not defending
                              and not (healing or reloading or moving or pending_attack)))):
                    if supervisor.discard_step(inventory):
                        event('inventory_cleanup_complete')
                        continue  # Resample geometry, life and inventory after closing the bag.

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
                        if supervisor and hp >= .99:
                            # A level-up can restore HP before F1 consumes a potion.
                            # Fresh full health permits combat; do not claim a heal.
                            event('healing_unneeded',reason='health_restored_without_verified_consumption')
                        else:
                            reason = "healing_not_verified"
                            break
                    else:
                        verified_heals += 1
                    healing = None
                    if inventory.count(config.potion_type) <= 0 and not supervisor:
                        reason = "potions_exhausted"
                        break
                healing_threshold=max(config.heal_below,.75) if supervisor and (approaching or supervised.get('returning_after_revive')) else config.heal_below
                if config.healing_enabled and hp < healing_threshold and (not supervisor or inventory.count(config.potion_type)>0):
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
                    if supervisor and config.potion_type==1000020 and hasattr(supervisor,'heal_potion'):
                        receipt=supervisor.heal_potion(potion.uid)
                        last_heal=time.monotonic()
                        if receipt['consumed']:
                            verified_heals+=1
                            event('healing_outcome',outcome='verified',item_uid=potion.uid,receipt=receipt)
                        else:event('healing_unneeded',reason=receipt['reason'])
                        continue
                    if config.potion_key is None:
                        dispatch(point, "right",ui=True)
                    else:
                        if time.monotonic()-frame.timestamp > .35 or camera.geometry()!=frame.origin:
                            raise ValueError("Healing observation expired")
                        dispatch_key({
                            "guard": {"name_address":hex(addresses["name"]),"name":config.character,
                                      "hp_address":hex(addresses["max_hp"]),"max_hp":fields["max_hp"][0]},
                            "vk":config.potion_key,"expected_size":list(config.client_size),"require_foreground":True})
                    healing = HealingAttempt(potion.uid, potion.amount, absolute_health, issued,
                                             config.potion_type, inventory.count(config.potion_type))
                    last_heal = issued
                    event("healing_attempt", item_uid=potion.uid, point=point, health_ratio=hp)
                    continue
                if hp < config.minimum_health and not defending:
                    reason = "low_health"
                    if supervisor is None:
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
                if ammunition_reload_needed(inventory,config,proactive=bool(supervisor)):
                    reserve = tuple((i.uid,i.amount) for i in inventory.items
                                    if i.type_id == config.ammo_type and i.amount >= ammunition_per_attack(config))
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
                    if supervisor and hasattr(supervisor,'reload_arrows'):
                        supervisor.reload_arrows(inventory,config.ammo_type)
                    else:
                        dispatch_key({
                            "guard": {"name_address":hex(addresses["name"]),"name":config.character,
                                      "hp_address":hex(addresses["max_hp"]),"max_hp":fields["max_hp"][0]},
                            "vk":config.ammo_key,"expected_size":list(config.client_size),"require_foreground":True})
                    reloading = ReloadAttempt(inventory.equipped_ammo.uid if inventory.equipped_ammo else None,
                                              reserve, issued)
                    event("reload_attempt", reserve_stacks=len(reserve))
                    continue
                if (supervisor and config.attack_button=='right' and not observe_only
                        and hasattr(supervisor,'scatter_selection_step')
                        and supervisor.scatter_selection_step(lambda point:dispatch(point,ui=True))):
                    pending_attack=moving=None
                    last_action=0
                    continue
                if (supervisor and hasattr(supervisor,'xp_step') and not observe_only
                        and time.monotonic()>=escape_settle_until and supervisor.xp_step(lambda point:dispatch(point,ui=True))):
                    pending_attack=moving=None
                    last_action=0
                    continue
                if search and not approaching and not pending_attack and not defending:
                    expanded_config=search.expand_config(config,supervisor.recovery.terrain,time.monotonic(),
                        regional=speed.regional_search_expansion)
                    if expanded_config is not None:
                        config=expanded_config
                        l,t,r,b=config.boundary
                        if not config.patrol_search.regions:
                            waypoint=min(range(len(config.route)),key=lambda i:math.dist((x,y),config.route[i]))
                            moving=None
                        event('patrol_expanded',boundary=config.boundary,idle_seconds=config.patrol_search.idle_seconds,
                              expansion=search.expansions,patrol=config.route,
                              retained_regional_patrol=bool(config.patrol_search.regions))
                if moving:
                    before_position, issued, expected_position = moving
                    arrived=supervisor and math.dist((x,y),expected_position)<.5
                    jumped = supervisor and max(abs(a-b) for a,b in zip(before_position,expected_position)) >= 8
                    settle = (speed.jump_arrival_seconds if config.jump_scatter and jumped else .5) if arrived else 1.5
                    if supervisor and jumped and math.dist((x,y),before_position)<.5:
                        settle=min(settle,.8)
                    if approaching and getattr(getattr(supervisor,'runback_watch',None),'urgent',False):settle=min(settle,.5)
                    changed = math.dist((x,y), before_position) > .5
                    inflight_cast = (speed.scatter_during_jump and config.jump_scatter and jumped
                        and changed and not arrived and not approaching and not defending
                        and speed.jump_attack_guard_seconds<=time.monotonic()-issued<settle)
                    # A short/redirected landing still completes this movement.
                    # Mid-jump casting must not suppress verification forever.
                    if not inflight_cast:
                        if time.monotonic() - issued < settle:
                            time.sleep(.05)
                            continue
                        changed = math.dist((x,y), before_position) > .5
                        if changed and supervisor and hasattr(supervisor,'movement_succeeded'):
                            supervisor.movement_succeeded(before_position,(x,y),arrived=bool(arrived))
                        movement_failures = 0 if changed else movement_failures + 1
                        event("movement_verified" if changed else "movement_stuck", position=[x,y],
                              arrived=bool(arrived), elapsed=time.monotonic()-issued)
                        moving = None
                        if not changed and supervisor:
                            if getattr(supervisor,'runback_watch',None):supervisor.runback_watch.recovery()
                            supervisor.movement_failed((x,y),expected_position)
                            event('movement_recovery',position=[x,y],blocked_landing=list(expected_position),
                                  failures=movement_failures,activity='Blocked movement; taking another path')
                            # Keep healing, defense and fresh target observations active.
                            # The next movement is planned around the failed segment.
                            continue
                        if movement_failures >= 3 and not supervisor:
                            reason = "movement_failure_limit"
                            break
                if time.monotonic()<escape_settle_until:
                    time.sleep(.05)
                    continue
                if pending_attack:
                    before_counter, issued, previous_ammo = pending_attack
                    counter = fields["kill_counter"][0]
                    if (pending_attack_button=="right" and
                            (time.monotonic()-issued>=.8 or (supervisor and config.jump_scatter
                             and scatter_receipt_ready(time.monotonic()-issued,previous_ammo,inventory.equipped_ammo.amount,speed.scatter_receipt_seconds,speed.scatter_receipt_arrows)))):
                        pending_attack=None
                        last_action=0
                    elif attack_interrupted:
                        pending_attack=None
                        attack_interrupted=False
                        last_action=0
                    elif time.monotonic()-issued < config.attack_progress_timeout:
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
                        if supervisor:
                            supervisor.finish_target('no_attack_progress')
                        elif attack_failures>=3:
                            reason="attack_progress_failure_limit"
                            break
                strategy=None
                if supervisor and config.adaptive_scatter:
                    strategy=supervisor.attack_strategy()
                # Reuse only a successful same-iteration scan with its original age.
                reuse_scene=(supervisor and speed.scene_reuse_seconds>0
                    and escape_observation is not None and escape_observed_at is not None
                    and getattr(supervisor,'targets_observation_available',False)
                    and 0<=time.monotonic()-escape_observed_at<=speed.scene_reuse_seconds)
                if supervisor:
                    origin=camera.geometry()
                    if origin!=frame.origin:
                        raise CaptureUnavailable('Game origin changed before target scan')
                    frame=Frame(escape_observed_at if reuse_scene else time.monotonic(),None,origin)
                observed = (escape_observation if reuse_scene else
                            supervisor.memory_targets(config.client_size) if supervisor else
                            targets(frame.image, template, config.target_threshold, config.monster))
                if supervisor and time.monotonic()-frame.timestamp>.35:
                    raise CaptureUnavailable('Target scan expired; reobserving before attack or patrol')
                if strategy:strategy.observe(observed,time.monotonic())
                from conquest.attack_strategy import nearby_group_size
                isolated=(config.single_isolated_targets and
                          nearby_group_size(observed,(x,y),config.attack_range_tiles)<2)
                def mode(name):
                    return 'left' if isolated else strategy.button(name)
                attack_button=mode(config.monster) if strategy else config.attack_button
                attack_range=config.single_attack_range_tiles if strategy and attack_button=='left' else config.attack_range_tiles
                targeting_config=config.model_copy(update={'attack_range_tiles':attack_range})
                if supervisor and strategy:
                    # An untested variant may still need the shorter Scatter range.
                    family_ranges=[config.single_attack_range_tiles if mode(name)=='left' else config.attack_range_tiles
                                   for name in (config.monster,*config.monster_variants)]
                    supervisor.scatter_standoff=max(2,min(family_ranges)-2)
                if strategy:
                    choices=[]
                    for candidate in observed:
                        button=mode(candidate.name)
                        radius=config.single_attack_range_tiles if button=='left' else config.attack_range_tiles
                        valid=choose_target([candidate],hp,(x,y),config.model_copy(update={'attack_range_tiles':radius}),
                                            time.monotonic()-frame.timestamp)
                        if valid:choices.append(valid)
                    target=min(choices,key=lambda t:(t.x-config.player_anchor[0])**2+(t.y-config.player_anchor[1])**2,default=None)
                    if target:
                        attack_button=mode(target.name)
                        supervisor.scatter_standoff=max(2,(config.single_attack_range_tiles if attack_button=='left' else config.attack_range_tiles)-2)
                else:
                    target = choose_target(observed, hp, (x, y), targeting_config, time.monotonic() - frame.timestamp)
                if supervisor and target is None and not getattr(supervisor,'targets_observation_available',True):
                    # A changing memory scene is not evidence that the group is dead.
                    if rotation:rotation.observe(time.monotonic(),(x,y),False,available=False)
                    time.sleep(.05)
                    continue
                if rotation and not approaching and not defending and not observe_only:
                    occupied=target is not None or any(
                        m.current_hp and m.current_hp>0 and
                        max(abs(a-b) for a,b in zip(m.position,(x,y)))<=attack_range
                        for m in getattr(supervisor,'chase_monsters',()))
                    changed=rotation.observe(time.monotonic(),(x,y),occupied,
                        available=getattr(supervisor,'targets_observation_available',True))
                    if changed:
                        config=config.model_copy(update={'route':rotation.region.patrol})
                        waypoint=0;moving=None;supervisor.patrol_chase=None
                        event('region_rotated',**changed)
                # Use the actual attack mode: an adaptive strategy may have switched
                # to single attacks. Its mere presence must not suppress all loot.
                hold_for_scatter=attack_button=="right" and target is not None
                if supervisor and not observe_only and not approaching and not defending:
                    supervisor.loot_boundary=(l,t,r,b)
                    collecting=False
                    if not hold_for_scatter:
                        collecting=supervisor.loot_step(inventory,(x,y),dispatch)
                    elif hasattr(supervisor,'combat_loot_step'):
                        collecting=supervisor.combat_loot_step(inventory,(x,y),dispatch)
                    if collecting:
                        time.sleep(.05)
                        continue
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
                if loot_template is not None and not observe_only and not approaching and not defending:
                    drops = (nearby_drops(frame.image,loot_template,(x,y),config.boundary,config.loot_distance_pixels,
                             calibrated_size=config.client_size,player_anchor=config.player_anchor) if supervisor else
                             nearby_drops(frame.image,loot_template,(x,y),config.boundary,config.loot_distance_pixels))
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
                if defending and time.monotonic()-frame.timestamp<=.35:
                    close=[t for t in observed if math.dist((t.x,t.y),config.player_anchor)<=180]
                    if close:
                        target=min(close,key=lambda t:math.dist((t.x,t.y),config.player_anchor))
                scatter_destination=None
                if (supervisor and config.jump_scatter and attack_button=='right' and (scatter_jump_due or target is None)
                        and not approaching and not defending and not observe_only and not moving
                        and time.monotonic()-last_action>=config.interval):
                    from conquest.scatter_movement import scatter_landing,wounded_group_in_range
                    if not (target and attack_button=='right' and
                            wounded_group_in_range(supervisor,observed,(x,y),config.attack_range_tiles)):
                        scatter_boundary=rotation.region.boundary if rotation else (l,t,r,b)
                        scatter_destination=scatter_landing(supervisor,observed,(x,y),scatter_boundary,config.attack_range_tiles,
                            minimum_count=1 if scatter_jump_due or speed.cross_region_scatter else 3,
                            anchor=config.player_anchor,hunting_boundary=(l,t,r,b))
                    if scatter_destination is not None:target=None
                if time.monotonic() - last_action >= config.interval:
                    event("observation", position=[x, y], health_ratio=hp, targets=len(observed),
                          ammo=inventory.equipped_ammo.amount, potions=inventory.count(config.potion_type),
                          occupied_slots=len(inventory.items), inventory_source="read_only_memory")
                    if target and not observe_only:
                        # Geometry and physical emergency stop are checked again at
                        # dispatch. The hosted supervisor rechecks life and target ID
                        # in memory immediately before sending input.
                        if (camera.geometry() != frame.origin or
                                time.monotonic() - frame.timestamp > .35 or
                                (supply_stop_reason(inventory, config, time.monotonic()) is not None
                                 and not (supervisor and inventory.count(config.potion_type)==0
                                          and supply_stop_reason(inventory,config,time.monotonic())=='potions_exhausted')) or
                                win32api.GetAsyncKeyState(0x7B) & 0x8000):
                            if supervisor:
                                raise CaptureUnavailable("Attack observation expired or foreground changed; reobserving")
                            raise ValueError("Action expired or foreground changed")
                        # Defense may have selected another group from the same fresh scene.
                        attack_button=mode(target.name) if strategy else config.attack_button
                        if attack_button=="right" and time.monotonic()-last_scatter_cast<speed.scatter_recast_seconds:
                            time.sleep(.03)
                            continue
                        issued=time.monotonic()
                        dispatch((target.x, target.y),button=attack_button,target=target)
                        if supervisor:target=supervisor.last_target or target
                        if strategy:strategy.issued(target,attack_button,issued)
                        if attack_button=="right":
                            from conquest.scatter_movement import remember_scatter
                            if supervisor:remember_scatter(supervisor,observed)
                            last_scatter_cast=issued
                            scatter_jump_due=config.jump_scatter
                        pending_attack_button=attack_button
                        pending_attack=(fields["kill_counter"][0],issued,inventory.equipped_ammo.amount)
                        attack_interrupted=False
                        attempts += 1
                        if search:
                            search.attacked(issued)
                        event("attack_attempt", number=attempts, point=[target.x, target.y],button=attack_button,
                              during_jump=bool(moving), movement_elapsed=time.monotonic()-moving[1] if moving else None,
                              ability="Scatter" if attack_button=="right" else "Single attack",
                              entity_id=target.entity_id,object_address=target.object_address,
                              world_position=target.world_position,target_hp=target.current_hp,
                              distance_tiles=(max(abs(target.world_position[0]-x),abs(target.world_position[1]-y)) if target.world_position else None),
                              confidence=target.confidence, outcome="awaiting_observation")
                        if supervisor is None:
                            cv2.imwrite(str(output / f"attempt-{attempts:02d}.png"), frame.image)
                        if attempts >= config.maximum_actions:
                            reason = "action_limit"
                            break
                    elif (config.route or approaching or scatter_destination) and not observe_only and not moving:
                        if scatter_destination:
                            destination=scatter_destination
                        elif approaching:
                            if supervisor and config.hunting_anchor:
                                # Combat/loot can displace the player mid-return.
                                # Replan toward the goal from current memory, not
                                # back toward an old approach waypoint behind us.
                                destination=config.hunting_anchor
                            else:
                                while (approach_waypoint < len(config.approach_route)-1
                                       and math.dist((x,y), config.approach_route[approach_waypoint]) <= (0 if supervisor else 2)):
                                    approach_waypoint += 1
                                destination = config.approach_route[approach_waypoint]
                        else:
                            if math.dist((x,y), config.route[waypoint]) <= 2:
                                waypoint = (waypoint + 1) % len(config.route)
                            destination = config.route[waypoint]
                        if supervisor and scatter_destination is None:
                            if approaching:
                                destination=supervisor.patrol_step((x,y),destination,(l,t,r,b),chase=False)
                            else:
                                from conquest.patrol_search import patrol_step
                                patrol_boundary=(rotation.region.boundary if rotation and rotation.region.contains((x,y)) else (l,t,r,b))
                                destination,waypoint=patrol_step(supervisor,(x,y),config.route,waypoint,patrol_boundary,
                                    chase=not rotation or rotation.region.contains((x,y)))
                        dx,dy=destination[0]-x,destination[1]-y
                        scale=min(1,(12 if supervisor else 6)/max(abs(dx),abs(dy),1))
                        dx,dy=dx*scale,dy*scale
                        if movement_failures == 1 and not supervisor: dx+=1; dy-=1
                        if movement_failures == 2 and not supervisor: dx-=1; dy+=1
                        if supervisor:
                            from conquest.navigation import native_movement_delta
                            dx,dy=native_movement_delta(dx,dy,viewport=config.client_size)
                        else:
                            dx,dy=visible_movement_delta(dx,dy)
                        if not (l <= x+dx <= r and t <= y+dy <= b):
                            reason="reposition_outside_boundary"
                            break
                        point=(round(config.player_anchor[0]+(dx-dy)*32), round(config.player_anchor[1]+(dx+dy)*16))
                        from conquest.viewport import clear_scene,scene_bounds
                        if not (clear_scene(point,config.client_size) if supervisor else (80<point[0]<1100 and 140<point[1]<550)):
                            if supervisor:
                                from conquest.scene_input import visible_route_delta
                                shorter=visible_route_delta((dx,dy),config.player_anchor,scene_bounds(config.client_size))
                                if shorter is None:
                                    supervisor.movement_failed((x,y),(x+dx,y+dy))
                                    time.sleep(.08)
                                    continue
                                dx,dy=shorter
                                point=(round(config.player_anchor[0]+(dx-dy)*32),round(config.player_anchor[1]+(dx+dy)*16))
                            else:
                                reason="movement_point_obscured"
                                break
                        issued=time.monotonic()
                        long_jump=max(abs(dx),abs(dy))>=8-1e-6
                        dispatch(point, control=long_jump if supervisor else not approaching)
                        # Consume the jump only after input succeeds. A stale
                        # observation/focus retry must not skip its next cast.
                        scatter_jump_due=False
                        if supervisor and long_jump:
                            escape_settle_until=max(escape_settle_until,issued+(speed.jump_attack_guard_seconds if config.jump_scatter else .55))
                        moving=((x,y),issued,(x+dx,y+dy))
                        if navigation_waiting:
                            event('navigation_resumed')
                            navigation_waiting=False
                        event("movement_attempt", point=point, waypoint=destination, approaching=approaching,
                              scatter_approach=bool(scatter_destination),
                              scatter_plan=getattr(supervisor,'scatter_plan',None) if scatter_destination else None,
                              region=rotation.region.name if rotation else None,
                              movement='jump' if (long_jump if supervisor else not approaching) else 'run')
                    last_action = time.monotonic()
                time.sleep(.08)
            except CaptureUnavailable as error:
                if supervisor and speed.moving_observation_retry_seconds<.1 and str(error) in ('Life state changed during observation','Player moved before projection'):
                    # No input was submitted. Re-read immediately instead of treating
                    # a normal moving-frame race as a focus loss with a 100ms pause.
                    time.sleep(speed.moving_observation_retry_seconds)
                    continue
                if str(error)=='Waiting for a traversable patrol step':
                    if not navigation_waiting:
                        event('navigation_wait',reason=str(error))
                    navigation_waiting=True
                    time.sleep(.1)
                    continue
                if not focus_paused:
                    event("paused", reason=str(error))
                focus_paused = True
                time.sleep(.1)
                # Re-enter with a fresh observation; never reuse this action.
                continue
    except (ValueError, OSError, RuntimeError) as error:
        reason = "observation_or_input_failure"
        import traceback
        event("trial_error", detail=str(error),traceback=traceback.format_exc())
    except KeyboardInterrupt:
        reason = "interrupt"
    finally:
        if supervisor and hasattr(supervisor,'finish_runback'):supervisor.finish_runback(reason)
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
