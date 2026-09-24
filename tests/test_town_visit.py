from copy import deepcopy
import json
import sqlite3

import pytest

from conquest.town_visit import TownVisit, kill_checkpoint


@pytest.fixture(autouse=True)
def clean_context(monkeypatch):
    monkeypatch.delenv('CONQUEST_DATA_ROOT',raising=False)
    monkeypatch.delenv('CONQUEST_PROFILE_ID',raising=False)


@pytest.fixture
def trip(tmp_path):
    now=[1000.0]
    sample={'available':True,'observed_at':1000,'cursor':10,'session_id':900,
            'kills':20,'last_kill':{'rowid':8,'time':990,'count':1}}
    visit=TownVisit(tmp_path/'trip.json',clock=lambda:now[0],probe=lambda:deepcopy(sample),profile='farmer-1')
    health={'target':{'pid':7,'creation_time_100ns':100,'path':'game.exe'},'embedded_controls':{
        'observed_at':1021,'control':{'enabled':True},'manual_input_fence':False,'manual_mouse':False,
        'life':{'map_id':1000,'dead_candidate':False,'current_hp':100}}}
    return visit,now,sample,health


def returned(trip):
    visit,now,sample,health=trip
    visit.begin('restock',hunt_map_id=1000,route_id='bandit')
    now[0]=1020;sample.update(observed_at=1020,cursor=15,kills=22)
    visit.complete_town_work('restock')
    visit.returning(1000,target=health['target'])
    now[0]=1021;sample.update(observed_at=1021,cursor=16,kills=23,
                            last_kill={'rowid':16,'time':1021,'count':1})
    return visit,now,sample,health


def test_required_trip_reuses_identity_and_original_baseline_across_restart(trip):
    visit,now,sample,health=trip
    first=visit.begin('restock',hunt_map_id=1000,route_id='bandit')
    now[0]=1010;sample['kills']=22
    restart=TownVisit(visit.path,clock=visit.clock,probe=visit.probe,profile='farmer-1')
    second=restart.begin('urgent_banking',hunt_map_id=1000)
    assert second['town_visit_id']==first['town_visit_id']
    assert second['required_at']==1000 and second['baseline']['kills']==20
    assert second['reasons']==['restock','urgent_banking']
    assert restart.active_id()==first['town_visit_id']


def test_no_visit_is_created_by_hunting_or_unnecessary_town_request(trip):
    visit,now,sample,health=trip
    assert visit.returning(1000,target=health['target']) is None
    assert visit.observe_hunting(health) is None and not visit.path.exists()
    with pytest.raises(ValueError):visit.begin('periodic_merchant_trip',hunt_map_id=1000)
    assert not visit.path.exists()


def test_missing_town_work_completion_blocks_return_even_after_restart(trip):
    visit,now,sample,health=trip
    visit.begin('restock',hunt_map_id=1000)
    now[0]=1020;sample.update(observed_at=1020,cursor=15,kills=22)
    restart=TownVisit(visit.path,clock=visit.clock,probe=visit.probe,profile='farmer-1')
    with pytest.raises(ValueError,match='Unfinished town work'):
        restart.returning(1000,target=health['target'])
    assert restart.state()['phase']=='town_work'
    assert not restart.state().get('town_work_completed_at')


def test_trip_completes_only_after_return_and_a_new_verified_kill(trip):
    visit,now,sample,health=trip
    begin=visit.begin('restock',hunt_map_id=1000)
    assert visit.observe_hunting(health) is None
    visit,now,sample,health=returned(trip)
    completed=visit.observe_hunting(health)
    assert completed['town_visit_id']==begin['town_visit_id']
    assert completed['phase']=='complete' and completed['elapsed_seconds']==21
    assert completed['baseline']['kills']==20 and completed['return_baseline']['kills']==22
    assert completed['first_verified_resume_kill']=={'rowid':16,'time':1021,'count':1}
    assert visit.active_id() is None and visit.observe_hunting(health) is None
    now[0]=1030;next_visit=visit.begin('restock',hunt_map_id=1000)
    assert next_visit['town_visit_id']!=completed['town_visit_id']
    assert next_visit['history'][0]['town_visit_id']==completed['town_visit_id']
    assert next_visit['history'][0]['phase']=='complete' and 'history' not in next_visit['history'][0]


@pytest.mark.parametrize('failure',['off','paused','manual','dead','map','stale','client',
                                  'session','counter_reset','counter_unchanged','old_row','old_time','metrics'])
