"""Clock-independent farming decisions, separated from observation and input."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
import math


class State(StrEnum):
    INITIALIZE = "INITIALIZE"
    PATROL = "PATROL"
    ACQUIRE = "ACQUIRE"
    ATTACK = "ATTACK"
    LOOT = "LOOT"
    RECOVER = "RECOVER"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class Entity:
    key: str
    name: str
    kind: str
    position: tuple[float, float]
    screen: tuple[int, int]


@dataclass(frozen=True)
class Observation:
    timestamp: float
    valid: bool
    reason: str = ""
    position: tuple[float, float] = (0, 0)
    map_id: str = ""
    health: float = 0
    max_health: int = 0
    experience: float = 0
    potions: tuple[tuple[int, int], ...] = ()
    occupied_slots: int = 0
    inventory_capacity: int = 40
    ammo: int | None = None
    connected: bool = True
    entities: tuple[Entity, ...] = ()
    loot: tuple[Entity, ...] = ()


@dataclass(frozen=True)
class Action:
    kind: str
    expires_at: float
    point: tuple[int, int] | None = None
    destination: tuple[float, float] | None = None
    target: str | None = None


class StateReader(Protocol):
    def read(self) -> Observation: ...


class FrameSource(Protocol):
    def read(self): ...
    def close(self): ...


class InputBackend(Protocol):
    def execute(self, action: Action, observation: Observation): ...
    def close(self): ...


@dataclass(frozen=True)
class Policy:
    route: tuple[tuple[float, float], ...]
    boundary: tuple[float, float, float, float]
    map_id: str
    monsters: frozenset[str] = frozenset({"Pheasant"})
    items: frozenset[str] = frozenset({"Stancher"})
    tolerance: float = 2
    heal_below: float = 0.4
    potion_cooldown: float = 1
    attack_wait: float = 1.8
    pickup_distance: float = 6
    pickup_limit: int = 2
    movement_wait: float = 1.5
    stale_after: float = 0.5
    invalid_limit: int = 3
    recovery_limit: int = 3
    require_ammo: bool = True
    require_potions: bool = True


class Controller:
    def __init__(self, policy):
        if not policy.route or not policy.map_id:
            raise ValueError("A calibrated route and map are required")
        self.policy = policy
        self.state = State.INITIALIZE
        self.stop_reason = None
        self.waypoint = 0
        self.pending = None
        self.last_potion = -math.inf
        self.invalid = self.failures = self.recoveries = 0
        self.pickups = 0
        self.pickup_attempts = {}
        self.paused = False
        self.last_outcome = None

    def stop(self, reason):
        self.stop_reason = reason
        self.state = State.STOPPED
        self.pending = None

    def pause(self, paused=True):
        self.paused = paused
        self.pending = None
        if self.state != State.STOPPED:
            self.state = State.PAUSED if paused else State.INITIALIZE

    def inside(self, point):
        l, t, r, b = self.policy.boundary
        return l <= point[0] <= r and t <= point[1] <= b

    def issue(self, action, observation, now):
        self.pending = (action, observation, now)
        return action

    def decide(self, observation, now):
        p, o = self.policy, observation
        self.last_outcome = None
        if self.state == State.STOPPED:
            return None
        if self.paused:
            self.state = State.PAUSED
            return None
        if not o.valid or not 0 <= now - o.timestamp <= p.stale_after:
            self.invalid += 1
            self.state = State.PAUSED
            if self.invalid >= p.invalid_limit:
                self.stop("persistently_invalid_state")
            return None
        self.invalid = 0
        if not o.connected:
            self.stop("disconnected")
        elif o.health <= 0:
            self.stop("death")
        elif o.map_id != p.map_id:
            self.stop("map_changed")
        elif not self.inside(o.position):
            self.stop("outside_boundary")
        elif o.occupied_slots >= o.inventory_capacity:
            self.stop("inventory_full")
        elif p.require_ammo and (o.ammo is None or o.ammo <= 0):
            self.stop("ammo_unavailable")
        if self.state == State.STOPPED:
            return None

        # Resolve healing before another action, including after combat damage.
        if self.pending and self.pending[0].kind == "heal":
            action, before, issued = self.pending
            if len(o.potions) < len(before.potions) and o.health > before.health:
                self.last_outcome = "healing_verified"
                self.pending = None
            elif now - issued < 2:
                return None
            else:
                self.stop("healing_not_verified")
                return None
        if o.health < p.heal_below:
            self.state = State.RECOVER
            if not o.potions:
                self.stop("potions_exhausted")
                return None
            if now - self.last_potion < p.potion_cooldown:
                return None
            self.last_potion = now
            return self.issue(Action("heal", now + 0.3, point=o.potions[0]), o, now)
        if p.require_potions and not o.potions:
            self.stop("potions_exhausted")
            return None

        if self.pending:
            action, before, issued = self.pending
            wait = p.movement_wait if action.kind == "move" else p.attack_wait
            if now - issued < wait:
                return None
            self.pending = None
            if action.kind == "attack":
                progress = (
                    o.experience != before.experience
                    or o.max_health != before.max_health
                )
                self.last_outcome = (
                    "attack_progress_verified"
                    if progress
                    else "attack_progress_missing"
                )
                self.failures = 0 if progress else self.failures + 1
            elif action.kind == "loot":
                # An allowlisted potion count increase is evidence. A vanished
                # ground label alone never counts as a successful pickup.
                if len(o.potions) > len(before.potions):
                    self.pickups += len(o.potions) - len(before.potions)
                    self.last_outcome = "pickup_verified"
                else:
                    self.last_outcome = "pickup_not_verified"
            elif action.kind == "move":
                moved = math.dist(o.position, before.position) > 0.5
                self.last_outcome = "movement_verified" if moved else "movement_stuck"
                self.failures = 0 if moved else self.failures + 1

        if self.failures >= 3:
            self.state = State.RECOVER
            self.recoveries += 1
            self.failures = 0
            if self.recoveries > p.recovery_limit:
                self.stop("recovery_limit")
                return None
            self.waypoint = (self.waypoint + 1) % len(p.route)
            return self.issue(
                Action("move", now + 0.3, destination=p.route[self.waypoint]), o, now
            )

        monsters = [
            e
            for e in o.entities
            if e.kind == "monster" and e.name in p.monsters and self.inside(e.position)
        ]
        if monsters:
            self.state = State.ACQUIRE
            target = min(monsters, key=lambda e: math.dist(e.position, o.position))
            self.state = State.ATTACK
            return self.issue(
                Action("attack", now + 0.3, point=target.screen, target=target.key),
                o,
                now,
            )
        items = [
            e
            for e in o.loot
            if e.name in p.items
            and self.inside(e.position)
            and math.dist(e.position, o.position) <= p.pickup_distance
            and self.pickup_attempts.get(e.key, 0) < p.pickup_limit
        ]
        if items:
            self.state = State.LOOT
            target = min(items, key=lambda e: math.dist(e.position, o.position))
            self.pickup_attempts[target.key] = (
                self.pickup_attempts.get(target.key, 0) + 1
            )
            return self.issue(
                Action("loot", now + 0.3, point=target.screen, target=target.key),
                o,
                now,
            )
        self.state = State.PATROL
        if math.dist(o.position, p.route[self.waypoint]) <= p.tolerance:
            self.waypoint = (self.waypoint + 1) % len(p.route)
        return self.issue(
            Action("move", now + 0.3, destination=p.route[self.waypoint]), o, now
        )
