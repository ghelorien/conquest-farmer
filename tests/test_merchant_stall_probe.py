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
              gui=NS(viewport_size=lambda:(1888,665),model=lambda *a:0x200000))
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
