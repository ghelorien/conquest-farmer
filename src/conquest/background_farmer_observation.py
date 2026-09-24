"""Bounded read-only evidence from the farmer's existing in-app session.

No observer, controller, bridge, or input object is created. The result never
qualifies input, selects an action, or changes Farming On/Off intent.
"""

from conquest.character_context import farmer_name
from copy import deepcopy
from dataclasses import asdict
import struct
import time

from conquest.memory_life import CLIENT_SHA256, read_life
from conquest.memory_ground import MemoryGroundReader, wanted_drop
from conquest.merchants.background_observation import BackgroundObservationReader


def _unavailable(reason):
    return {
        "schema_version": 1,
        "available": False,
        "character": farmer_name(),
        "source": "read_only_memory",
        "input_qualified": False,
        "reason": reason,
    }


def snapshot(ui):
    app = ui.app
    observer = getattr(app, "observer", None)
    if observer is None:
        return _unavailable("Farmer has no attached memory observer")
    if getattr(observer, "character", None) != farmer_name():
        return _unavailable("Attached farmer observer is not Parasite")
    if not observer.lock.acquire(timeout=0.1):
        return _unavailable("Farmer memory observer is busy")
    try:
        return _snapshot(app, observer)
    except (ValueError, OSError, RuntimeError, struct.error) as error:
        result = _unavailable("Farmer observation could not be verified")
        result["error_type"] = type(error).__name__
        return result
    finally:
        observer.lock.release()


def _snapshot(app, observer, *, clock=time.monotonic):
    started = clock()
    deadline = started + 3
    session = observer.adapter
    if session.expected_sha256 != CLIENT_SHA256:
        raise ValueError("Unsupported farmer fingerprint")
    session.assert_identity()
    identity = deepcopy(session.identity)
    control = app.control.snapshot()
    target = observer.operations.target
    geometry = target.snapshot()
    if geometry["pid"] != identity["pid"]:
        raise ValueError("Farmer geometry belongs to another process")
    client = getattr(app, "client", None)
    if client is not None and (
        client[0] != identity["pid"]
        or client[1] != target.hwnd
        or client[2] != identity
    ):
        raise ValueError("Farmer client selection differs from observer")
    life = read_life(session, observer.health_layout, farmer_name())
    motion_raw = session.read_block(life.object_address + 0x118, 8)
    if len(motion_raw) != 8:
        raise ValueError("Incomplete farmer motion sample")
    motion, frame = struct.unpack("<II", motion_raw)
    motion_stable = session.read_block(life.object_address + 0x118, 8) == motion_raw
    result = {
        "schema_version": 1,
        "available": True,
        "character": farmer_name(),
        "source": "read_only_memory",
        "input_qualified": False,
        "identity": identity,
        "geometry": geometry,
        "life": {**asdict(life), "dead_candidate": life.dead_candidate},
        "motion": {
            "id": motion,
            "animation_frame": frame,
            "stable": motion_stable,
            "jump_observed": motion_stable and motion in (130, 131),
            "run_observed": motion_stable and motion in (120, 121),
        },
        "control": {
            key: control.get(key) for key in ("enabled", "revision", "input_mode")
        },
        "selected_skill": {
            "available": False,
            "reason": "No qualified current selected-skill reader exists; learned skills do not prove selection",
        },
    }

    def optional(name, read):
        if clock() >= deadline:
            result[name] = {
                "available": False,
                "reason": "Observation budget exhausted",
            }
            return
        try:
            result[name] = {"available": True, "snapshot": read()}
        except (
            ValueError,
            OSError,
            RuntimeError,
            AttributeError,
            struct.error,
        ) as error:
            result[name] = {
                "available": False,
                "reason": "Memory reader unavailable or unstable",
                "error_type": type(error).__name__,
            }

    optional("gui", lambda: BackgroundObservationReader(session).snapshot())
    optional("inventory", lambda: asdict(observer.town_trade.inventory.read()))

    def entities():
        scene = observer.entities.read()
        ordered = sorted(
            scene.monsters,
            key=lambda monster: max(
                abs(a - b) for a, b in zip(monster.position, life.position)
            ),
        )
        return {
            "count": len(scene.monsters),
            "object_count": scene.object_count,
            "sampled_at": scene.timestamp,
            "nearby": [asdict(monster) for monster in ordered[:12]],
            "truncated": len(ordered) > 12,
            "note": "Scene membership and max HP do not prove current life or attack success",
        }

    optional("entities", entities)

    def ground():
        items = MemoryGroundReader(observer.entities).read()
        ordered = sorted(
            items,
            key=lambda item: max(
                abs(a - b) for a, b in zip(item.position, life.position)
            ),
        )
        return {
            "count": len(items),
            "nearby": [
                {**asdict(item), "eligible": wanted_drop(item)} for item in ordered[:12]
            ],
            "truncated": len(ordered) > 12,
            "note": "Disappearance does not prove receipt; compare verified inventory snapshots",
        }

    optional("ground", ground)
    after = read_life(session, observer.health_layout, farmer_name())
    if after.object_address != life.object_address or after.map_id != life.map_id:
        raise ValueError("Farmer actor or map changed during observation")
    session.assert_identity()
    if app.observer is not observer or session.identity != identity:
        raise ValueError("Farmer observer identity changed")
    latest_geometry = target.snapshot()
    if any(
        latest_geometry.get(key) != geometry.get(key)
        for key in ("pid", "hwnd", "client_size")
    ):
        raise ValueError("Farmer geometry changed during observation")
    result["position_changed_during_read"] = after.position != life.position
    result["ending_life"] = {**asdict(after), "dead_candidate": after.dead_candidate}
    result["control_changed_during_read"] = app.control.snapshot().get(
        "revision"
    ) != control.get("revision")
    result["elapsed_seconds"] = round(clock() - started, 4)
    # Existing readers have their own deadlines; never begin another optional
    # scan after this budget. This is not a hard interruption of an in-flight read.
    result["observation_budget_seconds"] = 3
    return result
