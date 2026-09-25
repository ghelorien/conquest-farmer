"""A successful travel heal must not stop the route over an Inventory hover race.

Live incident (2026-09-25 04:00:27, restock town visit 4d2f63a0...): travelling
from the Bandit field (469,428) toward the Phoenix Warehouseman, travel care
consumed a Painkiller successfully, then its ``finally`` closed Inventory.  The
worker's close raised HoverNotReady ("Pointer is not over the memory-identified
merchant control") inside panel_close.click_close's pre-press hover wait, so no
mouse button was pressed.  Because the heal had succeeded, travel care
re-raised that close error, the route failed in the field, Farming turned Off
and the farmer was killed.  No file outside tmp_path, game process or native
input is touched here.

Failure modes, written before the implementation (each is a test below):

 FM1  Heal verified, then the Inventory close reports the exact pre-press
      hover race (either HoverNotReady text) -> a retryable
      TravelStateChanged, never a fatal route error.  The verified heal is
      still recorded, the heal cooldown starts, and the deferral is recorded.
 FM2  Other typed "nothing was sent" close failures after a verified heal
      (TownObservationUnavailable, InputAcquisitionBusy, a message saying no
      button/input/click was sent) -> the same retryable state.
 FM3  No extra potion: the retry that follows a deferred close sends no
      consume-healing inside the heal cooldown, and the very next care pass
      runs the panel-clearing pass (which closes the still-open Inventory)
      before any route input can be planned.
 FM4  The heal itself failed with an uncertain error -> that heal error
      propagates unchanged even if the close then hits the hover race.
 FM5  The heal's own focus race (no key sent) -> unchanged "Regaining focus"
      retry; the close failure stays suppressed as before.
 FM6  Genuine uncertain close results after a verified heal ("Town panel
      close was not verified", a changed panel after a submitted close, a
      transaction dialog, a bridge OSError) -> unchanged: still raised, never
      converted into the retryable state.
 FM7  Damage-masked but consumed potion ("consumption unverified", count
      dropped) followed by the hover race -> the same retryable state (never
      route input in that pass), the next pass clears the panel first; no
      second potion.
 FM8  The panel-clearing pass itself hits the pre-press hover race -> bounded
      retry: at most three consecutive not-ready closes (shared with the heal
      close) are retried; the next one is raised unchanged.  A successful
      close resets the bound.  An unverified close in that pass keeps its
      existing "uncertain" handling.
 FM9  End to end through the real OvernightLoop._run_route: hunting stops for
      ammo, restock begins, the Warehouseman trip heals once and meets the
      hover race, the route keeps travelling instead of failing in the field,
      closes Inventory through the panel pass, and reaches the warehouse.  A
      JSON artifact under tmp_path records the worker operations and events.
"""

import json
from types import SimpleNamespace as NS

import field_fakes
import pytest

from conquest import travel_care as t
from conquest.capture import CaptureUnavailable
from conquest.merchants.coordination import InputAcquisitionBusy
from conquest.town_trade import TownObservationUnavailable

HOVER = field_fakes.HOVER


def care_with(monkeypatch, *, hp=727, potions=3):
    care = t.TravelCare.__new__(t.TravelCare)
    care.exact_1078 = False
    care.revive_state = {}
    care.next_panel_check = float("inf")  # Isolate the heal pass by default.
    care.info = "worker"
    care.session = None
    care.layout = None
    care.pending = None
    care.last_heal = -float("inf")
    x = NS(events=[], sent=[], now=[100.0], potions=[potions], hp=[hp], close=[])
    care.notify = x.events.append

    def snapshot():
        value = x.potions[0]  # One memory read: a frozen observation.
        return NS(
            count=lambda item: value,
            items=[NS(uid=42, type_id=1000020, amount=1)],
        )

    care.inventory = NS(read=snapshot)
    monkeypatch.setattr(t.time, "monotonic", lambda: x.now[0])

    def health():
        return {
            "embedded_controls": {
                "control": {"enabled": False},
                "life": {
                    "current_hp": x.hp[0],
                    "max_hp": 1000,
                    "dead_candidate": False,
                    "status": 0,
                },
            }
        }

    x.health = health
    x.consume = lambda body: {
        "consumed": True,
        "hp_after": x.hp[0] + 150,
        "remaining": x.potions[0] - 1,
    }

    def request(info, operation, body):
        x.sent.append(body["action"])
        if body["action"] == "consume-healing":
            result = x.consume(body)
            x.potions[0] -= 1
            return result
        if body["action"] in ("close", "clear-travel-panels"):
            if x.close:
                raise x.close.pop(0)
            return (
                {"closed": True}
                if body["action"] == "close"
                else {"closed_panel": "Inventory"}
            )
        raise AssertionError(body)

    monkeypatch.setattr(t, "request", request)
    return care, x


NOT_READY = [
    ValueError(HOVER),
    ValueError("Merchant hover changed during observation"),
    TownObservationUnavailable("Panel layout changed; reobserving before close"),
    InputAcquisitionBusy("Merchant input is busy"),
    ValueError("Display panel moved before closing; no button pressed"),
    CaptureUnavailable("Cursor moved away from the target; no input sent"),
]


