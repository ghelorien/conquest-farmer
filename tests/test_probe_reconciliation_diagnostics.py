from copy import deepcopy
from types import SimpleNamespace as NS
import json
import sqlite3
import time

import pytest

from conquest.merchants import delivery_probe as probe
from conquest.merchants.manual_sessions import canonical_ownership
from conquest.merchants.ui import UnifiedUI
from test_delivery_probe_manual_ownership import supervised
from test_delivery_probe_trade_contention import false_pair
from test_manual_runtime import rig


ERROR='Request/trade window evidence is unavailable'
REASONS=('Farmer has no attached memory observer','Attached memory reader is unavailable')


def contents(x):
    with x.runtime.manual_sessions._probe_connection(True) as db:return list(db.iterdump())


def gap_pair(x,counts=(2,2)):
    rows=false_pair(x)
    store=x.runtime.manual_sessions
    for row,reason,count,read in zip(rows,REASONS,counts,(x.farmer_read,x.read)):
        for _ in range(count):
            x.now+=.01;store.observe(row['id'],{'reader_error':reason},now=x.now)
        x.now+=.01;store.observe(row['id'],read(),now=x.now)
    x.runtime._sync_manual_fence()
    return rows


def inspect(x):return x.runtime.inspect_probe_reconciliation('Dutch',x.farmer_read(),x.read(),now=x.now)


def test_live_bracketed_restart_gaps_diagnose_without_any_mutation_then_retract_atomically(supervised,monkeypatch):
    x=supervised;rows=gap_pair(x,(160,64));store=x.runtime.manual_sessions
    before=contents(x);fence=deepcopy(x.guard.manual_sessions);journal=x.path.read_bytes()
    original_sync=x.runtime._sync_manual_fence
    monkeypatch.setattr(x.runtime,'_sync_manual_fence',lambda:pytest.fail('Diagnostic must not mutate fences'))
    result=inspect(x)
    assert result['outcome']=='validated' and result['bot_owned']
    assert result['eligible_sessions']==2 and result['outage_intervals']==2
    assert result['read_only'] and result['input_authorized'] is False
    assert contents(x)==before and x.guard.manual_sessions==fence and x.path.read_bytes()==journal
    assert not hasattr(x.runtime,'last_probe_reconciliation')
    monkeypatch.setattr(x.runtime,'_sync_manual_fence',original_sync)
    x.now+=.01
    assert x.runtime.reconcile_probe_pair('Dutch',x.farmer_read(),x.read(),now=x.now)
    assert x.runtime.last_probe_reconciliation['retracted_sessions']==2
    for row,count in zip(rows,(160,64)):
        terminal=store.get(row['id'])['terminal'];gap=terminal['reader_outage_intervals'][0]
        assert gap['error_count']==count and gap['before_evidence_id']<gap['first_error_id']<=gap['last_error_id']<gap['after_evidence_id']
        evidence={r['id']:r for r in store.evidence(row['id'])}
        for side in ('before','after'):
            bracket=evidence[gap[side+'_evidence_id']]
            for field in ('digest','ownership_digest','observed_at','recorded_at'):
                assert gap[side+'_'+field]==bracket[field]
        assert terminal['sales_receipt'] is False and terminal['gameplay_input'] is False
    assert store.verify_audit() and x.runtime.manual_status()==[] and x.calls==[]
    after=contents(x)
    assert x.runtime.reconcile_probe_pair('Dutch',x.farmer_read(),x.read(),now=x.now)
    assert contents(x)==after
    with store._probe_connection(True) as db:
        for table in ('sales','manual_replans','manual_decline_claims','visitor_permissions'):
            assert db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0


@pytest.mark.parametrize('fault',['error_last','wrong_role','extra_key','rollover_error','foreign_valid','stock_valid',
                                 'malformed_json','error_with_ownership','bad_digest','permission','approved','stable'])
