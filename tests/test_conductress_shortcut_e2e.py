"""Ride the Twin City Conductress toward a far hunting field.

Alex, 2026-09-27: "Fastest way to get to apparitions is to talk to the
conductress and select ape city." Terrain paths measured the same day: Twin
City to the Apparition field is ~600-700 walking tiles; from the south gate
~350 and from the west gate ~310. The Conductress's city options drop the
farmer beside that city's gate on the Twin City map (Phoenix Castle lands at
(958, 555) by the east gate).

Failure modes, written before the code:
1. The ride is taken on a route without a shortcut, or outside town.
2. A failure before payment (dialog, walking to her) stops the route
   instead of letting the farmer walk.
3. A ride whose arrival or fare is not verified is paid again.
4. The landing is not remembered, so later rides cannot be judged.
5. A remembered landing farther from the field than the farmer is ridden to.
6. A straight line judges the ride: the river makes the Ape gate look
   farther from the Apparition field than town (352 vs ~225 tiles in a line)
   though it is far shorter to walk (356 vs 653), so every ride after the
   first was skipped (live 2026-09-27).
7. A fare the farmer cannot pay is found out at her dialog, whose refusal
   stops the route (live 2026-09-27: 39 silver, an empty bank).
8. A farmer left on her tile, south of the town boundary, never boards
   again and walks instead.
"""

import json
from types import SimpleNamespace as NS

import pytest

from conquest import conductress_shortcut as shortcut

APPARITION = NS(id="apparition", hunting_anchor=(300, 605))
BANDIT = NS(id="bandit", hunting_anchor=(500, 500))


class Twin:
    def __init__(self, *, silver=500, lands=(560, 950), fare=100, dialog_ok=True, moves=True):
        self.silver = silver
        self.position = [466, 333]
        self.lands, self.fare = lands, fare
        self.dialog_ok, self.moves = dialog_ok, moves
        self.actions = []
        self.events = []

    def town(self, action, **fields):
        self.actions.append(action)
        if action == "supplies":
            return {"silver": self.silver, "items": []}
        if action == "conductress-travel":
            if self.moves:
                self.position = list(self.lands)
                self.silver -= self.fare
            return {"destination_selected": fields["destination"]}
        return {"ok": True}

    def life(self):
        return {
            "map_id": 1002,
            "position": list(self.position),
            "object_address": 7,
            "dead_candidate": False,
            "timestamp": 1,
        }


def loop_for(twin, route=APPARITION):
    return NS(
        route=route,
        town=twin.town,
        travel=lambda target, **kw: twin.actions.append("travel"),
        living=lambda: {"embedded_controls": {"life": twin.life()}},
        health=lambda: {"embedded_controls": {"life": twin.life(), "observed_at": shortcut.time.time()}},
        record=lambda event, **fields: twin.events.append(event),
    )


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(shortcut, "LANDINGS", tmp_path / "landings.json")
    monkeypatch.setattr("conquest.banking.ensure_transport", lambda loop, minimum: None)
    monkeypatch.setattr("conquest.conductress.prepare_destination", lambda loop, option: None)
    monkeypatch.setattr(shortcut.time, "sleep", lambda s: None)
    return tmp_path


def test_rides_and_remembers_the_landing(state):
    twin = Twin()
    assert shortcut.ride(loop_for(twin)) is True
    assert twin.actions.count("conductress-travel") == 1
    assert json.loads(shortcut.LANDINGS.read_text()) == {"Ape Mountain": [560, 950]}  # 4
    assert "conductress_shortcut_arrived" in twin.events


def test_no_ride_without_a_shortcut_or_outside_town(state):
    # 1
    twin = Twin()
    assert shortcut.ride(loop_for(twin, BANDIT)) is False
    twin.position = [300, 600]  # already in the field
    assert shortcut.ride(loop_for(twin)) is False
    assert "conductress-travel" not in twin.actions


