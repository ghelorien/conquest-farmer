"""GeneralPeace's crossing into the Desert (desert_gate).

Alex 2026-09-30 19:3x: "there is a npc that will bring you to the desert". His
dialog was unread when this was written, so a crossing presses only approved
pages or pages offer() accepts, and counts only once memory shows the Desert.

Failure modes, written before the change:
1. A page asking for input, a fare above MAX_FARE or another currency, or
   with several affirmatives, gets an option pressed.
2. A page nobody approved and offer() refuses is pressed, or is not saved.
3. A crossing counts without the Desert's map in memory, or presses again
   after a press that did not cross.
4. A crossing starts without the route's gate home (the Desert has no saved
   way out), or with an NPC whose identity differs from the survey.
5. The approved pages, the fare and the landing are not kept after a
   crossing, so the next one is not exact.
6. The way in rides the Conductress again although the farmer stands beside
   him, or skips MrBuffer when his buff is due on the square.
"""

import time
from types import SimpleNamespace as NS

import pytest

from conquest import desert_gate

PEACE = {
    "map_id": 1002,
    "type_id": 0,
    "name": "GeneralPeace",
    "model": 296,
    "position": [60, 463],
}
APE_GATE = 1060022
TWIN_GATE = 1060020
TWIN_TOWN = {
    "map_id": 1002,
    "town_boundary": [348, 209, 507, 433],
    "services": {"pharmacist": [466, 333]},
}
END =[{"kind": 3, "option": 255, "text": ""}, {"kind": 4, "option": 255, "text": ""}]


def page(text, *options):
    return [
        {"kind": 0, "option": 0, "text": text},
        *({"kind": 1, "option": i, "text": o} for i, o in enumerate(options)),
        *END,
    ]


ASK = page(
    "I can bring you to the Desert City for 100 silver.", "Yes, please.", "No, thanks."
)


class Loop:
    """Twin City beside GeneralPeace: dialog pages in order, and what each
    press does ("arrive", "next" or "close")."""

    def __init__(self, pages, outcomes, gates=2):
        self.life = {
            "map_id": 1002,
            "position": [69, 473],
            "object_address": 7,
            "dead_candidate": False,
        }
        self.pages = list(pages)
        self.outcomes = list(outcomes)
        self.open = None
        self.silver = 1000
        self.items = [{"type_id": APE_GATE, "amount": gates}] if gates else []
        self.pressed, self.events, self.walks, self.opened = [], [], [], 0
        self.bought = []
        self.identity = dict(PEACE)
        self.route = NS(restock_map_id=1020, supplies=NS(minimum_free_slots=4))
        self.info = "worker"

    def living(self):
        return {"embedded_controls": {"life": dict(self.life)}}

    def health(self):
        return {"embedded_controls": {"life": dict(self.life), "observed_at": time.time()}}

    def check_stop(self):
        pass

    def record(self, event, **fields):
        self.events.append(event)

    def travel(self, destination, **fields):
        self.walks.append(tuple(destination))
        self.life["position"] = list(destination)

    def dialog(self):
        if self.open is None:
            raise ValueError("NPC dialog is absent")
        return {"records": self.open, "viewport": (1416, 876)}

    def town(self, action, **fields):
        if action == "supplies":
            return {"silver": self.silver, "items": list(self.items), "capacity": 40}
        if action == "service-locate":
            return {"identity": dict(self.identity), "npc": {"position": PEACE["position"]}}
        if action in ("close", "open"):
            return {}
        if action == "shop":
            return {"products": [{"type_id": TWIN_GATE, "price": 200}]}
        if action == "buy":
            assert fields == {"vendor_type": 3, "type_id": TWIN_GATE}
            self.bought.append(TWIN_GATE)
            self.items.append({"type_id": TWIN_GATE, "amount": 1})
            self.silver -= 200
            return {"bought": TWIN_GATE, "amount": 1, "price": 200}
        if action == "service-open":
            self.opened += 1
            self.open = self.pages[0] if self.pages else None
            return {"interacted": True}
        if action == "service-select":
            assert fields["records"] == self.open
            self.pressed.append(fields["option"])
            outcome = self.outcomes.pop(0)
            if outcome == "arrive":
                self.life.update(map_id=1000, position=[480, 630])
                self.silver -= 100
                self.open = None
            elif outcome == "next":
                self.pages.pop(0)
                self.open = self.pages[0]
            else:
                self.open = None
            return {"interacted": True}
        if action == "service-close-panel":
            self.open = None
            return {"closed": True}
        raise AssertionError(action)


