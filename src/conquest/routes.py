"""Reusable hunting routes, separate from the progress of any one run."""

import os
from pathlib import Path
from uuid import uuid4
from typing import Literal

from conquest.patrol_search import PatrolSearchConfig, AdaptivePatrol

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml

from conquest.navigation import straight_waypoints

MONSTER_NAMES = {
    1: "Pheasant",
    2: "Turtledove",
    3: "Robin",
    4: "Apparition",
    5: "Poltergeist",
    6: "WingedSnake",
    7: "Bandit",
    8: "Ratling",
    9: "FireSpirit",
    10: "Macaque",
    11: "GiantApe",
    12: "ThunderApe",
}


def monster_family(base_type):
    return _monster_families().get(str(base_type), [])


from functools import lru_cache


@lru_cache(maxsize=1)
def _monster_families():
    import json

    return json.loads(
        Path("profiles/monster-families.json").read_text(encoding="utf-8")
    )


@lru_cache(maxsize=1)
def _family_names():
    return frozenset(
        m["name"] for family in _monster_families().values() for m in family
    )


def route_monster_names(route):
    base = route.monster_type_ids[0]
    members = {m["type_id"]: m["name"] for m in monster_family(base)}
    if base not in MONSTER_NAMES or not set(route.monster_type_ids) <= members.keys():
        raise ValueError(
            "Native farming requires one supported leveling monster family"
        )
    return tuple(members[k] for k in route.monster_type_ids)


def route_monster_name(route):
    return route_monster_names(route)[0]


def boss_name(name):
    """Whether a monster name is a boss or elite: never a target, kept at a
    distance. The elite tiers around the leveling fields are Messenger (81xx),
    Aide (82xx) and King (83xx): RatAide and RatMessenger share the Ratling
    field and BanditAide the Bandit one. An ordinary leveling family is never
    a boss, whatever its name ends with (HawKing, levels 92-96)."""
    import re

    if name in _family_names():
        return False
    return bool(
        re.search(
            r"(?:king|queen|boss|leader|chieftain|aide|messenger)$",
            name,
            flags=re.IGNORECASE,
        )
    )


# Tiles an archer keeps from a boss: bosses hit from range. On 2026-09-28
# Toxic took ~140-damage hits every 1.5-2 s with no ordinary monster within
# 5-6 tiles (Bandit field, 00:42-01:03), and died 2 tiles from the
# WingedSnakeKing (40,000 HP) at 02:03.
BOSS_CLEARANCE = 9
# Landings prefer this much room from every boss. The WingedSnakeKing roams
# the middle of the WingedSnake field (~0.44 tiles/s, Toxic 2026-09-28
# 10:16): landing just outside BOSS_CLEARANCE meant another boss escape
# moments later (63 of 209 escapes in 14 minutes).
BOSS_ROOM = BOSS_CLEARANCE + 4


def messenger(name):
    """A Messenger-tier elite (81xx: RatMessenger, ElfMessenger...). Where its
    field's family lists it, it is a target killed with left clicks, not a
    boss (Alex 2026-09-28: "if there ever is a messenger version of the
    monster just kill it with left clicks")."""
    import re

    return bool(re.search(r"messenger$", name or "", flags=re.IGNORECASE))


def king_tier(name):
    """A King-tier boss (…King/Queen/Boss/Leader/Chieftain), not an Aide or
    Messenger. A RatKing roamed the Ratling field and trailed Suicide at 13-16
    tiles, twice closing to 3 (2026-09-28 12:35-12:52)."""
    import re

    return boss_name(name) and bool(
        re.search(r"(?:king|queen|boss|leader|chieftain)$", name, flags=re.IGNORECASE)
    )


def boss_clearance(name, king_clearance=BOSS_CLEARANCE, elite_clearance=BOSS_CLEARANCE):
    """Tiles to keep from this boss: a route's ``king_clearance`` for the King
    tier, its ``elite_clearance`` for Aides and Messengers (neither ever less
    than BOSS_CLEARANCE)."""
    return max(king_clearance if king_tier(name) else elite_clearance, BOSS_CLEARANCE)


def near_boss(
    point,
    monsters,
    clearance=None,
    *,
    king_clearance=BOSS_CLEARANCE,
    elite_clearance=BOSS_CLEARANCE,
):
    """Whether ``point`` lies within a boss's clearance: ``clearance`` tiles
    when given, else each boss's own (boss_clearance with the route's)."""
    for m in monsters:
        name = getattr(m, "name", "") or ""
        if not boss_name(name):
            continue
        reach = (
            clearance
            if clearance is not None
            else boss_clearance(name, king_clearance, elite_clearance)
        )
        if max(abs(m.position[0] - point[0]), abs(m.position[1] - point[1])) <= reach:
            return True
    return False


def boss_room(
    point, monsters, *, king_clearance=BOSS_CLEARANCE, elite_clearance=BOSS_CLEARANCE
):
    """Whether ``point`` keeps BOSS_ROOM - BOSS_CLEARANCE tiles beyond every
    boss's clearance (BOSS_ROOM from an ordinary one)."""
    margin = BOSS_ROOM - BOSS_CLEARANCE
    for m in monsters:
        name = getattr(m, "name", "") or ""
        if boss_name(name) and max(
            abs(m.position[0] - point[0]), abs(m.position[1] - point[1])
        ) < boss_clearance(name, king_clearance, elite_clearance) + margin:
            return False
    return True