def test_uncertain_history_preserves_both_holds_but_is_not_failed_current_bot_proof(supervised,fault):
    x=supervised;rows=gap_pair(x);store=x.runtime.manual_sessions;row=rows[1]
    if fault in ('error_last','wrong_role','extra_key','rollover_error','foreign_valid','stock_valid'):
        evidence={'reader_error':REASONS[1]}
        if fault=='wrong_role':evidence['reader_error']=REASONS[0]
        if fault=='extra_key':evidence['trade']=None
        if fault=='rollover_error':evidence['reader_error']='Game process is unavailable or changed'
        if fault in ('foreign_valid','stock_valid'):
            evidence=x.read()
            if fault=='foreign_valid':evidence['trade']['participant']='Other'
            else:evidence['silver']+=1
        x.now+=.01;store.observe(row['id'],evidence,now=x.now)
        if fault!='error_last':
            x.now+=.01;store.observe(row['id'],x.read(),now=x.now)
    else:
        with store.db() as db:
            if fault in ('malformed_json','error_with_ownership','bad_digest'):
                encoded='{' if fault=='malformed_json' else json.dumps({'reader_error':REASONS[1]})
                from conquest.merchants.manual_sessions import _digest
                digest='changed' if fault=='bad_digest' else _digest({'reader_error':REASONS[1]})
                db.execute('INSERT INTO manual_evidence(session_id,recorded_at,digest,ownership_digest,snapshot_json,error) VALUES(?,?,?,?,?,?)',
                    (row['id'],x.now,digest,'bad' if fault=='error_with_ownership' else None,encoded,ERROR))
            if fault=='permission':db.execute('INSERT INTO visitor_permissions VALUES(?,?,?,?,1,?)',('Dutch','Parasite','America',55,x.now))
            if fault=='approved':db.execute('UPDATE manual_sessions SET ever_approved=1 WHERE id=?',(row['id'],))
            if fault=='stable':db.execute('UPDATE manual_sessions SET stable_since=? WHERE id=?',(x.now,row['id']))
        if fault in ('malformed_json','error_with_ownership','bad_digest'):
            x.now+=.01;store.observe(row['id'],x.read(),now=x.now)
    before=contents(x)
    result=inspect(x)
    assert result['outcome']=='protected' and result['bot_owned'] and not result['input_authorized']
    assert result['stage']=='manual_history' and result['checked_through']=='final_probe_recheck'
    assert x.runtime.reconcile_probe_pair('Dutch',x.farmer_read(),x.read(),now=x.now)
    assert x.runtime.last_probe_reconciliation['reason']=='manual_history_unverified'
    assert contents(x)==before
    assert all(store.get(r['id'])['holds_automation'] for r in rows)
    assert x.guard.manual_session_blocked('Farmer') and x.guard.manual_session_blocked('Dutch') and not x.calls


def test_error_first_admission_never_uses_bracketed_outage_exception(supervised):
    x=supervised
    from test_delivery_probe_manual_ownership import open_trade
    open_trade(x,phase='offer_verified',offered=True)
    missing=x.read();del missing['request']
    row=x.runtime.manual_sessions.observe_target('Dutch',missing,now=x.now)
    x.now+=.1;x.runtime.manual_sessions.observe(row['id'],x.read(),now=x.now)
    result=inspect(x)
    assert result['outcome']=='protected' and result['bot_owned']
    assert x.runtime.manual_sessions.get(row['id'])['holds_automation']


@pytest.mark.parametrize('failure',['ownership','digest','storage','history_program','sync'])
def test_bounded_diagnostics_locate_failures_without_leaking_exception_data(supervised,monkeypatch,failure):
    x=supervised;false_pair(x)
    if failure=='ownership':x.state['silver']+=1
    if failure=='digest':
        count=[0]
        def read(**kw):
            count[0]+=1
            return deepcopy(x.probe) if count[0]==1 else {**x.probe,'unknown_change':True}
        monkeypatch.setattr(probe,'read_probe',read)
    if failure in ('storage','history_program'):
        def fail(*a,**k):raise (sqlite3.OperationalError if failure=='storage' else TypeError)('do-not-expose snapshot contents')
        monkeypatch.setattr(x.runtime.manual_sessions,'retract_probe_pair',fail)
    if failure=='sync':
        monkeypatch.setattr(x.runtime,'_sync_manual_fence',lambda:(_ for _ in ()).throw(RuntimeError('do-not-expose')))
    assert not x.runtime.reconcile_probe_pair('Dutch',x.farmer_read(),x.read(),now=x.now)
    result=x.runtime.last_probe_reconciliation
    assert result['outcome']=='error' and not result['input_authorized']
    assert result['stage']=={'ownership':'ownership','digest':'probe_recheck','storage':'manual_history',
                              'history_program':'manual_history','sync':'fence_sync'}[failure]
    encoded=json.dumps(result)
    assert len(encoded)<1500 and 'do-not-expose' not in encoded and 'inventory' not in encoded


def test_probe_replacement_during_history_cannot_be_reported_as_current_owned(supervised,monkeypatch):
    x=supervised;false_pair(x);before=contents(x)
    original=x.runtime.manual_sessions.inspect_probe_pair
    def replace(*a,**k):
        result=original(*a,**k)
        x.probe['phase']='merchant_confirm_submitted';x.save()
        return result
    monkeypatch.setattr(x.runtime.manual_sessions,'inspect_probe_pair',replace)
    result=inspect(x)
    assert result['outcome']=='error' and not result['bot_owned'] and result['reason']=='probe_changed'
    assert contents(x)==before


