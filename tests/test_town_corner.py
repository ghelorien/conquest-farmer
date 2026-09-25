from types import SimpleNamespace
import pytest
from conquest import town_corner as tc


@pytest.mark.parametrize(
    "change", ["character", "map", "hash", "source", "dead", "blocked", None]
)
def test_corner_recovery_never_issues_walking_input(change):
    now = [10.0]
    events = []
    panels = []
    terrain = SimpleNamespace(
        map_id=1002 if change == "map" else 1011,
        source_sha256="other" if change == "hash" else tc.TERRAIN_SHA256,
        walkable=lambda p: change != "blocked",
        travel_path=lambda *a: [tc.DESTINATION, (227, 243)],
    )

    def living():
        now[0] += 0.01
        life = dict(
            character="Other" if change == "character" else "Parasite",
            map_id=terrain.map_id,
            position=[194, 227] if change == "source" else list(tc.SOURCE),
            dead_candidate=change == "dead",
            object_address=0x600000,
            max_hp=1274,
            timestamp=now[0],
        )
        return {
            "embedded_controls": {
                "life": life,
                "manual_mouse": False,
                "control": {"enabled": False},
            },
            "window": {"client_size": [1416, 907]},
        }

    loop = SimpleNamespace(
        terrain=terrain,
        living=living,
        info="test",
        town=lambda *a: panels.append(a) or {"closed_panel": None},
        check_stop=lambda: None,
        record=lambda *a, **kw: events.append(a[0]),
    )

    if change is None:
        # The exit itself was qualified only on the retired 1074 client.
        with pytest.raises(ValueError, match="not qualified"):
            tc.recover_corner(loop, (227, 243))
        assert panels == [("clear-travel-panels",)]
    else:
        assert tc.recover_corner(loop, (227, 243)) is False
        assert panels == []
    assert events == []
