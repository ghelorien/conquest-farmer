"""Operator-confirmed one-shot abort of an exact unaccepted supervised offer.

An immediate attribution-free baseline tolerates historical merchant booth
sales, but never proves delivery. No submitted close is ever replayed.
"""
from copy import deepcopy
from pathlib import Path
import json
import threading
import time

from conquest.capture import CaptureUnavailable
from conquest.merchants import delivery_probe as probe
from conquest.merchants import delivery_abort_sessions as sessions
from conquest.merchants.delivery import exact_items, validate_snapshot
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.manual_sessions import canonical_ownership, _fresh
from conquest.recovery_override import evidence_digest as digest

PURPOSE = 'delivery_probe_abort'


def path(suffix=''):
    return Path(str(probe.JOURNAL)+'.abort'+suffix+'.json')


def read(suffix=''):
    try:value=json.loads(path(suffix).read_text(encoding='utf-8'))
    except FileNotFoundError:return None
    if not isinstance(value,dict):raise ValueError('Abort journal is unreadable')
    return value


def save(value, suffix=''):
    probe.write_probe(path(suffix),value)


def _prior(state):
    previous=read()
    if previous and previous.get('probe_digest')==digest(state):
        if previous.get('phase')!='abort_prepared':
            raise ValueError('Existing submitted abort is one-shot; reconcile without another close')
        return previous
    return None


def _archive_prepared(previous):
    if previous is None:return
    probe.archive_probe(previous)  # Atomic publish, verify existing bytes, fsync.
    # Do not remove the durable old attempt until a new confirmed one replaces
    # it. A restart can always see whether any close was possibly submitted.


def _profiles(ui, state):
    if (state.get('target_profile_id') != ui.runtime.manual_target(state['character'])
            or state.get('farmer_profile_id') != ui.runtime.manual_target('Farmer')):
        raise ValueError('Abort profile binding changed')


def ownership(state, farmer, merchant, *, now):
    """Qualify current abort terms without relaxing ordinary delivery proof."""
    from conquest.merchants.delivery_probe_ownership import _context, _saved_boundary, _local_trade
    if state.get('phase') != 'offer_verified':raise ValueError('Only an offer_verified probe may be aborted')
    intent, original, items = _context(state,state['character'],state['target_profile_id'],state['farmer_profile_id'],now=now)
    _saved_boundary(state,intent,original,items)
    if len(items) != 1:raise ValueError('Abort qualification requires exactly one selected probe item')
    result = {}
    for role,snapshot in (('farmer',farmer),('merchant',merchant)):
        _fresh(snapshot,now)
        allowed = deepcopy(original)
        if role == 'merchant':
            # Drift is explicitly unknown history, never a sale/delivery proof.
            current=canonical_ownership(snapshot,require_closed=False)
            allowed[role]['silver']=current['silver'];allowed[role]['booth']=current['booth']
        proof = _local_trade(state,intent,allowed,items,role,snapshot,now=now)
        if set(items) & set(exact_items(snapshot['booth'])):
            raise ValueError('Selected probe item unexpectedly appears in a booth')
        if role == 'merchant' and set(items) & set(exact_items(snapshot['inventory'])):
            raise ValueError('Selected probe item already belongs to the merchant')
        result[role]={**proof,'map_id':snapshot['map_id'],'position':snapshot['position']}
    return result


def _same_open(abort, farmer, merchant, *, now):
    current=ownership(abort['probe'],farmer,merchant,now=now)
    if digest(current) != abort['baseline_digest']:
        raise ValueError('Immediate abort baseline changed; no close is authorized')
    return current


