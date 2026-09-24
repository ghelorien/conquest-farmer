import struct
from types import SimpleNamespace as NS
import pytest
from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.flag_target import CODE, flag_target


@pytest.fixture
def flag():
    base = 0x140000000
    actor = 0x20000000
    graphics = 0x21000000
    renderer = 0x22000000
    model = 0x23000000
    mesh = 0x24000000
    box = 0x25000000
    data = {}

    def put(a, fmt, *values):
        data.update({a + i: v for i, v in enumerate(struct.pack(fmt, *values))})

    for rva, encoded in CODE.items():
        raw = bytes.fromhex(encoded)
        put(base + rva, f"<{len(raw)}s", raw)
    identity = (
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    )
    world = list(identity)
    world[12:15] = [-64.0, -32.5, 0.0]
    projection = list(identity)
    projection[0] = 2 / 1888
    projection[5] = -2 / 665
    fields = [
        (actor, "<Q", (base + 0x5C5E20,)),
        (actor + 0x78, "<I", (101465,)),
        (actor + 0x84, "<I", (1086,)),
        (actor + 0xA4, "<32s", (b"ShopFlag",)),
        (actor + 0xE8, "<2I", (269, 174)),
        (actor + 0xF8, "<2i", (880, 300)),
        (actor + 0x2F8, "<Q", (graphics,)),
        (graphics, "<Q", (base + 0x5D4850,)),
        (graphics + 0x98, "<Q", (renderer,)),
        (renderer, "<Q", (base + 0x597768,)),
        (renderer + 0x50, "<Q", (model,)),
        (model + 0x140, "<2I", (1, 9990129)),
        (model + 0x230, "<Q", (mesh,)),
        (mesh, "<Q", (base + 0x576CF0,)),
        (mesh + 0x164, "<I", (1,)),
        (mesh + 0x168, "<Q", (box,)),
        (box + 0x60, "<6f", (-4.0, -28.0, -4.0, 10.0, -20.0, 4.0)),
        (model + 12, "<16f", world),
        (base + 0x676750, "<16f", identity),
        (base + 0x676790, "<16f", projection),
        (base + 0x676718, "<4I2f", (0, 0, 1888, 665, 0.0, 1.0)),
        (base + 0x699360 + 0x26C, "<4i", (256, 1888, 665, 100)),
        (base + 0x6545A0, "<I", (1,)),
    ]
    for a, fmt, values in fields:
        put(a, fmt, *values)
    session = NS(
        expected_sha256=CLIENT_SHA256,
        modules=[dict(name="imconquer.exe", base=base, size=0x3000000)],
        read_block=lambda a, n: bytes(data.get(a + i, 0) for i in range(n)),
        assert_identity=lambda: None,
    )
    entity = dict(
        address=actor,
        uid=101465,
        name="ShopFlag",
        model=1086,
        type_id=0,
        position=[269, 174],
        draw_position=[880, 300],
    )
    return NS(
        observer=NS(adapter=session),
        entity=entity,
        put=put,
        base=base,
        actor=actor,
        model=model,
        mesh=mesh,
        box=box,
    )


def test_collision_center_replaces_fixed_npc_offset(flag):
    assert flag_target(flag.observer, flag.entity)["point"] == (883, 276)


@pytest.mark.parametrize(
    "case",
    [
        "build",
        "uid",
        "model",
        "bounds",
        "matrix",
        "viewport",
        "orientation",
        "code",
        "torn",
        "expired",
    ],
)
def test_unknown_or_changed_geometry_never_produces_target(flag, case, monkeypatch):
    s = flag.observer.adapter
    b = flag.base
    if case == "build":
        s.expected_sha256 = "unknown"
    if case == "uid":
        flag.put(flag.actor + 0x78, "<I", 1)
    if case == "model":
        flag.put(flag.model + 0x140, "<I", 2)
    if case == "bounds":
        flag.put(flag.box + 0x60, "<f", float("nan"))
    if case == "matrix":
        flag.put(flag.model + 12, "<f", float("inf"))
    if case == "viewport":
        flag.put(b + 0x676718 + 8, "<I", 100)
    if case == "orientation":
        flag.put(b + 0x6545A0, "<I", 2)
    if case == "code":
        flag.put(b + 0x3019C0, "<B", 0)
    if case == "torn":
        original = s.read_block
        count = 0

        def read(a, n):
            nonlocal count
            if a == flag.actor + 0x78:
                count += 1
                if count > 1:
                    return struct.pack("<I", 1)
            return original(a, n)

        s.read_block = read
    if case == "expired":
        ticks = iter([0, 3])
        monkeypatch.setattr(
            "conquest.merchants.flag_target.time.monotonic", lambda: next(ticks)
        )
    with pytest.raises(ValueError):
        flag_target(flag.observer, flag.entity)


def test_resource_lookup_is_bounded_and_returns_exact_model(flag):
    b = flag.base
    manager = 0x26000000
    head = 0x27000000
    node = 0x28000000
    resource = 0x29000000
    for a, v in [
        (flag.model + 0x230, 0),
        (b + 0x675120, manager),
        (manager, b + 0x577590),
        (manager + 0x260, head),
        (head, node),
        (node, head),
        (node + 0x18, resource),
        (resource + 8, flag.mesh),
    ]:
        flag.put(a, "<Q", v)
    flag.put(node + 0x10, "<I", 9990129)
    assert flag_target(flag.observer, flag.entity)["point"] == (883, 276)
    flag.put(node + 0x10, "<I", 123)
    flag.put(node, "<Q", node)
    with pytest.raises(ValueError, match="collection"):
        flag_target(flag.observer, flag.entity)
