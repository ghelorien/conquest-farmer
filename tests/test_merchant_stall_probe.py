from types import SimpleNamespace as NS
import pytest
from conquest.merchants import stall_probe
from conquest.merchants.journal import Journal


@pytest.mark.parametrize('case',['dialog','pending','ambiguous','inventory_changed','focus_denied',
                               'occupied','occupancy_unknown','occupied_before_press'])
def test_stall_probe_journals_once_and_never_confirms(tmp_path,monkeypatch,case):
    journal=Journal(tmp_path/'state.sqlite3');calls=[];clicked=False
    if case=='pending':journal.set('Dutch','stall_probe',{'phase':'submitted'})
    flag={'uid':101503,'position':[230,181],'draw_position':[1008,364]}
    monkeypatch.setattr('conquest.merchants.flag_target.flag_target',lambda *a:{'point':(1008,332)})
    monkeypatch.setattr('conquest.scene_pointer.wait_scene_pointer',lambda s,p,check:check())
    vacancy_reads=0
    def vacant(observer,spec):
        nonlocal vacancy_reads
        vacancy_reads+=1
        if case=='occupied' or (case=='occupied_before_press' and vacancy_reads>1):return []
        return [flag,flag] if case=='ambiguous' else [flag]
    monkeypatch.setattr(stall_probe,'vacant_flags',vacant)
    monkeypatch.setattr(stall_probe,'read_life',lambda *a:NS(map_id=1036,position=(228,181),
        dead_candidate=False,object_address=0x100000))
    def dialog(observer):
        if not clicked:raise ValueError('NPC dialog is absent')
        return {'records':[{'kind':1,'option':1,'text':'Set up booth'}]}
    monkeypatch.setattr('conquest.conductress.read_dialog',dialog)
    def inventory():
        return NS(items=(NS(uid=9),) if not (clicked and case=='inventory_changed') else (),silver=100)
    before={'map_id':1036,'booth_open':False,'trade':None,'request':None,
            'position':[228,181],'identity':{'pid':1},'windows':[],'inventory':[{'uid':9}],'booth':[]}
    observer=NS(character='Dutch',health_layout=None,adapter=NS(read_block=lambda a,n:bytes(n)))
    memory=NS(read=lambda:before,inventory=NS(read=inventory),
              gui=NS(viewport_size=lambda:(1888,665),model=lambda *a:0x200000,windows=lambda:[]))
    def qualified(capability):
        assert capability=='stall_occupancy'
        if case=='occupancy_unknown':raise ValueError('Stall occupancy qualification is pending')
        return {'shop_setup':{'vacancy_offset':0x100,'vacancy_mask':1,'vacancy_value':0}}
    driver=NS(observer=observer,memory=memory,require_qualified=qualified)
    def click(point,check,*,before_press):
        nonlocal clicked
        assert journal.get('Dutch','stall_probe')['phase']=='submitted'
        if case=='focus_denied':raise ValueError('No foreground focus; no button pressed')
        before_press();calls.append(point);clicked=True
    if case=='dialog':
        result=stall_probe.inspect_flag(driver,NS(click=click),journal,lambda:None)
        assert result['phase']=='observed' and result['inventory_unchanged']
        assert result['confirmation_submitted'] is False and calls==[(1008,332)]
    else:
        with pytest.raises(ValueError):stall_probe.inspect_flag(driver,NS(click=click),journal,lambda:None)
        assert len(calls)==(1 if case=='inventory_changed' else 0)
        if case=='inventory_changed':assert journal.get('Dutch','stall_probe')['phase']=='submitted'
        if case=='focus_denied':
            state=journal.get('Dutch','stall_probe')
            assert state['phase']=='cancelled_before_press' and state['retry_safe']


@pytest.mark.parametrize('case',['legacy','exact','changed','open_dialog'])
def test_interrupted_stall_reconciliation_never_claims_unavailable_attribute_evidence(tmp_path,monkeypatch,case):
    journal=Journal(tmp_path/'state.sqlite3')
    record={'phase':'submitted','identity':{'pid':1},'inventory_uids':[9],'silver':100}
    if case in ('exact','changed'):record.update(inventory_before=[{'uid':9,'plus':1}],booth_before=[])
    journal.set('Dutch','stall_probe',record)
    snapshot={'identity':{'pid':1},'map_id':1036,'booth_open':False,'trade':None,'request':None,
              'inventory':[{'uid':9,'plus':2 if case=='changed' else 1}],
              'booth':[],'silver':100,'position':[225,206]}
    driver=NS(observer=NS(character='Dutch'),memory=NS(read=lambda:snapshot))
    def dialog(*args):
        if case=='open_dialog':return {'records':[]}
        raise ValueError('NPC dialog is absent')
    monkeypatch.setattr('conquest.conductress.read_dialog',dialog)
    if case in ('changed','open_dialog'):
        with pytest.raises(ValueError):stall_probe.reconcile_interrupted_probe(driver,journal)
        assert journal.get('Dutch','stall_probe')['phase']=='submitted'
    else:
        result=stall_probe.reconcile_interrupted_probe(driver,journal)
        assert result['phase']=='reconciled_cancelled' and not result['input_qualified']
        assert result['exact_attributes_verified']==(case=='exact')


