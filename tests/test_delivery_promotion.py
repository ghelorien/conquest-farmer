from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace as NS

import pytest

from conquest.character_context import merchant_directory, resolve_merchant, state_path
from conquest.character_profiles import ProfileRegistry
from conquest.memory_life import CLIENT_SHA256
from conquest.merchants import delivery_promotion as module, delivery_probe, delivery_qualification
from conquest.merchants.delivery_readiness import describe, RECEIVER_CAPABILITIES
from conquest.merchants.driver import MerchantDriver
from conquest.merchants.farmer_qualification import qualification_path
from conquest.merchants.coordination import InputCoordinator
from conquest.merchants.journal import Journal
from conquest.merchants.runtime import MerchantRuntime
from conquest.merchants.ui import UnifiedUI
from test_delivery_qualification import evidence


@pytest.fixture
def rig(tmp_path,monkeypatch):
    profiles=ProfileRegistry(tmp_path/'data')
    farmer_profile=profiles.add('Parasite');merchant_profile=profiles.add('Spiritual',role='Merchant')
    profiles.bind(farmer_profile.id,'Parasite','America',1)
    profiles.bind(merchant_profile.id,'Spiritual','America',2)
    monkeypatch.setenv('CONQUEST_DATA_ROOT',str(profiles.root))
    monkeypatch.setenv('CONQUEST_PROFILE_ID',farmer_profile.id)
    character=resolve_merchant(merchant_profile.id)
    state=evidence()
    state.update(character='Spiritual',farmer_profile_id=farmer_profile.id,
                 target_profile_id=merchant_profile.id)
    state['recipient']['address']=1234
    for role in ('farmer','merchant'):
        for snapshot in (state['intent'][role],state[role+'_after']):
            snapshot.update(booth_open=True,own_booth_uid=snapshot['character_uid'])
    receipt=Path(state_path('reports/merchants/delivery-request-probe.json'))
    receipt.parent.mkdir(parents=True,exist_ok=True)
    monkeypatch.setattr(delivery_probe,'JOURNAL',receipt)
    receipt.write_text(json.dumps(state))
    candidate=Path(state_path('reports/merchants/trade-layout-candidate.json'))
    candidate.write_text(json.dumps({'client_sha256':CLIENT_SHA256,
        'recipient':{'draw_format':'i32'},'target_mode':{'rva':0x699290,'value':19}}))
    peer_path=merchant_directory(character)/'qualification.json'
    peer_path.parent.mkdir(parents=True,exist_ok=True)
    peer={'character':'Spiritual','server':'America','client_sha256':CLIENT_SHA256,
          'capabilities':{cap:cap not in ('trade','trade_request') for cap in RECEIVER_CAPABILITIES},
          'controls':{'preserved':{'verified':True}},'evidence':'earlier-booth-proof'}
    peer_path.write_text(json.dumps(peer))

    def observer(role):
        account=state['intent'][role]
        return NS(character=account['character'],lock=threading.RLock(),
            adapter=NS(identity=deepcopy(account['identity']),expected_sha256=CLIENT_SHA256,
                       assert_identity=lambda:None),
            operations=NS(target=NS(snapshot=lambda:{'client_size':[800,600]})))

    source=observer('farmer');receiver=observer('merchant')
    owner=InputCoordinator(lambda:True,path=tmp_path/'input.lock')
    owner.on_acquire=lambda *_:pytest.fail('Promotion must not acquire gameplay input')
    runtime=MerchantRuntime(object(),owner,journal=Journal(tmp_path/'journal.sqlite3'),
                            market_path=tmp_path/'market.json')
    driver=object.__new__(MerchantDriver)
    driver.observer=receiver;driver.qualification=peer_path
    runtime.observers[character]=receiver;runtime.controllers[character]=NS(driver=driver)
    control={'enabled':False,'revision':4,'paused':False}
    ui=NS(coordinator=owner,runtime=runtime,closed=False,calibrating=set(),safe_to_yield=lambda:True,
          app=NS(observer=source,control=NS(snapshot=lambda:dict(control)),closing=False))
    snapshots=[deepcopy(state['farmer_after']),deepcopy(state['merchant_after'])]
    def fresh(*args,**kwargs):
        return tuple({**deepcopy(snapshot),'timestamp':time.time()} for snapshot in snapshots)
    monkeypatch.setattr(module,'pair',fresh)
    monkeypatch.setattr(module,'validate_candidate',lambda *args:None)
    runtime.latest[character]=fresh()[1]
    return NS(ui=ui,state=state,profiles=profiles,farmer_profile=farmer_profile,character=character,
        peer_path=peer_path,receipt=receipt,candidate=candidate,source=source,receiver=receiver,
        farmer_path=qualification_path(source,migrate=False),snapshots=snapshots,control=control,
        peer=peer,owner=owner,runtime=runtime)


