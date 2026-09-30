"""TheHempKnight's daily double EXP, visited from the Twin City buff trip.

Alex 2026-09-30: "talk to him once every 24 hours to get double exp" and
"when you are confident about your route, and you dont die too much it would
be valuable to go grab the double exp". His dialog was never read, so nothing
is pressed on a page that was not approved record for record.
"""

from types import SimpleNamespace as NS

import pytest

from conquest import buff_trip as b
from conquest import dialog_geometry
from conquest import hempknight as h

GREETING = [
    {"kind": 0, "option": 0, "text": "Hello, adventurer. Want to double your experience?"},
    {"kind": 1, "option": 1, "text": "Yes, double my experience."},
    {"kind": 1, "option": 2, "text": "Just passing by."},
]
THANKS = [
    {"kind": 0, "option": 0, "text": "Done. Come back tomorrow."},
    {"kind": 1, "option": 1, "text": "Thanks."},
]


@pytest.fixture
def clock(monkeypatch):
    now = [2_000_000.0]
    monkeypatch.setattr(h.time, "time", lambda: now[0])
    monkeypatch.setattr(h.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(h.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(dialog_geometry, "scroll_direction", lambda data, option, viewport=None: 0)
    return now


def enable():
    b.write_json(b.POLICY, {"stigma": True, "double_exp": True})


def approve(*steps):
    data = h.read_json(h.STATE)
    data["approved"] = list(steps)
    h.write_json(h.STATE, data)


class Farmer:
    """A buff-trip farmer beside MrBuffer on Twin City's square."""

    def __init__(self, pages=(GREETING, THANKS), *, knight_in_view=True):
        self.map_id, self.position = 1002, [425, 386]
        self.pages = [list(p) for p in pages]
        self.page = None
        self.knight_in_view = knight_in_view
        self.calls, self.events = [], []

    def living(self):
        return {"embedded_controls": {"life": {"map_id": self.map_id, "position": list(self.position)}}}

    def record(self, event, **fields):
        self.events.append((event, fields))

    def check_stop(self):
        pass

    def travel(self, point, **kw):
        self.calls.append(("travel", tuple(point), kw.get("arrival_radius", 0)))
        self.position = list(point)

    def town(self, action, **kw):
        self.calls.append((action, kw.get("option") or kw.get("name") or kw.get("window")))
        if action == "service-locate":
            if not self.knight_in_view:
                raise ValueError("One memory-identified TheHempKnight is required in the scene")
            return {"npc": {"position": list(h.SPOT)}}
        if action == "service-open":
            self.page = 0
            return {"interacted": True}
        if action == "service-dialog":
            if self.page is None or self.page >= len(self.pages):
                raise ValueError("NPC dialog is absent")
            return {"records": self.pages[self.page], "viewport": (1024, 768)}
        if action == "service-select":
            assert kw["records"] == self.pages[self.page]  # exact records only
            self.page += 1
            return {"interacted": True, "option": kw["option"]}
        if action == "service-close-panel":
            self.page = None
            return {"closed": True}
        return {}


def selects(farmer):
    return [c[1] for c in farmer.calls if c[0] == "service-select"]


def test_off_by_default_then_a_first_visit_only_reads(clock):
    assert h.next_visit() is None
    b.write_json(b.POLICY, {"stigma": True})
    assert h.next_visit() is None  # the buff trip alone does not talk to him
    enable()
    assert h.next_visit() == "explore"
    farmer = Farmer()
    assert h.visit(farmer) == "explored"
    assert selects(farmer) == []  # nothing pressed on an unread dialog
    assert ("travel", h.APPROACH, 1) in farmer.calls
    assert farmer.calls[-1] == ("service-close-panel", "Dialog")
    state = h.read_json(h.STATE)
    assert [p["records"] for p in state["pages"]] == [GREETING]
    assert [e for e, _ in farmer.events] == ["hempknight_page", "hempknight_explored"]
    # Nothing new approved: no second visit for the same page.
    clock[0] += h.RETRY_SECONDS + 1
    assert h.next_visit() is None


def test_approved_steps_are_walked_and_the_claim_waits_for_its_approval(clock):
    enable()
    h.write_json(h.STATE, {"pages": [{"records": GREETING}], "explored_with": 0})
    approve({"records": GREETING, "option": "Yes, double my experience."})
    assert h.next_visit() == "explore"  # one more step approved: read on
    farmer = Farmer()
    assert h.visit(farmer) == "explored"
    assert selects(farmer) == ["Yes, double my experience."]
    assert [p["records"] for p in h.read_json(h.STATE)["pages"]] == [GREETING, THANKS]
    assert "claimed_at" not in h.read_json(h.STATE)  # no step marked as the claim


def test_the_claim_runs_once_a_day_and_never_on_a_changed_page(clock):
    enable()
    approve(
        {"records": GREETING, "option": "Yes, double my experience.", "claims": True},
        {"records": THANKS, "option": "Thanks."},
    )
    assert h.next_visit() == "claim"
    farmer = Farmer()
    assert h.visit(farmer) == "claimed"
    assert selects(farmer) == ["Yes, double my experience.", "Thanks."]
    assert farmer.events[-1][0] == "double_exp_claimed"
    assert farmer.events[-1][1]["options"] == selects(farmer)
    assert h.read_json(h.STATE)["claims"] == 1
    clock[0] += h.RETRY_SECONDS + 1
    assert h.next_visit() is None  # once every 24 hours
    clock[0] += h.CLAIM_INTERVAL
    assert h.next_visit() == "claim"
    # He says something else today: saved, nothing pressed, no claim.
    changed = [dict(GREETING[0], text="Double EXP costs 50 CPs today."), *GREETING[1:]]
    farmer = Farmer(pages=(changed,))
    assert h.visit(farmer) == "explored"
    assert selects(farmer) == []
    assert h.read_json(h.STATE)["claims"] == 1


def test_no_claim_within_three_hours_of_a_death(clock):
    enable()
    approve({"records": GREETING, "option": "Yes, double my experience.", "claims": True})
    b.write_json(b.STATE, {"stigma_at": clock[0]})
    b.lost("death")  # the living hook on dead_candidate
    assert h.read_json(h.STATE)["died_at"] == clock[0]
    assert h.next_visit() is None
    clock[0] += 30
    assert not h.note_death()  # the same death, seen again
    clock[0] += h.DEATH_QUIET_SECONDS
    assert h.next_visit() == "claim"


def test_a_failed_visit_never_stops_the_buff_trip(clock):
    enable()
    farmer = Farmer(knight_in_view=False)
    assert h.visit(farmer) == "failed"
    assert farmer.events[-1][0] == "hempknight_failed"
    assert h.next_visit() is None  # retried after RETRY_SECONDS
    clock[0] += h.RETRY_SECONDS
    assert h.next_visit() == "explore"
    elsewhere = Farmer()
    elsewhere.map_id = 1020  # not in Twin City: no visit
    assert h.visit(elsewhere) is None and elsewhere.calls == []


def test_an_approved_option_missing_from_its_page_is_not_pressed(clock):
    enable()
    approve({"records": GREETING, "option": "Give me triple experience.", "claims": True})
    farmer = Farmer()
    assert h.visit(farmer) == "failed"
    assert selects(farmer) == []
    assert ("service-close-panel", "Dialog") in farmer.calls


def test_a_user_stop_is_not_swallowed(clock, monkeypatch):
    from conquest.overnight import OvernightStopped

    enable()
    farmer = Farmer()

    def stop():
        raise OvernightStopped("Stopped by user")

    farmer.check_stop = stop
    with pytest.raises(OvernightStopped):
        h.visit(farmer)


def test_the_buff_trip_visits_him_after_mrbuffer(clock, monkeypatch):
    order = []
    monkeypatch.setattr(b, "visit_buffer", lambda loop: order.append("buffer"))
    monkeypatch.setattr(h, "visit", lambda loop: order.append("knight"))
    monkeypatch.setattr(b, "buy_gates", lambda loop: order.append("gates"))
    monkeypatch.setattr(b, "arrived", lambda loop, map_id: None)

    class Loop:
        route = NS(restock_map_id=1020)
        phase = "restocking"

        def living(self):
            return {"embedded_controls": {"life": {"map_id": 1002, "position": [429, 378]}}}

        def town(self, action, **kw):
            assert action == "supplies"
            return {"items": [{"type_id": 1060022, "amount": 1}]}  # the gate home

        def record(self, *a, **k):
            pass

    monkeypatch.setattr("conquest.return_scroll.read_gate", lambda loop, city: True)
    b.trip(Loop())
    assert order == ["buffer", "knight", "gates"]
