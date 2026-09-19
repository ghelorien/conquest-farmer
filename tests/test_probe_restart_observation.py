from copy import deepcopy

import pytest

from conquest.merchants import delivery_probe as probe
from conquest.merchants.runtime import MerchantRuntime
from test_delivery_probe_manual_ownership import supervised
from test_manual_runtime import rig


def absent_source(x):
    x.runtime.manual_farmer_provider=lambda:None


def test_verified_receipt_suppresses_restart_admission_until_farmer_attaches(supervised, monkeypatch):
    x=supervised
    restarted=MerchantRuntime(object(),x.guard,journal=x.journal)
    x.runtime=restarted
    x.now=10_000  # Old verified receipt; exact merchant memory remains fresh.
    retractions=[]
    monkeypatch.setattr(restarted.manual_sessions,'retract_probe_admission',
                        lambda *a,**kw:retractions.append((a,kw)))
    original=x.path.read_bytes()
    for _ in range(3):
        assert restarted.process_manual('Dutch',x.read(),decline_enabled=True)
        assert not restarted.process_probe_owned('Dutch',x.read(),require_bilateral=True)
    assert restarted.manual_status('Dutch') is None
    assert x.path.read_bytes()==original and retractions==[]
    assert x.calls==[] and restarted.manual_sessions.permissions()==[]
    with x.journal.db() as db:
        assert db.execute('SELECT COUNT(*) FROM manual_sessions').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]==0
    # Existing observer wiring resumes the normal full bilateral proof.
    restarted.configure_manual_farmer(lambda:x.source,lambda:{'enabled':False})
    assert restarted.process_probe_owned('Dutch',x.read(),require_bilateral=True)
    assert restarted.manual_status('Dutch') is None and x.calls==[]


@pytest.mark.parametrize('phase',['prepared','request_submitted','accept_submitted','cancel_submitted',
                                  'trade_open_verified','cancel_verified','delivery_verified','operator_overridden'])
def test_absent_provider_reserves_only_request_verified(supervised, phase):
    x=supervised;absent_source(x)
    x.probe['phase']=phase;x.save()
    assert x.runtime.process_manual('Dutch',x.read())
    assert x.runtime.manual_status('Dutch')['phase']=='approval_pending'
    assert x.calls==[]


@pytest.mark.parametrize('change',[
    'request_uid','request_name','request_extra','merchant_pid','merchant_creation_time',
    'merchant_inventory','merchant_silver','merchant_slot','target_profile','farmer_profile',
    'saved_farmer_process','saved_farmer_inventory','saved_merchant_inventory','saved_request_uid',
    'missing_farmer_receipt','missing_merchant_receipt','missing_verified_time','receipt_future',
    'receipt_before_start','receipt_after_verified','other_farmer','corrupt','digest_race',
])
def test_absent_provider_never_hides_changed_request_or_receipt(supervised, monkeypatch, change):
    x=supervised;absent_source(x)
    if change=='request_uid':x.state['request']['participant_uid']+=1
    if change=='request_name':x.state['request'].update(participant='Visitor',message='Visitor wishes to trade with you.')
    if change=='request_extra':x.state['request']['instance']='different'
    if change=='merchant_pid':x.state['identity']['pid']+=1
    if change=='merchant_creation_time':x.state['identity']['creation_time_100ns']+=1
    if change=='merchant_inventory':x.state['inventory'][0]['plus']+=1
    if change=='merchant_silver':x.state['silver']+=1
    if change=='merchant_slot':x.state['inventory'][0]['slot']=5
    if change=='target_profile':x.probe['target_profile_id']='other-target'
    if change=='farmer_profile':x.probe['farmer_profile_id']='other-farmer'
    if change=='saved_farmer_process':x.probe['farmer_after']['identity']['pid']+=1
    if change=='saved_farmer_inventory':x.probe['farmer_after']['inventory'][0]['plus']+=1
    if change=='saved_merchant_inventory':x.probe['merchant_after']['inventory'][0]['plus']+=1
    if change=='saved_request_uid':x.probe['merchant_after']['request']['participant_uid']+=1
    if change=='missing_farmer_receipt':x.probe.pop('farmer_after')
    if change=='missing_merchant_receipt':x.probe.pop('merchant_after')
    if change=='missing_verified_time':x.probe.pop('updated_at')
    if change=='receipt_future':x.probe['farmer_after']['timestamp']=101
    if change=='receipt_before_start':x.probe['farmer_after']['timestamp']=99
    if change=='receipt_after_verified':
        x.now=102;x.probe['farmer_after']['timestamp']=101
    if change=='other_farmer':
        for farmer in (x.probe['intent']['farmer'],x.probe['farmer_after']):farmer['character']='Visitor'
        for request in (x.state['request'],x.probe['merchant_after']['request']):
            request.update(participant='Visitor',message='Visitor wishes to trade with you.')
    x.save()
    if change=='corrupt':x.path.write_text('{corrupt',encoding='utf-8')
    if change=='digest_race':
        states=iter([deepcopy(x.probe),{**x.probe,'phase':'cancel_verified'}])
        monkeypatch.setattr(probe,'read_probe',lambda:next(states))
    assert x.runtime.process_manual('Dutch',x.read())
    assert x.runtime.manual_status('Dutch')['phase']=='approval_pending'
    assert x.calls==[] and x.runtime.manual_sessions.permissions()==[]


@pytest.mark.parametrize('phase',['pending','approved','decline_claimed','needs_attention'])
def test_restart_observation_leaves_all_existing_manual_sessions_unchanged(supervised, phase):
    x=supervised
    store=x.runtime.manual_sessions
    row=store.begin_request('Dutch',x.read(),now=x.now)
    if phase=='approved':store.allow_and_activate(row['approval_binding'],x.read(),operator='Floor',now=x.now)
    if phase=='decline_claimed':
        store.reject(row['approval_binding'],operator='Floor',now=x.now)
        store.claim_decline(row['id'],x.read(),now=x.now)
    if phase=='needs_attention':store.observe(row['id'],{'reader_error':'unavailable'},now=x.now)
    x.runtime._sync_manual_fence()
    before=store.get(row['id']);audit=store.audit(row['id'])
    absent_source(x);x.now+=1
    assert x.runtime.process_manual('Dutch',x.read(),decline_enabled=True)
    assert store.get(row['id'])==before and store.audit(row['id'])==audit
    assert x.guard.manual_session_blocked('Dutch') and x.calls==[]


def test_absent_provider_cannot_reserve_farmer_side(supervised):
    x=supervised;absent_source(x)
    incoming={**x.farmer_read(),'request':{'participant':'Dutch','participant_uid':123,
                                          'message':'Dutch wishes to trade with you.'}}
    assert x.runtime.process_manual('Farmer',incoming)
    assert x.runtime.manual_status('Farmer')['phase']=='approval_pending'
    assert x.calls==[]


@pytest.mark.parametrize('change',['name','process'])
def test_present_wrong_provider_cannot_use_restart_fallback(supervised, change):
    x=supervised
    if change=='name':x.source.character='Visitor'
    else:x.source.adapter.identity={**x.source.adapter.identity,'pid':999}
    assert x.runtime.process_manual('Dutch',x.read())
    assert x.runtime.manual_status('Dutch')['phase']=='approval_pending'
    assert x.calls==[]
