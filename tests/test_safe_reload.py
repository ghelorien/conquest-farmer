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


def test_preparation_deadline_also_bounds_nested_living_wait(monkeypatch,tmp_path):
    from contextlib import contextmanager
    monkeypatch.chdir(tmp_path);(tmp_path/'.runtime').mkdir()
    now=[0.];events=[]
    monkeypatch.setattr(s.time,'monotonic',lambda:now[0])
    monkeypatch.setattr('conquest.merchants.delivery_operation.guard_reload',lambda:None)
    monkeypatch.setattr('conquest.worker.request',lambda *a:health())
    @contextmanager
    def guard():yield True
    monkeypatch.setattr('conquest.route_controller.controller_guard',guard)
    class Loop:
        def __init__(self,route):pass
        def refresh(self):pass
        def check_stop(self):pass
        def record(self,event,**kwargs):events.append(event)
    monkeypatch.setattr('conquest.overnight.OvernightLoop',Loop)
    def blocked_living(loop,*args,**kwargs):
        now[0]=121
        loop.check_stop()
        pytest.fail('Nested recovery wait must expire')
    monkeypatch.setattr(s,'park',blocked_living)
    with pytest.raises(ValueError,match='living focused control'):
        s.prepare('worker','bandit',threading.Event(),lambda note:None)
    assert events[-1]=='reload_preparation_finished'


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


def test_dense_local_search_falls_back_to_fixed_town_route_with_care(monkeypatch):
    import numpy as np
    from conquest.navigation import TerrainMap
    now=[0.];moves=[];checks=[];notes=[];h=health();life=h['embedded_controls']['life'];life['position']=[50,50]
    h['window']={'client_size':[1420,1009]}
    monkeypatch.setattr(s.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(s.time,'sleep',lambda delay:now.__setitem__(0,now[0]+delay))
    monkeypatch.setattr(s,'nearby_escape',lambda *a,**kw:None)
    monkeypatch.setattr('conquest.scene_input.memory_player_anchor',lambda *a:(710,504))
    def living():
        x,y=life['position']
        h['embedded_controls']['monsters']=[] if (x,y)==(10,10) else [{'position':[x+1,y],'alive':True}]
        return h
    def step(target,**kw):
        moves.append((tuple(life['position']),target));life['position']=list(target)
        return {'reached':True}
    loop=NS(living=living,care=NS(check=lambda h:checks.append(now[0]),session=None),
            terrain=TerrainMap(1002,100,100,np.zeros((100,100),dtype=bool),'',(),()),
            route=NS(restock_map_id=1002,restock_anchor=(10,10)),stepper=NS(step_to=step))
    proof=s.park(loop,threading.Event(),notes.append)
    assert proof['position']==[10,10] and 18<=now[0]<120
    assert len(checks)>len(moves)>1
    assert all(max(abs(x-10),abs(y-10))>max(abs(a-10),abs(b-10)) for (x,y),(a,b) in moves)
    assert any('heading toward town' in text for text in notes)


def test_final_safe_check_failure_preserves_old_app(monkeypatch):
    from conquest.desktop_app import DesktopApp
    calls=[]
    app=NS(thread=None,host=NS(saved=True),last={'worker_info_path':'worker'},reload_proof={},reload_resume=True,
        release=lambda:pytest.fail('Must retain client'),state_text=NS(set=lambda note:calls.append(note)))
    monkeypatch.setattr('conquest.desktop_app.subprocess.run',lambda *a,**k:NS(returncode=0))
    monkeypatch.setattr('conquest.safe_reload.validate_handoff',lambda *a:(_ for _ in ()).throw(ValueError('monster approached')))
    assert not DesktopApp._restart_now(app)
    assert 'monster approached' in calls[0]


@pytest.mark.parametrize('lock_available',[True,False])
def test_terminal_unqueryable_pid_still_requires_exclusive_controller_lock(monkeypatch,tmp_path,lock_available):
    from contextlib import contextmanager
    monkeypatch.chdir(tmp_path);(tmp_path/'.runtime').mkdir()
    h=health();calls=[]
    monkeypatch.setattr(s,'read_json',lambda *args:{'pid':999,'phase':'stopped'})
    monkeypatch.setattr(s,'process_alive',lambda pid:None)
    monkeypatch.setattr('conquest.worker.request',lambda *args:h)
    @contextmanager
    def guard():
        calls.append('lock');yield lock_available
    monkeypatch.setattr('conquest.route_controller.controller_guard',guard)
    loop=NS(refresh=lambda:None,record=lambda *a,**k:None,check_stop=lambda:None)
    monkeypatch.setattr('conquest.overnight.OvernightLoop',lambda route:loop)
    monkeypatch.setattr(s,'park',lambda *a:calls.append('park') or {'verified':True})
    if lock_available:
        assert s.prepare('worker','bandit',threading.Event(),lambda text:None)=={'verified':True}
        assert calls==['lock','park']
    else:
        with pytest.raises(ValueError,match='Another route owns input'):
            s.prepare('worker','bandit',threading.Event(),lambda text:None)
        assert calls==['lock']
