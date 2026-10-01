"""The crossing into the Desert at an NPC in Twin City's west (desert_gate).

Alex 2026-09-30 19:3x: "there is a npc that will bring you to the desert".
GeneralPeace by the Desert City landing only warns (20:32:57) and the SpaceMark
at (96, 323) sells Water Taoists' scrolls (21:03:58); the FoodCarrier by the
Mine is next. A candidate's dialog is unread when it is listed, so a crossing
presses only approved pages or pages offer() accepts, a click that teleports
needs none, a refused NPC is skipped next time, and a crossing counts only once
memory shows the Desert.

Failure modes, written before the change:
1. A page asking for input, a fare above MAX_FARE or another currency, or
   with several affirmatives, gets an option pressed.
2. A page nobody approved and offer() refuses is pressed, or is not saved.
3. A crossing counts without the Desert's map in memory, or presses again
   after a press that did not cross; a click that teleports is not counted.
4. A crossing starts without the route's gate home (the Desert has no saved
   way out), or with an NPC whose identity differs from the survey.
5. The approved pages, the fare and the landing are not kept after a
   crossing, so the next one is not exact.
6. The way in rides the Conductress again although the farmer is already on
   the SpaceMark's side of the map, or skips MrBuffer when his buff is due.
7. The hops' TwinCityGates run out, or their purchase spends the fares.
"""

import time
from types import SimpleNamespace as NS

import pytest

from conquest import desert_gate

MARK = {
    "map_id": 1002,
    "type_id": 0,
    "name": "GeneralPeace",
    "model": 296,
    "position": [60, 463],
}
FOOD = ("FoodCarrier", (76, 401), (78, 404))
FOOD_IDENTITY = {**MARK, "name": "FoodCarrier", "model": 7200, "position": [76, 401]}
APE_GATE = 1060022
TWIN_GATE = 1060020
TWIN_TOWN = {
    "map_id": 1002,
    "town_boundary": [348, 209, 507, 433],
    "services": {"pharmacist": [466, 333]},
}
END = [{"kind": 3, "option": 255, "text": ""}, {"kind": 4, "option": 255, "text": ""}]


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
    """Twin City at the Desert City landing: the candidate NPC's dialog pages in
    order, and what each press does ("arrive", "next" or "close"). With
    ``teleport`` the click itself moves the farmer, with no dialog."""

    def __init__(self, pages, outcomes, gates=2, teleport=False):
        self.life = {
            "map_id": 1002,
            "position": list(desert_gate.LANDING),
            "object_address": 7,
            "dead_candidate": False,
        }
        self.pages = list(pages)
        self.outcomes = list(outcomes)
        self.teleport = teleport
        self.open = None
        self.silver = 1000
        self.items = [{"type_id": APE_GATE, "amount": gates}] if gates else []
        self.pressed, self.events, self.walks, self.opened = [], [], [], 0
        self.bought = []
        self.identity = dict(MARK)
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

    def arrive(self):
        self.life.update(map_id=1000, position=[480, 630])
        self.silver -= 100
        self.open = None

    def dialog(self):
        if self.open is None:
            raise ValueError("NPC dialog is absent")
        return {"records": self.open, "viewport": (1416, 876)}

    def town(self, action, **fields):
        if action == "supplies":
            return {"silver": self.silver, "items": list(self.items), "capacity": 40}
        if action == "service-locate":
            return {"identity": dict(self.identity), "npc": {"position": MARK["position"]}}
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
            if self.teleport:
                self.arrive()
            else:
                self.open = self.pages[0] if self.pages else None
            return {"interacted": True}
        if action == "service-select":
            assert fields["records"] == self.open
            self.pressed.append(fields["option"])
            outcome = self.outcomes.pop(0)
            if outcome == "arrive":
                self.arrive()
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

    path = tmp_path / "desert-gate.json"
    monkeypatch.setattr(desert_gate, "STATE", path)
    monkeypatch.setattr(desert_gate, "ARRIVAL_SECONDS", 0.3)
    monkeypatch.setattr(desert_gate, "DIALOG_WAIT_SECONDS", 0.3)
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
        # GeneralPeace's own page (Suicide, 2026-09-30 20:32:57): a warning.
        page(
            "This is the way to the Desert City. Although you are excellent, "
            "it is dangerous to go ahead.",
            "I see.",
        ),
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
    assert "desert_gate_page" in loop.events and loop.life["map_id"] == 1002


def test_an_offered_page_crosses_and_becomes_the_approved_one(state):
    # 3, 5
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    assert desert_gate.cross(loop) == [480, 630]
    assert loop.pressed == ["Yes, please."] and loop.walks == [desert_gate.CANDIDATES[0][2]]
    data = read(state.path)
    assert data["approved"] == [{"npc": "GeneralPeace", "records": ASK, "option": "Yes, please."}]
    assert data["crossings"][-1]["fare"] == 100
    assert data["crossings"][-1]["landing"] == [480, 630]
    assert "desert_gate_crossed" in loop.events
    # The next crossing presses the approved page exactly as saved.
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    desert_gate.cross(loop)
    assert loop.pressed == ["Yes, please."]
    assert len(read(state.path)["crossings"]) == 2


