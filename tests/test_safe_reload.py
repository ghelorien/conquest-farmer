import threading
from types import SimpleNamespace as NS
import pytest
from conquest import safe_reload as s


def health():
    return {'target':{'pid':1,'creation_time_100ns':2},'embedded_controls':{
        'observations_available':True,'observed_at':s.time.time(),'monsters':[],
        'external_execution':False,'control':{'enabled':False},'life':{
            'object_address':123,'map_id':1002,'position':[100,350],
            'dead_candidate':False,'current_hp':600,'max_hp':644}}}


@pytest.mark.parametrize('bad',['nearby','unknown_life','stale','injured','dead','mouse','missing'])
def test_uncertain_or_unsafe_spot_never_qualifies(bad):
    h=health();d=h['embedded_controls']
    if bad in ('nearby','unknown_life'):d['monsters']=[{'position':[110,355],'alive':True if bad=='nearby' else None}]
    if bad=='stale':d['observed_at']-=2
    if bad=='injured':d['life']['current_hp']=100
    if bad=='dead':d['life']['dead_candidate']=True
    if bad=='mouse':d['manual_mouse']=True
    if bad=='missing':d['observations_available']=False
    assert not s.clear_observation(h)


def test_handoff_rechecks_damage_and_new_monsters_before_release(monkeypatch):
    h=health();life=h['embedded_controls']['life']
    proof={'target':h['target'],'map_id':1002,'position':[100,350],'hp':600,'verified_at':s.time.time()}
    monkeypatch.setattr('conquest.worker.request',lambda *a:h)
    s.validate_handoff('worker',proof)
    life['current_hp']=599
    with pytest.raises(ValueError,match='Safe spot changed'):s.validate_handoff('worker',proof)
    life['current_hp']=600;h['embedded_controls']['monsters']=[{'position':[101,350]}]
    with pytest.raises(ValueError):s.validate_handoff('worker',proof)


def test_parking_requires_quiet_interval_and_keeps_care_running(monkeypatch):
    now=[0.];checks=[]
    monkeypatch.setattr(s.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(s.time,'sleep',lambda delay:now.__setitem__(0,now[0]+delay))
    h=health();loop=NS(living=lambda:h,care=NS(check=lambda h:checks.append(now[0])),terrain=NS(map_id=1002))
    proof=s.park(loop,threading.Event(),lambda note:None)
    assert now[0]>=3 and len(checks)>=15 and proof['hp']==600


def test_settings_label_error_does_not_starve_control_poll(monkeypatch):
    from conquest.desktop_app import DesktopApp
    calls=[]
    monkeypatch.setattr('conquest.session_plan.plan_note',lambda:(_ for _ in ()).throw(TypeError('old schema')))
    app=NS(session_note=NS(set=lambda text:None),_poll=lambda:calls.append('controls') or True,
           root=NS(after=lambda *args:calls.append('scheduled')),poll=lambda:None)
    DesktopApp.poll(app)
    assert calls==['controls','scheduled']


def test_new_app_resumes_only_the_same_client_and_consumes_handoff(monkeypatch,tmp_path):
    monkeypatch.setattr(s,'RESUME',tmp_path/'resume.json');h=health();calls=[]
    monkeypatch.setattr('conquest.worker.request',lambda *a:h)
    s.write_json(s.RESUME,{'source_pid':-1,'target':h['target'],'resume':True,'expires_at':s.time.time()+60})
    app=NS(last={'worker_info_path':'worker'},runtime=True,update_control=lambda body:calls.append(body))
    s.resume_after_embed(app);s.resume_after_embed(app)
    assert calls==[{'enabled':True}] and not s.RESUME.exists()


def test_local_escape_moves_away_using_walkable_tiles():
    import numpy as np
    from conquest.navigation import TerrainMap
    terrain=TerrainMap(1002,100,100,np.zeros((100,100),dtype=bool),'0'*64,(),())
    step=s.nearby_escape(terrain,[50,50],[{'position':[50,51],'alive':None}])
    assert step and terrain.walkable(step)
    assert max(abs(step[0]-50),abs(step[1]-51))>1
    assert max(abs(step[0]-50),abs(step[1]-50))<=12


def test_cancel_never_starts_safe_spot_movement():
    cancelled=threading.Event();cancelled.set()
    loop=NS(living=lambda:pytest.fail('No input after cancellation'))
    with pytest.raises(ValueError,match='canceled'):s.park(loop,cancelled,lambda note:None)


def test_final_safe_check_failure_preserves_old_app(monkeypatch):
    from conquest.desktop_app import DesktopApp
    calls=[]
    app=NS(thread=None,host=NS(saved=True),last={'worker_info_path':'worker'},reload_proof={},reload_resume=True,
        release=lambda:pytest.fail('Must retain client'),state_text=NS(set=lambda note:calls.append(note)))
    monkeypatch.setattr('conquest.desktop_app.subprocess.run',lambda *a,**k:NS(returncode=0))
    monkeypatch.setattr('conquest.safe_reload.validate_handoff',lambda *a:(_ for _ in ()).throw(ValueError('monster approached')))
    assert not DesktopApp._restart_now(app)
    assert 'monster approached' in calls[0]