def test_readonly_probe_diagnostic_never_completes_pending_override(supervised,monkeypatch):
    x=supervised;false_pair(x)
    pending=probe.Path(str(x.path)+'.override-intent.json');pending.write_text('{}')
    original=x.path.read_bytes()
    result=inspect(x)
    assert result['outcome']=='error' and result['stage']=='probe_read'
    assert pending.read_text()=='{}' and x.path.read_bytes()==original


def test_bridge_reconciliation_diagnostic_is_query_only_and_exact_schema(supervised,monkeypatch):
    x=supervised;gap_pair(x);before=contents(x)
    ui=NS(coordinator=x.guard,runtime=x.runtime)
    monkeypatch.setattr('conquest.merchants.delivery_bridge.pair',lambda *a:(x.farmer_read(),x.read()))
    result=UnifiedUI.dispatch(ui,{'action':'probe-delivery-reconciliation-diagnostic'})
    assert result['read_only'] and result['outcome']=='validated' and contents(x)==before
    with pytest.raises(ValueError,match='Unsupported'):
        UnifiedUI.dispatch(ui,{'action':'probe-delivery-reconciliation-diagnostic','input':True})


def test_retraction_failure_rolls_back_both_bracketed_gap_sessions(supervised,monkeypatch):
    x=supervised;rows=gap_pair(x);store=x.runtime.manual_sessions;before=contents(x)
    original=store._audit;calls=[]
    def fail(*args):
        calls.append(1)
        if len(calls)==2:raise sqlite3.OperationalError('injected second audit failure')
        return original(*args)
    monkeypatch.setattr(store,'_audit',fail)
    assert not x.runtime.reconcile_probe_pair('Dutch',x.farmer_read(),x.read(),now=x.now)
    assert contents(x)==before and all(store.get(row['id'])['holds_automation'] for row in rows)


@pytest.mark.parametrize('name',['manual_reader_hold','unrelated_request_decline'])
@pytest.mark.parametrize('managed',[False,True])
def test_diagnostic_projects_real_profile_scoped_state_holds_without_fence_sync(supervised,monkeypatch,name,managed):
    from test_delivery_probe_manual_ownership import open_trade
    x=supervised;open_trade(x,phase='offer_verified',offered=True)
    farmer_id='a1f7f6ac-1340-40cf-a0ad-57d8c146d341' if managed else 'Farmer'
    merchant_id='fd27c7ee-7f58-4080-b284-68e53c739fcb' if managed else 'Dutch'
    monkeypatch.setattr(x.runtime,'manual_target',lambda owner:farmer_id if owner=='Farmer' else merchant_id)
    x.probe.update(farmer_profile_id=farmer_id,target_profile_id=merchant_id);x.save()
    store=x.runtime.manual_sessions
    store.observe_target(farmer_id,x.farmer_read(),now=x.now)
    store.observe_target(merchant_id,x.read(),now=x.now)
    x.runtime._manual_set('Dutch',name,{'phase':'submitted','target_profile_id':merchant_id})
    before=contents(x);fence=deepcopy(x.guard.manual_sessions)
    monkeypatch.setattr(x.runtime,'_sync_manual_fence',lambda:pytest.fail('Diagnostic must not sync'))
    result=inspect(x)
    assert result['outcome']=='protected' and result['reason']=='reader_or_decline_hold'
    assert result['stage']=='hold_read' and result['checked_through']=='final_probe_recheck'
    assert result['active_session_count']==2 and result['target_profile_id']==merchant_id
    assert contents(x)==before and x.guard.manual_sessions==fence


@pytest.mark.parametrize('fault',['audit_chain','observed_at'])
def test_gap_retraction_requires_immutable_audit_and_exact_record_observation_time(supervised,fault):
    x=supervised;rows=gap_pair(x);store=x.runtime.manual_sessions
    with store.db() as db:
        if fault=='audit_chain':
            db.execute('INSERT INTO manual_audit(session_id,event,at,payload_json,previous_digest,digest) VALUES(?,?,?,?,?,?)',
                       (rows[0]['id'],'needs_attention',x.now,'{}','incorrect','incorrect'))
        else:
            from conquest.merchants.manual_sessions import _digest
            snapshot=x.read();proof=canonical_ownership(snapshot,require_closed=False)
            db.execute('INSERT INTO manual_evidence(session_id,observed_at,recorded_at,digest,ownership_digest,snapshot_json) VALUES(?,?,?,?,?,?)',
                       (rows[1]['id'],x.now-.1,x.now,_digest(snapshot),_digest(proof),json.dumps(snapshot)))
    before=contents(x)
    assert inspect(x)['outcome']=='protected'
    assert x.runtime.reconcile_probe_pair('Dutch',x.farmer_read(),x.read(),now=x.now)
    assert contents(x)==before and all(store.get(row['id'])['holds_automation'] for row in rows)


