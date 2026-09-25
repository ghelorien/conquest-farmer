"""Read XP charge and the ready Fly popup; activate only through normal input."""

import struct
import time
from conquest.addressing import checked_address
from conquest.memory_life import CLIENT_SHA256, read_life
from conquest.memory_shop import MemoryGui


def _read_xp(session, life, layout):
    if life.dead_candidate:
        raise ValueError("Living character required for XP skill")
    actor = life.object_address
    raw = session.read_block(actor + layout.xp_charge_offset, 4)
    status_raw = session.read_block(actor + layout.life_status_offset, 8)
    charge = struct.unpack("<I", raw)[0]
    status = struct.unpack_from("<I", status_raw)[0]
    if not 0 <= charge <= 100:
        raise ValueError("XP charge outside HUD bounds")
    if (
        session.read_block(actor + layout.xp_charge_offset, 4) != raw
        or session.read_block(actor + layout.life_status_offset, 8) != status_raw
    ):
        raise ValueError("XP state changed during observation")
    session.assert_identity()
    return {
        "charge": charge,
        "ready": bool(status & 0x10),
        "flying": bool(status & 0x8000000),
        "actor": actor,
        "source": "read_only_memory",
    }


def read_xp(observer):
    s = observer.adapter
    life = (
        observer.read_life()
        if hasattr(observer, "read_life")
        else read_life(s, observer.health_layout, observer.character)
    )
    if life.dead_candidate:
        raise ValueError("Living character required for XP skill")
    from conquest.memory_build_layout import read_build_layout

    result = _read_xp(s, life, read_build_layout(s))
    latest = (
        observer.read_life()
        if hasattr(observer, "read_life")
        else read_life(s, observer.health_layout, observer.character)
    )
    if latest.object_address != life.object_address or latest.dead_candidate:
        raise ValueError("XP state changed during observation")
    return result


def read_xp_for_session(session, character):
    """Explicit exact-build XP telemetry; it cannot activate Fly."""
    from conquest.memory_build_layout import read_build_layout
    from conquest.memory_life import MemoryLifeReader

    reader = MemoryLifeReader.for_session(session, character)
    s = reader.session
    life = reader.read()
    result = _read_xp(s, life, read_build_layout(s))
    latest = MemoryLifeReader.for_session(session, character).read()
    if latest.object_address != life.object_address or latest.dead_candidate:
        raise ValueError("XP state changed during observation")
    return result


def fly_point(observer, state):
    if state["charge"] != 100 or not state["ready"] or state["flying"]:
        raise ValueError("Fly requires full XP and the ready status")
    from conquest.memory_build_layout import read_build_layout

    s = observer.adapter
    # Exact-build offsets: the XP-skill vector and skill vtable differ between
    # client builds (1078: 0x19C0 / 0x5EB7B8); an unqualified build fails here.
    layout = read_build_layout(s)
    actor = state["actor"]
    base = next(m["base"] for m in s.modules if m["name"].lower() == "imconquer.exe")
    # The XP popup iterates this vector (separate from the learned-skill list).
    header = s.read_block(actor + layout.xp_skills_offset, 24)
    start, end, capacity = struct.unpack("<3Q", header)
    if (
        not 16 <= end - start <= 8 * 16
        or (end - start) % 16
        or not end <= capacity <= start + 128 * 16
        or (capacity - start) % 16
    ):
        raise ValueError("XP skill vector bounds changed")
    entries = s.read_block(checked_address(start, end - start), end - start)
    pointers = [
        checked_address(struct.unpack_from("<Q", entries, i)[0])
        for i in range(0, len(entries), 16)
    ]
    if len(set(pointers)) != len(pointers):
        raise ValueError("Duplicate XP skill entries")
    records = [s.read_block(pointer, 0x68) for pointer in pointers]
    if any(
        struct.unpack_from("<Q", raw)[0] != base + layout.skill_vtable_rva
        or struct.unpack_from("<I", raw, 8)[0] not in (0, 1)
        for raw in records
    ):
        raise ValueError("XP skill entry identity changed")
    matches = [
        i
        for i, raw in enumerate(records)
        if struct.unpack_from("<I", raw, 0x10)[0] == 8002
    ]
    if len(matches) != 1:
        raise ValueError("One unambiguous Fly entry is required")
    index = matches[0]
    raw = records[index]
    if (
        struct.unpack_from("<I", raw, 8)[0] != 1
        or raw[0x18:0x1C] != b"Fly\0"
        or struct.unpack_from("<QQ", raw, 0x28) != (3, 15)
        or struct.unpack_from("<I", raw, 0x44)[0] != 2
    ):
        raise ValueError("Ready XP entry is not the self-target Fly skill")
    gui = MemoryGui(s, layout=layout)
    window = gui.read("##SkillsPopup")
    # Pinned renderer 0x9b2c0 iterates every non-null entry, including disabled
    # buttons, in vector order. 0xab610 draws 40x40 icons with 8px spacing and
    # 8px window padding. Live Fly + ArrowRain measures 104x56 (single: 56x56).
    if window.size != (56.0 + 48 * (len(records) - 1), 56.0) or window.scroll != (
        0.0,
        0.0,
    ):
        raise ValueError("Fly popup geometry changed")
    if (
        s.read_block(actor + layout.xp_skills_offset, 24) != header
        or s.read_block(start, len(entries)) != entries
        or any(
            s.read_block(pointer, 0x68) != raw
            for pointer, raw in zip(pointers, records)
        )
        or gui.read("##SkillsPopup") != window
        or read_xp(observer) != state
    ):
        raise ValueError("Fly readiness changed before activation")
    return (round(window.position[0] + 28 + 48 * index), round(window.position[1] + 28))


class XpSkill:
    def __init__(self, observer, notify):
        self.observer, self.notify = observer, notify
        self.last = None
        self.pending = None
        self.attempts = 0
        self.next_attempt = 0

    def step(self, dispatch):
        now = time.monotonic()
        try:
            state = read_xp(self.observer)
        except (ValueError, OSError):
            return False
        telemetry = {k: state[k] for k in ("charge", "ready", "flying", "source")}
        if telemetry != self.last:
            self.notify("xp_skill_state", telemetry)
            self.last = telemetry
        if self.pending and state["actor"] != self.pending["actor"]:
            self.pending = None
        if self.pending and state["flying"]:
            self.notify(
                "xp_fly_verified",
                {
                    "activity": "Fly active; continuing combat",
                    "charge": state["charge"],
                    "source": "read_only_memory",
                },
            )
            self.pending = None
        if not state["ready"] or state["charge"] < 100:
            self.attempts = 0
            if self.pending and now - self.pending["at"] > 3:
                self.pending = None
            return False
        if state["flying"] or now < self.next_attempt or self.attempts >= 3:
            return False
        try:
            point = fly_point(self.observer, state)
        except (ValueError, OSError):
            return False
        dispatch(point)
        self.attempts += 1
        self.next_attempt = now + 1.5
        self.pending = {"actor": state["actor"], "at": now}
        self.notify(
            "xp_fly_attempt",
            {
                "activity": "XP full; activating Fly",
                "point": point,
                "attempt": self.attempts,
                "charge": 100,
            },
        )
        return True