@pytest.fixture
def state(tmp_path, monkeypatch):
    from conquest import city_travel, dialog_geometry, navigation, worker

    path = tmp_path / "general-peace.json"
    monkeypatch.setattr(desert_gate, "STATE", path)
    monkeypatch.setattr(desert_gate, "ARRIVAL_SECONDS", 0.3)
    monkeypatch.setattr(dialog_geometry, "scroll_direction", lambda *a: None)
    monkeypatch.setattr(navigation, "read_terrain", lambda root, map_id: NS(map_id=map_id))
    monkeypatch.setattr(city_travel, "city_for", lambda map_id: TWIN_TOWN)
    current = {}

    def request(info, path_, body):
        assert body == {"action": "service-dialog"}
        return current["loop"].dialog()

    monkeypatch.setattr(worker, "request", request)
    return NS(path=path, current=current)


def read(path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def test_offer_presses_only_one_desert_or_affirmative_option():
    # 1
    assert desert_gate.offer(ASK) == "Yes, please."
    assert desert_gate.offer(page("Where to?", "Desert City", "Just passing by.")) == "Desert City"
    # A confirmation after a page that spoke of the Desert.
    assert desert_gate.offer(page("That will be 100 silver.", "OK", "Cancel"), True) == "OK"
    assert desert_gate.offer(page("That will be 100 silver.", "OK", "Cancel")) is None
    refused = [
        page("The Desert costs 5,000 silver.", "Yes"),
        page("The Desert costs 20 CPs.", "Yes"),
        page("Off to the Desert?", "Yes", "Sure"),
        page("Off to the Desert?", "No, I'll stay.", "Never mind."),
        page("Off to the Desert?", "Desert City", "Desert Road"),
        page("Hello, traveller.", "Yes"),
        [*page("Name your destination in the Desert", "Go"), {"kind": 2, "option": 0, "text": ""}],
    ]
    for records in refused:
        assert desert_gate.offer(records) is None, records


def test_an_unknown_page_is_saved_and_closed_without_a_press(state):
    # 2
    greeting = page("Peace be with you.", "Thanks.", "Goodbye.")
    loop = state.current["loop"] = Loop([greeting], [])
    with pytest.raises(ValueError, match="needs approval"):
        desert_gate.cross(loop)
    assert loop.pressed == [] and loop.open is None
    assert read(state.path)["pages"][0]["records"] == greeting
    assert "general_peace_page" in loop.events and loop.life["map_id"] == 1002


def test_an_offered_page_crosses_and_becomes_the_approved_one(state):
    # 3, 5
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    assert desert_gate.cross(loop) == [480, 630]
    assert loop.pressed == ["Yes, please."]
    data = read(state.path)
    assert data["approved"] == [{"records": ASK, "option": "Yes, please."}]
    assert data["crossings"][-1]["fare"] == 100
    assert data["crossings"][-1]["landing"] == [480, 630]
    assert "general_peace_crossed" in loop.events
    # The next crossing presses the approved page exactly as saved.
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    desert_gate.cross(loop)
    assert loop.pressed == ["Yes, please."]
    assert len(read(state.path)["crossings"]) == 2


def test_a_confirmation_page_follows_the_desert_page(state):
    # 3, 5
    confirm = page("That will be 100 silver.", "OK", "Cancel")
    loop = state.current["loop"] = Loop([ASK, confirm], ["next", "arrive"])
    desert_gate.cross(loop)
    assert loop.pressed == ["Yes, please.", "OK"]
    assert [s["option"] for s in read(state.path)["approved"]] == ["Yes, please.", "OK"]


def test_a_press_that_does_not_cross_fails_without_a_second_press(state):
    # 3
    loop = state.current["loop"] = Loop([ASK], ["close"])
    with pytest.raises(ValueError, match="closed without a crossing"):
        desert_gate.cross(loop)
    assert loop.pressed == ["Yes, please."] and loop.life["map_id"] == 1002
    assert "crossings" not in read(state.path)


def test_no_crossing_without_the_gate_home_or_with_another_npc(state):
    # 4
    loop = state.current["loop"] = Loop([ASK], ["arrive"], gates=0)
    with pytest.raises(ValueError, match="No gate home"):
        desert_gate.cross(loop)
    assert loop.opened == 0 and loop.walks == []
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    loop.identity["position"] = [80, 463]
    with pytest.raises(ValueError, match="identity"):
        desert_gate.cross(loop)
    assert loop.opened == 0 and loop.pressed == []


def test_the_way_in_rides_only_from_afar_and_fetches_a_due_buff(state, monkeypatch):
    # 6
    from conquest import buff_trip, conductress

    rides, buffs = [], []

    def ride(loop, destination):
        rides.append(destination)
        loop.life["position"] = list(desert_gate.LANDING)
        return True

    monkeypatch.setattr(conductress, "take_saved_trip", ride)
    monkeypatch.setattr(buff_trip, "enabled", lambda: True)
    monkeypatch.setattr(buff_trip, "cooling_down", lambda now=None: False)
    monkeypatch.setattr(buff_trip, "stigma_left", lambda now=None: 0.0)
    monkeypatch.setattr(buff_trip, "visit_buffer", lambda loop: buffs.append(1) or True)
    # Landed on the square by TwinCityGate: the next hops' gates at the
    # Pharmacist, MrBuffer, the ride, then him.
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    loop.life["position"] = [429, 378]
    assert desert_gate.travel(loop) == [480, 630]
    assert loop.bought == [TWIN_GATE] * 3 and loop.walks[0] == (466, 333)
    assert buffs == [1] and rides == [1000]
    # Already beside him (a retry after a saved page): no second fare.
    rides.clear()
    buffs.clear()
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    desert_gate.travel(loop)
    assert rides == [] and buffs == []
    # No gate home: nothing paid at all.
    loop = state.current["loop"] = Loop([ASK], ["arrive"], gates=0)
    loop.life["position"] = [429, 378]
    with pytest.raises(ValueError, match="No gate home"):
        desert_gate.travel(loop)
    assert rides == [] and buffs == []


def test_the_hops_twincitygates_are_topped_up_keeping_the_fares(state, monkeypatch):
    # 7: Ape City sells no TwinCityGate; each Desert hop reads one.
    from conquest import gear_circuit

    walks_into_town = []

    def enter_town(loop):
        walks_into_town.append(tuple(loop.life["position"]))
        loop.life["position"] = [440, 420]

    monkeypatch.setattr(gear_circuit, "enter_town", enter_town)
    loop = state.current["loop"] = Loop([], [])
    loop.items.append({"type_id": TWIN_GATE, "amount": 2})
    assert desert_gate.stock_hop_gates(loop) == 2  # enough for two hops
    assert loop.walks == [] and loop.bought == []
    # One left but only the fares carried (100 + his guessed 300): no buy.
    loop = state.current["loop"] = Loop([], [])
    loop.items.append({"type_id": TWIN_GATE, "amount": 1})
    loop.silver = 500
    assert desert_gate.stock_hop_gates(loop) == 1
    assert loop.bought == [] and "hop_gates_short" in loop.events
    # None left after the ride in by Twin City's south gate: into town first.
    loop = state.current["loop"] = Loop([], [])
    loop.life["position"] = [555, 957]
    assert desert_gate.stock_hop_gates(loop) == 3
    assert walks_into_town == [(555, 957)] and loop.walks == [(466, 333)]
    assert loop.silver == 1000 - 3 * 200
    # Once a crossing shows his fare, that is what stays carried.
    desert_gate.write_json(desert_gate.STATE, {"crossings": [{"fare": 100}]})
    assert desert_gate.fare_reserve() == 200
