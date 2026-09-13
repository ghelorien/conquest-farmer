from types import SimpleNamespace as NS
import copy
import time
import pytest
from conquest.capture import CaptureUnavailable
from conquest.merchants.farmer_trade import FarmerTradeDriver,partial_offer
from conquest.merchants.delivery import prepare


def batch():
    items=[dict(uid=n,type_id=130009,plus=0,gem1=0,gem2=0,quantity=1,bound=False,slot=n-10)
           for n in (10,11)]
    def participant(name,uid,inventory):
        return dict(character=name,character_uid=uid,identity={'pid':uid},server='America',
            timestamp=time.time(),map_id=1036,hp=100,silver=200,capacity=40,
            inventory=inventory,booth=[],trade=None,request=None)
    f=participant('Parasite',1,items);m=participant('Dutch',2,[])
    intent=prepare(f,m,items)
    f,m=copy.deepcopy(f),copy.deepcopy(m)
    for a,b in ((f,m),(m,f)):
        a['trade']=dict(participant=b['character'],participant_uid=b['character_uid'],
            own_items=[],items=[],own_silver=0,other_silver=0)
    return intent,f,m


def test_partial_trade_keeps_remaining_items_and_supplies_with_farmer():
    intent,f,m=batch()
    assert partial_offer(intent,f,m)==[]
    f['trade']['own_items']=intent['items'][:1];m['trade']['items']=intent['items'][:1]
    f['inventory']=f['inventory'][1:]
    assert [i['uid'] for i in partial_offer(intent,f,m)]==[10]
    f['inventory']=[]
    with pytest.raises(ValueError,match='supplies changed'):partial_offer(intent,f,m)


@pytest.mark.parametrize('change',['uid','extra','currency','merchant_item'])
def test_partial_trade_rejects_wrong_recipient_or_unintended_value(change):
    intent,f,m=batch()
    if change=='uid':f['trade']['participant_uid']=99
    if change=='extra':f['trade']['own_items']=[{**intent['items'][0],'uid':99}]
    if change=='currency':m['trade']['own_silver']=1
    if change=='merchant_item':m['trade']['own_items']=intent['items'][:1]
    with pytest.raises(ValueError):partial_offer(intent,f,m)


@pytest.mark.parametrize('change',['enabled','paused','revision','handoff','merchant'])
def test_native_delivery_rechecks_user_intent_and_recipient_permission(change):
    state={'enabled':False,'paused':False,'revision':2}
    ui=NS(coordinator=NS(check=lambda:None),closed=False,
          app=NS(control=NS(snapshot=lambda:state)),safe_to_yield=lambda:True,
          runtime=NS(enabled=lambda c:True))
    d=FarmerTradeDriver.__new__(FarmerTradeDriver);d.ui=ui;d.revision=2;d.recipient='Dutch'
    if change=='enabled':state['enabled']=True
    if change=='paused':state['paused']=True
    if change=='revision':state['revision']=3
    if change=='handoff':ui.safe_to_yield=lambda:False
    if change=='merchant':ui.runtime.enabled=lambda c:False
    with pytest.raises(CaptureUnavailable):d.check()


def test_incomplete_native_qualification_never_authorizes_input():
    d=FarmerTradeDriver.__new__(FarmerTradeDriver)
    d.driver=NS(require_qualified=lambda capability:{'controls':{}})
    with pytest.raises(ValueError,match='qualification is incomplete'):d.require_qualified()


