"""A restarted controller selects its own route in the app before hunting.

Toxic 2026-09-28 15:53: deployed as its hold moved to Ratlings, the new
controller loaded the plan's Ratling route while the app restored its saved
WingedSnake selection, and every start failed with "Selected route and
monster group differ".
"""

import json

from conquest import overnight
from conquest.overnight import OvernightLoop, select_app_route
from conquest.routes import RouteLibrary


def loop_with_app(tmp_path, monkeypatch, state):
    app = tmp_path / "app-state.json"
    app.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(
        overnight,
        "state_path",
        lambda value: str(app) if str(value).endswith("app-state.json") else value,
    )
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.info = "unused"
    loop.route = RouteLibrary().load("ratling")
    loop.check_stop = lambda: None
    events, stops, calls = [], [], []
    loop.record = lambda event, **fields: events.append((event, fields))
    loop.stop_farm = lambda: stops.append(True)

    def request(info, operation, body):
        calls.append(body)
        if "route_id" in body:
            app.write_text(
                json.dumps({"selected_route": body["route_id"]}), encoding="utf-8"
            )
        return {"route_queued": body.get("route_id")}

    monkeypatch.setattr(overnight, "request", request)
    return loop, calls, stops, events


def test_a_restarted_controller_selects_its_route_in_the_app(tmp_path, monkeypatch):
    loop, calls, stops, events = loop_with_app(
        tmp_path, monkeypatch, {"selected_route": "wingedsnake"}
    )
    assert select_app_route(loop)
    assert stops and calls == [{"route_id": "ratling"}]
    assert events[-1][0] == "app_route_selected"
    assert events[-1][1]["previous_route"] == "wingedsnake"


def test_an_app_without_a_selection_gets_the_controllers_route(tmp_path, monkeypatch):
    loop, calls, stops, events = loop_with_app(
        tmp_path, monkeypatch, {"selected_route": None}
    )
    assert select_app_route(loop)
    assert calls == [{"route_id": "ratling"}]


def test_a_matching_app_route_is_left_alone(tmp_path, monkeypatch):
    loop, calls, stops, events = loop_with_app(
        tmp_path, monkeypatch, {"selected_route": "ratling"}
    )
    assert not select_app_route(loop)
    assert not stops and not calls and not events


def test_a_refusal_while_the_farm_winds_down_is_retried(tmp_path, monkeypatch):
    loop, calls, stops, events = loop_with_app(
        tmp_path, monkeypatch, {"selected_route": "wingedsnake"}
    )
    answer = overnight.request
    refusals = [ValueError("Stop farming before selecting another route")]

    def request(info, operation, body):
        if refusals:
            raise refusals.pop()
        return answer(info, operation, body)

    monkeypatch.setattr(overnight, "request", request)
    assert select_app_route(loop)
    assert calls == [{"route_id": "ratling"}]


def test_an_unreadable_or_older_app_state_leaves_the_hunt_as_it_was(
    tmp_path, monkeypatch
):
    # An app state without the field (older app or test fakes) is not a
    # selection to correct.
    loop, calls, stops, events = loop_with_app(tmp_path, monkeypatch, {"kills": 0})
    assert not select_app_route(loop)
    (tmp_path / "app-state.json").unlink()
    assert not select_app_route(loop)
    assert not stops and not calls
