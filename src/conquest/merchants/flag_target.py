"""Project the pinned flag collision box using read-only native geometry."""

import math
import struct
import time

from conquest.addressing import checked_address
from conquest.memory_life import CLIENT_SHA256

CONTROL = {"mode": "native_flag_bounds", "revision": 1}
CODE = {
    0x260780: "40535556574883ec38488bd9418bf9488b8998000000418bf08bea4885c9750fe8fb781f00488bc8488983980000008b842480000000448bcf4c8b11448bc68bd58944242041ff52180fb6c04883c4385f5e5d5bc3",
    0x4576A0: "405356574883ec50488b0551de1f004833c44889442440488bd9418bf0488b49508bfa4885c97431e8332a000084c074284c8d4c24208bd64c8d4424308bcfe80cfce9ff488b4b504c8d442420488d542430e829240000eb0232c0",
    0x459B40: "498bf8488bda4c8d410c488be9488d4c246033d2e89e6fffff4c8d442460488bd3488d4c2430e8806fffff4c8d442460488bd7488d4c2420e85c6fffff",
    0x3019C0: "8b8164010000c3",
    0x301660: "448b91640100008bc2ba010000004d8bd9443bd2440f42d233d241f7f24c63ca4a8b84c968010000f20f104060f2410f11008b4068418940084a8b84c968010000f20f10406cf2410f11038b407441894308c3",
    0x31010E: "e8cdf8dcff482386880200004c8b86700200004803c0488b9660020000498b4cc008483bca7460498b04c03b791074106690483bc87450488b49083b791075f24885c9480f44ca483bca743b488b5918",
    0x31018C: "488b4308e9d8000000",
    0x335800: "8b059aed310083f8017504488b01c3",
}


def project(point, matrices, viewport):
    values = [*point, 1.0]
    for matrix in matrices:
        if len(matrix) != 16 or not all(math.isfinite(x) for x in matrix):
            raise ValueError("Invalid flag projection matrix")
        values = [
            sum(values[i] * matrix[i * 4 + j] for i in range(4)) for j in range(4)
        ]
    if not all(math.isfinite(x) for x in values) or abs(values[3]) < 1e-6:
        raise ValueError("Invalid flag projection")
    x, y, width, height = viewport[:4]
    return (
        x + (values[0] / values[3] + 1) * width / 2,
        y + (1 - values[1] / values[3]) * height / 2,
    )


def flag_target(observer, flag):
    """A target is geometry only: callers must separately recheck vacancy."""
    s = observer.adapter
    started = time.monotonic()
    if s.expected_sha256 != CLIENT_SHA256:
        raise ValueError("Flag target client is unqualified")
    modules = [m for m in s.modules if m["name"].casefold() == "imconquer.exe"]
    if len(modules) != 1 or modules[0]["size"] < 0x699600:
        raise ValueError("Flag target module is invalid")
    base = modules[0]["base"]
    samples = []

    def read(address, fmt):
        size = struct.calcsize(fmt)
        checked_address(address, size)
        raw = s.read_block(address, size)
        samples.append((address, raw))
        return struct.unpack(fmt, raw)

    def pointer(address):
        result = read(address, "<Q")[0]
        checked_address(result)
        return result

    s.assert_identity()
    for rva, encoded in CODE.items():
        expected = bytes.fromhex(encoded)
        if s.read_block(base + rva, len(expected)) != expected:
            raise ValueError("Native flag collision code changed")
    a = flag["address"]
    if (
        flag.get("name") != "ShopFlag"
        or flag.get("model") != 1086
        or flag.get("type_id") != 0
        or read(a, "<Q")[0] != base + 0x5C5E20
        or read(a + 0x78, "<I")[0] != flag["uid"]
        or read(a + 0x84, "<I")[0] != 1086
        or read(a + 0xA4, "<32s")[0].split(b"\0")[0] != b"ShopFlag"
        or list(read(a + 0xE8, "<2I")) != flag["position"]
    ):
        raise ValueError("Flag actor identity changed")
    graphics = pointer(a + 0x2F8)
    if read(graphics, "<Q")[0] != base + 0x5D4850:
        raise ValueError("Unsupported flag graphics")
    renderer = pointer(graphics + 0x98)
    if read(renderer, "<Q")[0] != base + 0x597768:
        raise ValueError("Unsupported flag renderer")
    model = pointer(renderer + 0x50)
    if read(model + 0x140, "<2I") != (1, 9990129):
        raise ValueError("Unsupported flag collision model")
    mesh = read(model + 0x230, "<Q")[0]
    if not mesh:
        manager = pointer(base + 0x675120)
        if read(manager, "<Q")[0] != base + 0x577590:
            raise ValueError("Flag resource manager changed")
        head = pointer(manager + 0x260)
        node = pointer(head)
        seen = set()
        while node != head:
            if node in seen or len(seen) >= 4096:
                raise ValueError("Flag resource collection is invalid")
            seen.add(node)
            if read(node + 0x10, "<I")[0] == 9990129:
                mesh = pointer(pointer(node + 0x18) + 8)
                break
            node = pointer(node)
        if not mesh:
            raise ValueError("Flag collision resource is not loaded")
    if read(mesh, "<Q")[0] != base + 0x576CF0 or read(mesh + 0x164, "<I")[0] != 1:
        raise ValueError("Unsupported flag collision bounds")
    bounds = read(pointer(mesh + 0x168) + 0x60, "<6f")
    if not all(math.isfinite(v) and abs(v) < 1000 for v in bounds) or any(
        not bounds[i] < bounds[i + 3] for i in range(3)
    ):
        raise ValueError("Invalid flag collision bounds")
    matrices = [
        read(model + 0xC, "<16f"),
        read(base + 0x676750, "<16f"),
        read(base + 0x676790, "<16f"),
    ]
    viewport = read(base + 0x676718, "<4I2f")
    width, height = viewport[2:4]
    camera = base + 0x699360
    zoom, camera_width, camera_height, percent = read(camera + 0x26C, "<4i")
    if (
        read(base + 0x6545A0, "<I")[0] != 1
        or viewport[:2] != (0, 0)
        or viewport[4:] != (0.0, 1.0)
        or (width, height) != (camera_width, camera_height)
        or not 300 <= min(width, height) <= max(width, height) <= 16384
        or not 1 <= zoom <= 1024
        or not 1 <= percent <= 400
    ):
        raise ValueError("Unsupported flag viewport or camera")
    origin = project((0.0, 0.0, 0.0), matrices, viewport)
    draw = read(a + 0xF8, "<2i")
    if list(draw) != flag["draw_position"] or any(
        abs(p - q) > 1 for p, q in zip(origin, draw)
    ):
        raise ValueError("Flag model projection differs from its scene position")
    center = tuple((bounds[i] + bounds[i + 3]) / 2 for i in range(3))
    unscaled = project(center, matrices, viewport)
    scale = (zoom * percent // 100) / 256
    if not 0.25 <= scale <= 4:
        raise ValueError("Unsupported flag zoom")
    point = tuple(
        round((v - d // 2) * scale + d // 2) for v, d in zip(unscaled, (width, height))
    )
    if not (80 < point[0] < width - 80 and 170 < point[1] < height - 160):
        raise ValueError("Flag target is outside the usable scene")
    if any(s.read_block(address, len(raw)) != raw for address, raw in samples):
        raise ValueError("Flag geometry changed during observation")
    s.assert_identity()
    if time.monotonic() - started > 2:
        raise ValueError("Flag geometry observation expired")
    return {"point": point, "bounds": bounds, "control": dict(CONTROL)}
