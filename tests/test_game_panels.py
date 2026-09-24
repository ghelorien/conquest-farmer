from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from conquest import game_panels as panels
from conquest.capture import CaptureUnavailable


@pytest.fixture
def rig(monkeypatch):
    state={'windows':[]};events=[]
    gui=NS(windows=lambda:deepcopy(state['windows']),
           assert_hovered=lambda w,label:events.append(('hover',label)))
    monkeypatch.setattr(panels,'GuiReader',NS(for_session=lambda adapter:gui))
    def close_point(driver,snapshot,*,display_only=False):
        assert display_only
        return (20,20),next(w for w in snapshot['windows'] if w['name']=='Booth')
    monkeypatch.setattr(panels,'close_point',close_point)
    monkeypatch.setattr(panels,'wait_hover_validation',lambda guard,check:guard())
    class Trade:
        observer=NS(character='Parasite',adapter=object())
        input_attempted=False
        def __call__(self,body):events.append(body);state['windows']=[]
        def click(self,point,*,before_press):
            before_press()
            assert self.input_attempted
            events.append(('click',point));state['windows']=[]
    return Trade(),state,events


def test_empty_scene_needs_no_input(rig):
    trade,state,events=rig
    assert panels.close_one(trade) is None and events==[]


def test_selected_portable_farmer_can_close_panels(rig,monkeypatch):
    trade,state,events=rig
    monkeypatch.setattr(panels,'farmer_name',lambda:'Kilhiam')
    trade.observer=NS(character='Kilhiam',adapter=object())
    state['windows']=[{'name':'Inventory'}]
    assert panels.close_one(trade)=='Inventory'
    assert events==[{'action':'close','window':'Inventory'}]


@pytest.mark.parametrize('character',['Spiritual','Dutch'])
def test_seller_panels_are_never_closed(rig,character):
    trade,state,events=rig;trade.observer=NS(character=character)
    with pytest.raises(ValueError,match='farmer'):panels.close_one(trade)
    assert events==[]


@pytest.mark.parametrize('name',sorted(panels.TRANSACTIONS)+['Trade###Confirm','Unknown###Confirm'])
def test_transaction_blocks_cleanup(rig,name):
    trade,state,events=rig;state['windows']=[{'name':'Shop'},{'name':name}]
    with pytest.raises(CaptureUnavailable,match='reconciliation'):panels.close_one(trade)
    assert events==[]


@pytest.mark.parametrize('name',['Shop','Warehouse','Inventory','Dialog'])
def test_regular_panel_uses_existing_close_action(rig,name):
    trade,state,events=rig;state['windows']=[{'name':name}]
    assert panels.close_one(trade)==name
    assert events==[{'action':'service-close-panel' if name=='Dialog' else 'close','window':name}]


def test_foreign_booth_closes_only_verified_display_control(rig):
    trade,state,events=rig;state['windows']=[{'name':'Booth','geometry':[1,2,30,40]}]
    assert panels.close_one(trade)=='Booth'
    assert events==[('hover','#CLOSE'),('click',(20,20))]


@pytest.mark.parametrize('change',['transaction','moved','stopped'])
def test_booth_guard_rejects_changed_state_before_press(rig,change):
    trade,state,events=rig;state['windows']=[{'name':'Booth','geometry':[1,2,30,40]}]
    checks=[]
    def check():
        checks.append(True)
        if len(checks)>1:
            if change=='transaction':state['windows'].append({'name':'###Confirm'})
            elif change=='moved':state['windows'][0]['geometry'][0]+=1
            else:raise CaptureUnavailable('Stopped')
    with pytest.raises(CaptureUnavailable):panels.close_one(trade,check)
    assert events==[]


@pytest.mark.parametrize('closed',[None,'Booth'])
def test_travel_cleanup_is_throttled_and_reobserves_after_closing(monkeypatch,closed):
    from conquest import travel_care as module
    care=module.TravelCare.__new__(module.TravelCare)
    care.exact_1078=False
    care.info='worker';care.pending=None;care.notify=lambda event:None;care.xp_step=lambda health:None
    now=[10.0];calls=[]
    monkeypatch.setattr(module.time,'monotonic',lambda:now[0])
    def request(info,operation,body):
        assert operation=='town' and body['action']=='clear-travel-panels'
        calls.append(body);return {'closed_panel':closed}
    monkeypatch.setattr(module,'request',request)
    health={'embedded_controls':{'control':{'enabled':False},'life':{
        'dead_candidate':False,'current_hp':100,'max_hp':100}}}
    if closed:
        with pytest.raises(module.TravelStateChanged,match='rechecking'):care.check(health)
    else:care.check(health)
    care.check(health)
    assert len(calls)==1
    now[0]+=1
    if closed:
        with pytest.raises(module.TravelStateChanged):care.check(health)
    else:care.check(health)
    assert len(calls)==2


def test_changing_gui_registry_defers_cleanup_without_stopping_farmer(rig,monkeypatch):
 trade,state,events=rig
 def changed():raise panels.GuiObservationChanged('GUI registry changed')
 monkeypatch.setattr(panels,'GuiReader',NS(for_session=lambda adapter:NS(windows=changed)))
 with pytest.raises(CaptureUnavailable,match='GUI observation changed'):
  panels.close_one(trade)
 assert events==[]


def test_unconfirmed_panel_close_rechecks_but_is_bounded(monkeypatch):
    from conquest.travel_care import TravelCare,TravelStateChanged
    care=object.__new__(TravelCare);care.info='worker';care.notify=lambda e:None
    care.exact_1078=False
    health={'embedded_controls':{'control':{'enabled':False},'life':{'dead_candidate':False}}}
    calls=[]
    def fail(*a,**k):
        calls.append(1)
        raise ValueError('Town panel close was not verified')
    monkeypatch.setattr('conquest.travel_care.request',fail)
    care.next_panel_check=0
    with pytest.raises(TravelStateChanged,match='Rechecking'):care.check(health)
    care.next_panel_check=0
    with pytest.raises(ValueError,match='remains uncertain'):care.check(health)
    assert len(calls)==1
