import copy
import pytest
from conquest.merchants.delivery import prepare, plan_deliveries, validate_offers, reconcile, DeliveryTransaction
from conquest.merchants.journal import Journal


def item(uid,kind=130009,plus=0):
    return dict(uid=uid,type_id=kind,plus=plus,gem1=0,gem2=0,quantity=1,bound=False,slot=0,name='Loot')


def snapshot(name,uid,items=()):
    return dict(character=name,character_uid=uid,identity={'pid':uid,'creation':1},server='America',
        timestamp=100,map_id=1036,hp=100,silver=100,capacity=40,inventory=list(items),booth=[],
        booth_open=True,trade=None,request=None)


def offered(intent):
    f,m=copy.deepcopy(intent['farmer']),copy.deepcopy(intent['merchant'])
    for own,other,is_farmer in ((f,m,True),(m,f,False)):
        own['trade']=dict(participant=other['character'],participant_uid=other['character_uid'],
            own_items=intent['items'] if is_farmer else [],items=[] if is_farmer else intent['items'],
            own_silver=0,other_silver=0)
    return f,m


def delivered(intent):
    f,m=copy.deepcopy(intent['farmer']),copy.deepcopy(intent['merchant'])
    ids={i['uid'] for i in intent['items']}
    f['inventory']=[i for i in f['inventory'] if i['uid'] not in ids]
    m['inventory']+=copy.deepcopy(intent['items'])
    return f,m


def test_prefers_capacity_then_verified_distance_and_splits():
    f=snapshot('Parasite',1,[item(i) for i in range(100,125)])
    d=snapshot('Dutch',2,[item(i) for i in range(200,230)])
    s=snapshot('Spiritual',3,[item(i) for i in range(300,320)])
    states=[dict(character=n['character'],snapshot=n,ready=True,verified_travel_distance=dist)
            for n,dist in ((d,1),(s,100))]
    plans=plan_deliveries(f,states,now=100)
    assert [(p['merchant'],len(p['items'])) for p in plans['deliveries']]==[('Spiritual',5)]*4+[('Dutch',5)]
    s['inventory']=s['inventory'][:10]
    d['inventory']=d['inventory'][:10]
    assert plan_deliveries(f,states,now=100)['deliveries'][0]['merchant']=='Dutch'


def test_preserves_supplies_equipped_and_storage_only_loot():
    rows=[item(10,1050002),item(11,1000020),item(12,1090000),item(13,130008),
          item(14,2000031),item(15,130009),item(16,1088001),item(17,130003,2),item(18,720027)]
    rows[5]['slot']=None
    f=snapshot('Parasite',1,rows);m=snapshot('Dutch',2)
    plan=plan_deliveries(f,[dict(character='Dutch',snapshot=m,ready=True,verified_travel_distance=1)],reserved=[17],now=100)
    assert [i['uid'] for i in plan['deliveries'][0]['items']]==[18]
    assert [i['uid'] for i in plan['warehouse']]==[14,16]


@pytest.mark.parametrize('change', ['recipient','currency','extra_item','missing_item','capacity','supplies','identity'])
def test_rejects_changed_offer_before_confirmation(change):
    i=item(10);f=snapshot('Parasite',1,[i,item(11,1050002)]);m=snapshot('Dutch',2)
    intent=prepare(f,m,[i],now=100)
    f,m=offered(intent)
    if change=='recipient':m['trade']['participant_uid']=99
    if change=='currency':f['trade']['other_silver']=1
    if change=='extra_item':m['trade']['own_items']=[item(99)]
    if change=='missing_item':m['trade']['items']=[]
    if change=='capacity':m['capacity']=0
    if change=='supplies':f['inventory']=f['inventory'][:1]
    if change=='identity':f['identity']={'pid':9}
    with pytest.raises(ValueError):validate_offers(intent,f,m,now=100)


def test_offered_item_can_leave_source_deque_but_success_needs_both_accounts():
    i=item(10);f=snapshot('Parasite',1,[i,item(11,1050002)]);m=snapshot('Dutch',2)
    intent=prepare(f,m,[i],now=100)
    of,om=offered(intent);of['inventory']=of['inventory'][1:]
    validate_offers(intent,of,om,now=100)
    after_f,after_m=delivered(intent)
    assert reconcile(intent,after_f,after_m,now=100)
    assert not reconcile(intent,after_f,m,now=100)
    assert not reconcile(intent,f,after_m,now=100)
    after_m['inventory'][0]['plus']=2
    assert not reconcile(intent,after_f,after_m,now=100)


