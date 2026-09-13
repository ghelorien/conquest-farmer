import copy
from types import SimpleNamespace
import pytest
from conquest.capture import CaptureUnavailable
from conquest.merchants.journal import Journal
from conquest.merchants.shop_return import ShopReturn
from conquest.merchants.coordination import InputCoordinator


def item(uid,price=None):
    return dict(uid=uid,type_id=120007,name='Necklace',plus=1,gem1=0,gem2=0,
                quantity=1,bound=False,price=price)


@pytest.fixture
def run(tmp_path):
    j=Journal(tmp_path/'journal.sqlite3');now=[100]
    r=ShopReturn('Dutch',j,clock=lambda:now[0])
    s=dict(character='Dutch',identity={'pid':1},map_id=1036,position=[264,214],
           timestamp=100,silver=1000,inventory=[],booth=[item(1,100),item(2,900)],
           booth_open=True,trade=None,request=None,windows=[{'name':'Inventory'}])
    r.remember(s)
    r.save({'phase':'returning','before':copy.deepcopy(s),'home':j.get('Dutch','shop_home'),
            'moves':0,'stalls':0})
    guard=InputCoordinator(lambda:True,path=tmp_path/'input.lock')
    active=[True];calls=[]
    def check():
        if not active[0]:raise CaptureUnavailable('paused')
        guard.check()
    c=SimpleNamespace(active=lambda:active[0],check=check,coordinator=guard,
                      apply_price=lambda p:calls.append(('listing',p)))
    t=SimpleNamespace(read=lambda:copy.deepcopy(s),qualify_movement=lambda:None,
        move=lambda before,destination,check:calls.append(('move',destination)) or {**s,'position':list(destination)},
        prepare_transfer=lambda before,check:['Market'],transfer=lambda records,check:calls.append(('transfer',records)),
        stall=lambda preferred:{'position':preferred},
        prepare_shop=lambda chosen,check:copy.deepcopy(s),start_shop=lambda before,check:calls.append(('shop',before)))
    return SimpleNamespace(r=r,j=j,s=s,c=c,t=t,now=now,active=active,calls=calls)


def test_reconnect_routes_to_conductress_before_stock_reconciliation(run):
    run.s.update(map_id=1002,position=[400,400],inventory=[],booth=[],booth_open=False)
    assert run.r.step(run.s,run.c,run.t) is False
    assert run.calls==[('move',(438,444))]
    assert run.r.state()['moves']==1


def test_paid_transfer_is_not_repeated_after_restart(run):
    run.s.update(map_id=1002,position=[438,444],booth_open=False)
    run.r.step(run.s,run.c,run.t)
    assert run.r.state()['phase']=='transfer_submitted'
    recreated=ShopReturn('Dutch',run.j,clock=lambda:run.now[0])
    assert not recreated.step(run.s,run.c,run.t)
    assert len(run.calls)==1
    run.now[0]+=11
    with pytest.raises(ValueError,match='uncertain'):recreated.step(run.s,run.c,run.t)
    assert len(run.calls)==1
    assert run.j.get('Dutch','attention')['kind']=='shop_return'


def test_arrival_requires_correct_fare_then_routes_to_stall(run):
    run.r.save(run.r.state(),'transfer_submitted',transfer_silver=1100,submitted_at=100)
    run.s.update(position=[211,196],booth_open=False)
    assert not run.r.step(run.s,run.c,run.t)
    assert run.calls==[('move',(264,214))]


def test_wrong_fare_never_claims_recovery(run):
    run.r.save(run.r.state(),'transfer_submitted',transfer_silver=1500,submitted_at=80)
    with pytest.raises(ValueError,match='uncertain'):run.r.step(run.s,run.c,run.t)
    assert not run.calls


def test_pause_and_farmer_handoff_prevent_movement(run):
    run.s.update(map_id=1002,position=[400,400],booth_open=False)
    run.active[0]=False
    with pytest.raises(CaptureUnavailable,match='Paused'):run.r.step(run.s,run.c,run.t)
    run.active[0]=True;run.c.coordinator.safe_to_yield=lambda:False
    with pytest.raises(CaptureUnavailable,match='handoff'):run.r.step(run.s,run.c,run.t)
    assert not run.calls and run.r.state()['moves']==0


def test_closed_shop_records_intent_and_never_blindly_repeats(run):
    run.s['booth_open']=False
    run.r.step(run.s,run.c,run.t)
    assert run.r.state()['phase']=='shop_submitted'
    run.r.step(run.s,run.c,run.t)
    assert len(run.calls)==1
    run.now[0]+=11
    with pytest.raises(ValueError,match='Shop setup'):run.r.step(run.s,run.c,run.t)


def test_restores_highest_value_first_at_verified_previous_price(run):
    run.s.update(inventory=[item(1),item(2)],booth=[])
    assert not run.r.step(run.s,run.c,run.t)
    kind,plan=run.calls[0]
    assert kind=='listing' and plan['uid']==2 and plan['price']==900
    assert run.r.state()['phase']=='restoring_listings'
    run.s.update(inventory=[],booth=[item(1,100),item(2,900)])
    assert run.r.step(run.s,run.c,run.t)
    assert run.r.state()['phase']=='complete'
    assert run.j.get('Dutch','new_stock') is True