def test_unsafe_or_unverified_resume_never_completes_trip(trip,failure):
    visit,now,sample,health=returned(trip)
    data=health['embedded_controls']
    if failure=='off':data['control']['enabled']=False
    if failure=='paused':data['control']['paused']=True
    if failure=='manual':data['manual_mouse']=True
    if failure=='dead':data['life']['dead_candidate']=True
    if failure=='map':data['life']['map_id']=1036
    if failure=='stale':data['observed_at']=1010
    if failure=='client':health['target']['pid']=8
    if failure=='session':sample['session_id']=901
    if failure=='counter_reset':sample['kills']=1
    if failure=='counter_unchanged':sample['kills']=22
    if failure=='old_row':sample['last_kill']['rowid']=15
    if failure=='old_time':sample['last_kill']['time']=1019
    if failure=='metrics':sample['available']=False
    assert visit.observe_hunting(health) is None
    assert visit.state()['phase']=='returning_to_hunt'


def test_restart_keeps_return_baseline_and_does_not_reset_kill_metrics(trip):
    visit,now,sample,health=returned(trip)
    restart=TownVisit(visit.path,clock=visit.clock,probe=visit.probe,profile='farmer-1')
    restart.returning(1000,target=health['target'])
    assert restart.state()['return_baseline']['cursor']==15
    assert restart.state()['return_started_at']==1020
    before=deepcopy(sample)
    assert restart.observe_hunting(health)['phase']=='complete'
    assert sample==before


def test_unavailable_return_baseline_requires_a_later_kill_after_recovery(trip):
    visit,now,sample,health=trip
    visit.begin('restock',hunt_map_id=1000)
    sample['available']=False;now[0]=1020
    visit.complete_town_work('restock')
    visit.returning(1000,target=health['target'])
    sample.update(available=True,observed_at=1021,cursor=16,kills=23,
                  last_kill={'rowid':16,'time':1021,'count':1});now[0]=1021
    assert visit.observe_hunting(health) is None
    assert visit.state()['return_baseline']['cursor']==16
    sample.update(observed_at=1022,cursor=17,kills=24,last_kill={'rowid':17,'time':1022,'count':1})
    now[0]=1022;health['embedded_controls']['observed_at']=1022
    assert visit.observe_hunting(health)['phase']=='complete'


def test_stop_before_first_return_kill_rebases_new_session_then_requires_later_kill(trip,tmp_path):
    from conquest.session_kills import SessionKills
    from conquest.discord_notify import write_json
    visit,now,sample,health=trip
    output=tmp_path/'stats';output.mkdir()
    with sqlite3.connect(output/'trial.sqlite3') as db:
        db.execute('CREATE TABLE events(time REAL,event TEXT,payload TEXT)')
    stats=SessionKills(output,clock=lambda:now[0]);stats.begin()
    def publish():
        write_json(output/'app-state.json',{'character':'Parasite','updated_at':now[0],**stats.refresh()})
        health['embedded_controls']['observed_at']=now[0]
    visit.probe=lambda:kill_checkpoint(now=now[0],output=output)
    publish();visit.begin('restock',hunt_map_id=1000)
    now[0]=1020;publish();visit.complete_town_work('restock')
    visit.returning(1000,target=health['target'])
    original=visit.state()['return_baseline']
    now[0]=1021;stats.stop();publish();health['embedded_controls']['control']['enabled']=False
    assert visit.observe_hunting(health) is None
    now[0]=1030;stats.begin();publish();health['embedded_controls']['control']['enabled']=True
    assert visit.observe_hunting(health) is None
    row=visit.state()
    assert row['phase']=='returning_to_hunt' and visit.active_id()==row['town_visit_id']
    assert row['return_baseline']['session_id']==1030 and row['return_baseline']['kills']==0
    assert row['return_baseline_history'][0]['previous_baseline']==original
    assert row['baseline']['session_id']==1000  # Historical town metrics are preserved.
    assert visit.observe_hunting(health) is None and len(visit.state()['return_baseline_history'])==1
    now[0]=1031
    with sqlite3.connect(output/'trial.sqlite3') as db:
        db.execute('INSERT INTO events VALUES(?,?,?)',(now[0],'kill_verified',json.dumps({'count':1})))
    publish()
    completed=visit.observe_hunting(health)
    assert completed['phase']=='complete' and completed['resumed_hunting']['session_id']==1030
    assert completed['first_verified_resume_kill']['time']==1031 and visit.active_id() is None
    assert stats.snapshot()['kills']==1


def test_kills_already_seen_in_the_new_session_cannot_complete_the_rebase(trip):
    visit,now,sample,health=returned(trip)
    now[0]=1030;health['embedded_controls']['observed_at']=1030
    sample.update(session_id=1030,observed_at=1030,cursor=99,kills=50,
                  last_kill={'rowid':99,'time':1030,'count':1})
    assert visit.observe_hunting(health) is None
    assert visit.state()['return_baseline']['kills']==50
    assert visit.observe_hunting(health) is None
    now[0]=1031;health['embedded_controls']['observed_at']=1031
    sample.update(observed_at=1031,cursor=100,kills=51,last_kill={'rowid':100,'time':1031,'count':1})
    assert visit.observe_hunting(health)['first_verified_resume_kill']['rowid']==100


