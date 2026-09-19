"""Observe an already-submitted request; never resend it or accept a trade."""
import json
import os
from pathlib import Path
import tempfile
import time

from conquest.merchants import delivery_probe as probe
from conquest.merchants.delivery import exact_items,validate_snapshot
from conquest.merchants.manual_sessions import canonical_ownership
from conquest.recovery_override import evidence_digest


def ownership(intent,farmer,merchant,*,now):
    """Exact saved owners with just the intended incoming request added."""
    expected_name=intent['farmer']['character']
    expected_uid=intent['farmer']['character_uid']
    request=merchant.get('request')
    if (farmer.get('request') is not None or not isinstance(request,dict)
            or request.get('participant')!=expected_name
            or type(request.get('participant_uid')) is not int
            or request['participant_uid']!=expected_uid
            or request.get('message')!=expected_name+' wishes to trade with you.'
            or request.get('server',merchant.get('server'))!=intent['farmer']['server']):
        raise ValueError('The exact submitted farmer request is not present')
    result={}
    for role,snapshot in (('farmer',farmer),('merchant',merchant)):
        before=intent[role]
        if before.get('map_id')!=1036:
            raise ValueError('Submitted request was not prepared in Market')
        validate_snapshot(snapshot,before['character'],now)
        observed=canonical_ownership(snapshot,require_closed=False)
        original=canonical_ownership(before)
        # All ownership, process and character fields stay fixed. The one
        # permitted difference is the exact receiver request verified above.
        comparable={**observed,'request':None}
        if (comparable!=original or snapshot.get('position')!=before.get('position')
                or any({item['uid']:item.get('slot') for item in snapshot[field]}!=
                       {item['uid']:item.get('slot') for item in before[field]}
                       for field in ('inventory','booth'))):
            raise ValueError('Submitted request participants, position or ownership changed')
        result[role]={**observed,'position':snapshot['position'],'map_id':snapshot['map_id']}
    return result


def observe(ui,state):
    from conquest.merchants.request_identity import participant_uid
    character=state['character'];intent=state['intent']
    farmer,merchant=probe.pair(ui,character)
    proof=ownership(intent,farmer,merchant,now=time.time())
    observer=ui.runtime.observers.get(character)
    if observer is None:raise ValueError('The saved merchant is no longer attached')
    with observer.lock:
        uid=participant_uid(observer,intent['farmer']['character'])
    if type(uid) is not int or uid!=intent['farmer']['character_uid']:
        raise ValueError('Pinned incoming request actor differs from the saved farmer UID')
    return {'farmer':farmer,'merchant':merchant,'participant_uid':uid,
            'evidence_digest':evidence_digest(proof)}


def persist(state):
    """Flush before publication, so a failed write cannot expose a new phase."""
    raw=json.dumps(state,indent=2).encode('utf-8')
    path=probe.JOURNAL
    fd,name=tempfile.mkstemp(prefix=path.name+'.request-reconcile.',suffix='.tmp',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as out:
            if out.write(raw)!=len(raw):raise OSError('Incomplete request reconciliation journal write')
            out.flush();os.fsync(out.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def reconcile_request(ui):
    """Strictly advance request observation only; all later stages are explicit."""
    probe.recovery_available(ui)
    state=probe.read_probe()
    if not state or state.get('phase') not in ('request_submitted','request_verified'):
        raise ValueError('Only a submitted or already verified request can be reconciled')
    original_digest=evidence_digest(state)
    intent=state['intent'];recipient=state.get('recipient',{})
    if (state['character']!=intent['merchant']['character']
            or recipient.get('uid')!=intent['merchant']['character_uid']
            or recipient.get('name')!=intent['merchant']['character']
            or recipient.get('position')!=intent['merchant']['position']):
        raise ValueError('Submitted recipient evidence differs from the saved merchant')
    # Take the coordination mutex without a lease: it blocks concurrent bot
    # input but never focuses a surface or grants input during reconciliation.
    if not ui.coordinator.lock.acquire(blocking=False):
        raise ValueError('Wait for current input before request reconciliation')
    try:
        if ui.coordinator.owner is not None:
            raise ValueError('Wait for current input before request reconciliation')
        probe.recovery_available(ui)
        first=observe(ui,state)
        time.sleep(.1)
        second=observe(ui,state)
        if (first['evidence_digest']!=second['evidence_digest']
                or any(second[role]['timestamp']<=first[role]['timestamp']
                       for role in ('farmer','merchant'))):
            raise ValueError('Request ownership or identity did not stabilize across fresh observations')
        ownership(intent,second['farmer'],second['merchant'],now=time.time())
        selected=probe.selected_intent({**second['farmer'],'request':None},
            {**second['merchant'],'request':None},state.get('selected_uids'))
        if exact_items(selected['items'])!=exact_items(intent['items']):
            raise ValueError('Submitted selection differs from the authorized exact item')
        probe.recovery_available(ui)
        if evidence_digest(probe.read_probe())!=original_digest:
            raise ValueError('Trade probe changed during read-only request reconciliation')
        idempotent=state['phase']=='request_verified'
        if idempotent:
            farmer,merchant=state.get('farmer_after'),state.get('merchant_after')
            if not isinstance(farmer,dict) or not isinstance(merchant,dict):
                raise ValueError('Verified request lacks its original observation evidence')
            saved=ownership(intent,farmer,merchant,now=max(farmer['timestamp'],merchant['timestamp']))
            if evidence_digest(saved)!=second['evidence_digest']:
                raise ValueError('Already verified request evidence changed')
        else:
            state={**state,'phase':'request_verified','error':None,'updated_at':time.time(),
                'farmer_after':second['farmer'],'merchant_after':second['merchant'],
                'request_reconciliation':{'original_phase':'request_submitted',
                    'original_incident_digest':original_digest,'observations':[first,second],
                    'evidence_digest':second['evidence_digest'],'verified_at':time.time()}}
            persist(state)
        return {'phase':'request_verified','character':state['character'],
                'uids':state['selected_uids'],'idempotent':idempotent,
                'evidence_digest':second['evidence_digest'],'gameplay_input':False}
    finally:
        ui.coordinator.lock.release()
