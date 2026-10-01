"""Player actors near the farmer, read from the client's scene.

Back2Classic allows PK. Players never appear among the health monsters, so a
player hitting the farmer looked like damage from nowhere: Suicide died at
(450, 347) in the Phoenix Bandit box on 2026-09-30 23:18 (100-200 a hit,
then ~820 in under a second), and at 02:09 laptop2-ops/hit_logger.py named
the hitters: "Andria" at 1-14 tiles, then "Sesshomaru" at 3, with no monster
within 15 tiles on most of the eight hits.

Players are actors with server uids from 1,000,000 on the common actor
vtable, like MrBuffer (buff_trip.find_buffer). The farmer's own actor is not
in this scene list.
"""

import struct

PLAYER_UID_MIN = 1_000_000


def players_near(session, position, reach):
    """[(name, uid, (x, y), distance)] of players within ``reach`` tiles,
    nearest first."""
    from conquest.addressing import checked_address
    from conquest.memory_entities import MemoryEntityReader

    entities = MemoryEntityReader.for_session(session)
    layout = entities.layout
    base, collection, _ = entities._resolve()
    begin, end = (
        struct.unpack("<Q", session.read_block(collection + offset, 8))[0]
        for offset in (layout.begin_offset, layout.end_offset)
    )
    if not begin <= end or (end - begin) % layout.entry_stride:
        return []
    raw = session.read_block(checked_address(begin), end - begin) if end > begin else b""
    addresses = []
    for i in range(0, len(raw), layout.entry_stride):
        try:
            addresses.append(
                checked_address(struct.unpack_from("<Q", raw, i + layout.entry_object_offset)[0], 0x100)
            )
        except ValueError:
            pass
    vtable = base + layout.monster_vtable_rva
    found = []
    for start in range(0, len(addresses), 900):
        for block in session.read_blocks(addresses[start : start + 900], 0x100):
            if block is None or struct.unpack_from("<Q", block)[0] != vtable:
                continue
            uid = struct.unpack_from("<I", block, layout.id_offset)[0]
            if uid < PLAYER_UID_MIN:
                continue
            tile = struct.unpack_from("<2I", block, layout.position_offset)
            distance = max(abs(a - b) for a, b in zip(tile, position))
            if distance <= reach:
                name = block[layout.name_offset : layout.name_offset + 32].split(b"\0", 1)[0]
                found.append((name.decode("utf-8", "replace"), uid, tuple(tile), distance))
    return sorted(found, key=lambda row: row[3])