# Beside a boss already, route travel keeps off only this close, so the walk
# can still leave its zone.
BOSS_INNER = 3


def boss_zone(
    source,
    destination,
    monsters,
    clearance=None,
    *,
    king_clearance=BOSS_CLEARANCE,
    elite_clearance=BOSS_CLEARANCE,
):
    """Tiles route travel keeps off: within each living boss's clearance
    (``clearance`` when given, else boss_clearance with the route's).

    ``monsters`` are bridge scene records (dicts) or scene objects. A boss
    whose zone holds the destination is skipped (there is nothing to detour
    to); one whose zone holds ``source`` keeps only BOSS_INNER tiles.
    """
    tiles = set()
    for monster in monsters:
        get = (
            monster.get
            if isinstance(monster, dict)
            else lambda key, default=None: getattr(monster, key, default)
        )
        position = get("position")
        name = get("name") or ""
        if (
            not position
            or not boss_name(name)
            or get("alive") is False
            or get("current_hp", 1) == 0
        ):
            continue
        full = (
            clearance
            if clearance is not None
            else boss_clearance(name, king_clearance, elite_clearance)
        )
        bx, by = position[0], position[1]
        if max(abs(destination[0] - bx), abs(destination[1] - by)) <= full:
            continue
        reach = (
            BOSS_INNER
            if max(abs(source[0] - bx), abs(source[1] - by)) <= full
            else full
        )
        tiles.update(
            (x, y)
            for x in range(bx - reach, bx + reach + 1)
            for y in range(by - reach, by + reach + 1)
        )
    tiles.discard(tuple(source))
    return frozenset(tiles)


