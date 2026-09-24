"""Record real map-coordinate travel from read-only memory; never send input."""

import math
import time
from pathlib import Path

import yaml

from conquest.addressing import PlayerLayout, WorkerPointerSession, resolve_player


def read_position(session, layout, character):
    started = time.monotonic()
    addresses = resolve_player(session, layout)
    sample = session.request(
        "sample",
        {
            "fields": [
                {"name": n, "address": hex(addresses[n]), "kind": k}
                for n, k in (("name", "utf8"), ("position", "xy_u32"), ("map", "u32"))
            ]
        },
    )
    values = {f["name"]: f["value"] for f in sample["fields"]}
    if values["name"] != character or resolve_player(session, layout) != addresses:
        raise ValueError("Character identity changed during route recording")
    if time.monotonic() - started > 1:
        raise ValueError("Route position expired")
    point = tuple(values["position"])
    if len(point) != 2 or any(not 0 <= v <= 1024 for v in point):
        raise ValueError("Invalid map position")
    return values["map"][0], point


def record_route(
    reader,
    seconds,
    *,
    interval=0.5,
    spacing=3,
    clock=time.monotonic,
    sleep=time.sleep,
    stopped=lambda: False,
):
    if not math.isfinite(seconds) or not 0 < seconds <= 1800:
        raise ValueError("Route recording duration must be between 0 and 1800 seconds")
    if not 0.1 <= interval <= 5 or not 1 <= spacing <= 10:
        raise ValueError("Invalid recording interval or waypoint spacing")
    started = clock()
    map_id, previous, last = None, None, None
    route, observations, reason = [], [], "duration_limit"
    while clock() - started < seconds:
        if stopped():
            reason = "requested_stop"
            break
        current_map, point = reader()
        point = tuple(point)
        if map_id is None:
            map_id = current_map
        if current_map != map_id:
            reason = "map_changed"
            break
        if previous is not None and math.dist(previous, point) > 30:
            reason = "position_discontinuity"
            break
        observations.append(
            {"elapsed": round(clock() - started, 3), "position": list(point)}
        )
        if not route or math.dist(route[-1], point) >= spacing:
            route.append(list(point))
        previous = last = point
        sleep(interval)
    if last is not None and list(last) != route[-1]:
        route.append(list(last))
    return {
        "schema_version": 1,
        "map_id": map_id,
        "route": route,
        "observations": observations,
        "duration": round(clock() - started, 3),
        "reason": reason,
        "qualified": False,
        "input_sent": False,
        "closed_loop": False,
    }


def run_recording(profile, info, output, character, seconds, spacing=3):
    import win32api

    layout = PlayerLayout.model_validate(yaml.safe_load(Path(profile).read_text()))
    session = WorkerPointerSession(info, layout.expected_sha256)
    result = record_route(
        lambda: read_position(session, layout, character),
        seconds,
        spacing=spacing,
        stopped=lambda: bool(win32api.GetAsyncKeyState(0x7B) & 0x8000),
    )
    result.update(
        character=character,
        expected_sha256=layout.expected_sha256,
        process_identity=session.identity,
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(result, sort_keys=False), encoding="utf-8")
    return {
        "output": str(output),
        "waypoints": len(result["route"]),
        "reason": result["reason"],
        "input_sent": False,
        "qualified": False,
    }
