"""Cancel an identified empty delivery window without touching offered items."""
import threading
import time
import json
from pathlib import Path
from conquest.character_context import state_path
from conquest.merchants.delivery import exact_items, validate_snapshot
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.booth_panel_probe import CLOSE_CODE
from conquest.merchants.memory import unpack

PATH='reports/merchants/empty-delivery-cancel.json'
PURPOSE='empty_delivery_cancel'
CODE={0x10f0cb:'33d241b861000020488d0d7ec04b00e861cbf0ff4c8d4dd70f14f666490f7ef0488d157ec04b00498bcee85642f6ff',
      0x110339:'807dd7007516e8ec170700488bc8e8e4110700498b06498bceff5070'}

def unchanged(before, current):
    for role,other in (('farmer','merchant'),('merchant','farmer')):
        a,b=before[role],current[role]
        validate_snapshot(b,a['character'],time.time())
        if any(a[k]!=b[k] for k in ('identity','character_uid','map_id','position','silver')):
            raise ValueError('Empty-trade participant changed')
        if any(exact_items(a[k])!=exact_items(b[k]) for k in ('inventory','booth')):
            raise ValueError('Empty-trade ownership changed')
        if b.get('request'):raise ValueError('Another trade request is active')
        t=b.get('trade')
        if t and (t.get('participant')!=before[other]['character'] or
                  t.get('participant_uid')!=before[other]['character_uid'] or
                  t.get('own_items') or t.get('items') or
                  t.get('own_silver')!=0 or t.get('other_silver')!=0 or
                  t.get('accepted') is not False or t.get('other_accepted') is not False):
            raise ValueError('Only an empty unaccepted delivery may be cancelled')

def control(driver,snapshot):
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    if driver.observer.adapter.expected_sha256==CLIENT_SHA256_1078:
        from conquest.merchants.empty_trade_control_1078 import control as locate
        return locate(driver,snapshot)
    g=driver.memory.gui;s=g.session
    # Trade uses the same custom header as the qualified booth panel. Its
    # p_open false branch calls the server cancellation then closes the model.
    for rva,encoded in {**{k:CLOSE_CODE[k] for k in (0x73637,0x7367c)},**CODE}.items():
        expected=bytes.fromhex(encoded)
        if s.read_block(g.base+rva,len(expected))!=expected:
            raise ValueError('Native trade cancellation control changed')
    model=g.model(14,0x5cb328)
    if unpack(s,model+12,'<B')[0]!=1:raise ValueError('Trade model is not open')
    windows=[w for w in snapshot['windows'] if w['name']=='Trade##TradeWindow']
    if len(windows)!=1:raise ValueError('Trade window is absent or ambiguous')
    w=windows[0];x,y,width,height=w['geometry']
    point=(round(x+width-23.5),round(y+12))
    if not x<point[0]<x+width or not y<point[1]<y+height:raise ValueError('Invalid trade close geometry')
    return w,point

def read():
    try:value=json.loads(Path(state_path(PATH)).read_text(encoding='utf-8'))
    except FileNotFoundError:return None
    if not isinstance(value,dict):raise ValueError('Empty cancellation receipt is unreadable')
    return value


def save(state):
    from conquest.merchants.delivery_probe import write_probe
    write_probe(state_path(PATH),state)


def digest(value):
    from conquest.recovery_override import evidence_digest
    return evidence_digest(value)


def _open(ui,probe,farmer,merchant):
    from conquest.merchants.delivery_probe_ownership import ownership
    if (probe.get('phase')!='trade_open_verified' or probe.get('offered_uids')
            or any(k in probe for k in ('placing_uid','confirming_role','confirm_point','cancel_point','submitted_at'))):
        raise ValueError('Cleanup requires an exact bot trade before any item placement or confirmation')
    ownership(probe,probe['character'],ui.runtime.manual_target(probe['character']),
              ui.runtime.manual_target('Farmer'),farmer,merchant,now=time.time())
    unchanged(probe['intent'],{'farmer':farmer,'merchant':merchant})
    if farmer.get('trade') is None or merchant.get('trade') is None:
        raise ValueError('Both exact empty bot trade windows must be open')


