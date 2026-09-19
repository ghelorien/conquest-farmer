from copy import deepcopy
from types import SimpleNamespace as NS
import threading
import time

import pytest

from conquest.capture import CaptureUnavailable
from conquest.merchants import manual_farmer
from conquest.merchants.coordination import check_input,input_scope
from test_manual_runtime import rig,snapshot


def farmer(rig,monkeypatch):
    x=rig
    x.state=snapshot(character='Parasite',character_uid=91,own_booth_uid=0,booth_open=False,booth=[])
    x.observer=NS(character='Parasite',lock=threading.RLock(),adapter=NS(assert_identity=lambda:None))
    x.runtime.configure_manual_farmer(lambda:x.observer,lambda:{'enabled':True})
    x.runtime.farmer_bot_owned=lambda:False
    x.reads=[]
    class Memory:
        def __init__(self,observer):assert observer is x.observer
        def read(self,*,farmer_preflight=False):
            assert farmer_preflight
            x.reads.append(1)
            return x.read()
    monkeypatch.setattr(manual_farmer,'presence',lambda observer:bool(x.state['request'] or x.state['trade']))
    monkeypatch.setattr(manual_farmer,'MerchantMemory',Memory)
    monkeypatch.setattr('conquest.merchants.memory.MerchantMemory',Memory)
    monkeypatch.setattr(manual_farmer,'controller',lambda runtime,observer:NS(driver=NS(observer=observer)))
    return x


def test_farmer_request_is_observed_and_approved_through_same_exact_api(rig,monkeypatch):
    x=farmer(rig,monkeypatch)
    assert x.runtime.observe_manual_farmer()
    pending=x.runtime.manual_status('Farmer')
    assert pending['phase']=='approval_pending' and pending['fence_scope']=='target'
    assert pending['approval_binding']['target_profile_id']=='Farmer'
    with pytest.raises(CaptureUnavailable):check_input()
    with pytest.raises(CaptureUnavailable):
        with input_scope():pytest.fail('Pending farmer session must block farmer')
    with x.guard.lease('Dutch',purpose='trade'):pass
    x.now+=1;x.runtime.approve_manual(pending['approval_binding'],operator='Floor')
    assert x.runtime.manual_status('Farmer')['fence_scope']=='global'
    with pytest.raises(CaptureUnavailable):
        with x.guard.lease('Dutch',purpose='trade'):pass
    assert x.reads==[1,1] and x.runtime.manual_farmer_status()['input_fenced']


def test_farmer_settlement_queues_only_farmer_replan_and_no_receipts(rig,monkeypatch):
    x=farmer(rig,monkeypatch);x.runtime.observe_manual_farmer()
    row=x.runtime.manual_status('Farmer');x.now+=1;x.runtime.approve_manual(row['approval_binding'])
    x.state.update(request=None,inventory=[],silver=2000)
    x.now+=1;x.runtime.observe_manual_farmer();x.now+=5;x.runtime.observe_manual_farmer()
    assert x.runtime.manual_status('Farmer') is None
    with x.journal.db() as db:
        signal=db.execute('SELECT * FROM manual_replans').fetchone()
        assert signal['merchant_pending']==0 and signal['farmer_pending']==1
        assert db.execute('SELECT COUNT(*) FROM sales').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]==0
        assert not db.execute("SELECT 1 FROM state WHERE character='Farmer' AND name='new_stock'").fetchone()


@pytest.mark.parametrize('phase',['approval_pending','needs_attention'])
def test_farmer_uuid_target_hold_blocks_all_farmer_input_not_other_targets(rig,monkeypatch,phase):
    x=rig
    monkeypatch.setattr('conquest.character_context.current',lambda:NS(profile=NS(id='farmer-uuid',role='Farmer')))
    x.guard.set_manual_sessions([dict(target_profile_id='farmer-uuid',holds_automation=True,
                                    ever_approved=False,phase=phase,request_state='pending')])
    with pytest.raises(CaptureUnavailable):check_input()
    with pytest.raises(CaptureUnavailable):
        with input_scope():pytest.fail('Farmer UUID is an input fence')
    with pytest.raises(CaptureUnavailable):
        with x.guard.lease('Farmer'):pytest.fail('Farmer lease is also fenced')
    with x.guard.lease('Dutch',purpose='trade'):pass
    assert not x.guard.manual_session_blocked('different-farmer-id')


