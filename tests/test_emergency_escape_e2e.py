"""Leave a losing fight: a low-HP escape through a crowd, then a scroll.

Live 2026-09-27 13:19 on Laptop2: Suicide (level 20, 348 HP) walked into
about 30 Apparitions at (356, 595). HP fell ~100 a second against a
Painkiller every ~1.8 s; ranged_escape found no landing with fewer monsters
than were attacking, so it stood, healed four times and died (11:39 was the
same pattern with three adjacent Apparitions).

Failure modes, written before the code:
1. A crowd with no "fewer monsters" landing leaves the farmer tanking at
   low HP.
2. The low-HP layer fires at healthy HP (jumping about needlessly).
3. The low-HP landing ends within 4 tiles of an attacker.
4. The emergency scroll is read while healing still holds (HP above the
   line, or no recent verified heal).
5. The scroll is tried without one carried, or again right after a refusal.
6. After an emergency return the route reads a second scroll in town.
"""

from types import SimpleNamespace as NS

import pytest

from conquest import native_farm, trial
from test_native_farm import setup


def crowd(monkeypatch, clock, share):
    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: clock[0])
    supervisor.scene_timestamp = clock[0]
    supervisor.health_share = share
    supervisor.last_damage_at = clock[0]
    supervisor.escape_damage_consumed_at = -float("inf")
    # One attacker beside us and monsters beside every possible landing
    # (5-13 tiles out), so no landing has fewer monsters than attack us.
    ring = [
        (20 + dx, 20 + dy)
        for dx in range(-13, 14, 3)
        for dy in range(-13, 14, 3)
        if 5 <= max(abs(dx), abs(dy)) <= 13
    ]
    supervisor.escape_monsters = tuple(NS(position=p) for p in [(21, 20)] + ring)
    supervisor.recovery.terrain = NS(walkable=lambda p: True)
    return supervisor


def test_low_hp_leaves_a_crowd_the_old_rules_would_tank_in(monkeypatch):
    clock = [100.0]
    healthy = crowd(monkeypatch, clock, share=0.9)
    # 2: at healthy HP the crowd still has no qualifying landing: stay.
    assert healthy.ranged_escape((20, 20), (0, 0, 60, 60)) is None
    low = crowd(monkeypatch, clock, share=0.35)
    landing = low.ranged_escape((20, 20), (0, 0, 60, 60))
    # 1: low HP jumps anyway ...
    assert landing is not None
    # 3: ... and never within 4 tiles of the attacker.
    assert max(abs(a - b) for a, b in zip(landing, (21, 20))) >= 4


def test_emergency_scroll_only_when_healing_is_not_holding():
    scrolls = NS(count=lambda t: 2 if t == trial.EMERGENCY_SCROLL else 0)
    none = NS(count=lambda t: 0)
    farmer = NS(emergency_return=lambda: None)
    due = trial.emergency_return_due
    assert due(farmer, 0.25, 99.0, 0, scrolls, 100.0)  # heal 1 s ago, still 25%
    assert not due(farmer, 0.45, 99.0, 0, scrolls, 100.0)  # 4: above the line
    assert not due(farmer, 0.25, 90.0, 0, scrolls, 100.0)  # 4: no recent heal
    assert not due(farmer, 0.25, 99.0, 0, none, 100.0)  # 5: no scroll carried
    assert not due(farmer, 0.25, 99.0, 105.0, scrolls, 100.0)  # 5: just refused
    assert not due(None, 0.25, 99.0, 0, scrolls, 100.0)  # the legacy loop


def test_emergency_return_reads_the_scroll_through_the_town_trade(monkeypatch):
    supervisor, _, life, _ = setup(monkeypatch)
    life.dead_candidate = False
    calls = []
    supervisor.observer.town_trade = lambda body: calls.append(body) or {"state": "verified"}
    monkeypatch.setattr(supervisor, "dispatch", lambda callback, **kw: callback())
    assert supervisor.emergency_return() == {"state": "verified"}
    assert calls == [
        {"action": "return-scroll"},
        {"action": "close", "window": "Inventory"},
    ]


def test_emergency_return_restocks_without_a_second_scroll(monkeypatch):
    # 6: the route treats the runner's emergency_return like a supply stop.
    from conquest import overnight

    loop = overnight.OvernightLoop.__new__(overnight.OvernightLoop)
    events = []
    loop.record = lambda event, **fields: events.append((event, fields))
    loop.stop_farm = lambda: events.append(("stop_farm", {}))
    loop.route = NS(map_id=1002, monster_type_ids=[4], hunting_boundary=(0, 0, 1, 1),
                    patrol_search=NS(expansion_tiles=0, maximum_expansions=0), id="apparition")
    loop.info = "bridge"
    monkeypatch.setattr(overnight, "request", lambda *a, **k: {})
    monkeypatch.setattr("conquest.world_travel.travel_to_map", lambda loop, map_id: None)
    monkeypatch.setattr("conquest.city_travel.ensure_city_visit", lambda loop: None)
    monkeypatch.setattr("conquest.conductress_shortcut.ride", lambda loop: False)
    loop.living = lambda: {"embedded_controls": {"life": {"map_id": 1002, "position": [300, 600]}}}
    loop.health = lambda: {
        "embedded_controls": {
            "control": {
                "enabled": True,
                "execution_state": "runner_stopped",
                "note": "Farm runner stopped: emergency_return",
            }
        }
    }
    monkeypatch.setattr("conquest.merchant_loop_acceptance.observe_hunting", lambda loop, h: False)
    assert loop.hunt() is None
    assert ("return_required", {"reason": "emergency_return",
            "activity": "Returning to town to sell loot and restock"}) in events
    assert not any(e == "return_scroll_verified" for e, _ in events)
