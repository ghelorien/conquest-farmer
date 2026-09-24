from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from conquest import town_visit as t
from conquest.discord_notify import write_json


@pytest.fixture
def tail(tmp_path,monkeypatch):
    from conquest import urgent_town_recovery,overnight
    now=[1000.]
    monkeypatch.setattr(t.time,'time',lambda:now[0])
    monkeypatch.setattr(urgent_town_recovery,'transaction_holds',lambda:False)
    monkeypatch.setattr(overnight,'supply_counts',lambda bag,route:bag)
    monkeypatch.setattr(overnight,'needs_town',lambda counts,route:False)
    visit=t.TownVisit(tmp_path/'visit.json',clock=lambda:now[0],probe=lambda:{},profile='farmer')
    target={'pid':7,'creation_time_100ns':100,'path':'game.exe'}
    health={'target':deepcopy(target),'embedded_controls':{'observed_at':1000.,
        'manual_input_fence':False,'manual_mouse':False,
        'life':{'map_id':1011,'dead_candidate':False,'current_hp':100}}}
    bag={'items':[{'uid':1,'type_id':1000020,'amount':5,'limit':1,'slot':0,'plus':0}],
         'equipped_ammo':{'uid':2,'type_id':1050002,'amount':5000,'limit':5000,'slot':None},
         'capacity':40,'silver':200,'timestamp':10.}
    calls=[]
    def town(action,**fields):
        assert action=='supplies', 'Completion recovery must send no game input'
        calls.append(action)
        return deepcopy(bag)
    loop=NS(town_visit=visit,identity=target,health=lambda:deepcopy(health),town=town,
            route=NS(id='bandit',map_id=1000,restock_map_id=1011),
            adopt_ammunition=lambda:calls.append('read_ammunition'))
    visit.begin('restock',hunt_map_id=1000,route_id='bandit')
    return loop,visit,health,bag,calls,now


def test_restart_settles_only_proved_final_write_without_replaying_work(tail):
    loop,visit,health,bag,calls,now=tail
    assert t.checkpoint_verified_tail(loop,'restock')
    assert 'town_work_completed_at' not in visit.state()
    loop.town_visit=t.TownVisit(visit.path,clock=visit.clock,probe=lambda:{},profile='farmer')
    bag['timestamp']=20.  # Fresh read time is not changed ownership.
    assert t.resume_verified_tail(loop)
    assert visit.state()['town_work_completed_kind']=='restock'
    assert visit.state()['phase']=='town_work'  # A new verified hunt is still required.
    assert calls==['supplies','read_ammunition','supplies']
    assert not t.resume_verified_tail(loop)


def test_legacy_unfinished_town_work_remains_held_even_with_full_supplies(tail):
    loop,visit,health,bag,calls,now=tail
    assert not t.resume_verified_tail(loop)
    with pytest.raises(ValueError,match='reconciliation'):visit.require_town_work_complete()
    assert not calls


@pytest.mark.parametrize('changed',['process','map','route','silver','item','ammo','capacity',
                                   'manual','mouse','dead','stale','hold','supplies'])
def test_changed_or_unsafe_final_boundary_never_completes_or_sends_input(tail,monkeypatch,changed):
    from conquest import urgent_town_recovery,overnight
    loop,visit,health,bag,calls,now=tail
    t.checkpoint_verified_tail(loop,'restock')
    if changed=='process':health['target']['pid']=8
    if changed=='map':health['embedded_controls']['life']['map_id']=1036
    if changed=='route':loop.route.id='other'
    if changed=='silver':bag['silver']+=1
    if changed=='item':bag['items'][0]['uid']=3
    if changed=='ammo':bag['equipped_ammo']['amount']-=1
    if changed=='capacity':bag['capacity']=41
    if changed=='manual':health['embedded_controls']['manual_input_fence']=True
    if changed=='mouse':health['embedded_controls']['manual_mouse']=True
    if changed=='dead':health['embedded_controls']['life']['dead_candidate']=True
    if changed=='stale':health['embedded_controls']['observed_at']=990
    if changed=='hold':monkeypatch.setattr(urgent_town_recovery,'transaction_holds',lambda:True)
    if changed=='supplies':monkeypatch.setattr(overnight,'needs_town',lambda *a:True)
    with pytest.raises(ValueError):t.resume_verified_tail(loop)
    assert 'town_work_completed_at' not in visit.state()


def test_missing_native_item_identity_cannot_create_completion_proof(tail):
    loop,visit,health,bag,calls,now=tail
    del bag['items'][0]['uid']
    # This optional post-work observation cannot turn already returned native
    # work into a new failure. It still must not persist usable recovery proof.
    assert t.checkpoint_verified_tail(loop,'restock') is False
    assert 'verified_tail' not in visit.state()
    assert not t.resume_verified_tail(loop)
    assert 'town_work_completed_at' not in visit.state()


def test_urgent_checkpoint_requires_both_completed_native_stages(tail):
    loop,visit,health,bag,calls,now=tail
    visit.begin('urgent_banking',hunt_map_id=1000,target=loop.identity,
                urgent_items=[{'uid':3,'type_id':500003}])
    with pytest.raises(ValueError,match='not returned'):t.checkpoint_verified_tail(loop,'urgent_banking')
    visit.record_urgent_tail('banking',target=loop.identity)
    with pytest.raises(ValueError,match='not returned'):t.checkpoint_verified_tail(loop,'urgent_banking')
    visit.record_urgent_tail('followup',target=loop.identity)
    assert t.checkpoint_verified_tail(loop,'urgent_banking')
    assert t.resume_verified_tail(loop)


def test_successive_work_archives_previous_proof_instead_of_reusing_it(tail):
    loop,visit,health,bag,calls,now=tail
    t.checkpoint_verified_tail(loop,'restock')
    t.resume_verified_tail(loop)
    visit.begin('restock',hunt_map_id=1000,route_id='bandit')
    row=visit.state()
    assert 'verified_tail' not in row
    assert row['town_work_history'][0]['verified_tail']['kind']=='restock'
    assert not t.resume_verified_tail(loop)


def test_completion_write_failure_preserves_proof_for_exact_read_only_retry(tail,monkeypatch):
    loop,visit,health,bag,calls,now=tail
    t.checkpoint_verified_tail(loop,'restock')
    original=t.write_json
    monkeypatch.setattr(t,'write_json',lambda *a:(_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError,match='disk full'):t.resume_verified_tail(loop)
    assert 'town_work_completed_at' not in visit.state()
    monkeypatch.setattr(t,'write_json',original)
    assert t.resume_verified_tail(loop)


def test_checkpoint_write_failure_never_fabricates_completed_work(tail,monkeypatch):
    loop,visit,health,bag,calls,now=tail
    monkeypatch.setattr(t,'write_json',lambda *a:(_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):t.checkpoint_verified_tail(loop,'restock')
    assert not t.resume_verified_tail(loop)
    assert 'town_work_completed_at' not in visit.state()