@pytest.mark.parametrize('failure',[None,'absent','duplicate','name','position','mode','moving'])
def test_receiver_memory_requires_one_stable_matching_uid_and_target_mode(monkeypatch,failure):
    import struct
    from conquest.merchants import farmer_trade as module
    base=0x100000;collection=0x200000;begin=0x300000;obj=0x400000
    objects=([] if failure=='absent' else
             [obj,obj+0x100] if failure=='duplicate' else [obj])
    entries=b''.join(struct.pack('<QQ',0,p) for p in objects)
    raw=bytearray(44);struct.pack_into('<QI',raw,0,base+0x400,2)
    raw[12:17]=b'Wrong' if failure=='name' else b'Dutch'
    struct.pack_into('<IIii',raw,28,100,201 if failure=='position' else 200,400,300)
    reads={}
    def read(address,size):
        reads[address]=reads.get(address,0)+1
        if address==begin:return entries
        if address==base+0x600:return struct.pack('<I',8 if failure=='mode' else 7)
        if address in objects:
            result=bytearray(raw)
            if failure=='moving' and reads[address]>1:struct.pack_into('<i',result,36,401)
            return bytes(result)
        raise AssertionError((address,size))
    values={collection:begin,collection+8:begin+len(entries),collection+16:begin+len(entries)}
    values.update({p:base+0x400 for p in objects})
    monkeypatch.setattr(module,'sample_fields',lambda session,fields:[values[a] for a,t in fields])
    monkeypatch.setattr(module,'targeting_state',lambda session:
        {'rva':0x600,'value':7,'targeting_trade':struct.unpack('<I',session.read_block(base+0x600,4))[0]==7})
    observer=NS(adapter=NS(read_block=read,assert_identity=lambda:None),entities=NS(
        _resolve=lambda:(base,collection,[]),
        layout=NS(begin_offset=0,end_offset=8,capacity_offset=16,max_objects=4096)))
    profile={'recipient':dict(name_format='inline_utf8',uid_offset=8,name_offset=12,
        position_offset=28,draw_offset=36,draw_format='i32',name_capacity=16,vtable_rva=0x400),
        'gui_size':[1000,800],'target_mode':{'rva':0x600,'value':7}}
    merchant={'character_uid':2,'character':'Dutch','position':[100,200]}
    if failure:
        expected=(module.RecipientAbsent if failure=='absent' else
                  module.RecipientAmbiguous if failure=='duplicate' else ValueError)
        with pytest.raises(expected):module.recipient_record(observer,profile,merchant,targeting=True)
    else:
        r=module.recipient_record(observer,profile,merchant,targeting=True)
        assert r['uid']==2 and r['point']==[400,300]


def test_delivery_target_reports_exact_absent_recipient_without_collapsing_ambiguity(monkeypatch):
    from conquest.merchants import farmer_trade as module
    from conquest import memory_life,scene_input
    farmer={'character':'Parasite','position':[10,20],'windows':[]}
    merchant={'character':'Dutch','position':[50,40]}
    driver=FarmerTradeDriver.__new__(FarmerTradeDriver)
    driver.require_qualified=lambda:{'gui_size':[1000,800],'client_size':[1250,1000]}
    driver.read_pair=lambda name:(farmer,merchant)
    driver.driver=NS(observer=NS(adapter=object(),health_layout=object(),character='Parasite'))
    monkeypatch.setattr(module,'recipient_actionability',lambda *a,**k:
                        (_ for _ in ()).throw(module.RecipientAbsent('not in scene')))
    monkeypatch.setattr(memory_life,'read_life',lambda *a:NS(position=(10,20)))
    monkeypatch.setattr(scene_input,'memory_player_anchor',lambda *a:(500,400))
    result=driver.target_status('Dutch')
    assert result=={'schema_version':1,'ready':False,'actionable':False,
        'reason':'recipient_absent','character':'Parasite','farmer_position':[10,20],
        'merchant':'Dutch','merchant_position':[50,40],'point':None,
        'viewport':[1000,800],'client_size':[1250,1000],'anchor':[500,400],
        'occupied_tiles':[[10,20]]}

    monkeypatch.setattr(module,'recipient_actionability',lambda *a,**k:
                        (_ for _ in ()).throw(module.RecipientAmbiguous('duplicate UID')))
    with pytest.raises(module.RecipientAmbiguous,match='duplicate UID'):
        driver.target_status('Dutch')


