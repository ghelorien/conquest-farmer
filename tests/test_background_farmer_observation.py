from dataclasses import dataclass
from types import SimpleNamespace
import struct
import threading

import pytest

from conquest import background_farmer_observation as module
from conquest.memory_life import CLIENT_SHA256


@dataclass
class Life:
    object_address: int = 0x100000
    map_id: int = 1002
    position: tuple = (100, 101)
    current_hp: int = 100
    max_hp: int = 100
    dead_candidate: bool = False


@pytest.fixture
def ui(monkeypatch):
    identity = {'pid': 12, 'creation_time_100ns': 34}
    control = {'enabled': True, 'revision': 5, 'input_mode': 'foreground'}
    session = SimpleNamespace(expected_sha256=CLIENT_SHA256, identity=identity,
        assert_identity=lambda: None, read_block=lambda address, size: struct.pack('<II', 130, 2))
    observer = SimpleNamespace(character='Parasite', lock=threading.RLock(), adapter=session,
        health_layout=object(), operations=SimpleNamespace(target=SimpleNamespace(hwnd=56,
            snapshot=lambda: {'pid': 12, 'hwnd': 56, 'client_size': [1000, 700]})))
    app = SimpleNamespace(observer=observer, client=(12, 56, identity),
                          control=SimpleNamespace(snapshot=lambda: dict(control)))
    monkeypatch.setattr(module, 'read_life', lambda *args: Life())
    monkeypatch.setattr(module, 'BackgroundObservationReader', lambda s:
                        SimpleNamespace(snapshot=lambda: {'frame': 1}))
    return SimpleNamespace(app=app)


def test_reuses_observer_preserves_running_intent_and_reports_missing_sections(ui):
    observer = ui.app.observer
    result = module.snapshot(ui)
    assert result['available'] and not result['input_qualified']
    assert result['motion']['jump_observed']
    assert result['control']['enabled'] is True
    assert ui.app.observer is observer
    assert result['gui']['available']
    assert not result['inventory']['available']
    assert not result['entities']['available']
    assert not result['selected_skill']['available']


def test_absent_and_wrong_character_are_clear_unavailable(ui):
    ui.app.observer.character = 'Dutch'
    assert 'not Parasite' in module.snapshot(ui)['reason']
    ui.app.observer = None
    assert 'no attached' in module.snapshot(ui)['reason']


def test_map_transition_cannot_produce_cross_map_snapshot(ui, monkeypatch):
    calls = iter((Life(), Life(map_id=1036)))
    monkeypatch.setattr(module, 'read_life', lambda *args: next(calls))
    assert module.snapshot(ui)['available'] is False


def test_observer_replacement_invalidates_result(ui, monkeypatch):
    def read(*args):
        ui.app.observer = None
        return Life()
    monkeypatch.setattr(module, 'read_life', read)
    assert module.snapshot(ui)['available'] is False


def test_motion_race_does_not_claim_jump(ui):
    calls = iter((struct.pack('<II', 130, 2), struct.pack('<II', 120, 3)))
    ui.app.observer.adapter.read_block = lambda *args: next(calls)
    result = module.snapshot(ui)
    assert result['available']
    assert not result['motion']['stable']
    assert not result['motion']['jump_observed']


def test_optional_budget_never_starts_additional_scans(ui, monkeypatch):
    monkeypatch.setattr(module, 'BackgroundObservationReader', lambda s:
                        pytest.fail('optional scan started after deadline'))
    ticks = iter((0, 4, 4, 4, 4, 4))
    result = module._snapshot(ui.app, ui.app.observer, clock=lambda: next(ticks))
    assert result['gui']['reason'] == 'Observation budget exhausted'
    assert result['ground']['reason'] == 'Observation budget exhausted'