def test_unsupported_farmer_full_memory_persists_qualification_blocker(rig,monkeypatch):
    x=farmer(rig,monkeypatch)
    class MissingMemory:
        def __init__(self,observer):pass
        def read(self,**kw):raise ValueError('Merchant must be alive on the Market map')
    monkeypatch.setattr(manual_farmer,'MerchantMemory',MissingMemory)
    assert x.runtime.observe_manual_farmer()
    status=x.runtime.manual_farmer_status()
    assert status['session']['phase']=='needs_attention' and status['session']['approval_binding'] is None
    assert status['observation']['qualified_full_snapshot_maps']==[1002,1011,1036]
    assert 'Market map' in status['observation']['reason'] and status['input_fenced']
    with pytest.raises(CaptureUnavailable):check_input()
    assert x.runtime.manual_status('Dutch') is None


def test_farmer_missing_decline_qualification_does_not_prevent_manual_approval(rig,monkeypatch):
    x=farmer(rig,monkeypatch)
    def missing(*args):raise ValueError('trade_request input qualification pending')
    monkeypatch.setattr(manual_farmer,'controller',missing)
    x.runtime.observe_manual_farmer()
    assert 'qualification' in x.runtime.manual_farmer_status()['observation']['decline_blocker']
    binding=x.runtime.manual_status('Farmer')['approval_binding']
    x.now+=1;x.runtime.approve_manual(binding)
    assert x.runtime.manual_status('Farmer')['phase']=='manual_active'


def test_farmer_delivery_reservation_prevents_manual_admission(rig,monkeypatch):
    x=farmer(rig,monkeypatch);x.runtime.farmer_bot_owned=lambda:True
    assert not x.runtime.observe_manual_farmer()
    assert x.runtime.manual_status('Farmer') is None and not x.reads
    assert x.runtime.manual_farmer_status()['observation']['bot_owned']


def test_native_farmer_boundary_detects_request_before_recovery_or_panels(rig,monkeypatch):
    from test_native_farm import setup
    x=farmer(rig,monkeypatch)
    supervisor,control,life,_=setup(monkeypatch)
    x.observer=supervisor.observer
    supervisor.recovery.step=lambda *a:pytest.fail('Manual request must precede gameplay recovery')
    before=control.snapshot()
    assert supervisor.observe()['manual_session']
    assert control.snapshot()==before and x.runtime.manual_status('Farmer')['phase']=='approval_pending'


def test_farmer_observer_keeps_reading_when_saved_farming_intent_is_off(rig,monkeypatch):
    x=farmer(rig,monkeypatch)
    x.runtime.manual_farmer_control=lambda:{'enabled':False}
    x.runtime.observe_manual_farmer()
    assert x.runtime.manual_status('Farmer')['phase']=='approval_pending' and x.reads
    x.now=131;x.runtime.observe_manual_farmer()
    assert x.runtime.manual_status('Farmer')['request_state']=='decline_pending'
    assert not x.runtime.manual_farmer_control()['enabled']


def test_farmer_observer_closed_windows_needs_no_unqualified_full_reader(rig,monkeypatch):
    x=farmer(rig,monkeypatch);x.state['request']=None
    assert not x.runtime.observe_manual_farmer() and not x.reads
    assert x.runtime.manual_farmer_status()['observation']['windows_absent']


def test_farmer_loop_consumes_current_valuables_without_false_pickup_or_resume(rig,monkeypatch):
    from test_native_farm import setup
    x=farmer(rig,monkeypatch);x.runtime.observe_manual_farmer()
    binding=x.runtime.manual_status('Farmer')['approval_binding'];x.now+=1;x.runtime.approve_manual(binding)
    x.state['request']=None;x.now+=1;x.runtime.observe_manual_farmer();x.now+=5;x.runtime.observe_manual_farmer()
    supervisor,control,life,notifications=setup(monkeypatch)
    supervisor.map_id=1002;supervisor.position=(301,401)
    supervisor.pending_loot=('old',);supervisor.patrol_chase='old-route'
    supervisor.excluded_targets={1:9999};supervisor.movement_obstructions={1:9999}
    inventory=NS(started_at=time.monotonic(),items=[NS(uid=91,type_id=410009,plus=2,slot=0,amount=1)])
    x.now+=1;before=control.snapshot();supervisor.observe_inventory(inventory)
    assert supervisor.pending_loot is None and supervisor.patrol_chase is None
    assert control.snapshot()==before and supervisor.pickups==0
    event,payload=notifications[-1]
    assert event=='manual_session_replan' and payload['map_id']==1002 and payload['urgent_banking']
    assert supervisor.urgent_banking(inventory)
    with x.journal.db() as db:assert db.execute('SELECT farmer_pending FROM manual_replans').fetchone()[0]==0