def closed(abort, farmer, merchant, *, now):
    """Only trade closure and exact offer-to-source restoration may change."""
    wanted=exact_items(abort['probe']['intent']['items'])
    result={}
    for role,snapshot in (('farmer',farmer),('merchant',merchant)):
        _fresh(snapshot,now);current=canonical_ownership(snapshot)
        before=canonical_ownership(abort['baseline'][role],require_closed=False)
        validate_snapshot(snapshot,before['character'],now)
        if (snapshot['map_id'] != abort['baseline'][role]['map_id']
                or snapshot['position'] != abort['baseline'][role]['position']):
            raise ValueError('Abort participant moved')
        expected=deepcopy(before);expected.update(request=None,trade=None)
        inventory={i['uid']:i for i in expected['inventory']}
        if role=='farmer':
            inventory.update({i['uid']:i for i in canonical_ownership(abort['probe']['intent']['farmer'])['inventory'] if i['uid'] in wanted})
            expected['inventory']=sorted(inventory.values(),key=lambda item:item['uid'])
        if current != expected:
            raise ValueError('Closed abort ownership differs from the immediate baseline')
        if role=='merchant' and set(wanted) & (set(exact_items(snapshot['inventory'])) | set(exact_items(snapshot['booth']))):
            raise ValueError('Aborted item is present on the merchant')
        result[role]=current
    return result


def _idle(ui):
    probe.recovery_available(ui)
    ui.coordinator.check()
    c=ui.app.control.snapshot()
    if ui.closed or ui.app.closing or c['enabled'] or c.get('paused') or not ui.safe_to_yield():
        raise CaptureUnavailable('Abort requires stopped farming and a safe input handoff')
    return c


def recheck(ui):
    with ui.coordinator.lock:
        control=_idle(ui);state=probe.read_probe();_profiles(ui,state)
        previous=_prior(state)
        f,m=pair(ui,state['character'],farmer_preflight=True);now=time.time()
        proof=ownership(state,f,m,now=now)
        bound=sessions.binding(ui.runtime,state)
        if digest(probe.read_probe()) != digest(state):raise ValueError('Probe changed during abort preview')
        preview=dict(probe=state,probe_digest=digest(state),baseline={'farmer':f,'merchant':m},
            baseline_digest=digest(proof),sessions=bound,control_revision=control['revision'],
            merchant_enabled=ui.runtime.enabled(state['character']),
            created_at=now,expires_at=now+30,historical_outcome='unknown',sales_receipt=False,delivery_receipt=False)
        preview['prior_prepared_digest']=digest(previous) if previous else None
        preview['confirmation_reference']=digest(preview)
        save(preview,'-preview')
        return preview


def _confirmed(value, confirmation_reference, operator_confirmed, operator, *, allow_expired=False):
    if (operator_confirmed is not True or not isinstance(operator,str) or not operator.strip()
            or not isinstance(value,dict) or value.get('confirmation_reference') != confirmation_reference
            or digest({k:v for k,v in value.items() if k!='confirmation_reference'}) != confirmation_reference
            or time.time()<value['created_at'] or not allow_expired and time.time()>value['expires_at']):
        raise ValueError('Confirm the exact fresh operator preview digest')


def start(ui, *, confirmation_reference, operator_confirmed=False, operator):
    with ui.coordinator.lock:
        control=_idle(ui);preview=read('-preview')
        _confirmed(preview,confirmation_reference,operator_confirmed,operator)
        state=probe.read_probe();_profiles(ui,state)
        if digest(state)!=preview['probe_digest'] or control['revision']!=preview['control_revision']:
            raise ValueError('Probe or control intent changed after abort preview')
        if ui.runtime.enabled(state['character'])!=preview['merchant_enabled']:
            raise ValueError('Merchant enablement changed after abort preview')
        previous=_prior(state)
        if (digest(previous) if previous else None)!=preview.get('prior_prepared_digest'):
            raise ValueError('Previous abort attempt changed after preview')
        f,m=pair(ui,state['character'],farmer_preflight=True);_same_open(preview,f,m,now=time.time())
        refreshed=sessions.refresh_binding(ui.runtime,state,preview['sessions'])
        f,m=pair(ui,state['character'],farmer_preflight=True);_same_open(preview,f,m,now=time.time())
        abort={**preview,'phase':'abort_prepared','operator':operator.strip(),
               'baseline':{'farmer':f,'merchant':m},'prepared_at':time.time(),
               'sessions':refreshed,'confirmed_session_history_digest':preview['sessions']['digest']}
        _archive_prepared(previous)
        save(abort)
        def work():
            try:run(ui,abort)
            except Exception as error:
                # Preserve submitted state exactly. Failure never re-arms input.
                if abort.get('phase') in ('abort_prepared','cancel_submitted') and read()==abort:
                    abort.update(error=str(error),failed_at=time.time());save(abort)
        ui.delivery_probe_thread=threading.Thread(target=work,daemon=True,name='delivery-probe-abort')
        ui.delivery_probe_thread.start()
        return {'started':True,'phase':'abort_prepared','confirmation_reference':confirmation_reference}