@pytest.mark.parametrize('case',['opened','changed_target','pointer_timeout','foreign_booth'])
def test_owned_booth_probe_uses_native_tile_and_verifies_receipt(tmp_path,monkeypatch,case):
    import struct
    from conquest.capture import CaptureUnavailable
    from conquest.merchants.booth_target import CONTROL
    journal=Journal(tmp_path/'state.sqlite3');pressed=[];reads=0
    flag={'uid':103064,'position':[272,174],'draw_position':[976,348]}
    target={'point':(976,348),'tile':(272,174),'orientation':6,'footprint':((0,0),)}
    def targeting(*args):
        nonlocal reads
        reads+=1
        return {**target,'point':(980,348)} if case=='changed_target' and reads>1 else target
    monkeypatch.setattr(stall_probe,'owned_booth',lambda *a:flag)
    monkeypatch.setattr(stall_probe,'owned_booth_target',targeting)
    monkeypatch.setattr(stall_probe,'read_life',lambda *a:NS(map_id=1036,position=(271,174),
        dead_candidate=False,object_address=0x100000))
    def dialog(*args):raise ValueError('NPC dialog is absent')
    monkeypatch.setattr('conquest.conductress.read_dialog',dialog)
    def pointer(session,point,check):
        assert point==(976,348);check()
        if case=='pointer_timeout':raise CaptureUnavailable('No pointer; no button pressed')
    monkeypatch.setattr('conquest.scene_pointer.wait_scene_pointer',pointer)
    def read(address,n):
        if n==4:return struct.pack('<I',103064)
        raw=bytearray(n)
        if pressed:
            raw[12]=1
            struct.pack_into('<I',raw,0x4c,999 if case=='foreign_booth' else 103064)
        return bytes(raw)
    state={'map_id':1036,'booth_open':False,'trade':None,'request':None,'own_booth_uid':103064,
           'position':[271,174],'identity':{'pid':1},'windows':[],'inventory':[],'booth':[]}
    inv=NS(items=(),silver=100)
    observer=NS(character='Spiritual',health_layout=None,adapter=NS(read_block=read))
    driver=NS(observer=observer,require_qualified=lambda *a:{'shop_setup':{}},
        memory=NS(read=lambda:state,inventory=NS(read=lambda:inv),
                  gui=NS(viewport_size=lambda:(1888,665),model=lambda *a:0x200000,windows=lambda:[])))
    def click(point,check,*,before_press):
        assert not journal.get('Spiritual','stall_probe')['press_pending']
        before_press()
        assert journal.get('Spiritual','stall_probe')['press_pending']
        pressed.append(point)
    if case=='opened':
        result=stall_probe.inspect_flag(driver,NS(click=click),journal,lambda:None)
        assert result['control']==CONTROL and result['booth_open']
        assert result['displayed_booth_uid']==result['own_booth_uid']==103064
        assert result['inventory_unchanged'] and pressed==[(976,348)]
    else:
        with pytest.raises((ValueError,CaptureUnavailable)):
            stall_probe.inspect_flag(driver,NS(click=click),journal,lambda:None)
        record=journal.get('Spiritual','stall_probe')
        if case=='foreign_booth':
            assert pressed==[(976,348)] and record['phase']=='submitted'
        else:
            assert not pressed and record['phase']=='cancelled_before_press' and record['retry_safe']


@pytest.mark.parametrize('case',['adopted','no_event','wrong_uid','wrong_flag','pending_trade','changed_stock',
                               'new_request'])
def test_recorded_manual_setup_supersedes_only_its_own_old_flag_probe(tmp_path,monkeypatch,case):
    import time
    from copy import deepcopy
    journal=Journal(tmp_path/'state.sqlite3')
    old={'phase':'submitted','submitted_at':time.time()-100,'identity':{'pid':1},
         'flag':{'name':'ShopFlag','model':1086,'type_id':0,'position':[269,174]},
         'inventory_before':[{'uid':9}],'silver':100}
    journal.set('Spiritual','stall_probe',old)
    if case!='no_event':
        journal.event('Spiritual','manual_stall_setup_adopted',
                      own_booth_uid=999 if case=='wrong_uid' else 103064,
                      automatic_recovery_verified=False)
    if case=='pending_trade':monkeypatch.setattr(journal,'pending',lambda *a:[{'id':'uncertain-trade'}])
    current={'identity':{'pid':1},'map_id':1036,'position':[271,174],'own_booth_uid':103064,
             'booth_open':True,'trade':None,'request':None,'windows':[],'inventory':[],
             'booth':[],'silver':500}
    reads=0
    def read():
        nonlocal reads
        reads+=1
        fresh=deepcopy(current)
        if case=='changed_stock' and reads>1:fresh['silver']+=1
        if case=='new_request' and reads>1:fresh['request']={'uid':123}
        return fresh
    booth={'uid':103064,'position':[274,174] if case=='wrong_flag' else [272,174]}
    monkeypatch.setattr(stall_probe,'owned_booth',lambda *a:booth)
    def dialog(*a):raise ValueError('NPC dialog is absent')
    monkeypatch.setattr('conquest.conductress.read_dialog',dialog)
    driver=NS(observer=NS(character='Spiritual'),memory=NS(read=read))
    if case=='adopted':
        result=stall_probe.reconcile_interrupted_probe(driver,journal)
        assert result['phase']=='reconciled_manual_setup'
        assert result['manual_setup_superseded_probe'] and result['own_booth_uid']==103064
        assert not result['input_qualified'] and not result['original_inventory_result_verified']
        assert result['inventory_before']==old['inventory_before'] and result['silver']==100
    else:
        with pytest.raises(ValueError):stall_probe.reconcile_interrupted_probe(driver,journal)
        assert journal.get('Spiritual','stall_probe')['phase']=='submitted'
