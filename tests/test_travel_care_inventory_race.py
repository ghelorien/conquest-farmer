"""A racing inventory read never ends a travel.

Suicide 2026-09-29 11:16: a heavy-damage retreat started among six Macaques.
TravelCare.check read the carried inventory while potions were being drunk,
got "Inventory contains null or duplicate item pointers", and the ValueError
restarted the whole route. The farmer stood still at (640,610) with no care
and died within ten seconds.

Failure modes, written before the change:
1. A transient inventory ValueError propagates out of check().
2. A read that succeeds on a retry does not heal.
3. A persistent failure raises instead of skipping the tick (and repeats its
   event every tick).
4. The skipped-tick event is unknown to restock reconciliation, so a restart
   refuses the visit as possibly transacted.
"""

from types import SimpleNamespace

from conquest import travel_care
from conquest.restock_restart import NON_TRANSACTIONAL
from conquest.travel_care import TravelCare


def care_with(reads, events, requests):
    care = TravelCare.__new__(TravelCare)
    care.exact_1078 = False
    care.revive_state = {}
    care.pending = None
    care.last_heal = -float("inf")
    care.next_panel_check = float("inf")
    care.info = {}
    care.notify = events.append
    care.xp_step = lambda health: None

    def read():
        result = reads.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    care.inventory = SimpleNamespace(read=read)
    return care


def health(hp=300, max_hp=798):
    return {
        "embedded_controls": {
            "control": {"enabled": False},  # travel care never shares input with farming
            "life": dict(dead_candidate=False, current_hp=hp, max_hp=max_hp),
        }
    }


def race():
    return ValueError("Inventory contains null or duplicate item pointers")


def test_a_racing_read_is_retried_and_the_heal_goes_ahead(monkeypatch):
    # 1, 2
    monkeypatch.setattr(travel_care.time, "sleep", lambda s: None)
    potion = SimpleNamespace(uid=7, type_id=1000030, amount=1)
    bag = SimpleNamespace(items=(potion,))
    monkeypatch.setattr(travel_care, "potion_count", lambda inventory: 1)
    monkeypatch.setattr(travel_care, "pick_potion", lambda inventory, missing: potion)
    sent = []
    receipt = {"consumed": True, "uid": 7, "type_id": 1000030,
               "hp_before": 300, "hp_after": 560, "remaining": 5}
    monkeypatch.setattr(
        travel_care, "request", lambda info, op, body: sent.append(body) or receipt
    )
    events = []
    care = care_with([race(), race(), bag], events, sent)
    care.check(health())
    assert sent and sent[0]["action"] == "consume-healing"  # then its Inventory close


def test_a_persistent_race_skips_the_tick_without_raising(monkeypatch):
    # 3
    monkeypatch.setattr(travel_care.time, "sleep", lambda s: None)
    events = []
    care = care_with([race() for _ in range(8)], events, [])
    care.check(health())
    care.check(health())
    retries = [e for e in events if e["event"] == "travel_care_inventory_retry"]
    assert len(retries) == 1  # reported once per distinct detail


def test_the_skipped_tick_event_is_non_transactional():
    # 4
    assert "travel_care_inventory_retry" in NON_TRANSACTIONAL