def start(ui,character):
    from conquest.merchants import delivery_probe as probe
    from conquest.merchants import empty_delivery_cancel_sessions as sessions
    from conquest.merchants.journal import character_name
    character=character_name(character)
    with ui.coordinator.lock:
        probe.recovery_available(ui);ui.coordinator.check()
        state=probe.read_probe();old=read();c=ui.app.control.snapshot()
        if (not state or state.get('character')!=character or c['enabled'] or c.get('paused')
                or ui.runtime.enabled(character) or not ui.safe_to_yield()
                or ui.runtime.manual_handoff_status() is not None):
            raise ValueError('Pause merchant operations and farming before exact bot cleanup')
        prepared=bool(old and old.get('phase')=='prepared' and old.get('probe_digest')==digest(state)
                      and 'submitted_at' not in old and 'point' not in old)
        if old and old.get('phase')!='cancel_verified' and not prepared:
            raise ValueError('Existing empty cancellation is one-shot; use read-only reconciliation')
        if old and old.get('probe_digest')==digest(state) and not prepared:
            raise ValueError('This bot cleanup already has a terminal receipt')
        f,m=pair(ui,character);_open(ui,state,f,m)
        bound=(sessions.refresh_binding(ui.runtime,state,old['sessions']) if prepared
               else sessions.binding(ui.runtime,state))
        if old:probe.archive_probe(old)
        cancel={'phase':'prepared','character':str(character),'probe':state,'probe_digest':digest(state),
                'before':{'farmer':f,'merchant':m},'sessions':bound,'control_revision':c['revision'],
                'created_at':time.time(),'expires_at':time.time()+20,'sales_receipt':False,'delivery_receipt':False}
        if prepared:cancel['prior_prepared_digest']=digest(old)
        cancel=json.loads(json.dumps(cancel));save(cancel)
        def work():
            try:run(ui,cancel)
            except Exception as error:
                if digest(read())==digest(cancel) and cancel['phase']!='cancel_verified':
                    cancel.update(error=str(error),failed_at=time.time());save(cancel)
        ui.delivery_probe_thread=threading.Thread(target=work,daemon=True,name='empty-delivery-cancel')
        ui.delivery_probe_thread.start()
        return {'started':True,'character':str(character),'probe_digest':cancel['probe_digest']}


def _authorize(ui,state,expected):
    import ctypes
    from conquest.capture import CaptureUnavailable
    from conquest.merchants import delivery_probe as probe
    from conquest.merchants.empty_delivery_cancel_sessions import check_binding
    from conquest.merchants.delivery_reservation import active
    character=state['character'];c=ui.app.control.snapshot()
    if (ui.closed or ui.app.closing or c['enabled'] or c.get('paused')
            or c['revision']!=state['control_revision'] or ui.coordinator.stopped
            or ui.coordinator.manual_active() or not ui.safe_to_yield() or ui.runtime.enabled(character)
            or not state['created_at']<=time.time()<=state['expires_at']
            or digest(probe.read_probe())!=state['probe_digest'] or digest(read())!=expected
            or state['phase'] not in ('prepared','cancel_submitted')
            or ui.runtime.manual_handoff_status() is not None
            or getattr(ui.runtime,'delivery_window',None) or getattr(ui.runtime,'refill_window',None)
            or active(ui.runtime.journal,character) or ui.runtime.journal.pending(character)
            or any(ctypes.windll.user32.GetAsyncKeyState(k)&0x8000 for k in (0x7a,0x7b))):
        raise CaptureUnavailable('Exact empty-bot cleanup stopped or its authority changed')
    check_binding(ui.runtime,state['sessions'])
    if sorted(ui.coordinator.manual_sessions.values(),key=lambda r:r['id'])!=state['sessions']['holds']:
        raise CaptureUnavailable('Empty cleanup manual fences changed')
    if (ui.runtime.manual_target(character)!=state['probe']['target_profile_id']
            or ui.runtime.manual_target('Farmer')!=state['probe']['farmer_profile_id']):
        raise CaptureUnavailable('Empty cleanup profile binding changed')
    return {state['probe']['target_profile_id'],state['probe']['farmer_profile_id']}


def lease_authorized(ui,character):
    """The ephemeral scope grants only this worker's receipt-bound close."""
    c=ui.coordinator
    if c.purpose!=PURPOSE or not c.probe_abort_authorized(ui.runtime.manual_target(character)):return False
    state=read();worker=getattr(ui,'delivery_probe_thread',None)
    if not state or state['character']!=str(character) or worker is not threading.current_thread():return False
    driver=ui.runtime.controllers[character].driver;observer=ui.runtime.observers.get(character)
    if (observer is not driver.observer or driver.target is not observer.operations.target
            or observer.adapter.identity!=state['probe']['intent']['merchant']['identity']):return False
    observer.adapter.assert_identity()
    return True