def save(rig):rig.receipt.write_text(json.dumps(rig.state))


def listed_delivery(rig, *, uid=10, character=None, phase='verified', item=None):
    """Create the same complete receipt emitted by MerchantController."""
    character=character or str(rig.character)
    original=deepcopy(rig.state['merchant_after']['inventory'][0])
    item=deepcopy(original if item is None else item)
    before_snapshot=deepcopy(rig.state['merchant_after'])
    before_snapshot['timestamp']=time.time()
    before={'uid':uid,'price':750000,'item':item,'snapshot':before_snapshot}
    key=f'listing:{character}:promotion-test'
    rig.runtime.journal.begin(key,character,'listing',before)
    rig.runtime.journal.transition(key,'submitted')
    if phase=='verified':
        rig.runtime.journal.transition(key,'verified',{'uid':uid,'name':'MeteorScroll','old_price':None,'price':750000})
    current=deepcopy(rig.state['merchant_after'])
    current['inventory']=[]
    current['booth']=[{**original,'price':750000}]
    rig.snapshots[0]=deepcopy(rig.state['farmer_after'])
    rig.snapshots[1]=current
    return key


def append_listing(rig, snapshot, item, price, suffix):
    """Append a controller-shaped inventory-to-booth receipt to a replay."""
    key=f'listing:Spiritual:{suffix}'
    before_snapshot=deepcopy(snapshot);before_snapshot['timestamp']=time.time()
    before={'uid':item['uid'],'price':price,'item':deepcopy(item),'snapshot':before_snapshot}
    rig.runtime.journal.begin(key,'Spiritual','listing',before)
    rig.runtime.journal.transition(key,'submitted')
    rig.runtime.journal.transition(key,'verified',
        {'uid':item['uid'],'name':item.get('name','Item'),'old_price':None,'price':price})
    after=deepcopy(before_snapshot)
    after['inventory']=[row for row in after['inventory'] if row['uid']!=item['uid']]
    after['booth'].append({**item,'price':price})
    return after


def chain(rig):
    farmer,merchant=module.pair(rig.ui,rig.character)
    return module._listing_chain_reconciles(rig.state,rig.runtime.journal,farmer,merchant,now=time.time())


def test_promotion_accepts_only_a_complete_verified_native_listing_chain(rig):
    listed_delivery(rig)
    assert chain(rig)
    result=module.promote_current(rig.ui)
    assert result['promoted']
    manifest=Path(result['listing_chain_evidence'])
    assert manifest.is_file() and manifest.parent.name=='delivery-promotion-chain-audit'
    raw=manifest.read_bytes()
    assert manifest.stem==hashlib.sha256(raw).hexdigest()
    farmer=json.loads(rig.farmer_path.read_text());merchant=json.loads(rig.peer_path.read_text())
    assert farmer['listing_chain_evidence']==merchant['listing_chain_evidence']==str(manifest)
    assert module.promote_current(rig.ui)==result and manifest.read_bytes()==raw


def test_listing_chain_manifest_refuses_a_tampered_existing_audit(rig):
    listed_delivery(rig)
    farmer,merchant=module.pair(rig.ui,rig.character)
    manifest=module._chain_manifest(rig.state,rig.runtime.journal,farmer,merchant,now=time.time())
    receipt=delivery_probe.archive_probe(rig.state)
    path=module._archive_chain_manifest(receipt,manifest)
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='manifest differs'):
        module._archive_chain_manifest(receipt,manifest)


def test_listing_chain_replays_every_verified_listing_before_the_delivered_scroll(rig):
    bamboo={**rig.state['intent']['items'][0],'uid':11,'type_id':410339,'plus':0}
    rig.state['intent']['merchant']['inventory']=[deepcopy(bamboo)]
    rig.state['merchant_after']['inventory']=[deepcopy(bamboo),*rig.state['merchant_after']['inventory']]
    first=append_listing(rig,rig.state['merchant_after'],bamboo,1_000_000,'bamboo')
    second=append_listing(rig,first,rig.state['intent']['items'][0],750_000,'scroll')
    rig.snapshots[0]=deepcopy(rig.state['farmer_after']);rig.snapshots[1]=second
    assert chain(rig)