@pytest.mark.parametrize('failure',['pid','creation','path','missing_identity','map','stale','dead','hp',
                                  'off','paused','manual','fence','missing_fence','metrics','old_session'])
def test_session_rollover_requires_same_live_unfenced_return_evidence(trip,failure):
    visit,now,sample,health=returned(trip)
    before=visit.path.read_bytes()
    now[0]=1030;sample.update(session_id=1030,observed_at=1030,kills=0)
    data=health['embedded_controls'];data['observed_at']=1030
    if failure=='pid':health['target']['pid']=8
    if failure=='creation':health['target']['creation_time_100ns']=101
    if failure=='path':health['target']['path']='another.exe'
    if failure=='missing_identity':health['target'].pop('creation_time_100ns')
    if failure=='map':data['life']['map_id']=1036
    if failure=='stale':data['observed_at']=1020
    if failure=='dead':data['life']['dead_candidate']=True
    if failure=='hp':data['life']['current_hp']=0
    if failure=='off':data['control']['enabled']=False
    if failure=='paused':data['control']['paused']=True
    if failure=='manual':data['manual_mouse']=True
    if failure=='fence':data['manual_input_fence']=True
    if failure=='missing_fence':data.pop('manual_input_fence')
    if failure=='metrics':sample['available']=False
    if failure=='old_session':sample['session_id']=901
    assert visit.observe_hunting(health) is None
    assert visit.path.read_bytes()==before and visit.active_id() is not None


@pytest.mark.parametrize('change',[{'pid':8},{'creation_time_100ns':101},{'path':'other.exe'}])
def test_hunt_reentry_cannot_replace_an_unfinished_return_identity(trip,change):
    visit,now,sample,health=returned(trip)
    before=visit.path.read_bytes();health['target'].update(change)
    with pytest.raises(ValueError,match='target changed'):
        visit.returning(1000,target=health['target'])
    assert visit.path.read_bytes()==before


def test_readonly_checkpoint_uses_verified_rows_without_touching_counter_files(tmp_path):
    app={'character':'Parasite','updated_at':1000,'kill_session_active':True,
         'kill_session_started_at':900,'kills':20,'kill_metrics_note':None}
    path=tmp_path/'app-state.json';path.write_text(json.dumps(app));original=path.read_bytes()
    dbpath=tmp_path/'trial.sqlite3'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE events(time REAL,event TEXT,payload TEXT)')
        db.execute('INSERT INTO events VALUES(?,?,?)',(999,'kill_verified',json.dumps({'count':2})))
        db.execute('INSERT INTO events VALUES(?,?,?)',(1000,'attack',json.dumps({'count':100})))
    checkpoint=kill_checkpoint(now=1000,output=tmp_path)
    assert checkpoint['available'] and checkpoint['cursor']==2
    assert checkpoint['last_kill']=={'rowid':1,'time':999,'count':2}
    assert checkpoint['kills']==20 and path.read_bytes()==original
    assert not (tmp_path/'kill-session.json').exists()
    assert not kill_checkpoint(now=1010,output=tmp_path)['available']
    with sqlite3.connect(dbpath) as db:
        db.execute('INSERT INTO events VALUES(?,?,?)',(1000,'kill_verified',json.dumps({'count':1})))
    checkpoint=kill_checkpoint(now=1000,output=tmp_path,after_cursor=0,after_time=998)
    assert checkpoint['last_kill']['rowid']==3 and checkpoint['resume_kill']['rowid']==1


def test_market_visit_links_to_required_trip_without_renewing_budget(trip,tmp_path,monkeypatch):
    from conquest.merchants import service_visit
    import conquest.town_visit as module
    visit,now,sample,health=trip
    row=visit.begin('urgent_banking',hunt_map_id=1000)
    monkeypatch.setattr(module,'TownVisit',lambda:visit)
    assert service_visit.parent_visit()==row['town_visit_id']
    market=service_visit.MarketVisit(tmp_path/'market.json',clock=lambda:now[0])
    first=market.begin(parent=service_visit.parent_visit(),profile='farmer-1')
    now[0]=1040
    second=market.begin(parent=service_visit.parent_visit(),profile='farmer-1')
    assert first['visit_id']==second['visit_id'] and second['deadline']==1060
    assert second['town_visit_id']==row['town_visit_id']