@pytest.mark.parametrize('draw_format',[None,'f32'])
def test_receiver_memory_rejects_unqualified_projection_before_observation(draw_format):
    from conquest.merchants.farmer_trade import recipient_record
    spec={'name_format':'inline_utf8'}
    if draw_format is not None:spec['draw_format']=draw_format
    observer=NS(adapter=None,entities=None)
    with pytest.raises(ValueError,match='projection format is not qualified'):
        recipient_record(observer,{'recipient':spec},{},targeting=True)

def test_delivery_waits_for_receiver_to_release_input_without_replaying_body(monkeypatch):
    from contextlib import contextmanager,nullcontext
    from conquest.merchants import farmer_trade as module
    from conquest import desktop_runtime,focus_recovery
    calls=[]
    @contextmanager
    def lease(name):
        calls.append('lease')
        if calls.count('lease')==1:raise CaptureUnavailable('Waiting for input owner')
        yield
    class Queue:
        def put(self,item):item[1].set()
    d=FarmerTradeDriver.__new__(FarmerTradeDriver)
    d.require_qualified=lambda:{};d.check=lambda:None
    d.ui=NS(runtime=NS(enabled=lambda _:True),coordinator=NS(lease=lease),
            app=NS(show_game=lambda:None),ui_requests=Queue())
    d.driver=NS(target=NS(hwnd=1),observer=NS(adapter=NS(identity={})))
    monkeypatch.setattr(desktop_runtime,'physical_coordinates',nullcontext)
    monkeypatch.setattr(focus_recovery,'activate_client',lambda *a:True)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    with d.action({'merchant':{'character':'Spiritual'}}):calls.append('body')
    assert calls==['lease','lease','body']


def test_expired_delivery_grant_cannot_use_idle_farmer_fallback():
    state={'enabled':False,'paused':False,'revision':2}
    d=FarmerTradeDriver.__new__(FarmerTradeDriver);d.revision=2;d.recipient=None
    d.ui=NS(coordinator=NS(check=lambda:None),closed=False,grant={'expires_at':time.time()-1},
            app=NS(control=NS(snapshot=lambda:state)),safe_to_yield=lambda:True)
    with pytest.raises(CaptureUnavailable,match='window expired'):d.check()


@pytest.mark.parametrize('failure_stage',['acquire_timeout','transaction_body'])
def test_delivery_contention_retry_is_bounded_and_never_replays_transaction(monkeypatch,failure_stage):
    from contextlib import contextmanager,nullcontext
    from conquest.merchants import farmer_trade as module
    from conquest import desktop_runtime,focus_recovery
    calls=[];clock=[0.0]
    @contextmanager
    def lease(name):
        calls.append('lease')
        if failure_stage=='acquire_timeout':
            raise CaptureUnavailable('Waiting for input owner')
        yield
    class Queue:
        def put(self,item):item[1].set()
    d=FarmerTradeDriver.__new__(FarmerTradeDriver)
    d.require_qualified=lambda:{};d.check=lambda:None
    d.ui=NS(runtime=NS(enabled=lambda _:True),coordinator=NS(lease=lease),
            app=NS(show_game=lambda:None),ui_requests=Queue())
    d.driver=NS(target=NS(hwnd=1),observer=NS(adapter=NS(identity={})))
    monkeypatch.setattr(desktop_runtime,'physical_coordinates',nullcontext)
    monkeypatch.setattr(focus_recovery,'activate_client',lambda *a:True)
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(module.time,'sleep',lambda _:clock.__setitem__(0,clock[0]+1))
    with pytest.raises(CaptureUnavailable,match='Waiting for input owner'):
        with d.action({'merchant':{'character':'Spiritual'}}):
            calls.append('body')
            raise CaptureUnavailable('Waiting for input owner')
    if failure_stage=='acquire_timeout':
        assert calls==['lease']*4 and clock[0]==3
    else:
        assert calls==['lease','body'] and clock[0]==0
