"""Automatic leveling reads the level through the build-qualified reader.

Live 2026-09-26 on build 1078: the route process read the level at the actor
+0x6E8 and got 0 while the character was level 7 (+0x6F8, the 1078 player
layout's level field, also what the equipment reader returned). Every check
was rejected silently, so no route change ever happened on this PC.

Failure modes this module must catch (written before the implementation):

1. The level comes from a fixed offset the client build does not use, so
   automatic leveling never fires (the field reads 0) or fires for a wrong
   level (the field holds another small number).
2. A dead character, a stale observation or a changed character still yields
   a level.
3. A character the build-qualified reader does not identify as an archer
   yields a level.
4. A failed level read stops or delays the hunt instead of skipping this check.
5. Reading the level sends input.

The end-to-end test drives the route process's level check against a
memory image laid out like the live 1078 client, through the real 1078 player
layout and the real equipment reader, and writes ``level-read.json``; the
scenario runs twice in separate roots and must produce byte-identical
artifacts.
"""

import json
from pathlib import Path
import struct
import time
from types import SimpleNamespace

from conquest import leveling_routes, overnight, session_plan, world_travel
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.overnight import OvernightLoop
from conquest.routes import RouteLibrary

ACTOR = 0x2A000000
LEVEL = ACTOR + 0x6F8
PROFESSION = ACTOR + 0x6E4
OLD_FIELD = ACTOR + 0x6E8
READ_ONLY = {"health", "sample", "town:gear"}


class Client:
    """The farmer's memory as the 1078 client lays it out, behind one worker."""

    def __init__(self):
        self.cells = {LEVEL: 5, PROFESSION: 40, OLD_FIELD: 0}
        self.dead = False
        self.stale = False
        self.map_id = 1002
        self.operations = []
        self.selected = "pheasant"

    def read_block(self, address, size):
        if address in self.cells:
            return struct.pack("<I", self.cells[address]) + bytes(size - 4)
        return bytes(size)  # Empty equipment slots and unrelated fields.

    def life(self):
        return SimpleNamespace(
            object_address=ACTOR, dead_candidate=self.dead, map_id=self.map_id
        )

    def request(self, info, operation, body=None):
        if operation == "town" and body == {"action": "gear"}:
            self.operations.append("town:gear")
            from conquest.equipment import read_equipment

            adapter = SimpleNamespace(
                expected_sha256=CLIENT_SHA256_1078,
                modules=[{"name": "ImConquer.exe", "base": 0x400000}],
                read_block=self.read_block,
                assert_identity=lambda: None,
            )
            return read_equipment(SimpleNamespace(adapter=adapter, read_life=self.life))
        self.operations.append(
            operation if operation in ("health", "sample") else f"{operation}:{body}"
        )
        if operation == "health":
            return self.health()
        if operation == "sample":
            return {
                "fields": [
                    {
                        "value": [
                            struct.unpack(
                                "<I", self.read_block(int(f["address"], 16), 4)
                            )[0]
                        ]
                    }
                    for f in body["fields"]
                ]
            }
        if operation == "controls" and "route_id" in body:
            self.selected = body["route_id"]
        return {}

    def health(self):
        life = self.life()
        return {
            "embedded_controls": {
                "observed_at": time.time() - (5 if self.stale else 0),
                "life": {
                    "object_address": life.object_address,
                    "dead_candidate": life.dead_candidate,
                    "map_id": life.map_id,
                },
            }
        }


def _scenario(root, monkeypatch):
    root = Path(root)
    root.mkdir(parents=True)
    monkeypatch.setattr(session_plan, "PLAN", root / "no-plan.json")
    client = Client()
    monkeypatch.setattr(leveling_routes, "request", client.request)
    monkeypatch.setattr(overnight, "request", client.request)
    monkeypatch.setattr(
        overnight, "read_status", lambda path: {"selected_route": client.selected}
    )
    monkeypatch.setattr(overnight, "read_terrain", lambda *a: None)
    monkeypatch.setattr(world_travel, "travel_to_map", lambda loop, destination: None)
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = RouteLibrary().load("pheasant")
    loop.auto_level, loop.next_level_check, loop.last_level = True, 0, 0
    loop.info, loop.phase = "worker", "hunting"
    loop.health = client.health
    events = []
    loop.record = lambda event, **fields: events.append(event)
    loop.stop_farm = lambda: client.operations.append("stop_farm")
    loop.queue_route_optimization = lambda: None

    def check(label, **memory):
        for name, value in memory.items():
            if name in ("dead", "stale"):
                setattr(client, name, value)
            else:
                client.cells[
                    {"level": LEVEL, "profession": PROFESSION, "old": OLD_FIELD}[name]
                ] = value
        client.operations.clear()
        events.clear()
        loop.next_level_check = 0
        changed = loop.select_level_route(client.health())
        return {
            "step": label,
            "changed": changed,
            "route": loop.route.id,
            "app_route": client.selected,
            "operations": list(client.operations),
            "events": list(events),
        }

    steps = [
        check("level_5_old_field_0", level=5, old=0),
        check("level_6_old_field_0", level=6),
        check("level_7", level=7),
        check("dead_at_11", level=11, dead=True),
        check("stale_at_11", dead=False, stale=True),
        check("not_an_archer_at_11", stale=False, profession=10),
        check("level_11_archer", profession=40),
        check("level_12_old_field_3", level=12, old=3),
    ]
    path = root / "level-read.json"
    path.write_text(json.dumps(steps, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_level_read_e2e(tmp_path, monkeypatch):
    first = _scenario(tmp_path / "pc-a", monkeypatch)
    second = _scenario(tmp_path / "pc-b", monkeypatch)
    assert first.read_bytes() == second.read_bytes()
    steps = {s["step"]: s for s in json.loads(first.read_text(encoding="utf-8"))}
    route = {
        name: (s["changed"], s["route"], s["app_route"]) for name, s in steps.items()
    }
    # Mode 1: the real level (+0x6F8) drives the ladder; the old field never does.
    assert route == {
        "level_5_old_field_0": (False, "pheasant", "pheasant"),
        "level_6_old_field_0": (True, "turtledove", "turtledove"),
        "level_7": (False, "turtledove", "turtledove"),
        # Modes 2 and 3: no level, no move; the hunt simply continues.
        "dead_at_11": (False, "turtledove", "turtledove"),
        "stale_at_11": (False, "turtledove", "turtledove"),
        "not_an_archer_at_11": (False, "turtledove", "turtledove"),
        "level_11_archer": (True, "robin", "robin"),
        "level_12_old_field_3": (False, "robin", "robin"),
    }
    # Mode 4: a rejected read skips only this check (no stop, no event storm).
    for name in ("dead_at_11", "stale_at_11", "not_an_archer_at_11"):
        assert "stop_farm" not in steps[name]["operations"]
        assert steps[name]["events"] == []
    # Mode 5: reading the level sends no input; input only follows a real move.
    for step in steps.values():
        if not step["changed"]:
            assert all(op in READ_ONLY for op in step["operations"])
    for name, route_id in (
        ("level_6_old_field_0", "turtledove"),
        ("level_11_archer", "robin"),
    ):
        operations = steps[name]["operations"]
        assert all(op in READ_ONLY for op in operations[:-2])
        assert operations[-2:] == [
            "stop_farm",
            f"controls:{{'route_id': '{route_id}'}}",
        ]