@pytest.mark.parametrize('case', ['missing','malformed','unrelated','wrong_uid','wrong_merchant','wrong_attributes',
    'unverified','stale_current','sale_silver','ambiguous','step_tamper','event_tamper','updated_after_snapshot',
    'historical_trade','loose_meteor'])
def test_listing_chain_is_fail_closed_for_adversarial_evidence(rig,case):
    if case=='missing':
        current=deepcopy(rig.state['merchant_after']);current['inventory']=[]
        current['booth']=[{**rig.state['intent']['items'][0],'price':750000}]
        rig.snapshots[1]=current
    elif case=='wrong_uid':
        listed_delivery(rig,uid=99)
    elif case=='wrong_merchant':
        key=listed_delivery(rig)
        with rig.runtime.journal.db() as db:
            db.execute("UPDATE transactions SET character='wrong-merchant' WHERE id=?",(key,))
    elif case=='wrong_attributes':
        item={**rig.state['intent']['items'][0],'plus':1}
        listed_delivery(rig,item=item)
    elif case=='unverified':
        listed_delivery(rig,phase='submitted')
    else:
        key=listed_delivery(rig)
        if case=='malformed':
            with rig.runtime.journal.db() as db:
                db.execute("UPDATE transactions SET before_json='{' WHERE id=?",(key,))
        elif case=='unrelated':
            before=deepcopy(rig.state['merchant_after'])
            other={**rig.state['intent']['items'][0],'uid':99}
            before['inventory'].append(other)
            extra={'uid':99,'price':1,'item':other,'snapshot':before}
            rig.runtime.journal.begin('listing:Spiritual:unrelated','Spiritual','listing',extra)
            rig.runtime.journal.transition('listing:Spiritual:unrelated','submitted')
            rig.runtime.journal.transition('listing:Spiritual:unrelated','verified',
                {'uid':99,'name':'Other','old_price':None,'price':1})
        elif case=='stale_current':
            farmer=deepcopy(rig.state['farmer_after']);merchant=deepcopy(rig.snapshots[1])
            farmer['timestamp']=time.time();merchant['timestamp']=1
            assert not module._listing_chain_reconciles(rig.state,rig.runtime.journal,farmer,merchant,now=time.time())
            return
        elif case=='sale_silver':
            rig.snapshots[1]['silver']+=1
        elif case=='ambiguous':
            rig.snapshots[1]['inventory']=[deepcopy(rig.state['intent']['items'][0])]
        elif case=='step_tamper':
            with rig.runtime.journal.db() as db:
                db.execute("UPDATE transaction_steps SET payload='{}' WHERE transaction_id=? AND status='submitted'",(key,))
        elif case=='event_tamper':
            with rig.runtime.journal.db() as db:
                db.execute('DELETE FROM events WHERE event=?',('listing_verified',))
        elif case=='updated_after_snapshot':
            with rig.runtime.journal.db() as db:
                db.execute('UPDATE transactions SET updated=? WHERE id=?',(time.time()+3600,key))
        elif case in ('historical_trade','loose_meteor'):
            with rig.runtime.journal.db() as db:
                row=db.execute('SELECT before_json FROM transactions WHERE id=?',(key,)).fetchone()
                before=json.loads(row[0])
                if case=='historical_trade':
                    before['snapshot']['trade']={'participant':'Parasite'}
                else:
                    before['item']['type_id']=1088001
                    before['snapshot']['inventory'][0]['type_id']=1088001
                db.execute('UPDATE transactions SET before_json=? WHERE id=?',(json.dumps(before),key))
    assert not chain(rig)


def test_listing_chain_rejects_overlapping_transaction_receipts(rig):
    bamboo={**rig.state['intent']['items'][0],'uid':11,'type_id':410339,'plus':0}
    rig.state['intent']['merchant']['inventory']=[deepcopy(bamboo)]
    rig.state['merchant_after']['inventory']=[deepcopy(bamboo),*rig.state['merchant_after']['inventory']]
    first=append_listing(rig,rig.state['merchant_after'],bamboo,1_000_000,'bamboo')
    second=append_listing(rig,first,rig.state['intent']['items'][0],750_000,'scroll')
    rig.snapshots[0]=deepcopy(rig.state['farmer_after']);rig.snapshots[1]=second
    with rig.runtime.journal.db() as db:
        prior=db.execute("SELECT updated FROM transactions WHERE id='listing:Spiritual:bamboo'").fetchone()[0]
        db.execute("UPDATE transactions SET created=? WHERE id='listing:Spiritual:scroll'",(prior-.001,))
    assert not chain(rig)