class Supplies(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    arrow_type: int = 1050000
    arrows_return_below: int = Field(default=3, ge=1)
    arrows_restock_to: int = Field(default=2000, ge=1)
    healing_type: int = 1000000
    healing_return_below: int = Field(default=1, ge=1)
    healing_restock_to: int = Field(default=5, ge=1)
    healing_threshold: float = Field(default=0.4, gt=0, lt=1)
    minimum_free_slots: int = Field(default=4, ge=1, le=20)

    @model_validator(mode="after")
    def thresholds(self):
        if (
            self.arrows_restock_to <= self.arrows_return_below
            or self.healing_restock_to <= self.healing_return_below
        ):
            raise ValueError("Restock targets must exceed return thresholds")
        return self


class SavedRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    name: str = Field(min_length=1, max_length=100)
    map_id: int = Field(gt=0)
    restock_map_id: int = Field(default=1002, gt=0)
    monster_type_ids: tuple[int, ...] = Field(min_length=1, max_length=128)
    recommended_levels: tuple[int, int]
    town_anchor: tuple[int, int]
    restock_anchor: tuple[int, int] | None = None
    hunting_anchor: tuple[int, int]
    kite_when_surrounded: bool = False
    jump_scatter: bool = False
    attack_range_tiles: int = Field(default=16, ge=1, le=20)
    # Tiles combat and travel keep from a King-tier boss on this route
    # (boss_clearance).
    king_clearance: int = Field(default=BOSS_CLEARANCE, ge=BOSS_CLEARANCE, le=24)
    # The same for its Aides and Messengers. The ElfAide (FireSpirit field)
    # moves ~2 tiles/s and hit Toxic for 192 of 633 HP from 6 tiles twice in
    # 1.7 s (2026-09-28 16:36:44), inside the 9-tile clearance it had just
    # crossed.
    elite_clearance: int = Field(default=BOSS_CLEARANCE, ge=BOSS_CLEARANCE, le=24)
    hunting_boundary: tuple[int, int, int, int]
    patrol_search: PatrolSearchConfig = Field(default_factory=PatrolSearchConfig)
    patrol: tuple[tuple[int, int], ...] = Field(min_length=1, max_length=128)
    outbound_waypoints: tuple[tuple[int, int], ...] = Field(
        min_length=2, max_length=2048
    )
    return_waypoints: tuple[tuple[int, int], ...] = Field(min_length=2, max_length=2048)
    terrain_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    supplies: Supplies = Field(default_factory=Supplies)
    equipment_review_levels: tuple[int, ...] = ()
    tasks: tuple[
        Literal[
            "travel_to_hunt",
            "hunt_and_loot",
            "return_to_town",
            "restock",
            "check_equipment",
            "resume_hunt",
        ],
        ...,
    ]
    qualification: Literal["planned", "travel_verified", "cycle_verified"] = "planned"
    recover_after_death: bool = True
    movement: Literal["jump", "run"] = "jump"
    notes: str = ""

    @model_validator(mode="after")
    def validate_route(self):
        if not 1 <= self.recommended_levels[0] <= self.recommended_levels[1] <= 140:
            raise ValueError("Invalid recommended level range")
        if any(type(i) is not int or i <= 0 for i in self.monster_type_ids):
            raise ValueError("Invalid monster group ID")
        x0, y0, x1, y1 = self.hunting_boundary
        for region in self.patrol_search.regions:
            a, b, c, d = region.boundary
            if not (x0 <= a <= c <= x1 and y0 <= b <= d <= y1):
                raise ValueError(
                    "Hunting regions must remain inside the route boundary"
                )
        if (
            x0 > x1
            or y0 > y1
            or any(
                not (x0 <= x <= x1 and y0 <= y <= y1)
                for x, y in (self.hunting_anchor, *self.patrol)
            )
        ):
            raise ValueError("Patrol points must stay inside the hunting area")
        if (
            self.outbound_waypoints[0] != self.town_anchor
            or self.outbound_waypoints[-1] != self.hunting_anchor
            or self.return_waypoints[0] != self.hunting_anchor
            or self.return_waypoints[-1] != self.town_anchor
        ):
            raise ValueError("Travel paths must connect town and the hunting anchor")
        return self

    def return_reasons(self, inventory):
        s = self.supplies
        ammo = inventory.count(s.arrow_type)
        if inventory.equipped_ammo and inventory.equipped_ammo.type_id == s.arrow_type:
            ammo += inventory.equipped_ammo.amount
        reasons = []
        if ammo < s.arrows_return_below:
            reasons.append("arrows_low")
        from conquest import potion_tiers

        potions = (
            potion_tiers.count(inventory, s.healing_type)
            if s.healing_type in potion_tiers.HEALING_POTIONS
            else inventory.count(s.healing_type)
        )
        if potions < s.healing_return_below:
            reasons.append("healing_supplies_low")
        if len(inventory.items) >= inventory.capacity:
            reasons.append("inventory_full")
        return reasons


class RouteLibrary:
    def __init__(self, directory="profiles/routes"):
        self.directory = Path(directory)
        self.builtin_directory = None
        from conquest.character_context import current

        context = current()
        from conquest.application_layout import application_root

        if (
            context
            and self.directory.resolve()
            == application_root(verify=False) / "profiles/routes"
        ):
            self.builtin_directory = self.directory
            self.directory = context.state_dir / "routes"

    def all(self):
        files = (
            {p.name: p for p in sorted(self.builtin_directory.glob("*.yaml"))}
            if self.builtin_directory
            else {}
        )
        files.update({p.name: p for p in sorted(self.directory.glob("*.yaml"))})
        routes = [
            SavedRoute.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
            for p in files.values()
        ]
        if len({r.id for r in routes}) != len(routes):
            raise ValueError("Duplicate saved route IDs")
        # New saved routes inherit only the explicitly recorded normal variants.
        return [
            r.model_copy(
                update={
                    "monster_type_ids": tuple(
                        m["type_id"] for m in monster_family(r.monster_type_ids[0])
                    )
                }
            )
            if len(r.monster_type_ids) == 1 and monster_family(r.monster_type_ids[0])
            else r
            for r in routes
        ]

    def load(self, route_id):
        matches = [r for r in self.all() if r.id == route_id]
        if len(matches) != 1:
            raise ValueError(f"Unknown saved route: {route_id}")
        return matches[0]

    def save(self, route, *, replace=False):
        """Save a validated definition atomically; runs never modify the template."""
        route = SavedRoute.model_validate(
            route.model_dump() if isinstance(route, SavedRoute) else route
        )
        existing = {r.id: r for r in self.all()}
        if route.id in existing and not replace:
            raise ValueError(
                "Route already exists; choose another ID or explicitly replace it"
            )
        if any(
            r.name.casefold() == route.name.casefold() and r.id != route.id
            for r in existing.values()
        ):
            raise ValueError("Route name already exists")
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"{route.id}.yaml"
        temporary = self.directory / f".{route.id}-{uuid4().hex}.tmp"
        try:
            temporary.write_text(
                yaml.safe_dump(route.model_dump(mode="json"), sort_keys=False),
                encoding="utf-8",
            )
            if replace:
                os.replace(temporary, destination)
            else:
                # A hard link creates the destination only if it is still absent.
                os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination


def plan_travel(route, terrain, current, *, phase="outbound", patrol_index=0):
    if route.map_id != terrain.map_id or route.terrain_sha256 != terrain.source_sha256:
        raise ValueError(
            "Saved route terrain differs from the installed map; review the route"
        )
    if phase == "outbound":
        destination = route.hunting_anchor
    elif phase == "return":
        destination = route.town_anchor
    elif phase == "patrol":
        destination = route.patrol[patrol_index % len(route.patrol)]
    else:
        raise ValueError("Unknown route travel phase")
    # Replan from observed position, never replay an old run's last waypoint.
    path = terrain.path(tuple(current), destination)
    return {
        "route_id": route.id,
        "route_name": route.name,
        "phase": phase,
        "source": list(current),
        "destination": list(destination),
        "tile_steps": len(path) - 1,
        "waypoints": straight_waypoints(path, 4),
        "terrain_sha256": terrain.source_sha256,
    }