def _authorization(ui, abort, expected, *, submitted):
    import ctypes
    c=ui.app.control.snapshot();state=probe.read_probe();_profiles(ui,abort['probe'])
    if (ui.closed or ui.app.closing or c['enabled'] or c.get('paused')
            or c['revision']!=abort['control_revision'] or ui.coordinator.stopped
            or ui.runtime.enabled(abort['probe']['character'])!=abort['merchant_enabled']
            or ui.coordinator.manual_active() or not ui.safe_to_yield()
            or not abort['prepared_at'] <= time.time() <= abort['expires_at']
            or digest(state)!=abort['probe_digest'] or digest(read())!=expected()
            or abort['phase'] != ('cancel_submitted' if submitted() else 'abort_prepared')
            or getattr(ui.runtime,'delivery_window',None) or getattr(ui.runtime,'refill_window',None)
            or abort['probe']['character'] in getattr(ui.runtime,'refilling',{})):
        raise CaptureUnavailable('One-shot abort authority expired or changed')
    if any(ctypes.windll.user32.GetAsyncKeyState(k)&0x8000 for k in (0x7a,0x7b)):
        raise CaptureUnavailable('Probe abort stopped by F11/F12')
    binding=sessions.check_binding(ui.runtime,abort['sessions'])
    if binding!=abort['sessions'] or sorted(ui.coordinator.manual_sessions.values(),key=lambda row:row['id'])!=binding['holds']:
        raise CaptureUnavailable('Abort-bound manual holds changed')
    return {abort['probe']['target_profile_id'],abort['probe']['farmer_profile_id']}


def lease_authorized(ui, character):
    try:
        c=ui.coordinator
        if c.purpose!=PURPOSE or not c.probe_abort_authorized(ui.runtime.manual_target(character)):return False
        abort=read();state=abort['probe']
        if str(character)!=state['character']:return False
        observer=ui.runtime.observers.get(character)
        controller=ui.runtime.controllers.get(character)
        if (observer is None or observer.adapter.identity!=state['intent']['merchant']['identity']
                or controller is None or controller.driver.observer is not observer
                or controller.driver.target is not observer.operations.target
                or type(observer.operations.target.hwnd) is not int or observer.operations.target.hwnd<=0):return False
        observer.adapter.assert_identity()
        from conquest.merchants.delivery_reservation import active
        return not active(ui.runtime.journal,character) and not ui.runtime.journal.pending(character)
    except (ValueError,OSError,KeyError,TypeError,AttributeError):return False


def run(ui, abort):
    import ctypes
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    from conquest.merchants.driver import wait_hover_validation
    from conquest.merchants.empty_delivery_cancel import control
    if abort.get('phase')!='abort_prepared' or read()!=abort:
        raise ValueError('A submitted abort cannot send another close')
    character=abort['probe']['character'];driver=ui.runtime.controllers[character].driver
    expected=[digest(abort)];submitted=[False]
    def authorize():return _authorization(ui,abort,lambda:expected[0],submitted=lambda:submitted[0])
    def check():
        ui.coordinator.check();authorize()
        if any(ctypes.windll.user32.GetAsyncKeyState(k)&0x8000 for k in (0x7a,0x7b)):
            raise CaptureUnavailable('Probe abort stopped by F11/F12')
        if any(ui.coordinator.manual_session_blocked(target,purpose=PURPOSE) for target in authorize()):
            raise CaptureUnavailable('Abort participant is fenced')
    with ui.coordinator.lock:
        f,m=pair(ui,character,farmer_preflight=True);_same_open(abort,f,m,now=time.time())
        with ui.coordinator.probe_abort_scope(authorize):
            check()
            with ui.coordinator.lease(character,purpose=PURPOSE),physical_coordinates():
                check();f,m=pair(ui,character,farmer_preflight=True);_same_open(abort,f,m,now=time.time())
                window,point=control(driver,m);size=driver.target.snapshot()['client_size']
                if size!=driver.memory.gui.viewport_size():raise ValueError('Abort viewport changed')
                def guard():
                    check();a,b=pair(ui,character,farmer_preflight=True);_same_open(abort,a,b,now=time.time())
                    if control(driver,b)!=(window,point):raise ValueError('Native trade close control moved')
                    driver.memory.gui.assert_hovered(window,'#CLOSE')
                # The write is flushed before any possible press. The ephemeral
                # capability alone follows this one invocation into submission.
                abort.update(phase='cancel_submitted',submitted_at=time.time(),point=point)
                save(abort);expected[0]=digest(abort);submitted[0]=True
                foreground_click(driver.target,*point,tuple(size),require_foreground=False,
                    before_press=lambda:wait_hover_validation(guard,check))
                deadline=time.monotonic()+5
                while True:
                    check();f,m=pair(ui,character,farmer_preflight=True)
                    if all(s.get('trade') is None and s.get('request') is None for s in (f,m)):
                        closed(abort,f,m,now=time.time());break
                    if time.monotonic()>=deadline:raise ValueError('Abort close is uncertain; read-only reconciliation required')
                    time.sleep(.05)
        return _finish(abort,f,m)