def test_unknown_result_is_journaled_and_cannot_retry(tmp_path,monkeypatch):
    monkeypatch.setattr('conquest.merchants.delivery.time.time',lambda:100)
    f=snapshot('Parasite',1,[item(10)]);m=snapshot('Dutch',2)
    class Driver:
        calls=0
        def require_qualified(self,c):pass
        def read_pair(self,m):return f,globals()['snapshot']('Dutch',2)
        def open_trade(self,intent):
            self.calls+=1
            raise OSError('Disconnected')
    from types import SimpleNamespace
    driver=Driver();j=Journal(tmp_path/'delivery.sqlite3')
    t=DeliveryTransaction(j,driver,peer=SimpleNamespace(reserve=lambda *args:None))
    with pytest.raises(OSError):t.run('Dutch',f['inventory'])
    assert j.pending('Dutch')[0]['phase']=='uncertain'
    with pytest.raises(ValueError,match='Reconcile'):t.run('Dutch',f['inventory'])
    assert driver.calls==1


def test_receiver_waits_for_complete_batch_and_two_sided_receipt(tmp_path):
    from conquest.merchants import delivery_reservation as r
    from conquest.capture import CaptureUnavailable
    j=Journal(tmp_path/'journal.sqlite3')
    items=[item(10),item(11)]
    f=snapshot('Parasite',1,items);m=snapshot('Dutch',2)
    state=r.reserve(j,'batch:1',f,m,items,now=100)
    of,om=offered(state['intent'])
    partial=copy.deepcopy(om);partial['trade']['items']=partial['trade']['items'][:1]
    with pytest.raises(CaptureUnavailable,match='complete reserved'):
        r.validate_receiver(j,partial,now=101)
    with pytest.raises(ValueError,match='missing, changed'):
        r.ready(j,'Dutch','batch:1',of,partial,now=101)
    r.ready(j,'Dutch','batch:1',of,om,now=101)
    assert r.validate_receiver(j,om,now=101)['phase']=='offer_ready'
    af,am=delivered(state['intent'])
    with pytest.raises(ValueError,match='not reconciled'):
        r.finish(j,'Dutch','batch:1',f,am,now=102)
    assert r.active(j,'Dutch')
    # Read-only reconciliation remains possible after the input deadline.
    af['timestamp']=am['timestamp']=300
    assert r.finish(j,'Dutch','batch:1',af,am,now=300)['phase']=='verified'
    assert not r.active(j,'Dutch')
    assert j.get('Dutch','new_stock') is True


def test_expired_reservation_never_accepts_or_gets_overwritten(tmp_path):
    from conquest.merchants import delivery_reservation as r
    j=Journal(tmp_path/'journal.sqlite3')
    f=snapshot('Parasite',1,[item(10)]);m=snapshot('Dutch',2)
    state=r.reserve(j,'batch:1',f,m,f['inventory'],now=100)
    with pytest.raises(ValueError,match='expired'):
        r.validate_receiver(j,m,now=221)
    with pytest.raises(ValueError,match='previous delivery'):
        r.reserve(j,'batch:2',f,m,f['inventory'],now=221)
    assert r.active(j,'Dutch')['request_id']=='batch:1'


def test_reserved_delivery_blocks_refill_even_after_planning(tmp_path):
    from conquest.merchants import delivery_reservation as r
    from conquest.merchants.refill import RefillController
    from conquest.merchants.coordination import InputCoordinator
    from conquest.capture import CaptureUnavailable
    from types import SimpleNamespace
    j=Journal(tmp_path/'journal.sqlite3');guard=InputCoordinator(lambda:True,path=tmp_path/'lock')
    f=snapshot('Parasite',1,[item(10)]);m=snapshot('Dutch',2)
    r.reserve(j,'batch:1',f,m,f['inventory'],now=100)
    controller=RefillController('Dutch',j,SimpleNamespace(),guard)
    with pytest.raises(CaptureUnavailable,match='Reserved farmer delivery'):
        controller.apply_price({'uid':99,'price':100,'old_price':None})


