import struct
from types import SimpleNamespace as NS
import pytest
from conquest import xp_skill as xp
from conquest.memory_build_layout import CLIENT_SHA256_1078, READ_LAYOUTS

LAYOUT = READ_LAYOUTS[CLIENT_SHA256_1078]
ACTOR = 0x100000
CHARGE = ACTOR + LAYOUT.xp_charge_offset
STATUS = ACTOR + LAYOUT.life_status_offset
XP_SKILLS = ACTOR + LAYOUT.xp_skills_offset


def fixture(monkeypatch):
    actor = ACTOR
    array = 0x200000
    pointer = 0x300000
    base = 0x140000000
    skill = bytearray(0x68)
    struct.pack_into("<Q", skill, 0, base + LAYOUT.skill_vtable_rva)
    struct.pack_into("<I", skill, 8, 1)
    struct.pack_into("<I", skill, 0x10, 8002)
    skill[0x18:0x1C] = b"Fly\0"
    struct.pack_into("<QQ", skill, 0x28, 3, 15)
    struct.pack_into("<I", skill, 0x44, 2)
    blobs = {
        CHARGE: struct.pack("<I", 100),
        STATUS: struct.pack("<Q", 0x10),
        XP_SKILLS: struct.pack("<3Q", array, array + 16, array + 16),
        array: struct.pack("<2Q", pointer, 0),
        pointer: skill,
    }
    session = NS(
        expected_sha256=CLIENT_SHA256_1078,
        modules=[{"name": "ImConquer.exe", "base": base}],
        read_block=lambda address, size: bytes(blobs[address][:size]),
        assert_identity=lambda: None,
    )
    life = NS(object_address=actor, dead_candidate=False)
    window = NS(position=(490.0, 613.0), size=(56.0, 56.0), scroll=(0.0, 0.0))
    monkeypatch.setattr(
        xp, "MemoryGui", lambda s, layout=None: NS(read=lambda name: window)
    )
    # A 1078 observer supplies its own exact-build life read.
    observer = NS(adapter=session, character="Parasite", read_life=lambda: life)
    return observer, blobs, window


def test_charge_read_separate_from_experience_and_verified_flight(monkeypatch):
    observer, blobs, _ = fixture(monkeypatch)
    events = []
    clicks = []
    runner = xp.XpSkill(observer, lambda *args: events.append(args))
    assert runner.step(clicks.append)
    assert clicks == [(518, 641)] and events[-1][0] == "xp_fly_attempt"
    blobs[STATUS] = struct.pack("<Q", 0x8000000)
    blobs[CHARGE] = struct.pack("<I", 0)
    assert not runner.step(clicks.append)
    assert events[-1][0] == "xp_fly_verified" and len(clicks) == 1


@pytest.mark.parametrize(
    "charge,status", [(99, 0x10), (100, 0), (100, 0x8000010), (0, 0)]
)
def test_not_ready_or_already_flying_never_clicks(monkeypatch, charge, status):
    observer, blobs, _ = fixture(monkeypatch)
    blobs[CHARGE] = struct.pack("<I", charge)
    blobs[STATUS] = struct.pack("<Q", status)
    assert not xp.XpSkill(observer, lambda *args: None).step(
        lambda p: pytest.fail("Unexpected click")
    )


def test_changed_popup_or_wrong_skill_never_uses_old_coordinates(monkeypatch):
    observer, blobs, window = fixture(monkeypatch)
    runner = xp.XpSkill(observer, lambda *args: None)
    window.size = (112.0, 56.0)
    assert not runner.step(lambda p: pytest.fail("Unexpected click"))
    window.size = (56.0, 56.0)
    struct.pack_into("<I", blobs[0x300000], 0x10, 8001)
    assert not runner.step(lambda p: pytest.fail("Unexpected click"))


def test_unconfirmed_click_does_not_claim_flight_or_spam(monkeypatch):
    observer, _, _ = fixture(monkeypatch)
    events = []
    clicks = []
    now = [10.0]
    monkeypatch.setattr(xp.time, "monotonic", lambda: now[0])
    runner = xp.XpSkill(observer, lambda *args: events.append(args))
    for _ in range(100):
        runner.step(clicks.append)
        now[0] += 0.1
    assert len(clicks) == 3
    assert not any(event == "xp_fly_verified" for event, _ in events)


def two_skills(monkeypatch, *, fly_first=True):
    observer, blobs, window = fixture(monkeypatch)
    arrow = bytearray(blobs[0x300000])
    struct.pack_into("<I", arrow, 0x10, 8030)
    arrow[0x18:0x28] = b"ArrowRain\0".ljust(16, b"\0")
    struct.pack_into("<QQ", arrow, 0x28, 9, 15)
    struct.pack_into("<I", arrow, 0x44, 0)
    blobs[0x400000] = arrow
    pointers = [0x300000, 0x400000] if fly_first else [0x400000, 0x300000]
    blobs[XP_SKILLS] = struct.pack("<3Q", 0x200000, 0x200020, 0x200020)
    blobs[0x200000] = struct.pack("<4Q", pointers[0], 0, pointers[1], 0)
    window.size = (104.0, 56.0)
    return observer, blobs, window


@pytest.mark.parametrize("fly_first", [True, False])
def test_fly_with_arrow_rain_uses_its_own_button(monkeypatch, fly_first):
    observer, blobs, _ = two_skills(monkeypatch, fly_first=fly_first)
    # Disabled neighbors still occupy a button in the renderer.
    struct.pack_into("<I", blobs[0x400000], 8, 0)
    assert xp.fly_point(observer, xp.read_xp(observer)) == (
        (518 if fly_first else 566),
        641,
    )


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_fly",
        "duplicate_pointer",
        "null_pointer",
        "disabled_fly",
        "wrong_size",
        "scroll",
    ],
)
def test_ambiguous_or_unqualified_multi_popup_never_clicks(monkeypatch, change):
    observer, blobs, window = two_skills(monkeypatch)
    if change == "duplicate_fly":
        struct.pack_into("<I", blobs[0x400000], 0x10, 8002)
    elif change == "duplicate_pointer":
        blobs[0x200000] = struct.pack("<4Q", 0x300000, 0, 0x300000, 0)
    elif change == "null_pointer":
        blobs[0x200000] = struct.pack("<4Q", 0x300000, 0, 0, 0)
    elif change == "disabled_fly":
        struct.pack_into("<I", blobs[0x300000], 8, 0)
    elif change == "wrong_size":
        window.size = (122.0, 56.0)
    else:
        window.scroll = (1.0, 0.0)
    assert not xp.XpSkill(observer, lambda *args: None).step(
        lambda p: pytest.fail("Unexpected click")
    )


def test_reordered_vector_before_click_is_rejected(monkeypatch):
    observer, blobs, _ = two_skills(monkeypatch)
    read = observer.adapter.read_block
    calls = [0]

    def changing(address, size):
        if address == 0x200000:
            calls[0] += 1
            if calls[0] > 1:
                return struct.pack("<4Q", 0x400000, 0, 0x300000, 0)
        return read(address, size)

    observer.adapter.read_block = changing
    with pytest.raises(ValueError, match="readiness changed"):
        xp.fly_point(observer, xp.read_xp(observer))
