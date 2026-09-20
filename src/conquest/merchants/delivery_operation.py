"""Asynchronous native delivery operations with durable source-side receipts."""
from conquest.character_context import state_path
import json
from pathlib import Path
import threading
import sqlite3
import hashlib
from contextlib import nullcontext
from conquest.discord_notify import read_json
from conquest.merchants.journal import Journal,character_name
from conquest.merchants.delivery import (DeliveryTransaction,prepare,reconciliation_outcome,
    ReconciliationBlocked,exact_items)
from conquest.merchants.farmer_trade import FarmerTradeDriver

JOURNAL=Path(state_path('reports/banking/merchant-deliveries.sqlite3'))


def pending():
    if not JOURNAL.exists():return False
    try:
        with sqlite3.connect(JOURNAL.resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as db:
            return bool(db.execute("SELECT 1 FROM transactions WHERE kind='farmer_delivery' AND phase NOT IN ('verified','aborted','operator_overridden') LIMIT 1").fetchone())
    except sqlite3.Error as error:
        raise ValueError('Farmer delivery journal is unreadable; reconcile before input') from error


def guard_protected_assets():
    from conquest.protected_withdrawal import pending
    if pending():
        raise ValueError('Protected warehouse withdrawal needs inventory reconciliation before continuing')
    from conquest.merchants.trade_qualification_prep import pending as prep_pending
    if prep_pending():
        raise ValueError('Supervised trade preparation needs reconciliation before continuing')


def guard_reload():
    guard_protected_assets()
    from conquest.merchants.delivery_route import pending as route_pending
    if route_pending():raise ValueError('Reconcile the pending farmer delivery before reloading')
    if pending():raise ValueError('Reconcile the pending farmer delivery before reloading')


def clear_settled_attention(ui,key,receipt):
    """Clear only this incident after both durable journals prove no transfer."""
    runtime=getattr(ui,'runtime',None)
    overridden = receipt.get('phase')=='operator_overridden' and receipt.get('outcome')=='operator_overridden'
    settled = receipt.get('phase')=='aborted' and receipt.get('outcome')=='no_transfer'
    if (runtime is None or not (overridden or settled)
            or receipt.get('cleanup_pending') or receipt.get('next_action') not in ('retry_delivery','release_route')
            or (not overridden and not receipt.get('proof_digest'))):
        return False
    character=character_name(receipt['character'])
    with runtime.journal.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT value FROM state WHERE character=? AND name='delivery_reservation'",(character,)).fetchone()
        reservation=json.loads(row[0]) if row else {}
        if not reservation or reservation.get('request_id')!=key:
            return False
        if overridden:
            if reservation.get('phase')!='operator_overridden':return False
            if ((reservation.get('operator_override') or {}).get('confirmation_reference') !=
                    (receipt.get('operator_override') or {}).get('confirmation_reference')):
                return False
        elif (reservation.get('phase')!='no_transfer_reconciled'
              or (reservation.get('disposition') or {}).get('proof_digest')!=receipt['proof_digest']):
            return False
        row=db.execute("SELECT value FROM state WHERE character=? AND name='attention'",(character,)).fetchone()
        attention=json.loads(row[0]) if row else None
        if attention:
            if attention.get('kind')!='farmer_delivery' or attention.get('request_id')!=key:
                return False
            db.execute("UPDATE state SET value='null' WHERE character=? AND name='attention' AND value=?",(character,row[0]))
    getattr(ui,'delivery_errors',{}).pop(key,None)
    return True


def status(journal,key):
    with journal.db() as db:
        row=db.execute('SELECT * FROM transactions WHERE id=?',(key,)).fetchone()
    if not row:
        admission=journal.delivery_admission(key)
        if not admission:return None
        items=json.loads(admission['items_json']) if admission['items_json'] else []
        origin=json.loads(admission['origin_json'])
        reason=admission['reason'] or 'Admission was interrupted before any delivery worker or input'
        proof=hashlib.sha256(json.dumps({'request_id':key,'character':admission['character'],
            'uids':json.loads(admission['uids_json']),'items':items,'origin':origin,
            'phase':admission['phase']},sort_keys=True).encode()).hexdigest()
        return {'request_id':key,'character':admission['character'],'phase':'aborted',
                'operation_id':origin.get('operation_id',key),
                'town_visit_id':origin.get('town_visit_id'),'visit_id':origin.get('visit_id'),
                'farmer_profile_id':origin.get('farmer_profile_id'),
                'uids':json.loads(admission['uids_json']),'items':items,
                'outcome':'retryable_before_input','evidence_outcome':'admission_rejected',
                'reason':reason,'next_action':'release_route','proof_digest':proof,
                'cleanup_pending':[],'sale_receipts':[],'delivered':[],'remaining':items,
                'action_trace_initialized':False,'last_action_stage':None}
    before=json.loads(row['before_json'])
    result=(json.loads(row['result_json']) if row['result_json'] else {}) or {}
    trace=journal.trace(key)
    evidence_outcome=result.get('outcome')
    if row['phase']=='verified':evidence_outcome=evidence_outcome or 'delivered'
    elif row['phase']=='aborted':evidence_outcome=evidence_outcome or 'aborted'
    elif row['phase']=='operator_overridden':evidence_outcome=evidence_outcome or 'operator_overridden'
    outcome=({'delivered':'transferred','no_transfer':'no_transfer',
              'not_started':'retryable_before_input','partial_transfer':'deferred',
              'operator_overridden':'operator_overridden'}
             .get(evidence_outcome))
    if outcome is None:
        outcome='unresolved' if row['phase'] in ('submitted','uncertain') else 'deferred'
    reason=result.get('reason') or {
        'delivered':'Exact bilateral ownership proves the selected items transferred',
        'no_transfer':'Exact bilateral ownership proves no selected item transferred',
        'partial_transfer':'Exact bilateral ownership proves only part of the selected batch transferred',
        'not_started':'The worker was rejected before any gameplay input',
        'aborted':'The operation has a durable terminal abort receipt',
        'operator_overridden':'The operator closed this incident without asserting a transfer outcome',
    }.get(evidence_outcome,'Exact bilateral reconciliation is still required')
    cleanup_pending=result.get('cleanup_pending') or []
    cleanup_verified=any(s['stage'] in ('cleanup','cleanup_trade') and s['status']=='observed' for s in trace)
    receiver_verified=any(s['stage']=='receiver_receipt' and s['status']=='observed' for s in trace)
    if row['phase'] in ('verified','aborted','operator_overridden') and cleanup_pending and not cleanup_verified:
        next_action='cleanup_trade_modal'
    elif row['phase']=='verified' and not receiver_verified:
        next_action='finalize_receiver_receipt'
    elif row['phase']=='aborted' and evidence_outcome=='partial_transfer':
        next_action='replan_remaining_delivery'
    elif row['phase']=='aborted' and evidence_outcome=='no_transfer':
        next_action='retry_delivery'
    elif row['phase'] in ('verified','aborted','operator_overridden'):
        next_action='release_route'
    elif any(s['stage']=='action_trace' and s['status']=='initialized' for s in trace):
        next_action='reconcile_bilateral_ownership'
    else:
        next_action='explicit_exact_reconciliation'
    return {'request_id':key,'character':row['character'],'phase':row['phase'],
            'operation_id':before.get('operation_id',key),
            'town_visit_id':before.get('town_visit_id'),'visit_id':before.get('visit_id'),
            'farmer_profile_id':before.get('farmer_profile_id'),
            'uids':[item['uid'] for item in before['items']],
            'items':[{name:item.get(name) for name in
                      ('uid','type_id','plus','gem1','gem2','quantity','bound')}
                     for item in before['items']],
            'outcome':outcome,'evidence_outcome':evidence_outcome or 'unknown',
            'reason':reason,
            'next_action':next_action,'proof_digest':result.get('proof_digest'),
            'cleanup_pending':cleanup_pending if not cleanup_verified else [],
            'sale_receipts':result.get('sale_receipts',[]),
            'delivered':result.get('delivered'),
            'remaining':result.get('remaining'),
            'action_trace_initialized':any(s['stage']=='action_trace' and s['status']=='initialized' for s in trace),
            'last_action_stage':next((s['stage'] for s in reversed(trace)
                                      if s['status']=='before_action'),None)}


def reject_worker_admission(journal,key):
    """Close a prepared source intent when its captured grant expires pre-input."""
    current=status(journal,key)
    if (not current or current['phase']!='prepared'
            or current['action_trace_initialized']):
        return current
    journal.step(key,'worker_admission','rejected',{
        'outcome':'not_started','reason':'input_grant_rejected'})
    journal.transition(key,'aborted',{'outcome':'not_started',
        'reason':'input_grant_rejected','next_action':'release_route',
        'cleanup_pending':[]})
    return status(journal,key)


def operator_override(ui, journal, key, *, operator_confirmed=False,
                      confirmation_reference=None, operator=None, incident_digest=None):
    """Recheck both live participants, then close the linked hold manually.

    The snapshots are read-only and are recorded as planning evidence.  They
    are deliberately never passed to the normal reconciliation classifier:
    an override does not assert whether an old trade succeeded.
    """
    old=status(journal,key)
    if not old:raise ValueError('Unknown farmer delivery')
    worker=getattr(ui,'delivery_workers',{}).get(key)
    if worker and worker.is_alive() or key in getattr(ui,'delivery_admissions',set()):
        raise ValueError('Wait for the delivery worker before overriding this incident')
    if old['phase'] in ('verified','aborted') and old['outcome']!='operator_overridden':
        raise ValueError('A completed delivery cannot be overridden')
    character=old['character']
    from conquest.merchants.delivery_bridge import pair
    try:
        farmer,merchant=pair(ui,character)
        observed_at=max(farmer.get('timestamp',0),merchant.get('timestamp',0))
        fresh={'observed_at':observed_at,'farmer':farmer,'merchant':merchant}
    except (ValueError,OSError) as error:
        # Override is a journal disposition and may be performed while a
        # client is disconnected.  The next normal cycle still waits for
        # fresh memory before accepting work or stock as a stranger.
        fresh={'recheck_unavailable':type(error).__name__,'reason':'Fresh participant memory unavailable'}
    result=journal.operator_override(key,operator_confirmed=operator_confirmed,
                                     confirmation_reference=confirmation_reference,
                                     operator=operator,fresh_evidence=fresh,
                                     incident_digest=incident_digest)
    # Complete the other side after the source transaction.  If a process dies
    # here, the next identical request is idempotent and repairs this link.
    from conquest.merchants import delivery_reservation as reservations
    source_receipt=status(journal,key)
    reservations.operator_override(ui.runtime.journal,character,key,
                                    operator_confirmed=operator_confirmed,
                                    confirmation_reference=confirmation_reference,
                                    operator=operator,fresh_evidence=fresh,
                                    cleanup_pending=source_receipt.get('cleanup_pending') if source_receipt else None,
                                    intent=saved_intent(journal,key))
    receipt=source_receipt
    clear_settled_attention(ui,key,receipt)
    return {'request_id':key,'running':False,'receipt':receipt,'rechecked':fresh,
            'reservation_phase':ui.runtime.journal.get(character,'delivery_reservation',{}).get('phase')}


def repair_operator_link(ui, journal, key, receipt):
    """Finish a source/reservation override split after an app restart."""
    if not receipt or receipt.get('phase')!='operator_overridden':return receipt
    character=receipt['character'];reservation=ui.runtime.journal.get(character,'delivery_reservation',{}) or {}
    if reservation.get('request_id')==key and reservation.get('phase')=='operator_overridden':return receipt
    override=receipt.get('operator_override') or {}
    from conquest.merchants import delivery_reservation as reservations
    reservations.operator_override(ui.runtime.journal,character,key,
        operator_confirmed=True,confirmation_reference=override.get('confirmation_reference'),
        operator=override.get('operator'),fresh_evidence=override.get('fresh_evidence'),
        cleanup_pending=receipt.get('cleanup_pending'),intent=saved_intent(journal,key))
    return receipt


def saved_intent(journal,key):
    with journal.db() as db:
        row=db.execute('SELECT before_json FROM transactions WHERE id=?',(key,)).fetchone()
    if not row:raise ValueError('Unknown farmer delivery')
    return json.loads(row[0])


def prepare_new(ui,journal,key,character,uids,action,origin):
    """Qualify and persist a full intent after an admission receipt exists."""
    try:
        window=getattr(getattr(ui,'runtime',None),'delivery_window',None)
        if window and window!=key:
            raise ValueError('Delivery request ID must match its reserved work window')
        from conquest.merchants.farmer_preferences import permits_new_delivery
        from conquest.merchants.farmer_identity import ui_character
        permits_new_delivery(ui_character(ui))
        policy=read_json('profiles/merchant-deliveries.json')
        from conquest.merchant_loop_acceptance import trial_delivery_permitted
        trial = action == 'delivery-start' and trial_delivery_permitted(ui,key,character,uids,origin)
        if action!='delivery-test' and not trial and (not policy.get('enabled') or not policy.get('parity_verified')):
            raise ValueError('Merchant delivery rollout is not enabled')
        if action=='delivery-test' and len(uids)>5:
            raise ValueError('Supervised delivery test is limited to five items')
        driver=FarmerTradeDriver(ui)
        driver.require_qualified();driver.check()
        if not ui.runtime.enabled(character):raise ValueError('Merchant trading is paused')
        receiver=ui.runtime.controllers.get(character)
        if receiver is None:raise ValueError('Delivery recipient is not connected')
        receiver.driver.require_qualified('trade_request')
        receiver.driver.require_qualified('trade')
        farmer,merchant=driver.read_pair(character)
        if trial:
            from conquest.merchant_loop_acceptance import state, source_checked
            source_checked(farmer,state())
            if (not trial_delivery_permitted(ui,key,character,uids,origin)
                    or merchant['identity']!=state()['merchants'][str(character)]['identity']
                    or merchant['character_uid']!=state()['merchants'][str(character)]['character_uid']):
                raise ValueError('Acceptance merchant or trial permission changed before delivery admission')
        items=[item for item in farmer['inventory'] if item['uid'] in uids]
        if len(items)!=len(uids):raise ValueError('Selected delivery items are not carried')
        journal.update_delivery_admission(key,'admitted',items=items)
        intent=prepare(farmer,merchant,items);intent.update(origin)
        if not journal.begin(key,character,'farmer_delivery',intent,admission=True):
            raise ValueError('Delivery is already prepared; poll its status before recovery')
        return driver,intent
    except Exception as error:
        if status(journal,key) and not journal.pending(character):
            journal.update_delivery_admission(key,'rejected',reason=(
                str(error) if isinstance(error,(ValueError,OSError)) else 'Delivery admission failed'))
        raise


def dispatch(ui,body):
    if body=={'action':'delivery-readiness'}:
        from conquest.merchants.delivery_readiness import describe
        return describe(ui,FarmerTradeDriver)
    action=body.get('action');expected={'action','request_id'}
    if action in ('delivery-start','delivery-test'):
        expected|={'character','uids'}
    valid=(set(body)==expected or action in ('delivery-start','delivery-test')
           and set(body)==expected|{'items'})
    override_fields={'action','request_id','operator_confirmed','confirmation_reference'}
    if action=='delivery-override':
        allowed=override_fields|({'operator'} if 'operator' in body else set())|({'incident_digest'} if 'incident_digest' in body else set())
        if set(body)!=allowed:raise ValueError('Unsupported operator override arguments')
    if action=='delivery-recheck':
        if set(body)!={'action','request_id'}:raise ValueError('Unsupported delivery recheck arguments')
    if action not in ('delivery-start','delivery-test','delivery-status','delivery-reconcile','delivery-cleanup','delivery-recheck','delivery-override') or not valid and action not in ('delivery-recheck','delivery-override'):
        raise ValueError('Unsupported native delivery command')
    key=body['request_id']
    if not isinstance(key,str) or not 1<=len(key)<=100:raise ValueError('Invalid delivery request ID')
    if action in ('delivery-start','delivery-test'):
        guard_protected_assets()
    journal=Journal(JOURNAL)
    if not hasattr(ui,'delivery_workers'):
        ui.delivery_workers={};ui.delivery_errors={}
    if not hasattr(ui,'delivery_admissions'):
        ui.delivery_admissions=set()
    old=status(journal,key)
    worker=ui.delivery_workers.get(key)
    running=bool(worker and worker.is_alive()) or key in ui.delivery_admissions
    if action=='delivery-status':
        if old and old.get('phase')=='operator_overridden':
            try:repair_operator_link(ui,journal,key,old)
            except (ValueError,OSError):pass
        return {'request_id':key,'running':running,'receipt':old,'error':ui.delivery_errors.get(key)}
    if action in ('delivery-recheck','delivery-override'):
        if old is None:raise ValueError('Unknown farmer delivery')
        if running:raise ValueError('Wait for the delivery worker before rechecking this incident')
        if action=='delivery-recheck':
            from conquest.merchants.delivery_bridge import pair
            farmer,merchant=pair(ui,old['character'])
            fresh={'observed_at':max(farmer.get('timestamp',0),merchant.get('timestamp',0)),
                   'farmer':farmer,'merchant':merchant}
            digest=hashlib.sha256(json.dumps(fresh,sort_keys=True).encode()).hexdigest()
            journal.step(key,'operator_recheck','observed',{'evidence_digest':digest,
                                                            'observed_at':fresh['observed_at']})
            return {'request_id':key,'running':False,'receipt':status(journal,key),
                    'rechecked':fresh,'evidence_digest':digest,
                    'incident_digest':journal.original_evidence_digest(key)}
        return operator_override(ui,journal,key,
                                 operator_confirmed=body.get('operator_confirmed'),
                                 confirmation_reference=body.get('confirmation_reference'),
                                 operator=body.get('operator'),incident_digest=body.get('incident_digest'))
    if action in ('delivery-reconcile','delivery-cleanup'):
        if old is None:raise ValueError('Unknown farmer delivery')
        if running:return {'request_id':key,'running':True,'receipt':old}
        if action=='delivery-cleanup' and old['next_action']!='cleanup_trade_modal':
            raise ValueError('Delivery has no verified empty trade cleanup pending')
        if action=='delivery-reconcile' and old['phase']=='operator_overridden':
            repair_operator_link(ui,journal,key,old)
            clear_settled_attention(ui,key,old)
            return {'request_id':key,'running':False,'receipt':status(journal,key)}
        if action=='delivery-reconcile' and (old['phase']=='aborted'
                or (old['phase']=='verified' and old['next_action']=='release_route')):
            clear_settled_attention(ui,key,old)
            return {'request_id':key,'running':False,'receipt':old}
        character=old['character'];uids=old['uids']
    else:
        character=character_name(body['character']);uids=body['uids']
    if (not isinstance(uids,list) or not 1<=len(uids)<=20
            or any(type(uid) is not int or uid<=0 for uid in uids) or len(set(uids))!=len(uids)):
        raise ValueError('Select distinct delivery item UIDs')
    if old and action!='delivery-reconcile' and (old['character']!=character or sorted(old['uids'])!=sorted(uids)):
        raise ValueError('Delivery request ID reused for another batch')
    if old and action in ('delivery-start','delivery-test'):
        raise ValueError('Delivery already exists; use delivery-reconcile for read-only recovery')
    if running:return {'request_id':key,'running':True,'receipt':old}
    if not old:
        from conquest.character_context import current,farmer_name
        context=current();grant=getattr(ui,'grant',None) or {}
        origin={'operation_id':key,'town_visit_id':grant.get('town_visit_id'),
                'visit_id':grant.get('visit_id'),
                'farmer_profile_id':grant.get('farmer_profile_id') or
                    (context.profile.id if context else farmer_name())}
        requested=body.get('items')
        if requested is not None:
            if (not isinstance(requested,list) or sorted(exact_items(requested))!=sorted(uids)):
                raise ValueError('Delivery item fingerprints do not match requested UIDs')
        # A start response can be lost while synchronous qualification is
        # still running. Keep status non-terminal until either the durable
        # source transaction exists or admission is explicitly rejected.
        ui.delivery_admissions.add(key)
        try:
            journal.admit_delivery(key,character,uids,origin,requested)
            driver,intent=prepare_new(ui,journal,key,character,uids,action,origin)
        finally:
            ui.delivery_admissions.discard(key)
    else:
        from types import SimpleNamespace
        from conquest.merchants.delivery_bridge import pair
        driver=SimpleNamespace(read_pair=lambda name:pair(ui,name))
    ui.delivery_errors.pop(key,None)
    fence=getattr(getattr(ui,'coordinator',None),'fence',None)
    token=fence.capture() if fence is not None else None
    if action=='delivery-cleanup' and (token is None or token.request_id is None
            or token.scope!='market_visit'):
        raise ValueError('Delivery cleanup requires a current Market visit input grant')
    if action=='delivery-cleanup':
        cleanup_origin=saved_intent(journal,key)
        if (token.request_id!=key
                or token.farmer_profile_id!=cleanup_origin.get('farmer_profile_id')
                or (getattr(ui,'grant',None) or {}).get('visit_id')!=cleanup_origin.get('visit_id')
                or (getattr(ui,'grant',None) or {}).get('town_visit_id')!=cleanup_origin.get('town_visit_id')):
            raise ValueError('Delivery cleanup grant does not match the saved operation origin')
    def work():
        binding=(fence.bind_worker(token,action_capable=action=='delivery-cleanup' or not bool(old))
                 if fence is not None else nullcontext())
        try:
            with binding:
                from conquest.merchants.sales import qualified_delivery_receipts
                options={'sale_receipts':lambda intent,farmer,merchant:
                         qualified_delivery_receipts(ui.runtime.journal,intent,merchant)}
                if fence is not None:options['mark_read_only']=fence.mark_read_only
                transaction=DeliveryTransaction(journal,driver,**options)
                if action=='delivery-cleanup':
                    transaction.key=key;cleanup_intent=saved_intent(journal,key)
                    from conquest.merchants.delivery_bridge import pair
                    farmer,merchant=pair(ui,character)
                    sales=transaction.sale_receipts(cleanup_intent,farmer,merchant)
                    result=reconciliation_outcome(cleanup_intent,farmer,merchant,
                        trace=transaction.trace(key),sale_receipts=sales)
                    if (result['outcome']!=old['evidence_outcome']
                            or not result['cleanup_pending']):
                        raise ReconciliationBlocked('Terminal delivery cleanup evidence changed')
                    from conquest.merchants.delivery_cleanup import cleanup_empty_trade
                    cleanup_empty_trade(ui,character,{'farmer':farmer,'merchant':merchant},
                                        operation=transaction,check=ui.coordinator.check)
                    if fence is not None:fence.mark_read_only()
                    if old['phase']=='verified':transaction.finish_receiver(key,cleanup_intent)
                elif old:transaction.recover(key)
                else:transaction.execute(key,character,intent)
                receipt=status(journal,key)
                clear_settled_attention(ui,key,receipt)
                if hasattr(driver,'report'):
                    ready=receipt['next_action']=='release_route'
                    activity=('Transfer to '+character+' verified; ready to continue the route'
                              if ready and receipt['outcome']=='transferred' else
                              'Transfer to '+character+' reconciled as '+receipt['outcome'].replace('_',' ')
                              +'; next action: '+receipt['next_action'].replace('_',' '))
                    driver.report(activity,'complete' if ready else 'attention')
                if receipt['next_action']=='release_route':
                    attention=ui.runtime.journal.get(character,'attention',{}) or {}
                    if attention.get('kind')=='farmer_delivery' and attention.get('request_id')==key:
                        ui.runtime.journal.set(character,'attention',None)
        except Exception as error:
            from conquest.capture import CaptureUnavailable
            current=status(journal,key)
            if (isinstance(error,CaptureUnavailable) and current
                    and current['phase']=='prepared' and not current['action_trace_initialized']):
                current=reject_worker_admission(journal,key)
            note=str(error) if isinstance(error,(ValueError,OSError)) else 'Native delivery failed; reconcile before retrying'
            ui.delivery_errors[key]=note
            if current and current['outcome']=='retryable_before_input':
                if hasattr(driver,'report'):
                    driver.report('Transfer to '+character+' did not start; it is safe to retry','attention')
                ui.runtime.journal.event(character,'delivery_not_started',request_id=key,
                                         note='Captured input grant changed before worker entry')
                return
            if hasattr(driver,'report'):
                driver.report('Transfer to '+character+' paused: '+note+'. Checking the saved transaction before continuing.','attention')
            if not (isinstance(error,CaptureUnavailable) and current and current['phase']=='aborted'):
                ui.runtime.journal.set(character,'attention',{'kind':'farmer_delivery','request_id':key,'note':note})
            ui.runtime.journal.event(character,'delivery_needs_reconciliation',request_id=key,note=note)
    worker=threading.Thread(target=work,daemon=True,name='farmer-delivery')
    ui.delivery_workers[key]=worker;worker.start()
    return {'request_id':key,'running':True,'receipt':status(journal,key)}
