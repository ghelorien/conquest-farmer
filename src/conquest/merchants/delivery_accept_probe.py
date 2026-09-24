"""Qualify native acceptance of Parasite's exact incoming trade request."""
import math
import struct
import threading
import time
from conquest.capture import CaptureUnavailable
from conquest.merchants.delivery_probe import JOURNAL,write_probe as write_json
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.delivery import exact_items,validate_offers
from conquest.merchants.memory import string
from conquest.character_context import farmer_name


def lease_authorized(ui, character):
    """Narrow input permission for the one staged native accept operation.

    This deliberately does not reuse ``enabled`` or the broad calibration
    exception: accepting a durable incoming request is safe only for its
    current worker and exact receipt-bound merchant.  The coordinator keeps
    Stop, manual-session and farmer-handoff fences authoritative.
    """
    try:
        coordinator=ui.coordinator;runtime=ui.runtime
        worker=getattr(ui,'delivery_probe_thread',None)
        if (coordinator.purpose!='delivery_accept_probe' or worker is None
                or worker.ident!=threading.get_ident() or not worker.is_alive()
                or character not in ui.calibrating
                or ui.calibration_cancel[character].is_set()
                or getattr(runtime,'delivery_window',None)
                or getattr(runtime,'refill_window',None)
                or character in getattr(runtime,'refilling',{})):
            return False
        from conquest.merchants.delivery_probe import read_probe
        state=read_probe()
        if (not isinstance(state,dict) or state.get('phase') not in ('request_verified','accept_submitted')
                or state.get('character')!=str(character)
                or not isinstance(state.get('target_profile_id'),str)):
            return False
        intent=state.get('intent');merchant=intent.get('merchant') if isinstance(intent,dict) else None
        if (not isinstance(merchant,dict) or merchant.get('character')!=str(character)
                or merchant.get('server')!='America' or not isinstance(merchant.get('identity'),dict)):
            return False
        from conquest.character_context import registry, ProfileName
        profiles=registry();profile_id=state['target_profile_id']
        if profiles:
            resolved=profiles.resolve(profile_id,role='Merchant',server='America')
            if (resolved.id!=profile_id or not resolved.local_enabled
                    or resolved.name!=str(character) or resolved.server!=merchant['server']):
                return False
            profile=ProfileName(resolved.name,resolved.id)
        elif profile_id!=str(character):
            return False
        else:profile=character
        if coordinator.manual_session_blocked(profile,purpose='delivery_accept_probe'):
            return False
        observer=runtime.observers.get(profile)
        if (observer is None or observer.adapter.identity!=merchant['identity']
                or type(getattr(observer.operations.target,'hwnd',None)) is not int
                or observer.operations.target.hwnd<=0):
            return False
        observer.adapter.assert_identity()
        from conquest.merchants.delivery_reservation import active
        return not active(runtime.journal,profile) and not runtime.journal.pending(profile)
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return False