def test_uncertain_missing_stock_blocks_restoration_in_market(run):
    run.s['booth']=[item(1,100)]
    with pytest.raises(ValueError,match='Stock changed'):run.r.step(run.s,run.c,run.t)
    assert not run.calls


def test_obstructions_and_wrong_map_are_bounded(run):
    run.r.save(run.r.state(),stalls=6)
    with pytest.raises(ValueError,match='obstructed'):run.r.step(run.s,run.c,run.t)
    assert not run.calls


def test_characters_keep_independent_homes_and_recovery_state(run):
    other=ShopReturn('Spiritual',run.j)
    assert other.state() is None
    assert run.j.get('Spiritual','shop_home') is None


def test_shopflag_name_alone_never_proves_vacancy(monkeypatch):
    from conquest.merchants.stalls import vacant_flags
    import conquest.merchants.stalls as stalls
    import conquest.merchants.memory as memory
    flag={'address':1000,'uid':20,'type_id':0,'model':1086,'name':'ShopFlag'}
    monkeypatch.setattr(stalls,'scene_flags',lambda observer:[flag])
    monkeypatch.setattr(memory,'unpack',lambda *args:(99,))
    observer=SimpleNamespace(adapter=object())
    with pytest.raises(ValueError,match='qualification'):vacant_flags(observer,{})
    assert vacant_flags(observer,{'vacancy_offset':64,'vacancy_mask':0xffffffff,'vacancy_value':0})==[]
    monkeypatch.setattr(memory,'unpack',lambda *args:(0,))
    assert vacant_flags(observer,{'vacancy_offset':64,'vacancy_mask':0xffffffff,'vacancy_value':0})==[flag]


@pytest.mark.parametrize('panel,capability,method,phase',[
    ('booth','booth_panel','open_owned_booth','panel_submitted'),
    ('inventory','inventory_panel','open_inventory','inventory_submitted'),
])
def test_missing_owned_panel_opens_once_and_reconciles_after_restart(run,panel,capability,method,phase):
    run.s['own_booth_uid']=900
    if panel=='booth':run.s['booth_open']=False
    else:run.s['windows']=[]
    required=[]
    run.t.driver=SimpleNamespace(require_qualified=lambda cap:required.append(cap))
    def open_panel(snapshot,check,*,before_press):
        check()
        assert run.r.state()['phase']!=phase
        before_press()
        run.calls.append(panel)
    setattr(run.t,method,open_panel)
    assert not run.r.step(run.s,run.c,run.t)
    assert run.calls==[panel] and required==[capability]
    assert run.r.state()['phase']==phase
    recreated=ShopReturn('Dutch',run.j,clock=lambda:run.now[0])
    assert not recreated.step(run.s,run.c,run.t)
    assert run.calls==[panel]
    run.now[0]+=11
    with pytest.raises(ValueError,match='panel did not open'):
        recreated.step(run.s,run.c,run.t)
    assert run.calls==[panel]


def test_owned_booth_never_claims_another_flag_without_panel_qualification(run):
    run.s.update(own_booth_uid=900,booth_open=False)
    def unqualified(cap):raise ValueError('pending qualification')
    run.t.driver=SimpleNamespace(require_qualified=unqualified)
    with pytest.raises(ValueError,match='pending qualification'):
        run.r.step(run.s,run.c,run.t)
    assert not run.calls
    assert run.r.state()['phase']=='returning'


def test_existing_owned_booth_can_recover_without_an_old_home(run):
    run.s['own_booth_uid']=900
    run.r.save(run.r.state(),home=None)
    assert run.r.step(run.s,run.c,run.t)
    assert run.r.state()['phase']=='complete'
    assert not run.calls


def test_open_panels_do_not_override_missing_stock_reconciliation(run):
    run.s.update(own_booth_uid=900,windows=[])
    run.s['booth']=[]
    with pytest.raises(ValueError,match='Stock changed'):
        run.r.step(run.s,run.c,run.t)
    assert not run.calls


@pytest.mark.parametrize('assisted',[False,True])
def test_recovery_trial_preserves_manual_intervention(run,assisted):
    trial={'phase':'recovering','before':copy.deepcopy(run.r.state()['before'])}
    trial['before']['timestamp']-=.02
    if assisted:trial['manual_interventions']=['User completed Conductress transfer']
    run.j.set('Dutch','recovery_trial',trial)
    assert run.r.step(run.s,run.c,run.t)
    state=run.r.state();result=run.j.get('Dutch','recovery_trial')
    assert state['automatic_recovery_verified'] is not assisted
    assert result['phase']==('assisted' if assisted else 'verified')
    assert result['automatic_recovery_verified'] is not assisted
    if assisted:assert state['manual_interventions']==trial['manual_interventions']