def _finish(abort, farmer, merchant):
    state=probe.read_probe()
    if (digest(state)!=abort['probe_digest'] and not (state.get('phase')=='cancel_verified'
            and state.get('abort_receipt_digest')==digest(abort))):
        raise ValueError('Original probe changed before abort publication')
    if abort['phase']!='cancel_verified':
        closed(abort,farmer,merchant,now=time.time())
        abort.update(phase='cancel_verified',after={'farmer':farmer,'merchant':merchant},verified_at=time.time(),
            outcome='probe_abort_restored',historical_outcome='unknown',sales_receipt=False,delivery_receipt=False)
        save(abort)  # Crash here is resumable without input.
    else:
        closed(abort,abort['after']['farmer'],abort['after']['merchant'],now=abort['verified_at'])
    state=probe.read_probe()
    receipt_digest=digest(abort)
    if state.get('phase')=='cancel_verified' and state.get('abort_receipt_digest')==receipt_digest:
        probe.archive_probe(state)
        return abort
    if digest(state)!=abort['probe_digest']:raise ValueError('Original probe changed before abort publication')
    result={**state,'phase':'cancel_verified','updated_at':abort['verified_at'],
            'abort_receipt_digest':receipt_digest,'abort_receipt':deepcopy(abort),
            'sales_receipt':False,'delivery_receipt':False,'rebaseline_required':True}
    result['original_failure']={key:state[key] for key in ('error','failed_at','finished_at') if key in state}
    result.pop('failed_at',None)
    result.update(error=None,finished_at=abort['verified_at'])
    probe.write_probe(probe.JOURNAL,result)
    probe.archive_probe(result)
    return abort


def reconcile(ui):
    """After uncertainty/restart, this action can only read and publish proof."""
    probe.recovery_available(ui)
    with ui.coordinator.lock:
        abort=read()
        if not abort or abort.get('phase') not in ('cancel_submitted','cancel_verified'):
            raise ValueError('No submitted probe abort is available to reconcile')
        _profiles(ui,abort['probe'])
        if (digest(abort['probe'])!=abort['probe_digest']
                or digest(ownership(abort['probe'],abort['baseline']['farmer'],abort['baseline']['merchant'],
                                    now=abort['prepared_at']))!=abort['baseline_digest']):
            raise ValueError('Saved abort baseline is corrupt')
        if abort['phase']=='cancel_verified':return _finish(abort,None,None)
        f,m=pair(ui,abort['probe']['character'],farmer_preflight=True)
        closed(abort,f,m,now=time.time())
        return _finish(abort,f,m)


def _disposition_ownership(abort, farmer, merchant):
    # Later unrelated booth sales do not erase an already verified abort.
    # Capture them without attribution in the separate disposition preview.
    baseline=deepcopy(abort)
    baseline['baseline']['merchant']['booth']=deepcopy(merchant['booth'])
    baseline['baseline']['merchant']['silver']=merchant['silver']
    return closed(baseline,farmer,merchant,now=time.time())