# FM1/FM2
@pytest.mark.parametrize("error", NOT_READY, ids=lambda e: type(e).__name__)
def test_verified_heal_then_unsent_close_is_retryable(monkeypatch, error):
    care, x = care_with(monkeypatch)
    x.close.append(error)
    with pytest.raises(t.TravelStateChanged, match="not ready"):
        care.check(x.health())
    assert x.sent == ["consume-healing", "close"]
    names = [e["event"] for e in x.events]
    assert names == ["travel_heal_verified", "travel_heal_close_deferred"]
    assert x.events[1]["detail"] == str(error)
    assert care.last_heal == 100.0
    # The next care pass must clear panels before any route input.
    assert care.next_panel_check <= 100.0


# FM3
def test_deferred_close_retry_sends_no_second_potion_and_clears_panel_first(
    monkeypatch,
):
    care, x = care_with(monkeypatch, hp=500)  # Still below 75% after one potion.
    x.close.append(ValueError(HOVER))
    with pytest.raises(t.TravelStateChanged):
        care.check(x.health())
    x.hp[0] = 650
    x.now[0] += 0.2
    with pytest.raises(t.PanelTravelChanged):
        care.check(x.health())  # Panel pass closes the still-open Inventory.
    x.now[0] += 0.2
    care.check(x.health())  # Heal cooldown: no second potion yet.
    assert x.sent == ["consume-healing", "close", "clear-travel-panels"]
    assert x.potions[0] == 2


# FM4
def test_failed_heal_error_is_unchanged_by_a_close_race(monkeypatch):
    care, x = care_with(monkeypatch)

    def uncertain(body):
        raise ValueError("Input completion uncertain")

    x.consume = uncertain
    x.close.append(ValueError(HOVER))
    with pytest.raises(ValueError, match="Input completion uncertain") as raised:
        care.check(x.health())
    assert not isinstance(raised.value, t.TravelStateChanged)
    assert x.sent == ["consume-healing", "close"]
    assert care.last_heal == -float("inf")


# FM5
def test_heal_focus_race_unchanged_with_close_race(monkeypatch):
    care, x = care_with(monkeypatch)

    def no_key(body):
        raise ValueError("Game lost focus; no key sent")

    x.consume = no_key
    x.close.append(ValueError(HOVER))
    with pytest.raises(t.TravelStateChanged, match="Regaining focus"):
        care.check(x.health())
    assert care.last_heal == -float("inf")
    assert not any(e["event"] == "travel_heal_close_deferred" for e in x.events)


# FM6
@pytest.mark.parametrize(
    "error",
    [
        ValueError("Town panel close was not verified"),
        ValueError("Town panel changed after its close attempt"),
        ValueError("Transaction dialog appeared before retrying panel close"),
        OSError("Worker bridge closed"),
    ],
    ids=lambda e: str(e)[:24],
)
def test_uncertain_close_after_verified_heal_is_unchanged(monkeypatch, error):
    care, x = care_with(monkeypatch)
    x.close.append(error)
    with pytest.raises(type(error)) as raised:
        care.check(x.health())
    assert raised.value is error
    assert not isinstance(raised.value, t.TravelStateChanged)
    assert not any(e["event"] == "travel_heal_close_deferred" for e in x.events)


# FM7
def test_masked_consumed_heal_then_close_race_continues_travel(monkeypatch):
    care, x = care_with(monkeypatch)

    def masked(body):
        x.potions[0] -= 1  # The potion was used; damage hid the HP gain.
        raise ValueError("Healing consumption unverified; no repeat input issued")

    x.consume = masked
    x.close.append(ValueError(HOVER))
    with pytest.raises(t.TravelStateChanged, match="not ready"):
        care.check(x.health())
    assert care.last_heal == 100.0
    assert [e["event"] for e in x.events] == [
        "travel_heal_unconfirmed",
        "travel_heal_close_deferred",
    ]
    assert care.next_panel_check <= 100.0
    x.now[0] += 0.3
    with pytest.raises(t.PanelTravelChanged):
        care.check(x.health())
    assert x.sent == ["consume-healing", "close", "clear-travel-panels"]


# FM8
def test_panel_pass_hover_race_is_retried_but_bounded(monkeypatch):
    care, x = care_with(monkeypatch, hp=900)
    care.next_panel_check = 0
    x.close += [ValueError(HOVER), ValueError(HOVER)]
    for _ in range(2):
        with pytest.raises(t.TravelStateChanged, match="not ready"):
            care.check(x.health())
        x.now[0] += 1.0
    x.close.append(ValueError(HOVER))
    with pytest.raises(ValueError, match="Pointer is not over") as raised:
        care.check(x.health())
    assert not isinstance(raised.value, t.TravelStateChanged)


