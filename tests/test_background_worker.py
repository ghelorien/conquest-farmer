from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conquest.worker import Operations


@pytest.fixture
def probe_setup(monkeypatch):
    profile = yaml.safe_load((Path(__file__).parents[1] /
        'profiles/classic-1074-health-candidate.yaml').read_text())
    session = SimpleNamespace(expected_sha256=profile['player']['expected_sha256'],
        modules=[], assert_identity=lambda: None, read=lambda address,size: b'x'*size)
    operations = Operations.__new__(Operations)
    operations.session, operations.target, operations.read_only = session, object(), False
    reading = SimpleNamespace(current_hp=213, max_hp=213)
    def reader(adapter, layout, character):
        assert adapter.read_block(0x10000, 4) == b'xxxx'
        assert character == 'Parasite'
        return SimpleNamespace(read=lambda: reading)
    monkeypatch.setattr('conquest.memory_health.MemoryHealthReader', reader)
    calls = []
    def click(target, x, y, size):
        calls.append((target,x,y,size))
        return {'qualified':False, 'messages_queued':True}
    monkeypatch.setattr('conquest.worker.click_probe', click)
    return operations, reading, calls, {'health_profile':profile,
        'character':'Parasite', 'point':[100,200], 'expected_size':[800,600]}


def test_background_probe_uses_decoded_hp_and_remains_unqualified(probe_setup):
    operations, reading, calls, body = probe_setup
    result = operations.dispatch('background-click', body)
    assert len(calls) == 1
    assert result['hp_candidate_before'] == 213
    assert result['max_hp_candidate_before'] == 213
    assert result['qualified'] is False


@pytest.mark.parametrize('hp', [0, 1, 85])
def test_dead_or_low_candidate_health_prevents_probe(probe_setup, hp):
    operations, reading, calls, body = probe_setup
    reading.current_hp = hp
    with pytest.raises(ValueError, match='too low'):
        operations.dispatch('background-click', body)
    assert calls == []


def test_changed_fingerprint_prevents_probe(probe_setup):
    operations, reading, calls, body = probe_setup
    body['health_profile']['player']['expected_sha256'] = 'b'*64
    with pytest.raises(ValueError, match='fingerprint'):
        operations.dispatch('background-click', body)
    assert calls == []


def test_revive_has_separate_ghost_guard_and_fixed_point(probe_setup,monkeypatch):
    operations,_,_,body=probe_setup
    operations.target=SimpleNamespace(snapshot=lambda:{'client_size':[1036,793]})
    life=SimpleNamespace(revive_ready_candidate=True,position=(435,453),map_id=1002)
    monkeypatch.setattr('conquest.memory_life.read_life',lambda *args:life)
    calls=[]
    monkeypatch.setattr('conquest.worker.click_probe',lambda *args,**kwargs:calls.append((args,kwargs)) or {'qualified':False})
    body={k:body[k] for k in ('health_profile','character')}
    body['expected_size']=[1036,793]
    result=operations.dispatch('revive-click',body)
    assert calls[0][0][1:]==(518,640,[1036,793])
    assert result['death_position']==[435,453]
    life.revive_ready_candidate=False
    with pytest.raises(ValueError,match='ghost state'):
        operations.dispatch('revive-click',body)
    life.revive_ready_candidate=True
    with pytest.raises(ValueError,match='arbitrary'):
        operations.dispatch('revive-click',{**body,'point':[100,200]})
    assert len(calls)==1
    operations.read_only=True
    with pytest.raises(ValueError,match='read-only'):
        operations.dispatch('revive-click',body)
