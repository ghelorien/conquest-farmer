import struct
from types import SimpleNamespace as NS

import pytest

from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.booth_target import CODE, owned_booth_target


@pytest.fixture
def booth():
    base, address, graphics = 0x140000000, 0x20000000, 0x21000000
    data = {}

    def put(a, fmt, *values):
        for i, value in enumerate(struct.pack(fmt, *values)):
            data[a + i] = value

    for rva, value in CODE.items():
        raw = bytes.fromhex(value)
        put(base + rva, f"<{len(raw)}s", raw)
    for a, fmt, values in [
        (address, "<Q", (base + 0x5C5E20,)),
        (address + 0x10, "<Q", (base + 0x5CFA70,)),
        (base + 0x5CFA70 + 0x28, "<Q", (base + 0x181A20,)),
        (address + 0x78, "<I", (103064,)),
        (address + 0x84, "<I", (406,)),
        (address + 0xA4, "<32s", (b"Spiritual",)),
        (address + 0xE8, "<2I", (272, 174)),
        (address + 0xF8, "<2i", (976, 348)),
        (address + 0x2C0, "<I", (14,)),
        (address + 0x2F8, "<Q", (graphics,)),
        (graphics, "<Q", (base + 0x5D49D0,)),
        (base + 0x5D49D0 + 0x40, "<Q", (base + 0x262160,)),
        (graphics + 0xC, "<i", (6,)),
        (base + 0x5BF670 + 6 * 40, "<10i", (0, 1, 0, 0, 0, 0, 0, 0, 0, -1)),
        (base + 0x699360 + 0x30, "<2i", (384, 384)),
        (base + 0x699360 + 0x44, "<4i", (12288, 16, 14448, 6804)),
        (base + 0x699360 + 0x26C, "<4i", (256, 1888, 665, 100)),
        (base + 0x6545A0, "<I", (1,)),
        (base + 0x6766E0, "<2i", (1888, 665)),
        (base + 0x5D4D28, "<d", (1 / 64,)),
        (base + 0x5D4D40, "<d", (1 / 32,)),
    ]:
        put(a, fmt, *values)
    session = NS(
        expected_sha256=CLIENT_SHA256,
        modules=[{"name": "imconquer.exe", "base": base, "size": 0x3000000}],
        read_block=lambda a, n: bytes(data.get(a + i, 0) for i in range(n)),
        assert_identity=lambda: None,
    )
    entity = dict(
        address=address,
        uid=103064,
        name="Spiritual",
        model=406,
        type_id=0,
        position=[272, 174],
        draw_position=[976, 348],
    )
    return NS(
        observer=NS(adapter=session, character="Spiritual"),
        entity=entity,
        base=base,
        address=address,
        graphics=graphics,
        put=put,
    )


def test_owned_booth_uses_accepted_ground_tile_instead_of_flag_offset(booth):
    target = owned_booth_target(booth.observer, booth.entity)
    assert target["point"] == (976, 348)
    assert target["tile"] == (272, 174)
    assert target["footprint"] == ((0, -1), (0, 0), (0, 1))
    # The prior -32 Y point maps one tile northwest, outside this booth.
    old_relative_tile = (-1, -1)
    assert old_relative_tile not in target["footprint"]


@pytest.mark.parametrize(
    "case",
    [
        "foreign",
        "uid",
        "hit_kind",
        "graphics",
        "orientation",
        "code",
        "camera",
        "zoom",
        "projection",
        "torn",
        "expired",
    ],
)
def test_changed_or_unqualified_booth_has_no_target(booth, case, monkeypatch):
    b = booth.base
    a = booth.address
    if case == "foreign":
        booth.entity["name"] = "Dutch"
    if case == "uid":
        booth.put(a + 0x78, "<I", 999)
    if case == "hit_kind":
        booth.put(a + 0x2C0, "<I", 0)
    if case == "graphics":
        booth.put(booth.graphics, "<Q", b + 0x5D4850)
    if case == "orientation":
        booth.put(booth.graphics + 0xC, "<i", -1)
    if case == "code":
        booth.put(b + 0x262165, "<B", 0)
    if case == "camera":
        booth.put(b + 0x6545A0, "<I", 2)
    if case == "zoom":
        booth.put(b + 0x699360 + 0x26C, "<i", 0)
    if case == "projection":
        booth.put(b + 0x699360 + 0x44, "<i", 12290)
    if case == "torn":
        read = booth.observer.adapter.read_block
        seen = 0

        def torn(addr, n):
            nonlocal seen
            if addr == a + 0x78:
                seen += 1
                if seen > 1:
                    return struct.pack("<I", 999)
            return read(addr, n)

        booth.observer.adapter.read_block = torn
    if case == "expired":
        ticks = iter([0, 3])
        monkeypatch.setattr(
            "conquest.merchants.booth_target.time.monotonic", lambda: next(ticks)
        )
    with pytest.raises(ValueError):
        owned_booth_target(booth.observer, booth.entity)


@pytest.mark.parametrize(
    "width,height,percent", [(1416, 907, 100), (1888, 665, 75), (2560, 1440, 125)]
)
def test_camera_size_and_zoom_are_applied_and_round_trip_to_booth(
    booth, width, height, percent
):
    booth.put(booth.base + 0x699360 + 0x26C, "<4i", 256, width, height, percent)
    booth.put(booth.base + 0x6766E0, "<2i", width, height)
    target = owned_booth_target(booth.observer, booth.entity)
    assert target["tile"] == (272, 174)
    scale = (256 * percent // 100) / 256
    assert target["point"] == tuple(
        round((v - d // 2) * scale + d // 2)
        for v, d in zip((976, 348), (width, height))
    )