def test_successful_close_resets_the_not_ready_bound(monkeypatch):
    care, x = care_with(monkeypatch, hp=900)
    care.next_panel_check = 0
    for _ in range(3):
        x.close += [ValueError(HOVER), ValueError(HOVER)]
        for _ in range(2):
            with pytest.raises(t.TravelStateChanged, match="not ready"):
                care.check(x.health())
            x.now[0] += 1.0
        with pytest.raises(t.PanelTravelChanged):
            care.check(x.health())  # Closed: the bound starts again.
        x.now[0] += 1.0


def test_heal_close_race_counts_toward_the_panel_bound(monkeypatch):
    care, x = care_with(monkeypatch)
    x.close += [ValueError(HOVER), ValueError(HOVER), ValueError(HOVER)]
    with pytest.raises(t.TravelStateChanged):
        care.check(x.health())
    x.now[0] += 1.0
    with pytest.raises(t.TravelStateChanged):
        care.check(x.health())
    x.now[0] += 1.0
    with pytest.raises(ValueError, match="Pointer is not over") as raised:
        care.check(x.health())
    assert not isinstance(raised.value, t.TravelStateChanged)


def test_unverified_panel_pass_close_keeps_existing_uncertain_handling(monkeypatch):
    care, x = care_with(monkeypatch, hp=900)
    care.next_panel_check = 0
    x.close.append(ValueError("Town panel close was not verified"))
    with pytest.raises(t.TravelStateChanged, match="unconfirmed town panel close"):
        care.check(x.health())
    x.now[0] += 1.0
    with pytest.raises(ValueError, match="remains uncertain"):
        care.check(x.health())


# --------------------------------------------------------------------------
# FM9 end to end: the live incident through the real route controller.
# --------------------------------------------------------------------------


def test_e2e_restock_trip_survives_heal_close_hover_race(tmp_path, monkeypatch):
    from conquest import banking

    clock = field_fakes.FakeClock(1790323200.0)
    game = field_fakes.FakeGame(clock, hp=727)
    # Exactly the live sequence: heal verified, Inventory close hover race.
    game.close_failures = [ValueError(HOVER)]
    loop = field_fakes.install(monkeypatch, tmp_path, game, clock)
    banking.CONFIG.write_text(
        json.dumps({"enabled": True, "withdraw_essentials": True}), encoding="utf-8"
    )
    with pytest.raises(field_fakes.ReachedWarehouse):
        loop._run_route()
    events = field_fakes.events(loop)
    visit = loop.town_visit.state()
    ops = game.ops
    artifact = {
        "scenario": "restock_warehouse_trip_heal_close_hover_race",
        "town_visit": {
            "phase": visit["phase"],
            "reasons": visit["reasons"],
            "restock_target_pid": visit["restock_target"]["pid"],
        },
        "events": [
            {"event": e["event"], "phase": e["phase"]}
            for e in events
            if e["event"] not in ("runback_progress",)
        ],
        "town_actions": [
            o["action"] + (":" + o["window"] if "window" in o else "")
            for o in ops
            if o["op"] == "town"
        ],
        "route_jumps": sum(o["op"] == "route-jump" for o in ops),
        "controls": [o["enabled"] for o in ops if o["op"] == "controls"],
        "final_position": game.position,
        "potions_left": game.potions(),
        "open_panels": sorted(game.open_panels),
        "ended_at": "open-bank",
    }
    path = tmp_path / "travel-heal-close-race-e2e.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    saved = json.loads(path.read_text(encoding="utf-8"))

    names = [e["event"] for e in saved["events"]]
    assert "failed" not in names and "stopped" not in names
    order = ["supplies_ready", "hunt_started", "return_required", "travel"]
    assert [n for n in names if n in order][:4] == order
    heal = names.index("travel_heal_verified")
    assert names[heal + 1] == "travel_heal_close_deferred"
    assert names.index("travel_panel_closed") > heal
    assert names.count("travel_heal_verified") == 1
    assert saved["town_visit"] == {
        "phase": "town_work",
        "reasons": ["restock"],
        "restock_target_pid": 18532,
    }
    actions = [a for a in saved["town_actions"] if a != "vendor-status"]
    # One potion only; the failed close is followed by the panel pass that
    # closes Inventory before any further route jump.
    assert actions.count("consume-healing") == 1
    consume = actions.index("consume-healing")
    assert actions[consume + 1 : consume + 3] == [
        "close:Inventory",
        "clear-travel-panels",
    ]
    assert saved["potions_left"] == 3 and saved["open_panels"] == []
    at = next(i for i, o in enumerate(ops) if o.get("action") == "consume-healing")
    after = ops[at:]
    first_jump = next(i for i, o in enumerate(after) if o["op"] == "route-jump")
    assert {"op": "town", "action": "clear-travel-panels"} in after[:first_jump]
    assert saved["route_jumps"] >= 10
    assert max(abs(a - b) for a, b in zip(saved["final_position"], (227, 246))) <= 12
    assert actions[-3:] == ["warehouse-locate", "close:Shop", "open-bank"]
    # Off before hunting checks, On to hunt, Off for town travel; never On again.
    assert saved["controls"] == [False, True, False]
