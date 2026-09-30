"""A restock reads a TwinCityGate only when its shops are in Twin City.

The scroll always lands in Twin City and may be read on Phoenix Castle
(return_scroll.SCROLL_SOURCES), so a WingedSnake restock in Phoenix must walk
its 40 seconds instead of scrolling away and paying the Conductress back.
"""

from types import SimpleNamespace as NS

import pytest

from conquest import overnight, return_scroll
from conquest.overnight import OvernightLoop


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(overnight.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(overnight.time, "sleep", lambda d: now.__setitem__(0, now[0] + d))
    return now


@pytest.mark.parametrize("restock_map, expected", [(1002, ["scroll"]), (1011, [])])
def test_restock_scrolls_only_to_a_twin_city_restock(monkeypatch, restock_map, expected):
    calls = []
    monkeypatch.setattr(return_scroll, "return_to_town", lambda loop: calls.append("scroll") or True)
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = NS(restock_map_id=restock_map)
    assert loop.scroll_to_restock_town() is bool(expected)
    assert calls == expected


# Suicide died on the ThunderApe -> Ape City runback at (563, 432) on
# 2026-09-29 22:39: the walk crosses the bosses around the GiantApe plain.
@pytest.mark.parametrize(
    "map_id, position, expected",
    [
        (1020, (330, 300), ["gate:1020"]),  # the ThunderApe field, 245 out
        (1020, (605, 335), ["gate:1020"]),  # the GiantApe plain, 210 out
        (1020, (620, 645), []),  # the Macaque field walks home
        (1002, (381, 21), ["gate:1020"]),  # another map: the gate decides
    ],
)
def test_an_ape_city_restock_reads_its_gate_only_from_far_away(
    monkeypatch, clock, map_id, position, expected
):
    calls = []
    monkeypatch.setattr(
        return_scroll, "read_gate", lambda loop, home: calls.append(f"gate:{home}") or True
    )
    monkeypatch.setattr(
        return_scroll, "return_to_town", lambda loop: pytest.fail("a TwinCityGate lands in Twin City")
    )
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = NS(restock_map_id=1020)
    loop.living = lambda: {"embedded_controls": {"life": {"map_id": map_id, "position": list(position)}}}
    assert loop.scroll_to_restock_town() is bool(expected)
    assert calls == expected


def far_loop(monkeypatch, scene, jump=None):
    """An Ape City restock on the GiantApe plain; ``scene`` holds the live
    life/monsters, and each escape jump runs ``jump(scene)``."""
    events, jumps, gates = [], [], []
    monkeypatch.setattr(return_scroll, "read_gate", lambda loop, home: gates.append(home) or True)
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = NS(restock_map_id=1020, king_clearance=15, elite_clearance=13)
    loop.care = NS(check=lambda health: None)
    loop.record = lambda event, **fields: events.append(event)
    loop.living = lambda: {
        "embedded_controls": {
            "life": {"map_id": 1020, "position": list(scene["position"]), "current_hp": scene["hp"]},
            "monsters": [dict(m) for m in scene["monsters"]],
        }
    }

    def evade(health, tiles):
        # Like evade_in_field: a jump only with a living monster within
        # ``tiles`` or a boss inside its clearance (13 here).
        position = scene["position"]
        close = any(
            m.get("alive") is not False
            and max(abs(a - b) for a, b in zip(m["position"], position))
            <= (13 if m["name"].endswith(("King", "Aide")) else tiles)
            for m in scene["monsters"]
        )
        if not close:
            return False
        jumps.append(tiles)
        if jump:
            jump(scene)
        return True

    loop.evade_in_field = evade
    return loop, events, jumps, gates


def test_the_gate_waits_for_a_quiet_spot_under_fire(monkeypatch, clock):
    # Toxic died at (619, 281) on 2026-09-30 02:23 reading its ApeCityGate
    # beside the GiantApe pack and two Aides.
    scene = {
        "position": (619, 281),
        "hp": 600,
        "monsters": [{"name": "GiantApe", "position": [620, 282]},
                     {"name": "GiantApeAide", "position": [611, 298]}],
    }

    def jump(scene):
        # The first jumps are still hit; the third lands clear of everyone.
        scene["hp"] -= 50 if len(jumps) < 3 else 0
        if len(jumps) >= 3:
            scene["position"] = (650, 330)

    loop, events, jumps, gates = far_loop(monkeypatch, scene, jump)
    start = clock[0]
    assert loop.gate_home_from_afar()
    assert gates == [1020] and len(jumps) >= 3
    assert all(tiles == overnight.GATE_CLEAR_TILES for tiles in jumps)
    # No HP lost for GATE_QUIET_SECONDS before the read.
    assert clock[0] - start >= overnight.GATE_QUIET_SECONDS
    assert "gate_deferred_under_fire" not in events


@pytest.mark.parametrize(
    "monsters",
    [
        [{"name": "GiantApe", "position": [627, 281]}],  # a monster 8 tiles off
        [{"name": "GiantApeAide", "position": [619, 297]}],  # an Aide at 16 < 13 + 4
        [{"name": "GiantApeKing", "position": [601, 281]}],  # a King at 18 < 15 + 4
    ],
)
def test_no_gate_without_a_quiet_spot_the_restock_walks(monkeypatch, clock, monsters):
    scene = {"position": (619, 281), "hp": 900, "monsters": monsters}
    loop, events, jumps, gates = far_loop(monkeypatch, scene)
    start = clock[0]
    assert not loop.gate_home_from_afar()
    assert gates == []
    assert events == ["gate_deferred_under_fire"]
    assert clock[0] - start >= overnight.GATE_SETTLE_SECONDS


def test_a_quiet_field_reads_the_gate_after_the_quiet_wait(monkeypatch, clock):
    scene = {
        "position": (619, 281),
        "hp": 900,
        "monsters": [{"name": "GiantApe", "position": [630, 281]},  # 11 tiles
                     {"name": "GiantApe", "position": [619, 270], "alive": False}],
    }
    loop, events, jumps, gates = far_loop(monkeypatch, scene)
    start = clock[0]
    assert loop.gate_home_from_afar()
    assert gates == [1020] and jumps == [] and events == []
    assert overnight.GATE_QUIET_SECONDS <= clock[0] - start < overnight.GATE_QUIET_SECONDS + 1
