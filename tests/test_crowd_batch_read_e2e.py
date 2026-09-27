"""Town travel reads the whole scene crowd in one worker request.

Live 2026-09-27 every town travel step spent ~11.4 s in Crowd.observe: each of
~300 scene roles was its own read-block HTTP request under the observer lock,
so a 218-tile town walk took five minutes while monsters kept hitting.

Ways the batched crowd read can fail, written before the code:
1. A role object released between the vector read and its object read aborts
   the whole batch instead of skipping that one object.
2. A null or garbage pointer in the scene vector aborts the batch.
3. An older worker/bridge without "read-blocks" makes Crowd see nobody (every
   click treated as open ground) instead of falling back to per-object reads.
4. A malformed or oversized request (too many blocks, a block over 4 KiB, a
   bad address, more than 256 KiB in total) reaches ReadProcessMemory.
5. A reply with the wrong number of blocks is trusted and bodies are shifted
   onto the wrong objects.
6. The batch still costs one request per role (the regression itself).

The test drives Crowd.observe end to end: the real entity pointer chain and
the real worker Operations.dispatch over a fake process image, with every
request passed through a JSON round trip like the loopback bridge.
"""

import base64
import json
import struct

import pytest

from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.memory_health import HealthWorkerSession
from conquest.route_crowd import Crowd, FIRST_PLAYER_UID
from conquest.worker import Operations

BASE = 0x140000000
ROOT_RVA, COLLECTION_VTABLE_RVA, ROLE_VTABLE_RVA = 0x6B9B50, 0x5E8A60, 0x5E12D0
P1, P2, COLLECTION, VECTOR = 0x20000000, 0x20001000, 0x20002000, 0x20010000
OBJECTS = 0x30000000


class Image:
    """Process memory: readable segments only; anything else is released."""

    def __init__(self):
        self.segments = {}

    def put(self, address, data):
        self.segments[address] = bytearray(data)

    def drop(self, address):
        self.segments.pop(address)

    def read(self, address, size):
        for start, data in self.segments.items():
            if start <= address and address + size <= start + len(data):
                return bytes(data[address - start : address - start + size])
        raise OSError(f"ReadProcessMemory(address=0x{address:x}, size={size})")


def scene(roles):
    image = Image()
    image.put(BASE + ROOT_RVA, struct.pack("<Q", P1))
    image.put(P1, bytes(0x18) + struct.pack("<Q", P2) + bytes(0x20))
    image.put(P2, bytes(8) + struct.pack("<Q", COLLECTION) + bytes(0x10))
    collection = bytearray(0x80)
    struct.pack_into("<Q", collection, 0, BASE + COLLECTION_VTABLE_RVA)
    struct.pack_into("<QQ", collection, 0x58, VECTOR, VECTOR + 16 * len(roles))
    image.put(COLLECTION, collection)
    vector = bytearray(16 * len(roles))
    for index, (uid, tile, draw) in enumerate(roles):
        address = OBJECTS + index * 0x1000
        struct.pack_into("<Q", vector, index * 16 + 8, address)
        role = bytearray(0x100)
        struct.pack_into("<Q", role, 0, BASE + ROLE_VTABLE_RVA)
        struct.pack_into("<I", role, 0x78, uid)
        struct.pack_into("<2I", role, 0xE8, *tile)
        struct.pack_into("<2i", role, 0xF8, *draw)
        image.put(address, role)
    image.put(VECTOR, vector)
    return image