def control(driver,snapshot):
    s=driver.observer.adapter;g=driver.memory.gui
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    if s.expected_sha256==CLIENT_SHA256_1078:
        from conquest.merchants.trade_driver_1078 import locate
        w,point,_,_=locate(driver,snapshot,'native_trade_request')
        return w,point
    model=g.model(15,0x5c4f30)
    # Window slot 15 is shared by native confirmations.  Do not infer the
    # requested action from whichever dialog happens to be visible: accepting
    # is allowed only for the live, exact Trade confirmation model.
    if s.read_block(model+12,1)!=b'\x01' or [string(s,model+o) for o in (0x48,0x68,0x88,0xa8)]!=[
            'Trade###Confirm',f'{farmer_name()} wishes to trade with you.','Accept','Cancel']:
        raise ValueError('Confirmation is not Parasite trade acceptance')
    for rva,code in ((0x95fd6,'e835dcfaff'),(0x95fdf,'b201488bcbe857070000')):
        if s.read_block(g.base+rva,len(bytes.fromhex(code)))!=bytes.fromhex(code):
            raise ValueError('Native accept handler changed')
    # The slot-15 model and ImGui renderer have distinct object addresses.
    # The registry name can lag the active shared slot by a frame, so bind a
    # single *rendered* confirmation to its own raw geometry, while retaining
    # the exact active model address as a separate authority.  This does not
    # redirect by a name or geometry: before press re-proves both structures
    # and hover verifies this rendered window's Accept ID.
    confirmations=[w for w in snapshot['windows'] if str(w.get('name','')).endswith('###Confirm')]
    if (len(confirmations)!=1 or type(confirmations[0].get('address')) is not int
            or confirmations[0]['address']<=0):
        raise ValueError('Trade confirmation window is absent or ambiguous')
    w={**confirmations[0],'model_address':model};raw=s.read_block(w['address'],0x250)
    geometry=w.get('geometry')
    if not isinstance(geometry,(list,tuple)) or len(geometry)!=4:
        raise ValueError('Trade confirmation button layout changed')
    x,y,width,height=geometry
    raw_geometry=struct.unpack_from('<4f',raw,0x18)
    if (not all(type(value) in (int,float) and math.isfinite(value) for value in geometry)
            or tuple(geometry)!=raw_geometry):
        raise ValueError('Trade confirmation button layout changed')
    end_x,button_y=struct.unpack_from('<2f',raw,0xe8)
    line=struct.unpack_from('<f',raw,0x114)[0]
    if (width!=200 or not 100<=height<=400 or line!=18 or end_x!=x+width-8
            or not all(math.isfinite(value) for value in (end_x,button_y,line))):
        raise ValueError('Trade confirmation button layout changed')
    point=(round(x+width/2),round(button_y-22+line/2))
    if not x<point[0]<x+width or not y<point[1]<y+height:
        raise ValueError('Trade confirmation button layout changed')
    return w,point


def control_binding(window,point):
    """Return only the qualified identity that must survive pre-press reads.

    The registry name and draw metadata are observational and may legitimately
    lag or change while the native slot remains the same.  ``control`` has
    independently proved the rendered address/geometry and raw exact Trade
    model in the same fresh read; both identities are retained here.
    """
    return window['model_address'],window['address'],tuple(window['geometry']),point


def exact_incoming_request(intent, merchant):
    """Bind the hidden request actor, not merely its rendered dialog name."""
    request=merchant.get('request')
    farmer=intent.get('farmer') if isinstance(intent,dict) else None
    if not isinstance(farmer,dict) or not isinstance(request,dict):return False
    return (request.get('participant')==farmer.get('character')
            and type(request.get('participant_uid')) is int
            and request['participant_uid']==farmer.get('character_uid')
            and request.get('message')==f"{farmer.get('character')} wishes to trade with you."
            and request.get('server',merchant.get('server'))==farmer.get('server'))