def test_failure_before_payment_walks_instead(state, monkeypatch):
    # 2
    def refuse(loop, option):
        raise ValueError("Conductress choices differ from the qualified dialog")

    monkeypatch.setattr("conquest.conductress.prepare_destination", refuse)
    twin = Twin()
    assert shortcut.ride(loop_for(twin)) is False
    assert "conductress-travel" not in twin.actions
    assert "conductress_shortcut_skipped" in twin.events


def test_an_unaffordable_fare_walks_without_visiting_her(state):
    # 7: checked before walking to her, not refused by her dialog.
    twin = Twin(silver=39)
    assert shortcut.ride(loop_for(twin)) is False
    assert not {"travel", "conductress-open", "conductress-travel"} & set(twin.actions)
    assert "conductress_shortcut_skipped" in twin.events


def test_a_farmer_on_her_tile_still_boards(state):
    # 8: (438, 444) is below the Twin City town boundary's bottom edge (433).
    twin = Twin()
    twin.position = list(shortcut.CONDUCTRESS_TILE)
    assert shortcut.ride(loop_for(twin)) is True
    assert twin.actions.count("conductress-travel") == 1


def test_unverified_ride_is_never_paid_twice(state):
    # 3: the click went out but the farmer never moved and no fare was taken.
    twin = Twin(moves=False)
    with pytest.raises(ValueError, match="no repeat payment"):
        shortcut.ride(loop_for(twin))
    assert twin.actions.count("conductress-travel") == 1


def test_a_landing_farther_than_the_farmer_is_not_ridden(state):
    # 5
    shortcut.LANDINGS.write_text(json.dumps({"Ape Mountain": [900, 900]}))
    twin = Twin()
    assert shortcut.ride(loop_for(twin)) is False
    assert "conductress-travel" not in twin.actions


def twin_city(walks):
    """Twin City terrain whose checked walks have these lengths."""

    def travel_path(start, goal, **kwargs):
        tiles = walks.get((tuple(start), tuple(goal)))
        if tiles is None:
            raise ValueError("Route search exceeded its node budget")
        return [tuple(start)] * (tiles + 1)

    return NS(map_id=1002, travel_path=travel_path)


TOWN, HER, APE, FIELD = (466, 333), shortcut.CONDUCTRESS_TILE, (555, 957), (300, 605)


def test_walking_distance_not_the_straight_line_decides_the_ride(state):
    # 6: live Twin City numbers; the landing is farther in a line.
    shortcut.LANDINGS.write_text(json.dumps({"Ape Mountain": list(APE)}))
    twin = Twin(lands=APE)
    loop = loop_for(twin)
    loop.terrain = twin_city({(TOWN, FIELD): 653, (TOWN, HER): 111, (APE, FIELD): 356})
    assert shortcut.ride(loop) is True
    assert twin.actions.count("conductress-travel") == 1


def test_walking_to_her_counts_against_the_ride(state):
    # 5 on terrain: 111 to her + 600 from the landing loses to 272 on foot.
    shortcut.LANDINGS.write_text(json.dumps({"Ape Mountain": [900, 900]}))
    twin = Twin()
    loop = loop_for(twin)
    loop.terrain = twin_city(
        {(TOWN, FIELD): 272, (TOWN, HER): 111, ((900, 900), FIELD): 600}
    )
    assert shortcut.ride(loop) is False
    assert "conductress-travel" not in twin.actions


def test_unplannable_walk_from_town_takes_the_ride_but_a_dead_end_landing_does_not(
    state,
):
    # Poltergeist from town is beyond the planner's budget: ride.
    shortcut.LANDINGS.write_text(json.dumps({"Ape Mountain": list(APE)}))
    twin = Twin(lands=APE)
    loop = loop_for(twin)
    loop.terrain = twin_city({(TOWN, HER): 111, (APE, FIELD): 356})
    assert shortcut.ride(loop) is True
    # A landing with no checked walk to the field is never ridden to.
    twin = Twin(lands=APE)
    loop = loop_for(twin)
    loop.terrain = twin_city({(TOWN, FIELD): 653, (TOWN, HER): 111})
    assert shortcut.ride(loop) is False
    assert "conductress-travel" not in twin.actions