def test_live_scale_history_keeps_reconciliation_within_confirmation_deadline(supervised):
    x=supervised;rows=false_pair(x);store=x.runtime.manual_sessions
    # Populate exactly the live evidence cardinalities without thousands of
    # separate setup transactions. Every row keeps normal immutable digests.
    for row,read,total,errors,reason in zip(rows,(x.farmer_read,x.read),(1950,1041),(160,64),REASONS):
        with store.db() as db:
            for index in range(total-1):
                x.now+=.001
                if index<errors:
                    eid=store._evidence(db,row['id'],{'reader_error':reason},x.now,error=ERROR)
                    store._attention(db,store._row(db,row['id']),ERROR,x.now,eid)
                else:
                    snapshot=read()
                    store._evidence(db,row['id'],snapshot,x.now,canonical_ownership(snapshot,require_closed=False))
        assert len(store.evidence(row['id']))==total
    x.runtime._sync_manual_fence()
    start=time.perf_counter();result=inspect(x);inspect_seconds=time.perf_counter()-start
    assert result['eligible_sessions']==2 and result['outage_intervals']==2
    start=time.perf_counter()
    assert x.runtime.reconcile_probe_pair('Dutch',x.farmer_read(),x.read(),now=x.now)
    retract_seconds=time.perf_counter()-start
    print(f'live-scale inspect={inspect_seconds:.3f}s retract={retract_seconds:.3f}s')
    assert inspect_seconds<6 and retract_seconds<6  # Leave >9s of the 15s input window.
    assert x.runtime.manual_status()==[] and x.calls==[]


def test_non_trade_result_includes_finally_fence_sync_failure(supervised,monkeypatch):
    x=supervised  # The original fixture is an exact bot-owned incoming request.
    monkeypatch.setattr(x.runtime,'_sync_manual_fence',lambda:(_ for _ in ()).throw(RuntimeError('sync unavailable')))
    assert not x.runtime.reconcile_probe_pair('Dutch',x.farmer_read(),x.read(),now=x.now)
    result=x.runtime.last_probe_reconciliation
    assert result['stage']=='fence_sync' and result['reason']=='fence_sync_failed' and result['error_type']=='RuntimeError'


def test_bridge_preserves_bounded_last_dispatch_metadata_before_fresh_readonly_inspection(supervised,monkeypatch):
    x=supervised;gap_pair(x)
    previous={'stage':'manual_history','reason':'manual_history_validation_failed','outcome':'error',
              'error_type':'OperationalError','bot_owned':True,'probe_digest':'a'*64,'target_profile_id':'Dutch',
              'active_session_count':2,'input_authorized':True,'snapshot':{'secret':'never return'},'text':'never return'}
    x.runtime.last_probe_reconciliation=deepcopy(previous)
    before=contents(x);fence=deepcopy(x.guard.manual_sessions)
    monkeypatch.setattr('conquest.merchants.delivery_bridge.pair',lambda *a:(x.farmer_read(),x.read()))
    result=UnifiedUI.dispatch(NS(coordinator=x.guard,runtime=x.runtime),{'action':'probe-delivery-reconciliation-diagnostic'})
    assert result['outcome']=='validated' and result['last_attempt']=={k:v for k,v in previous.items() if k not in ('input_authorized','snapshot','text')}
    assert x.runtime.last_probe_reconciliation==previous and contents(x)==before and x.guard.manual_sessions==fence


def test_last_attempt_projection_rejects_unknown_oversized_and_non_metadata_values():
    from conquest.merchants.manual_runtime import probe_attempt_projection
    bad={'stage':'arbitrary secret','reason':'x'*10000,'outcome':['error'],'error_type':'private text',
         'bot_owned':1,'probe_digest':'x'*64,'evidence_digest':'a'*65,'target_profile_id':'secret\ntext',
         'farmer_profile_id':'x'*129,'active_session_count':True,'outage_intervals':-1,
         'eligible_sessions':10000001,'checked_through':'unknown','unknown':{'large':'x'*100000}}
    assert probe_attempt_projection(bad)=={}
    assert probe_attempt_projection(None) is None