def test_bridge_never_takes_caller_snapshots_and_waits_for_input_owner():
    from conquest.merchants.delivery_bridge import dispatch
    from conquest.merchants.coordination import InputCoordinator
    from types import SimpleNamespace
    ui=SimpleNamespace(coordinator=InputCoordinator())
    with pytest.raises(ValueError,match='arguments'):
        dispatch(ui,{'action':'delivery-pair','character':'Dutch','farmer':{'fake':True}})
    ui.coordinator.owner='Dutch'
    with pytest.raises(ValueError,match='current input'):
        dispatch(ui,{'action':'delivery-reserve','character':'Dutch','request_id':'batch:1','uids':[10]})


def test_old_delivery_request_never_reopens_after_restart_or_new_reservation(tmp_path):
    from conquest.merchants import delivery_reservation as r
    path=tmp_path/'journal.sqlite3';j=Journal(path)
    f=snapshot('Parasite',1,[item(10)]);m=snapshot('Dutch',2)
    first=r.reserve(j,'batch:1',f,m,f['inventory'],now=100)
    af,am=delivered(first['intent'])
    r.finish(j,'Dutch','batch:1',af,am,now=100)
    j=Journal(path)
    next_f=snapshot('Parasite',1,[item(11)])
    r.reserve(j,'batch:2',next_f,am,next_f['inventory'],now=100)
    # Delayed network retries return their durable receipt and leave batch 2 alone.
    assert r.reserve(j,'batch:1',f,m,f['inventory'],now=100)['phase']=='verified'
    assert r.finish(j,'Dutch','batch:1',next_f,am,now=100)['phase']=='verified'
    assert r.active(j,'Dutch')['request_id']=='batch:2'
    with pytest.raises(ValueError,match='reused'):
        r.reserve(j,'batch:1',next_f,m,next_f['inventory'],now=100)


def test_source_journal_precedes_receiver_confirmation_and_lost_receipt_never_retrades(tmp_path,monkeypatch):
    monkeypatch.setattr('conquest.merchants.delivery.time.time',lambda:100)
    f=snapshot('Parasite',1,[item(10)]);m=snapshot('Dutch',2)
    intent=prepare(f,m,f['inventory'],now=100)
    j=Journal(tmp_path/'source.sqlite3');calls=[];keys=[]
    class Driver:
        offered=False
        def require_qualified(self,c):pass
        def read_pair(self,name):return offered(intent) if self.offered else (f,m)
        def open_trade(self,intent):calls.append('open')
        def place_item(self,intent,item):calls.append('place');self.offered=True
        def confirm(self,intent):calls.append('confirm')
        def wait_pair(self,name):return delivered(intent)
    class Peer:
        fail=True
        def reserve(self,key,intent):
            keys.append(key);calls.append('reserve')
            assert j.pending('Dutch')[0]['phase']=='prepared'
        def ready(self,key,intent):
            assert j.pending('Dutch')[0]['phase']=='submitted'
            assert calls[-1]=='confirm'  # Farmer releases input before receiver acceptance.
            calls.append('ready')
        def finish(self,key,intent):
            assert not j.pending('Dutch')
            calls.append('finish')
            if self.fail:raise OSError('Lost release acknowledgement')
    peer=Peer();t=DeliveryTransaction(j,Driver(),peer)
    with pytest.raises(OSError,match='Lost release'):t.run('Dutch',f['inventory'])
    assert calls==['reserve','open','place','confirm','ready','finish']
    assert not j.pending('Dutch')
    peer.fail=False
    recovered=DeliveryTransaction(Journal(j.path),object(),peer)
    assert recovered.recover(keys[0])==keys[0]
    assert calls[-2:]==['finish','finish']


def test_source_recovery_needs_both_inventories_and_never_sends_input(tmp_path,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('conquest.merchants.delivery.time.time',lambda:100)
    f=snapshot('Parasite',1,[item(10)]);m=snapshot('Dutch',2)
    intent=prepare(f,m,f['inventory'],now=100);af,am=delivered(intent)
    j=Journal(tmp_path/'source.sqlite3');j.begin('batch:1','Dutch','farmer_delivery',intent)
    j.transition('batch:1','uncertain');calls=[]
    driver=SimpleNamespace(read_pair=lambda name:(af,m))
    peer=SimpleNamespace(finish=lambda *args:calls.append('release'))
    t=DeliveryTransaction(j,driver,peer)
    with pytest.raises(ValueError,match='remains uncertain'):t.recover('batch:1')
    assert not calls and j.pending('Dutch')
    driver.read_pair=lambda name:(af,am)
    t.recover('batch:1')
    assert calls==['release'] and not j.pending('Dutch')