def test_native_promotion_uses_canonical_archived_evidence_and_preserves_permissions(rig):
    result=UnifiedUI.dispatch(rig.ui,{'action':'probe-delivery-promote'})
    assert result['promoted'] and not result['input_sent'] and not result['permissions_changed']
    assert result['farmer']['profile_id']==rig.farmer_profile.id
    assert result['merchant']['profile_id']==rig.character.profile_id
    assert result['merchant']['identity']==rig.state['intent']['merchant']['identity']
    farmer=json.loads(rig.farmer_path.read_text());peer=json.loads(rig.peer_path.read_text())
    assert farmer['profile_id']==rig.farmer_profile.id and farmer['character_uid']==1
    assert farmer['capabilities']=={'farmer_delivery':True}
    assert peer['capabilities']['trade'] is peer['capabilities']['trade_request'] is True
    assert peer['controls']['preserved']=={'verified':True}
    archive=Path(result['evidence'])
    assert archive!=rig.receipt and archive.parent==rig.receipt.parent/'delivery-request-probe-audit'
    assert json.loads(archive.read_text())==rig.state and farmer['evidence']==str(archive)
    assert peer['trade_evidence']==[str(archive)]
    assert not rig.runtime.enabled(rig.character) and not rig.runtime.refill_enabled(rig.character)
    assert rig.control=={'enabled':False,'revision':4,'paused':False} and rig.owner.owner is None
    original=(rig.farmer_path.read_bytes(),rig.peer_path.read_bytes())
    assert module.promote_current(rig.ui)==result
    assert (rig.farmer_path.read_bytes(),rig.peer_path.read_bytes())==original


@pytest.mark.parametrize('extra', ['character','profile_id','receipt_path','candidate_path','farmer_path','merchant_path','enabled'])
def test_bridge_rejects_caller_paths_and_other_extra_fields_before_promotion(rig,extra):
    value=rig.character.profile_id if extra=='profile_id' else 'Spiritual' if extra=='character' else 'untrusted'
    with pytest.raises(ValueError):UnifiedUI.dispatch(rig.ui,{'action':'probe-delivery-promote',extra:value})
    assert not rig.farmer_path.exists() and json.loads(rig.peer_path.read_text())==rig.peer


@pytest.mark.parametrize('change', ['phase','receipt_farmer','receipt_merchant','source_process',
    'receiver_process','source_build','receiver_build','candidate_build','merchant_path',
    'merchant_uid','farmer_uid','live_uid','live_item','live_silver','stop','manual',
    'farming','revision','pending','worker','candidate_reader'])
def test_invalid_or_changed_authority_never_promotes(rig,monkeypatch,change):
    if change=='phase':rig.state['phase']='merchant_confirm_submitted';save(rig)
    elif change=='receipt_farmer':rig.state['farmer_profile_id']='another';save(rig)
    elif change=='receipt_merchant':rig.state['target_profile_id']=rig.farmer_profile.id;save(rig)
    elif change=='source_process':rig.source.adapter.identity={'pid':987}
    elif change=='receiver_process':rig.receiver.adapter.identity={'pid':987}
    elif change=='source_build':rig.source.adapter.expected_sha256='a'*64
    elif change=='receiver_build':rig.receiver.adapter.expected_sha256='a'*64
    elif change=='candidate_build':rig.candidate.write_text(json.dumps({'client_sha256':'a'*64}))
    elif change=='merchant_path':rig.runtime.controllers[rig.character].driver.qualification=rig.peer_path.with_name('other.json')
    elif change in ('merchant_uid','farmer_uid'):
        role=change.removesuffix('_uid')
        for snapshot in (rig.state['intent'][role],rig.state[role+'_after']):snapshot['character_uid']=55
        rig.snapshots[role=='merchant']['character_uid']=55;save(rig)
    elif change=='live_uid':rig.snapshots[1]['character_uid']=55
    elif change=='live_item':rig.snapshots[1]['inventory']=[]
    elif change=='live_silver':rig.snapshots[1]['silver']+=1
    elif change=='stop':rig.owner.stop()
    elif change=='manual':rig.owner.manual_sessions[rig.character.profile_id]={'holds_automation':True}
    elif change=='farming':rig.control['enabled']=True
    elif change=='revision':
        monkeypatch.setattr(module,'validate_candidate',lambda *args:rig.control.update(revision=5))
    elif change=='pending':rig.runtime.journal.begin('pending',rig.character,'listing',{})
    elif change=='worker':rig.ui.delivery_probe_thread=NS(is_alive=lambda:True)
    else:
        def reject(*args):raise ValueError('Current recipient layout differs')
        monkeypatch.setattr(module,'validate_candidate',reject)
    with pytest.raises(ValueError):module.promote_current(rig.ui)
    assert not rig.farmer_path.exists() and json.loads(rig.peer_path.read_text())==rig.peer


