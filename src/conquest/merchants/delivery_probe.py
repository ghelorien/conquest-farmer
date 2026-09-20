"""Explicit live qualification of a farmer trade request; no item confirmation."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import threading
import time

from conquest.capture import CaptureUnavailable
from conquest.discord_notify import read_json,write_json
from conquest.merchants.delivery import eligible,prepare,exact_items
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.journal import character_name

from conquest.character_context import state_path, farmer_name
JOURNAL=Path(state_path('reports/merchants/delivery-request-probe.json'))
TERMINAL={'delivery_verified','cancel_verified','operator_overridden'}
RECOVERABLE={'prepared','targeting_submitted','targeting_verified','request_submitted',
             'request_verified','accept_submitted','trade_open_verified','placement_submitted',
             'offer_verified','farmer_confirm_submitted','farmer_confirm_verified',
             'merchant_confirm_submitted','cancel_submitted','aborted_no_trade_observed'}


def write_probe(path,state):
    """Persist each one-shot input boundary before its native action."""
    write_json(path,state)
    with Path(path).open('r+b') as stream:
        stream.flush()
        os.fsync(stream.fileno())


def read_probe(*,read_only=False):
    # read_json intentionally tolerates malformed diagnostic files. A missing
    # transaction receipt is different: corruption must never authorize input.
    try:
        state=json.loads(JOURNAL.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError,ValueError) as error:
        raise ValueError('Trade probe evidence is unreadable; reconcile before input') from error
    if not isinstance(state,dict):
        raise ValueError('Trade probe evidence is unreadable; reconcile before input')
    intent_path=Path(str(JOURNAL)+'.override-intent.json')
    if intent_path.exists():
        if read_only:raise ValueError('Trade probe disposition requires separate recovery')
        try:
            override=json.loads(intent_path.read_text(encoding='utf-8'))
            if (not isinstance(override,dict) or not isinstance(override.get('record'),dict)
                    or override.get('terminal_phase')!='operator_overridden'
                    or not isinstance(override.get('audit'),str) or not override['audit']):
                raise ValueError('Invalid operator disposition intent')
        except (OSError,ValueError) as error:
            raise ValueError('Trade probe override evidence is unreadable; no input is authorized') from error
        from conquest.recovery_override import read_recovered
        state=read_recovered(JOURNAL)
    return state


def previous_probe():
    state=read_probe()
    if state is not None and state.get('phase') not in TERMINAL:
        raise ValueError('Reconcile existing trade request probe before further input')
    return state


def archive_probe(state):
    """Keep terminal proof when a supervised operator starts a fresh probe."""
    if state is None:return
    raw=json.dumps(state,sort_keys=True,separators=(',',':')).encode('utf-8')
    digest=hashlib.sha256(raw).hexdigest()
    path=JOURNAL.parent/'delivery-request-probe-audit'/f'{digest}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    def verify_and_sync():
        # A prior attempt may have published complete bytes but failed its
        # final flush. Equality alone is not a durable archival receipt.
        with path.open('r+b') as stream:
            if stream.read()!=raw:
                raise ValueError('Trade probe archive differs from its historical receipt')
            stream.flush();os.fsync(stream.fileno())
    if path.exists():
        verify_and_sync()
        return path
    fd,name=tempfile.mkstemp(prefix=digest+'.',suffix='.tmp',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as out:
            if out.write(raw)!=len(raw):raise OSError('Incomplete trade probe archive write')
            out.flush();os.fsync(out.fileno())
        # Same-volume hard-link publication is atomic and never overwrites an
        # existing receipt. A crash while writing the private temporary file
        # cannot leave a partial final record that poisons future retries.
        try:os.link(temporary,path)
        except FileExistsError:pass
        verify_and_sync()
    finally:
        temporary.unlink(missing_ok=True)
    return path


def recovery_available(ui):
    if (getattr(ui,'delivery_probe_thread',None) and ui.delivery_probe_thread.is_alive()
            or any(worker.is_alive() for worker in getattr(ui,'delivery_workers',{}).values())):
        raise ValueError('Wait for delivery input to finish before rechecking its evidence')


def recovery_evidence(ui,state):
    """Current ownership is planning evidence, never proof of an old outcome."""
    from conquest.merchants.manual_sessions import canonical_ownership
    from conquest.recovery_override import evidence_digest
    character=character_name(state.get('character'))
    intent=state.get('intent',{})
    farmer,merchant=pair(ui,character,farmer_preflight=True)
    proof={}
    now=time.time()
    for role,snapshot in (('farmer',farmer),('merchant',merchant)):
        old=intent.get(role,{})
        proof[role]=canonical_ownership(snapshot)
        if (any(snapshot.get(key)!=old.get(key) for key in ('character','character_uid','server'))
                or not 0<=now-snapshot['timestamp']<=5 or snapshot.get('hp',0)<=0):
            raise ValueError('Recheck requires fresh memory of the original named characters')
    return {'observed_at':now,'farmer':farmer,'merchant':merchant,
            'ownership_digest':evidence_digest(proof),'historical_outcome':'unknown'}


def recheck(ui):
    """Preview the exact incident and fresh pair without input or disposition."""
    from conquest.recovery_override import evidence_digest
    recovery_available(ui)
    state=read_probe()
    if not state or state.get('phase') not in RECOVERABLE:
        raise ValueError('No known unfinished trade probe is available for operator review')
    digest=evidence_digest(state)
    fresh=recovery_evidence(ui,state)
    if evidence_digest(read_probe())!=digest:
        raise ValueError('Trade probe changed during recheck')
    preview={'incident_digest':digest,'fresh_evidence':fresh,
             'evidence_digest':fresh['ownership_digest'],'original_phase':state['phase']}
    write_probe(Path(str(JOURNAL)+'.recheck.json'),preview)
    return preview


def operator_override(ui,*,operator_confirmed=False,confirmation_reference=None,
                      incident_digest=None,operator=None):
    """Explicitly close a legacy/uncertain probe without inventing a receipt."""
    from conquest.recovery_override import evidence_digest,operator_override as close
    recovery_available(ui)
    if (operator_confirmed is not True or not isinstance(incident_digest,str)
            or not incident_digest or confirmation_reference!=incident_digest):
        raise ValueError('Confirm the exact previewed trade-probe incident digest')
    state=read_probe()
    if state and state.get('phase')=='operator_overridden':
        previous=state.get('operator_override',{})
        if (previous.get('confirmation_reference')==confirmation_reference
                and previous.get('original_evidence_digest')==incident_digest):
            return {'phase':'operator_overridden','historical_outcome':'unknown',
                    'incident_digest':incident_digest,'replan_required':True}
        raise ValueError('Trade probe was already overridden with a different confirmation')
    if not state or state.get('phase') not in RECOVERABLE or evidence_digest(state)!=incident_digest:
        raise ValueError('Trade probe incident changed; recheck before overriding')
    preview=read_json(Path(str(JOURNAL)+'.recheck.json'))
    if (preview.get('incident_digest')!=incident_digest
            or not 0<=time.time()-preview.get('fresh_evidence',{}).get('observed_at',0)<=30):
        raise ValueError('A fresh trade-probe recheck is required before overriding')
    fresh=recovery_evidence(ui,state)
    if fresh['ownership_digest']!=preview.get('evidence_digest'):
        raise ValueError('Current ownership changed; preview the trade probe again')
    # The historical text "no trade observed" did not retain after-snapshots.
    # Preserve it verbatim, but disposition remains an explicit unknown outcome.
    archive_probe(state)
    result=close(JOURNAL,pending_phases=RECOVERABLE,operator_confirmed=True,
        confirmation_reference=confirmation_reference,incident_digest=incident_digest,
        operator=operator,fresh_evidence=fresh,incident='delivery-request-probe')
    write_probe(JOURNAL,result)
    return {'phase':'operator_overridden','historical_outcome':'unknown',
            'incident_digest':incident_digest,'replan_required':True}


def selected_intent(farmer,merchant,uids):
    """Select one exact ordinary +1 or MeteorScroll; never infer a batch/value."""
    if (not isinstance(uids,list) or len(uids)!=1
            or type(uids[0]) is not int or uids[0]<=0):
        raise ValueError('Select exactly one carried +1 equipment or MeteorScroll UID for qualification')
    if any(item.get('type_id')==1088001 for item in farmer['inventory']):
        raise ValueError('Bank loose Meteors before the supervised trade probe')
    items=[item for item in farmer['inventory'] if item.get('uid')==uids[0]]
    if len(items)!=1:
        raise ValueError('The selected qualification item is no longer carried')
    item=items[0];kind=item.get('type_id')
    equipment=(type(kind) is int and 100000<=kind<600000 and kind%10!=9
               and type(item.get('plus')) is int and item['plus']==1)
    scroll=(type(kind) is int and kind==720027
            and all(type(item.get(key)) is int and item[key]==value
                    for key,value in (('plus',0),('gem1',0),('gem2',0),('quantity',1))))
    if (not eligible(item) or not (equipment or scroll)
            or item.get('gem1')!=0 or item.get('gem2')!=0 or item.get('quantity')!=1):
        raise ValueError('Qualification requires one unbound, unsocketed, non-Super +1 equipment item or one exact MeteorScroll')
    return prepare(farmer,merchant,items)


def unchanged(intent,farmer,merchant):
    prepare(farmer,merchant,intent['items'])
    for role,current in (('farmer',farmer),('merchant',merchant)):
        old=intent[role]
        if (current['identity']!=old['identity'] or current['character_uid']!=old['character_uid']
                or current['position']!=old['position'] or current['silver']!=old['silver']
                or exact_items(current['inventory'])!=exact_items(old['inventory'])
                or exact_items(current['booth'])!=exact_items(old['booth'])):
            raise ValueError('Trade probe participants, stock or position changed')


def start(ui,character,*,uids=None):
    with ui.coordinator.lock:
        return _start(ui,character,uids=uids)


def _start(ui,character,*,uids=None):
    from conquest.merchants.farmer_preferences import permits_new_delivery
    from conquest.merchants.farmer_identity import ui_character
    permits_new_delivery(ui_character(ui))
    character=character_name(character)
    if getattr(ui,'delivery_probe_thread',None) and ui.delivery_probe_thread.is_alive():
        raise ValueError('Trade request probe is running')
    old=previous_probe()
    from conquest.merchants.delivery_abort_probe import require_rebaseline
    require_rebaseline(ui,old)
    ui.coordinator.check()
    if not ui.safe_to_yield() or ui.app.control.snapshot()['enabled']:
        raise ValueError('Trade probe requires stopped farming and released input')
    f,m=pair(ui,character)
    intent=selected_intent(f,m,uids)
    from conquest.merchants.approach import within_delivery_probe_range
    if not within_delivery_probe_range(f['position'],m['position']):
        raise ValueError('Approach the memory-identified merchant before the trade probe')
    revision=ui.app.control.snapshot()['revision']
    state={'phase':'prepared','character':character,'intent':intent,'started_at':time.time(),
           'selected_uids':list(uids),
           'target_profile_id':getattr(character,'profile_id',str(character))}
    from conquest.character_context import current
    context=current()
    state['farmer_profile_id']=context.profile.id if context and context.profile.role=='Farmer' else 'Farmer'
    archive_probe(old)
    write_probe(JOURNAL,state)
    def work():
        try:run(ui,intent,revision,state)
        except Exception as error:
            state.update(error=str(error),finished_at=time.time())
            write_probe(JOURNAL,state)
    ui.delivery_probe_thread=threading.Thread(target=work,name='delivery-request-probe',daemon=True)
    ui.delivery_probe_thread.start()
    return {'started':True,'character':character,'uids':[i['uid'] for i in intent['items']]}


def run(ui,intent,revision,state):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    from conquest.memory_shop import MemoryGui
    from conquest.merchants.memory import MerchantMemory
    from conquest.merchants.driver import wait_hover_validation
    from conquest.merchants.trade_controls import trade_button,targeting_state
    from conquest.merchants.farmer_trade import recipient_record,recipient_binding
    import ctypes
    deadline=time.monotonic()+15
    observer=ui.app.observer;memory=MerchantMemory(observer)
    character=intent['merchant']['character']
    def check():
        from conquest.merchants.farmer_preferences import permits_new_delivery
        from conquest.merchants.farmer_identity import ui_character
        permits_new_delivery(intent['farmer']['character'])
        ui.coordinator.check()
        control=ui.app.control.snapshot()
        if (ui.closed or ui.app.closing or control['enabled'] or control.get('paused')
                or control['revision']!=revision or not ui.safe_to_yield()
                or time.monotonic()>=deadline
                or ctypes.windll.user32.GetAsyncKeyState(0x7a)&0x8000
                or ctypes.windll.user32.GetAsyncKeyState(0x7b)&0x8000):
            raise CaptureUnavailable('Trade request probe stopped or expired')
    def save(phase,**fields):
        state.update(phase=phase,updated_at=time.time(),**fields);write_probe(JOURNAL,state)
    with ui.coordinator.lease('Farmer',purpose='delivery_request_probe'),physical_coordinates():
        from conquest.merchants.delivery_farmer_surface import prepare as present,verify_stage_pair
        presentation=present(ui,state,purpose='delivery_request_probe',revision=revision,deadline=deadline)
        f,m=pair(ui,character);verify_stage_pair(intent,f,m,stage='open');presentation()
        from conquest.focus_recovery import activate_client
        if not activate_client(observer.operations.target.hwnd,f['identity']):
            raise ValueError('Farmer focus unavailable; no request input sent')
        check()
        size=observer.operations.target.snapshot()['client_size']
        gui=memory.gui.viewport_size()
        if size!=gui:raise ValueError('Trade probe requires matching native and GUI dimensions')
        profile=read_json(state_path('reports/merchants/trade-layout-candidate.json'))
        profile={**profile,'gui_size':gui}
        if profile.get('client_sha256')!=observer.adapter.expected_sha256:
            raise ValueError('Trade layout build changed')
        f,m=pair(ui,character);unchanged(intent,f,m)
        recipient=recipient_record(observer,profile,m,farmer=f)
        point=trade_button(MemoryGui(observer.adapter))
        if targeting_state(observer.adapter)['current']!=16:
            raise ValueError('Farmer already has a targeting action active')
        def before_hud():
            check()
            f,m=pair(ui,character);unchanged(intent,f,m)
            if trade_button(MemoryGui(observer.adapter))!=point:
                raise ValueError('Trade button moved')
            window=next(w for w in f['windows'] if w['name']=='##Control')
            # The native HUD table pushes its ID before emitting Trade.
            # trade_button above verifies this exact live six-column table.
            memory.gui.assert_hovered(window,'Trade',seeds=[0x02a99238])
        save('targeting_submitted',hud_point=point)
        foreground_click(observer.operations.target,*point,tuple(size),require_foreground=True,
            before_press=lambda:wait_hover_validation(before_hud,check))
        until=time.monotonic()+1
        while not targeting_state(observer.adapter)['targeting_trade']:
            check()
            if time.monotonic()>=until:raise ValueError('Trade targeting transition not verified')
            time.sleep(.03)
        save('targeting_verified')
        f,m=pair(ui,character);unchanged(intent,f,m)
        recipient=recipient_record(observer,profile,m,farmer=f,targeting=True)
        binding=recipient_binding(recipient)
        def before_peer():
            check()
            f,m=pair(ui,character);unchanged(intent,f,m)
            fresh_recipient=recipient_record(observer,profile,m,farmer=f,targeting=True)
            if recipient_binding(fresh_recipient)!=binding:
                raise ValueError('Trade recipient changed before request')
        save('request_submitted',recipient=recipient)
        foreground_click(observer.operations.target,*recipient['point'],tuple(size),require_foreground=True,
                         before_press=before_peer)
        while True:
            check()
            f,m=pair(ui,character)
            if m.get('request'):
                if m['request'].get('participant')!=farmer_name():
                    raise ValueError('Merchant received a different trade request')
                save('request_verified',farmer_after=f,merchant_after=m)
                return
            time.sleep(.1)