def run(ui,state):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    from conquest.merchants.driver import wait_hover_validation
    from conquest.merchants.farmer_preferences import permits_new_delivery
    character=state['character'];intent=state['intent'];revision=ui.app.control.snapshot()['revision']
    # The journal serializes names but all managed runtime maps are profile-ID
    # keyed.  Bind the ID once for this worker; do not resolve by label later.
    from conquest.character_context import registry, ProfileName
    profiles=registry();profile_id=state.get('target_profile_id')
    if profiles:
        try:resolved=profiles.resolve(profile_id,role='Merchant',server='America')
        except ValueError as error:raise ValueError('Delivery acceptance merchant profile is unavailable') from error
        if (resolved.id!=profile_id or resolved.name!=character
                or intent.get('merchant',{}).get('character')!=character
                or resolved.server!=intent.get('merchant',{}).get('server')):
            raise ValueError('Delivery acceptance merchant profile changed')
        character=ProfileName(resolved.name,resolved.id)
    elif profile_id not in (None,str(character)):
        raise ValueError('Delivery acceptance merchant profile changed')
    deadline=time.monotonic()+15
    driver=ui.runtime.controllers[character].driver
    from conquest.recovery_override import evidence_digest
    from conquest.merchants.delivery_probe import read_probe
    from conquest.merchants.delivery_probe_ownership import ownership
    from contextlib import contextmanager
    def manual_fence():
        if any(ui.coordinator.manual_session_blocked(owner,purpose='delivery_accept_probe') for owner in
               (state.get('farmer_profile_id','Farmer'),profile_id or character)):
            raise CaptureUnavailable('Manual visitor session holds a delivery participant')
    def check():
        permits_new_delivery(intent['farmer']['character']);ui.coordinator.check()
        manual_fence()
        import ctypes
        c=ui.app.control.snapshot()
        if (c['enabled'] or c.get('paused') or c['revision']!=revision or time.monotonic()>=deadline
                or any(ctypes.windll.user32.GetAsyncKeyState(k)&0x8000 for k in (0x7a,0x7b))):
            raise CaptureUnavailable('Trade qualification was stopped or expired')
    def fresh():
        check();f,m=pair(ui,character)
        if f.get('trade') or m.get('trade') or f.get('request') or not exact_incoming_request(intent,m):
            raise ValueError('Expected incoming Parasite request changed')
        for role,snapshot in (('farmer',f),('merchant',m)):
            old=intent[role]
            if any(snapshot[k]!=old[k] for k in ('identity','character_uid','position','silver')) or exact_items(snapshot['inventory'])!=exact_items(old['inventory']):
                raise ValueError('Participants or inventory changed before request acceptance')
        current=read_probe()
        if (not isinstance(current,dict) or current.get('phase') not in ('request_verified','accept_submitted')
                or current.get('phase')!=state.get('phase')
                or evidence_digest(current)!=evidence_digest(state)):
            raise ValueError('Trade acceptance receipt changed before native input')
        ownership(current,str(character),profile_id or str(character),
                  current.get('farmer_profile_id','Farmer'),f,m,now=time.time())
        return f,m
    # Calibration grants only this checked input window; it never enables the
    # merchant trading controller or changes its persistent permissions.
    if character in ui.calibrating:raise ValueError('Merchant has another calibration active')
    ui.calibrating.add(character);ui.calibration_cancel[character]=threading.Event()
    try:
        # A transient observer contention must not have converted this exact
        # supervised request into a manual hold.  Full bilateral proof is
        # required here: it retracts only a false unapproved admission and
        # leaves genuine/claimed holds intact for the coordinator to deny.
        # Pair capture and its full proof share the coordinator mutex.  That
        # prevents a manual observer pass from admitting the same request
        # between the observer reads and false-session retraction.
        @contextmanager
        def lease():
            with ui.coordinator.lock:
                _farmer,merchant=pair(ui,character)
                reconciled=ui.runtime.reconcile_probe_owned(character,_farmer,merchant)
                if not reconciled:
                    raise CaptureUnavailable('Delivery acceptance needs fresh bilateral probe reconciliation')
                manual_fence()
                with ui.coordinator.lease(character,purpose='delivery_accept_probe'),physical_coordinates():yield
        # This purpose asks the UI handoff to select and re-embed this exact
        # merchant before the first live control read.  The subsequent
        # ``control`` checks remain the only authority for sending a click.
        with lease():
            f,m=fresh()
            from conquest.memory_build_layout import CLIENT_SHA256_1078
            native1078=driver.observer.adapter.expected_sha256==CLIENT_SHA256_1078
            if native1078:
                from conquest.merchants.trade_driver_1078 import native_foreground
                native_foreground(driver,m['identity'],activate=True)
                f,m=fresh()
            w,point=control(driver,m);binding=control_binding(w,point)
            size=driver.target.snapshot()['client_size']
            if size!=driver.memory.gui.viewport_size():raise ValueError('Native and GUI dimensions differ')
            def before():
                f,m=fresh()
                if native1078:native_foreground(driver,m['identity'])
                current,now_point=control(driver,m)
                if control_binding(current,now_point)!=binding:raise ValueError('Accept control moved')
                driver.memory.gui.assert_hovered(current,'Accept')
            state.update(phase='accept_submitted',accept_point=point,error=None,updated_at=time.time());write_json(JOURNAL,state)
            foreground_click(driver.target,*point,tuple(size),require_foreground=native1078,
                before_press=lambda:wait_hover_validation(before,check))
            while True:
                check();f,m=pair(ui,character)
                if f.get('trade') and m.get('trade'):
                    validate_offers({**intent,'items':[]},f,m)
                    accepted_at=time.time()
                    state.update(phase='trade_open_verified',farmer_after=f,merchant_after=m,
                                 accepted_at=accepted_at,updated_at=accepted_at);write_json(JOURNAL,state);return
                time.sleep(.1)
    finally:
        ui.calibrating.discard(character)