def test_a_click_that_teleports_crosses_without_a_dialog(state):
    # 3, 5
    loop = state.current["loop"] = Loop([], [], teleport=True)
    assert desert_gate.cross(loop) == [480, 630]
    assert loop.opened == 1 and loop.pressed == []
    data = read(state.path)
    assert data["approved"] == [] and data["crossings"][-1]["fare"] == 100


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
    # A click that opens nothing and moves nothing is tried twice, then fails.
    loop = state.current["loop"] = Loop([], [])
    with pytest.raises(ValueError, match="neither opened a dialog nor moved"):
        desert_gate.cross(loop)
    assert loop.opened == 2 and loop.pressed == []


def no_way_out(monkeypatch):
    """A field with no crossed way out (the Desert before 21:46:57)."""
    from conquest import world_travel

    def missing(source, destination, edges=None):
        raise ValueError("No memory-verified map connection")

    monkeypatch.setattr(world_travel, "connection_path", missing)


def test_no_crossing_without_the_gate_home_or_with_another_npc(state, monkeypatch):
    # 4
    no_way_out(monkeypatch)
    loop = state.current["loop"] = Loop([ASK], ["arrive"], gates=0)
    with pytest.raises(ValueError, match="No gate home"):
        desert_gate.cross(loop)
    assert loop.opened == 0 and loop.walks == []
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    loop.identity["position"] = [MARK["position"][0] + 20, MARK["position"][1]]
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
    # Pharmacist, MrBuffer, the ride, then the NPC.
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    loop.life["position"] = [429, 378]
    assert desert_gate.travel(loop) == [480, 630]
    assert loop.bought == [TWIN_GATE] * 3 and loop.walks[0] == (466, 333)
    assert buffs == [1] and rides == [1000]
    # Already on its side of the map (a retry from the landing): no second
    # fare, no gates, no buff walk across the maze.
    rides.clear()
    buffs.clear()
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    desert_gate.travel(loop)
    assert rides == [] and buffs == [] and loop.bought == []
    # No gate home and no way out: nothing paid at all.
    no_way_out(monkeypatch)
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
    # One left but only the fares carried (100 + its guessed 300): no buy.
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
    # Once a crossing shows its fare, that is what stays carried.
    desert_gate.write_json(desert_gate.STATE, {"crossings": [{"fare": 100}]})
    assert desert_gate.fare_reserve() == 200


def test_a_refused_npc_is_skipped_and_with_none_left_nothing_is_paid(state, monkeypatch):
    # 2: GeneralPeace and the SpaceMark were asked live and refused.
    from conquest import buff_trip, conductress

    second = ("SpaceMark", (96, 323), (98, 327))
    monkeypatch.setattr(desert_gate, "CANDIDATES", (FOOD, second))
    loop = state.current["loop"] = Loop([page("Peace be with you.", "Thanks.")], [])
    loop.identity = dict(FOOD_IDENTITY)
    with pytest.raises(ValueError, match="needs approval"):
        desert_gate.cross(loop)
    assert desert_gate.next_candidate() == second
    # The next try asks the next NPC, on its own approach.
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    loop.identity = {**MARK, "name": "SpaceMark", "model": 270, "position": [96, 323]}
    assert desert_gate.cross(loop) == [480, 630] and loop.walks == [(98, 327)]
    # Its approved page keeps it in play even after a later refusal.
    data = read(state.path)
    data["refused"]["SpaceMark"] = {"records": [], "at": 0}
    desert_gate.write_json(desert_gate.STATE, data)
    assert desert_gate.next_candidate() == second
    # Every NPC refused and none approved: the way in stops before any fare.
    data["approved"] = []
    desert_gate.write_json(desert_gate.STATE, data)
    assert desert_gate.next_candidate() is None
    rides = []
    monkeypatch.setattr(conductress, "take_saved_trip", lambda loop, d: rides.append(d))
    monkeypatch.setattr(buff_trip, "enabled", lambda: False)
    loop = state.current["loop"] = Loop([ASK], ["arrive"])
    loop.life["position"] = [429, 378]
    with pytest.raises(ValueError, match="No Desert NPC left"):
        desert_gate.travel(loop)
    assert rides == [] and loop.walks == [] and loop.opened == 0


def test_general_peaces_warning_is_pressed_as_alex_showed(state):
    # Alex 2026-09-30 21:2x: "you have to talk to the general peace and press
    # on 'I see'". His page, as Suicide read it at 20:32:57 and 21:16:04.
    warning = desert_gate.APPROVED[0]["records"]
    assert desert_gate.offer(warning) is None  # offer() alone refuses it
    assert desert_gate.next_candidate()[0] == "GeneralPeace"
    loop = state.current["loop"] = Loop([warning], ["arrive"])
    assert desert_gate.cross(loop) == [480, 630]
    assert loop.pressed == ["I see."] and loop.walks == [(64, 468)]
    data = read(state.path)
    # Approved in code: the saved list stays for pages learned live.
    assert data.get("approved") == [] and data["crossings"][-1]["npc"] == "GeneralPeace"
    # Even a refusal recorded against him keeps him in play.
    desert_gate.write_json(desert_gate.STATE, {**data, "refused": {"GeneralPeace": {}}})
    assert desert_gate.next_candidate()[0] == "GeneralPeace"