def roles(count):
    # Alternate players (server UIDs) and NPCs, spread over the screen.
    return [
        (
            (FIRST_PLAYER_UID + i) if i % 2 else (10000 + i),
            (300 + i % 40, 300 + i // 40),
            (-600 + (i % 40) * 30, -300 + (i // 40) * 60),
        )
        for i in range(count)
    ]


class Session:
    pid = 1
    identity = {"pid": 1, "path": "ImConquer.exe"}
    expected_sha256 = CLIENT_SHA256_1078
    modules = [{"name": "ImConquer.exe", "base": BASE, "size": 0x800000}]

    def __init__(self, image):
        self.image = image

    def assert_identity(self):
        pass

    def read(self, address, size):
        return self.image.read(address, size)


def worker_client(image, *, batch=True, truncate=False):
    """A HealthWorkerSession whose requests go through the real dispatch."""
    operations = Operations.__new__(Operations)
    operations.session = Session(image)
    operations.stopping = False
    operations.read_only = True
    operations.target = object()
    calls = []

    def request(operation, body=None):
        calls.append(operation)
        if operation == "health":
            return {"target": Session.identity}
        if operation == "read-blocks" and not batch:
            raise ValueError("Operation is not available in the embedded bridge")
        result = operations.dispatch(operation, json.loads(json.dumps(body or {})))
        result = json.loads(json.dumps(result))
        if operation == "read-blocks" and truncate:
            result["blocks"] = result["blocks"][:-1]
        return result

    client = HealthWorkerSession.__new__(HealthWorkerSession)
    client.request = request
    client.identity = Session.identity
    client.modules = Session.modules
    client.expected_sha256 = CLIENT_SHA256_1078
    return client, calls, operations


def test_scene_crowd_is_one_batch_request_and_keeps_every_body(tmp_path):
    population = roles(300)
    client, calls, _ = worker_client(scene(population))
    crowd = Crowd.observe(client, (512, 400))
    # The farmer's own sprite plus every role, each at its draw point.
    assert len(crowd.bodies) == 301
    assert crowd.bodies[0] == (0, (512, 400))
    assert crowd.bodies[1:] == tuple((uid, draw) for uid, _, draw in population)
    assert calls.count("read-blocks") == 1
    assert calls.count("read-block") == 3  # vector begin, end and entries
    assert len(calls) <= 12  # was 300+ requests, one per role
    uid, draw = crowd.bodies[5]
    assert crowd.covers((draw[0], draw[1] - 10))

    fallback_client, fallback_calls, _ = worker_client(
        scene(population), batch=False
    )
    fallback = Crowd.observe(fallback_client, (512, 400))
    assert fallback.bodies == crowd.bodies
    assert fallback_calls.count("read-block") == 303
    (tmp_path / "crowd-batch-read.json").write_text(
        json.dumps(
            {
                "roles": len(population),
                "bodies": len(crowd.bodies),
                "batched_requests": len(calls),
                "per_role_requests": len(fallback_calls),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_released_objects_and_bad_pointers_are_skipped_not_fatal():
    population = roles(40)
    image = scene(population)
    image.drop(OBJECTS + 5 * 0x1000)  # released between vector and object read
    vector = image.segments[VECTOR]
    struct.pack_into("<Q", vector, 7 * 16 + 8, 0)  # null pointer
    struct.pack_into("<Q", vector, 9 * 16 + 8, 0xFFFFFFFFFFFFFFF0)  # garbage
    client, calls, _ = worker_client(image)
    crowd = Crowd.observe(client, (512, 400))
    expected = [
        (uid, draw)
        for index, (uid, _, draw) in enumerate(population)
        if index not in (5, 7, 9)
    ]
    assert list(crowd.bodies[1:]) == expected
    assert calls.count("read-blocks") == 1


def test_a_reply_with_the_wrong_block_count_is_not_trusted():
    population = roles(12)
    client, calls, _ = worker_client(scene(population), truncate=True)
    crowd = Crowd.observe(client, (512, 400))
    # The short reply is rejected and every body comes from exact reads.
    assert crowd.bodies[1:] == tuple((uid, draw) for uid, _, draw in population)
    assert calls.count("read-block") == 15


@pytest.mark.parametrize(
    "blocks,message",
    [
        ([], "1 to 1024 blocks"),
        ([{"address": "0x20000", "size": 8}] * 1025, "1 to 1024 blocks"),
        ([{"address": "0x20000", "size": 4097}], "1 to 4096 bytes"),
        ([{"address": "0x20000", "size": 0}], "1 to 4096 bytes"),
        ([{"address": 131072, "size": 8}], "hexadecimal address"),
        (["0x20000"], "hexadecimal address"),
        ([{"address": "0x0", "size": 8}], "outside user memory"),
        ([{"address": "0x20000", "size": 4096}] * 65, "exceed 256 KiB"),
    ],
)
def test_worker_rejects_malformed_batches_before_reading(blocks, message):
    image = Image()
    _, _, operations = worker_client(image)

    def fail_read(*_):
        pytest.fail("A rejected batch must not read memory")

    operations.session.read = fail_read
    with pytest.raises(ValueError, match=message):
        operations.dispatch("read-blocks", {"blocks": blocks})


def test_worker_batch_preserves_bytes_and_marks_released_blocks():
    image = Image()
    image.put(0x20000, bytes(range(256)))
    _, _, operations = worker_client(image)
    result = operations.dispatch(
        "read-blocks",
        {
            "blocks": [
                {"address": "0x20000", "size": 16},
                {"address": "0x90000", "size": 16},
                {"address": "0x20010", "size": 4},
            ]
        },
    )
    assert result["encoding"] == "base64" and not result["qualified"]
    first, released, last = result["blocks"]
    assert base64.b64decode(first) == bytes(range(16))
    assert released is None
    assert base64.b64decode(last) == bytes(range(16, 20))
