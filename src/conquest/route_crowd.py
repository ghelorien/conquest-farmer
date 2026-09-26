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
    bodies = []
    for index in range(0, len(entries), p.entry_stride):
        address = struct.unpack_from("<Q", entries, index + p.entry_object_offset)[0]
        try:
            raw = session.read_block(checked_address(address), span)
        except (OSError, ValueError):
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
        (200,199) alternated until the travel stalled).
        """
        from conquest.navigation import clear_segment
        from conquest.scene_input import clear_route_point

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
                remaining = max(abs(point[0] - goal[0]), abs(point[1] - goal[1]))
                if remaining < current:
                    candidates.append((remaining, distance, point))
        for _, _, point in sorted(candidates):
            if hasattr(terrain, "travel_path"):
                try:
                    terrain.travel_path(point, goal)
                except ValueError:
                    continue
            return point
        return None
