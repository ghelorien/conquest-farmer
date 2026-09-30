"""Keep town route clicks off the players, booths and NPCs standing there.

Market is crowded. A route click that lands on a body selects that player,
opens a booth or an NPC dialog, and the farmer does not move. Live 09-26 the
farmer stood at the Market warehouse (189,183) for over ten minutes: every
run and jump click landed on a player (Chewgirl stood on the very tile it
clicked), on its own sprite, or on the LoveStone NPC.

Every scene Role (players, booths and NPCs, each with its UID, tile and client
draw point) is read-only memory, like the farmer's own anchor. A click inside
a body's sprite box is covered; travel clicks the nearest open ground instead.
"""

import struct

from conquest.addressing import checked_address

# Sprite boxes around a Role's draw (foot) point: left, top, right, bottom.
PLAYER_BOX = (-40, -120, 40, 16)
NPC_BOX = (-64, -176, 64, 24)
# Server-assigned player UIDs start here; NPC IDs are below it.
FIRST_PLAYER_UID = 1_000_000
# Off-walk landings replanned before giving up: a 630-tile Ape City walk
# plans in ~0.25-0.55 s.
LANDING_REPLANS = 4


def _objects(session, addresses, span):
    """Each scene object's first `span` bytes, or None when unreadable.

    A worker session reads the whole scene in one request. A worker without
    that operation, or a direct session, falls back to one read per object.
    """
    valid = []
    for address in addresses:
        try:
            valid.append(checked_address(address, span))
        except ValueError:
            valid.append(None)
    wanted = [a for a in valid if a is not None]
    batch = getattr(session, "read_blocks", None)
    found = None
    if batch is not None and wanted:
        try:
            found = {}
            for start in range(0, len(wanted), 1000):
                part = wanted[start : start + 1000]
                found.update(zip(part, batch(part, span)))
        except (OSError, ValueError):
            found = None  # Older worker: read one object at a time.
    result = []
    for address in valid:
        if address is None:
            result.append(None)
        elif found is not None:
            result.append(found.get(address))
        else:
            try:
                result.append(session.read_block(address, span))
            except (OSError, ValueError):
                result.append(None)
    return result


def _roles(session):
    from conquest.memory_entities import MemoryEntityReader

    reader = MemoryEntityReader.for_session(session)
    base, collection, _ = reader._resolve()
    p = reader.layout
    begin, end = (
        struct.unpack("<Q", session.read_block(collection + offset, 8))[0]
        for offset in (p.begin_offset, p.end_offset)
    )
    if (
        not begin <= end
        or (end - begin) % p.entry_stride
        or (end - begin) // p.entry_stride > p.max_objects
    ):
        raise ValueError("Scene vector bounds are invalid")
    entries = session.read_block(checked_address(begin), end - begin) if end > begin else b""
    role = base + p.monster_vtable_rva
    span = p.draw_position_offset + 8
    addresses = [
        struct.unpack_from("<Q", entries, index + p.entry_object_offset)[0]
        for index in range(0, len(entries), p.entry_stride)
    ]
    bodies = []
    for raw in _objects(session, addresses, span):
        if raw is None:
            continue  # Released while reading; the next observation replans.
        if struct.unpack_from("<Q", raw)[0] != role:
            continue
        uid = struct.unpack_from("<I", raw, p.id_offset)[0]
        tile = struct.unpack_from("<2I", raw, p.position_offset)
        draw = struct.unpack_from("<2i", raw, p.draw_position_offset)
        if uid and all(0 < v < 4096 for v in tile) and all(abs(v) < 16384 for v in draw):
            bodies.append((uid, draw))
    return bodies


class Crowd:
    def __init__(self, bodies=()):
        self.bodies = tuple(bodies)

    @classmethod
    def observe(cls, session, anchor):
        """The farmer's own sprite and every scene Role; empty without memory."""
        if session is None:
            return cls()
        try:
            roles = _roles(session)
        except (AttributeError, OSError, ValueError):
            roles = []  # An unreadable scene keeps the previous click choice.
        return cls([(0, tuple(anchor)), *roles])

    def covers(self, point):
        x, y = point
        for uid, (bx, by) in self.bodies:
            left, top, right, bottom = (
                PLAYER_BOX if uid == 0 or uid >= FIRST_PLAYER_UID else NPC_BOX
            )
            if bx + left <= x <= bx + right and by + top <= y <= by + bottom:
                return True
        return False

    def open_landing(self, terrain, source, goal, anchor, *, avoid=(), bounds):
        """Nearest-to-goal checked landing within jump range on open ground.

        Only a landing strictly closer to the goal qualifies: a sidestep lets
        the next reroute step straight back (live 09-26 10:21, (200,197) and
        (200,199) alternated until the travel stalled). Closer means a shorter
        walk, not only a shorter straight line: beside the Phoenix Pharmacist
        the nearest-looking open tile (193,247) was a pocket whose way on
        doubles back through the corridor the NPC's sprite covers, and the
        restock looped there for seven minutes (live 2026-09-28 06:25-06:32).
        """
        from conquest.navigation import clear_segment
        from conquest.scene_input import clear_route_point

        # The walk itself: its tiles are closer by walk with no replanning.
        # Replanning each straight-line-closer tile instead stood Suicide
        # still 13.9 s among a GiantApe pack at (563, 432), whose walk to
        # town runs 630 tiles round by the east road: 56 plans (2026-09-29
        # 22:39, it died there).
        walk_path = None
        if hasattr(terrain, "travel_path"):
            try:
                walk_path = terrain.travel_path(source, goal)
            except ValueError:
                walk_path = None
        walk = len(walk_path) if walk_path is not None else None
        along = {tuple(p): i for i, p in enumerate(walk_path or ())}
        farthest = None
        current = max(abs(source[0] - goal[0]), abs(source[1] - goal[1]))
        candidates = []
        for dx in range(-12, 13):
            for dy in range(-12, 13):
                distance = max(abs(dx), abs(dy))
                if distance < 2:
                    continue  # One-tile clicks land on the farmer's own sprite.
                point = (source[0] + dx, source[1] + dy)
                click = (anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16)
                if (
                    point in avoid
                    or not terrain.walkable(point)
                    or not clear_route_point(click, bounds)
                    or self.covers(click)
                    or not clear_segment(terrain, source, point, avoid=avoid)
                ):
                    continue
                if along.get(point, 0) > 0:
                    if farthest is None or along[point] > along[farthest]:
                        farthest = point
                    continue
                remaining = max(abs(point[0] - goal[0]), abs(point[1] - goal[1]))
                if remaining < current:
                    candidates.append((remaining, distance, point))
        if farthest is not None:
            return farthest
        replans = 0
        for _, _, point in sorted(candidates):
            if hasattr(terrain, "travel_path"):
                if replans >= LANDING_REPLANS:
                    return None  # The covered click, then stall recovery.
                replans += 1
                try:
                    steps = len(terrain.travel_path(point, goal))
                except ValueError:
                    continue
                if walk is not None and steps >= walk:
                    continue
            return point
        return None