@pytest.mark.parametrize('replacement', ['receipt','candidate','merchant'])
def test_replacement_during_validation_is_rejected_before_either_qualification_write(rig,monkeypatch,replacement):
    def replace(*args):
        path={'receipt':rig.receipt,'candidate':rig.candidate,'merchant':rig.peer_path}[replacement]
        data=json.loads(path.read_text());data['replaced']=True;path.write_text(json.dumps(data))
    monkeypatch.setattr(module,'validate_candidate',replace)
    with pytest.raises(ValueError,match='authority changed'):module.promote_current(rig.ui)
    assert not rig.farmer_path.exists()
    assert not json.loads(rig.peer_path.read_text())['capabilities']['trade']


def test_failed_second_file_write_is_safely_idempotent_and_does_not_enable_operations(rig,monkeypatch):
    write=delivery_qualification.write_json
    def fail_peer(path,value):
        if Path(path)==rig.peer_path:raise OSError('Simulated atomic replacement failure')
        write(path,value)
    monkeypatch.setattr(delivery_qualification,'write_json',fail_peer)
    with pytest.raises(OSError):module.promote_current(rig.ui)
    assert rig.farmer_path.exists() and not json.loads(rig.peer_path.read_text())['capabilities']['trade']
    assert not rig.runtime.enabled(rig.character) and not rig.runtime.refill_enabled(rig.character)
    monkeypatch.setattr(delivery_qualification,'write_json',write)
    assert module.promote_current(rig.ui)['promoted']
    assert len(json.loads(rig.peer_path.read_text())['trade_evidence'])==1


def test_status_and_readiness_observe_promoted_flags_without_resuming_any_permission(rig,monkeypatch):
    from conquest.merchants import delivery_readiness
    monkeypatch.setattr(delivery_readiness,'read_json',lambda _:{'enabled':True,'parity_verified':True})
    monkeypatch.setattr(delivery_readiness,'credential_path',lambda _:NS(is_file=lambda:True))
    def farmer_driver(ui):
        driver=object.__new__(MerchantDriver);driver.observer=rig.source;driver.qualification=rig.farmer_path
        return NS(require_qualified=lambda:driver.require_qualified('farmer_delivery'))
    before=describe(rig.ui,farmer_driver)
    assert not before['qualified'] and 'Spiritual:trade' in before['blockers']
    assert not rig.runtime.status()[rig.character]['qualification']['trade']
    module.promote_current(rig.ui)
    after=describe(rig.ui,farmer_driver)
    status=rig.runtime.status()[rig.character]
    assert after['qualified'] and 'Spiritual:trade' not in after['blockers']
    assert 'Spiritual:trading_paused' in after['blockers']
    assert status['qualification']['trade'] and not status['enabled'] and not status['ready']
    assert not status['refill']['enabled']


@pytest.mark.parametrize('change', [None,'viewport','actor','mode'])
def test_candidate_validation_uses_live_memory_reader_and_pinned_native_mode(monkeypatch,change):
    # No actionability or input is required: Inventory may still obscure the
    # completed trade target while its exact scene object remains readable.
    from conquest.merchants import farmer_trade, memory, trade_controls
    observer=NS(adapter=object(),operations=NS(target=NS(snapshot=lambda:{'client_size':[800,600]})))
    actor={'address':1234,'uid':2,'name':'Spiritual'}
    monkeypatch.setattr(memory,'GuiReader',lambda _:NS(viewport_size=lambda:[801,600] if change=='viewport' else [800,600]))
    calls=[]
    def read(source,candidate,merchant):
        calls.append((source,candidate,merchant))
        return {**actor,'address':4321} if change=='actor' else actor
    monkeypatch.setattr(farmer_trade,'_recipient_record',read)
    monkeypatch.setattr(trade_controls,'targeting_state',lambda _:{'rva':123,'value':19})
    candidate={'recipient':{'proven':'layout'},'target_mode':{'rva':123,'value':18 if change=='mode' else 19}}
    if change:
        with pytest.raises(ValueError):module.validate_candidate(observer,candidate,{}, {'character_uid':2},{'recipient':actor})
    else:
        module.validate_candidate(observer,candidate,{}, {'character_uid':2},{'recipient':actor})
        assert calls==[(observer,{**candidate,'gui_size':[800,600]},{'character_uid':2})]
