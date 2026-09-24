import pytest
from conquest.trial import scatter_receipt_ready


@pytest.mark.parametrize(
    "elapsed,before,after,ready",
    [
        (0.19, 100, 97, False),
        (0.2, 100, 97, True),
        (0.4, 100, 99, False),
        (0.4, 100, 100, False),
        (0.4, 100, 5000, False),
        (0.4, 100, 94, True),
    ],
)
def test_repositioning_requires_full_scatter_consumption(elapsed, before, after, ready):
    assert scatter_receipt_ready(elapsed, before, after) is ready


def test_two_arrow_receipt_is_explicit_and_does_not_change_default():
    assert scatter_receipt_ready(0.25, 100, 98, 0.2, 2)
    assert not scatter_receipt_ready(0.25, 100, 98, 0.2)
    assert not scatter_receipt_ready(0.1, 100, 98, 0.2, 2)
    assert not scatter_receipt_ready(0.25, 100, 99, 0.2, 2)
    assert not scatter_receipt_ready(0.25, 100, 200, 0.2, 2)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("scan_seconds", [0.01, 0.4])
@pytest.mark.parametrize("loot_seconds", [0, 0.4])
@pytest.mark.parametrize("projection_changed", [False, True])
@pytest.mark.parametrize("pickup", [False, True])
def test_fresh_target_timing_does_not_inherit_earlier_work_or_accept_slow_scans(
    tmp_path,
    monkeypatch,
    enabled,
    scan_seconds,
    loot_seconds,
    projection_changed,
    pickup,
):
    import logging, yaml, win32api
    from pathlib import Path
    from types import SimpleNamespace
    from conquest import trial
    from conquest.farmer_profile import CombatSpeed
    from conquest.memory_inventory import InventorySnapshot, Item
    from conquest.vision import Target

    now = [10.0]
    calls = []
    stages = []
    projections = [0]
    config = yaml.safe_load(Path("profiles/pheasant-foreground-trial.yaml").read_text())
    config.update(
        character="TimingTest",
        observation_mode="memory_only",
        kite_when_surrounded=False,
        adaptive_scatter=True,
        jump_scatter=False,
        attack_button="right",
        route=[],
        healing_enabled=False,
        loot_allowlist=[],
        interval=0.15,
        client_size=[1036, 793],
        player_anchor=[518, 396],
    )
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(trial.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        trial.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay)
    )
    monkeypatch.setattr(win32api, "GetAsyncKeyState", lambda _: 0)
    monkeypatch.setattr(
        trial,
        "load_combat_speed",
        lambda _: CombatSpeed(
            selected_target_refresh=enabled, coherent_projection=True
        ),
    )

    class Session:
        def request(self, operation, body=None):
            if operation == "health":
                return {"input_revision": 7, "window": {"hwnd": 1}}
            if operation == "sample":
                values = dict(
                    name="TimingTest",
                    position=[423, 455],
                    max_hp=[100],
                    kill_counter=[0],
                    level=[18],
                    map=[1002],
                )
                return {"fields": [{"name": k, "value": v} for k, v in values.items()]}
            assert operation == "foreground-click"
            calls.append(now[0])
            return {}

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.01
            return InventorySnapshot(
                now[0],
                now[0],
                (Item(1, 1000000, 1, 1, 0),),
                Item(2, 1050000, 200 - 3 * len(calls), 200, None),
                0,
                40,
            )

    def scan(*args):
        stages.append("targets")
        now[0] += scan_seconds
        return [Target("Pheasant", 600, 400, 1, 1, 1000, (427, 455), 100)]

    drop = object()
    dispatches = []

    def guarded_dispatch(callback, **kwargs):
        dispatches.append(kwargs)
        if pickup:
            assert kwargs["drop"] is drop and kwargs["target"] is None
        return callback()

    def loot(inventory, position, dispatch):
        stages.append("loot")
        now[0] += loot_seconds
        if pickup:
            dispatch((600, 400), drop=drop)
            return True
        return False

    def projection():
        projections[0] += 1
        # The second coherent read follows loot/strategy work. Movement there
        # must invalidate the earlier life/position rather than refresh its age.
        return (
            (424 if projection_changed and projections[0] % 2 == 0 else 423, 455),
            (518, 396),
        )

    strategy = SimpleNamespace(
        observe=lambda *a: None, button=lambda *a: "right", issued=lambda *a: None
    )

    def earlier_work():
        now[0] += 0.4
        return strategy

    supervisor = SimpleNamespace(
        last_target=None,
        recovery=SimpleNamespace(terrain=SimpleNamespace(width=1000, height=1000)),
        observe=lambda: {"health_ratio": 1.0, "waiting": False, "defending": False},
        memory_targets=scan,
        attack_strategy=earlier_work,
        dispatch=guarded_dispatch,
        loot_step=loot,
        player_projection=projection,
        finish_target=lambda *a: None,
    )
    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    monkeypatch.setattr(
        trial,
        "resolve_player",
        lambda *a: dict.fromkeys(
            ("name", "position", "max_hp", "kill_counter", "level", "map"), 1
        ),
    )
    camera = SimpleNamespace(geometry=lambda: (0, 0), close=lambda: None)
    result = trial.run_trial(
        path,
        None,
        tmp_path / "run",
        3,
        logging.getLogger("test"),
        session_override=Session(),
        camera_factory=lambda *a: camera,
        supervisor=supervisor,
    )
    assert result["reason"] == "duration_limit"
    assert bool(calls) == ((pickup or scan_seconds < 0.35) and not projection_changed)
    assert bool(dispatches) == bool(calls)
    assert stages[0] == "loot"
    if pickup:
        assert "targets" not in stages  # A real pickup retains priority over combat.
    elif not projection_changed:
        assert all(
            stages[i - 1] == "loot" for i, s in enumerate(stages) if s == "targets"
        )
