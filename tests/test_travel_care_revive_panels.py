"""After a revive is rejected before input, travel care clears display panels.

Toxic died in Ape City on 2026-09-29 10:44 with a travel heal's Inventory
open over the ReviveButton popup: every revive hover failed before input
("Pointer is not over the memory-identified merchant control"), and the
farmer stayed dead. The first attempt still goes straight to Revive (no town
input before it); only after a pre-input rejection does the next pass close
display panels first.
"""

from types import SimpleNamespace

import pytest

from conquest import travel_care


def care(tmp_path, monkeypatch, phase):
    ops = []

    def request(info, operation, body=None):
        ops.append(operation if operation != "town" else "town:" + body["action"])
        return {}

    monkeypatch.setattr(travel_care, "request", request)
    monkeypatch.setattr(travel_care, "farmer_name", lambda: "Toxic")
    care = travel_care.TravelCare.__new__(travel_care.TravelCare)
    care.info = "worker"
    care.notify = lambda event: None
    care.health_layout = {}
    care.exact_1078 = True
    care.revive_journal = tmp_path / "travel-revive.json"
    care.revive_state = {"phase": phase, "attempts": 0} if phase else {}
    care.last_revive = -float("inf")
    care.pending = None
    return care, ops


def dead_health():
    life = {
        "dead_candidate": True,
        "revive_ready_candidate": True,
        "object_address": 1,
        "map_id": 1020,
        "position": [602, 278],
    }
    return {
        "target": {"pid": 1},
        "embedded_controls": {"life": life, "control": {"enabled": False}},
        "window": {"client_size": [1416, 876]},
    }


def test_a_fresh_death_revives_without_town_input(tmp_path, monkeypatch):
    care_, ops = care(tmp_path, monkeypatch, None)
    with pytest.raises(travel_care.TravelStateChanged, match="living route position"):
        care_.check(dead_health())
    assert ops == ["revive-click"]


def test_a_rejected_revive_clears_panels_before_trying_again(tmp_path, monkeypatch):
    care_, ops = care(tmp_path, monkeypatch, "preinput_rejected")
    with pytest.raises(travel_care.TravelStateChanged, match="living route position"):
        care_.check(dead_health())
    assert ops == ["town:clear-travel-panels", "revive-click"]


def test_a_refused_panel_clear_still_tries_the_revive(tmp_path, monkeypatch):
    care_, ops = care(tmp_path, monkeypatch, "preinput_rejected")

    def request(info, operation, body=None):
        ops.append(operation)
        if operation == "town":
            raise ValueError("Town action requires a living character on the town map")
        return {}

    monkeypatch.setattr(travel_care, "request", request)
    with pytest.raises(travel_care.TravelStateChanged, match="living route position"):
        care_.check(dead_health())
    assert ops == ["town", "revive-click"]
