"""Resolve a named incoming request against one stable, qualified scene actor."""
import struct
import time

from conquest.addressing import checked_address
from conquest.memory_entities import sample_fields
from conquest.memory_life import CLIENT_SHA256


def participant_uid(observer, name):
    """A missing/ambiguous actor never supplies authority to accept a request.

    The confirmation exposes a name, not the open trade's participant UID.
    Resolve that name using the remote actor layout already qualified for
    farmer delivery (vtable 0x5c5e20, UID +0x78, inline name +0xa4).
    Controller trust checks and the opened trade's independent UID still apply.
    """
    session = observer.adapter
    if session.expected_sha256 != CLIENT_SHA256:
        raise ValueError('Incoming request client build is unqualified')
    if not isinstance(name, str) or not name or len(name.encode('utf-8')) >= 32:
        raise ValueError('Incoming request name is invalid')
    started = time.monotonic()
    session.assert_identity()
    entities = observer.entities
    base, collection, trace = entities._resolve()
    # The pinned confirmation renderer loads the source name from self+0xfd8.
    signature = bytes.fromhex('e8cd160700488d98d80f0000')
    if session.read_block(base + 0x11045e, len(signature)) != signature:
        raise ValueError('Incoming request name accessor changed')
    layout = entities.layout
    if layout.entry_stride != 16 or layout.entry_object_offset != 8:
        raise ValueError('Incoming request scene layout is unqualified')
    fields = [(collection + offset, 'u64') for offset in
              (layout.begin_offset, layout.end_offset, layout.capacity_offset)]
    header = sample_fields(session, fields)
    begin, end, capacity = header
    if not (0 < begin <= end <= capacity and (end - begin) % 16 == 0
            and (capacity - begin) % 16 == 0
            and (capacity - begin) // 16 <= layout.max_objects):
        raise ValueError('Incoming request scene bounds are invalid')
    entries = session.read_block(checked_address(begin), end - begin) if end > begin else b''
    pointers = [struct.unpack_from('<Q', entries, i + 8)[0]
                for i in range(0, len(entries), 16)]
    if len(set(pointers)) != len(pointers):
        raise ValueError('Incoming request scene has duplicate actors')
    def matching_actors():
        matches = []
        for address in pointers:
            raw = session.read_block(checked_address(address), 0xc4)
            if (struct.unpack_from('<Q', raw)[0] == base + 0x5c5e20
                    and raw[0xa4:0xc4].split(b'\0')[0] == name.encode('utf-8')):
                matches.append((address, struct.unpack_from('<I', raw, 0x78)[0]))
        return matches
    matches = matching_actors()
    # Re-evaluate the complete set, including previous nonmatches: a rename
    # into this name, duplicate or address takeover must change the binding.
    # Unrelated actors may change while remaining outside that matching set.
    if matching_actors() != matches:
        raise ValueError('Incoming request actor identity changed')
    if (sample_fields(session, fields) != header
            or (end > begin and session.read_block(begin, end - begin) != entries)
            or sample_fields(session, [(a, 'u64') for a, _ in trace]) != [v for _, v in trace]
            or time.monotonic() - started > 2):
        raise ValueError('Incoming request scene changed or expired')
    session.assert_identity()
    return matches[0][1] if len(matches) == 1 and matches[0][1] > 0 else None