def disposition_recheck(ui):
    probe.recovery_available(ui)
    with ui.coordinator.lock:
        abort=read();state=probe.read_probe()
        if not abort or abort.get('phase')!='cancel_verified' or state.get('abort_receipt_digest')!=digest(abort):
            raise ValueError('Verified abort receipt required before session disposition')
        _profiles(ui,abort['probe'])
        f,m=pair(ui,abort['probe']['character'],farmer_preflight=True)
        current=_disposition_ownership(abort,f,m)
        preview=dict(abort_receipt_digest=digest(abort),probe_digest=digest(state),
            sessions=sessions.binding(ui.runtime,abort['probe'],closed_after=abort['verified_at']),
            current={'farmer':f,'merchant':m},current_digest=digest(current),
            created_at=time.time(),expires_at=time.time()+30,
            historical_outcome='unknown',sales_receipt=False,delivery_receipt=False)
        preview['confirmation_reference']=digest(preview);save(preview,'-disposition-preview')
        return preview


def disposition_override(ui, *, confirmation_reference, operator_confirmed=False, operator):
    probe.recovery_available(ui)
    with ui.coordinator.lock:
        preview=read('-disposition-preview')
        _confirmed(preview,confirmation_reference,operator_confirmed,operator,allow_expired=True)
        abort=read();_profiles(ui,abort['probe'])
        def recheck():
            if digest(read())!=preview['abort_receipt_digest'] or digest(probe.read_probe())!=preview['probe_digest']:
                raise ValueError('Abort receipt changed after session disposition preview')
        recheck()
        terminal=all(ui.runtime.manual_sessions.get(record['row']['id'])['phase']=='operator_overridden'
                     for record in preview['sessions']['records'])
        if not terminal and time.time()>preview['expires_at']:
            raise ValueError('Session disposition preview expired before confirmation')
        if not terminal:
            f,m=pair(ui,abort['probe']['character'],farmer_preflight=True)
            if digest(_disposition_ownership(abort,f,m))!=preview['current_digest']:
                raise ValueError('Current closed ownership changed after disposition preview')
        try:
            result=sessions.disposition(ui.runtime,abort['probe'],abort,preview['sessions'],
                confirmation_reference=confirmation_reference,operator=operator.strip(),recheck=recheck)
        finally:ui.runtime._sync_manual_fence()
        return {'phase':'operator_overridden','session_ids':result,'rebaseline_required':True,
                'historical_outcome':'unknown','sales_receipt':False,'delivery_receipt':False}


def require_rebaseline(ui, old):
    """Called before archiving or replacing the terminal probe journal."""
    if ui.runtime._manual_rows():raise ValueError('Manual session/rebaseline holds block a fresh probe')
    if not old or not old.get('abort_receipt'):return
    ids=[record['row']['id'] for record in old['abort_receipt']['sessions']['records']]
    with ui.runtime.journal.db() as db:
        for session_id in ids:
            row=db.execute('SELECT phase FROM manual_rebaseline WHERE source_session_id=?',(session_id,)).fetchone()
            terminal=db.execute('SELECT phase FROM manual_sessions WHERE id=?',(session_id,)).fetchone()
            if not row or row[0]!='completed' or not terminal or terminal[0]!='operator_overridden':
                raise ValueError('Abort session disposition and two stable rebaseline samples are required')


def dispatch(ui, body):
    action=body.get('action')
    reads={'probe-delivery-abort-recheck':recheck,'probe-delivery-abort-reconcile':reconcile,
           'probe-delivery-abort-disposition-recheck':disposition_recheck}
    if action in reads:
        if set(body)!={'action'}:raise ValueError('Unsupported abort observation arguments')
        return reads[action](ui)
    writes={'probe-delivery-abort-start':start,'probe-delivery-abort-disposition-override':disposition_override}
    if action not in writes or set(body)!={'action','confirmation_reference','operator_confirmed','operator'}:
        raise ValueError('Unsupported confirmed abort arguments')
    return writes[action](ui,**{k:v for k,v in body.items() if k!='action'})