def run(ui,state):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    from conquest.merchants.driver import wait_hover_validation
    from conquest.merchants.trade_driver_1078 import native_foreground
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    character=state['character'];driver=ui.runtime.controllers[character].driver
    expected=[digest(state)];submitted=[False]
    def authorize():return _authorize(ui,state,expected[0])
    def check():ui.coordinator.check();authorize()
    with ui.coordinator.lock:
        if state['phase']!='prepared' or digest(read())!=expected[0]:
            raise ValueError('A previous cancellation must not send another close')
        f,m=pair(ui,character);_open(ui,state['probe'],f,m)
        from conquest.merchants.empty_delivery_cancel_sessions import refresh_binding
        state['sessions']=refresh_binding(ui.runtime,state['probe'],state['sessions'])
        save(state);expected[0]=digest(state)
        with ui.coordinator.probe_abort_scope(authorize):
            with ui.coordinator.lease(character,purpose=PURPOSE),physical_coordinates():
                check();native=driver.observer.adapter.expected_sha256==CLIENT_SHA256_1078
                if native:native_foreground(driver,m['identity'],activate=True)
                f,m=pair(ui,character);_open(ui,state['probe'],f,m)
                w,point=control(driver,m);size=driver.target.snapshot()['client_size']
                if size!=driver.memory.gui.viewport_size():raise ValueError('Trade viewport changed')
                def guard():
                    check();a,b=pair(ui,character);_open(ui,state['probe'],a,b)
                    if native:native_foreground(driver,b['identity'])
                    if control(driver,b)!=(w,point):raise ValueError('Trade close control moved')
                    driver.memory.gui.assert_hovered(w,'#CLOSE')
                def press():
                    wait_hover_validation(guard,check)
                    if submitted[0]:raise ValueError('Empty cancellation was already submitted')
                    state.update(phase='cancel_submitted',point=point,submitted_at=time.time())
                    save(state);expected[0]=digest(state);submitted[0]=True
                foreground_click(driver.target,*point,tuple(size),require_foreground=native,before_press=press)
    # No lease or input capability survives the one click. All later work is
    # closed-window observation, including after uncertainty or restart.
    deadline=time.monotonic()+8
    while True:
        result=_reconcile(ui,state)
        if result.get('manual_settled'):return result
        if time.monotonic()>=deadline:return result
        time.sleep(.1)


def _reconcile(ui,state):
    from conquest.merchants import delivery_probe as probe
    from conquest.merchants import empty_delivery_cancel_sessions as sessions
    from conquest.merchants.manual_sessions import canonical_ownership
    with ui.coordinator.lock:
        if state.get('phase') not in ('cancel_submitted','cancel_verified') or not state.get('submitted_at'):
            raise ValueError('No submitted empty cancellation exists; no input replay is authorized')
        if digest(read())!=digest(state):raise ValueError('Empty cancellation receipt changed')
        f,m=pair(ui,state['character']);now=time.time()
        unchanged(state['before'],{'farmer':f,'merchant':m})
        if any(s.get('trade') is not None or s.get('request') is not None for s in (f,m)):
            return {'phase':state['phase'],'manual_settled':False,'reason':'Exact empty trade remains open; no click replay'}
        for role,snapshot in (('farmer',f),('merchant',m)):
            if canonical_ownership(snapshot)!=canonical_ownership(state['probe']['intent'][role]):
                raise ValueError('Closed empty cancellation ownership changed')
        if state['phase']!='cancel_verified':
            state.update(phase='cancel_verified',after={'farmer':f,'merchant':m},verified_at=now,
                         historical_outcome='unknown')
            state['receipt_digest']=digest(state);save(state)
        elif digest({k:v for k,v in state.items() if k!='receipt_digest'})!=state['receipt_digest']:
            raise ValueError('Empty cancellation verification receipt changed')
        current=probe.read_probe()
        if digest(current)==state['probe_digest']:
            result={**current,'phase':'cancel_verified','updated_at':state['verified_at'],
                    'finished_at':state['verified_at'],'error':None,'empty_cancel_receipt':state,
                    'empty_cancel_digest':state['receipt_digest'],'historical_outcome':'unknown',
                    'sales_receipt':False,'delivery_receipt':False}
            probe.write_probe(probe.JOURNAL,result);probe.archive_probe(result)
        elif current.get('empty_cancel_digest')!=state['receipt_digest'] or current.get('phase')!='cancel_verified':
            raise ValueError('Original probe changed before cancellation publication')
        settled=sessions.settle(ui.runtime,state,f,m)
        return {'phase':'cancel_verified','manual_settled':settled,'receipt_digest':state['receipt_digest']}


def reconcile(ui,character):
    from conquest.merchants.delivery_probe import recovery_available
    from conquest.merchants.journal import character_name
    recovery_available(ui);state=read()
    if not state or state.get('character')!=str(character_name(character)):
        raise ValueError('No matching empty cancellation receipt')
    return _reconcile(ui,state)
