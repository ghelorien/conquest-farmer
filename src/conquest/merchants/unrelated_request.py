"""Decline one exact unrelated incoming request without opening a trade."""
import json,struct,time
from pathlib import Path

from conquest.addressing import checked_address
from conquest.capture import CaptureUnavailable
from conquest.character_context import current,state_path,trusted_delivery
from conquest.memory_entities import sample_fields
from conquest.merchants.memory import string


def _exact_inventory(items):
    """JSON-stable exact ownership, including binding, for restart checks."""
    from conquest.merchants.delivery import exact_items
    return [[uid,*details] for uid,details in sorted(exact_items(items).items())]


def _exact_booth(items):
    from conquest.merchants.delivery import exact_listings
    return [[uid,*details] for uid,details in sorted(exact_listings(items).items())]


def _reconcile_pending(controller,snapshot,pending):
    """Observe a submitted decline once; never repeat its native input."""
    if snapshot.get('request') or snapshot.get('trade'):
        raise CaptureUnavailable('Unrelated request decline awaits read-only reconciliation')
    required=('identity','inventory','booth','silver')
    if any(name not in pending for name in required):
        raise CaptureUnavailable('Legacy unrelated request decline lacks exact reconciliation evidence')
    if (snapshot.get('identity')!=pending['identity']
            or _exact_inventory(snapshot.get('inventory',[]))!=pending['inventory']
            or _exact_booth(snapshot.get('booth',[]))!=pending['booth']
            or snapshot.get('silver')!=pending['silver']):
        raise CaptureUnavailable('Unrelated request decline changed client, stock, booth, or silver')
    controller.journal.set(controller.character,'unrelated_request_decline',{
        **pending,'phase':'verified','verified_at':time.time()})
    return True


def _require_no_owned_work(controller):
    from conquest.merchants.delivery_reservation import active as reserved
    if reserved(controller.journal,controller.character) or controller.journal.pending(controller.character):
        raise CaptureUnavailable('Incoming request waits for transaction reconciliation')


def _shared_profile(observer):
    build=observer.adapter.expected_sha256
    paths=[Path(state_path('.runtime/merchants/farmer-delivery-qualified.json'))]
    context=current()
    if context is not None:
        paths.insert(0,context.state_dir/'.runtime'/'delivery-q'/(build[:16].lower()+'.json'))
    matches=[]
    for path in paths:
        try:data=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):continue
        if (data.get('client_sha256')==build and data.get('server')=='America'
                and data.get('recipient') and data.get('evidence')):
            matches.append(data)
    if not matches:raise ValueError('Remote-player identity layout is not qualified for this build')
    if any(m['recipient']!=matches[0]['recipient'] for m in matches[1:]):
        raise ValueError('Remote-player identity qualifications disagree')
    return matches[0]


def requester_identity(observer,name):
    """Resolve one exact request name to a stable live remote-player UID."""
    profile=_shared_profile(observer);spec=profile['recipient'];s=observer.adapter;e=observer.entities
    if spec.get('name_format')!='inline_utf8':raise ValueError('Remote-player name layout is not qualified')
    offsets=[spec[k] for k in ('uid_offset','name_offset','position_offset')]
    length=spec['name_capacity']
    if (any(type(o) is not int or not 8<=o<=2048 for o in offsets)
            or type(length) is not int or not 2<=length<=64):
        raise ValueError('Remote-player identity layout exceeds bounds')
    span=max(offsets[0]+4,offsets[1]+length,offsets[2]+8)
    base,collection,trace=e._resolve();p=e.layout
    headers=[(collection+o,'u64') for o in (p.begin_offset,p.end_offset,p.capacity_offset)]
    header=sample_fields(s,headers);begin,end,capacity=header
    if not (0<begin<=end<=capacity and (end-begin)%16==0 and (capacity-begin)//16<=p.max_objects):
        raise ValueError('Remote-player scene is invalid')
    entries=s.read_block(checked_address(begin),end-begin) if end>begin else b''
    objects=[struct.unpack_from('<Q',entries,i+8)[0] for i in range(0,len(entries),16)]
    if len(set(objects))!=len(objects):raise ValueError('Ambiguous remote-player ownership')
    vtable=base+spec['vtable_rva'];types=sample_fields(s,[(checked_address(o),'u64') for o in objects])
    matches=[]
    for obj,kind in zip(objects,types):
        if kind!=vtable:continue
        raw=s.read_block(checked_address(obj),span)
        observed=raw[offsets[1]:offsets[1]+length].split(b'\0')[0].decode('utf-8')
        if observed!=name:continue
        uid=struct.unpack_from('<I',raw,offsets[0])[0]
        position=list(struct.unpack_from('<II',raw,offsets[2]))
        fresh=s.read_block(obj,span)
        if any(raw[o:o+n]!=fresh[o:o+n] for o,n in ((0,8),(offsets[0],4),(offsets[1],length),(offsets[2],8))):
            raise ValueError('Remote requester moved during identity observation')
        matches.append({'uid':uid,'name':name,'position':position})
    if len(matches)!=1 or matches[0]['uid']<=0:
        raise ValueError('Incoming requester UID is absent or ambiguous in the live scene')
    if sample_fields(s,headers)!=header or (end>begin and s.read_block(begin,end-begin)!=entries):
        raise ValueError('Remote-player scene changed')
    s.assert_identity();return matches[0]


def _cancel_control(driver,snapshot,name):
    # The qualified native request layout/handler is shared; temporarily use
    # the generic geometry reader, while checking this exact message ourselves.
    s=driver.observer.adapter;g=driver.memory.gui;model=g.model(15,0x5c4f30)
    if [string(s,model+o) for o in (0x48,0x68,0x88,0xa8)]!=[
            'Trade###Confirm',f'{name} wishes to trade with you.','Accept','Cancel']:
        raise ValueError('Incoming request dialog identity changed')
    for rva,code in ((0x95fd6,'e835dcfaff'),(0x95fdf,'b201488bcbe857070000')):
        if s.read_block(g.base+rva,len(bytes.fromhex(code)))!=bytes.fromhex(code):
            raise ValueError('Native request handler changed')
    windows=[w for w in snapshot['windows'] if w['name'].endswith('###Confirm')]
    if len(windows)!=1:raise ValueError('Incoming request dialog is absent or ambiguous')
    w=windows[0];raw=s.read_block(w['address'],0x250);x,y,width,height=w['geometry']
    end_x,button_y=struct.unpack_from('<2f',raw,0xe8);line=struct.unpack_from('<f',raw,0x114)[0]
    if width!=200 or not 100<=height<=400 or line!=18 or end_x!=x+width-8:
        raise ValueError('Incoming request button layout changed')
    accept=(round(x+width/2),round(button_y-22+line/2))
    return w,(accept[0],accept[1]+22)


def decline_unrelated_request(controller,snapshot,*,operations_enabled,manual_session=None):
    """Return True only after one unrelated request is durably cleared."""
    if not operations_enabled or not controller.active():return False
    allowed_maps=(1002,1011,1036) if getattr(controller,'manual_farmer',False) else (1036,)
    if (snapshot.get('map_id') not in allowed_maps or type(snapshot.get('hp')) is not int
            or snapshot['hp']<=0):
        return False
    key='unrelated_request_decline';pending=controller.journal.get(controller.character,key)
    if pending and pending.get('phase')=='submitted':
        return _reconcile_pending(controller,snapshot,pending)
    request=snapshot.get('request')
    if not request or snapshot.get('trade'):return False
    _require_no_owned_work(controller)
    name=request.get('participant')
    if not isinstance(name,str) or not name or request.get('message')!=f'{name} wishes to trade with you.':
        return False  # Incomplete observations are never input authority.
    # A trusted name belongs to the delivery path, which performs its own
    # exact UID check. Never turn a temporarily incomplete trusted request
    # observation into an unrelated-request decline.
    if manual_session is None and trusted_delivery(controller.character,name,require_uid=False):return False
    identity=requester_identity(controller.driver.observer,name)
    if manual_session is None and trusted_delivery(controller.character,name,identity['uid']):return False
    driver=controller.driver;driver.require_qualified('trade_request')
    from conquest.desktop_runtime import physical_coordinates
    from conquest.focus_recovery import activate_client
    from conquest.foreground import foreground_click
    from conquest.merchants.driver import wait_hover_validation
    purpose='manual_decline' if manual_session else 'decline_request'
    from conquest.merchants.coordination import input_scope
    scope=(input_scope(purpose=purpose) if getattr(controller,'manual_farmer',False) else
           controller.coordinator.lease(controller.character,purpose=purpose))
    with scope,physical_coordinates():
        controller.check();_require_no_owned_work(controller);fresh=driver.read()
        if fresh['identity']!=snapshot['identity'] or fresh.get('trade') or fresh.get('request')!=request:
            raise ValueError('Incoming request changed before decline')
        if requester_identity(driver.observer,name)!=identity:raise ValueError('Incoming requester changed before decline')
        _cancel_control(driver,fresh,name)
        if not activate_client(driver.target.hwnd,fresh['identity']):
            raise CaptureUnavailable('Merchant focus unavailable; no decline input sent')
        layout=driver.layout_revision();revision=layout.stable()
        # Activation or a concurrent resize may have changed the native/GUI
        # relationship. Re-read and derive the physical point from the stable
        # revision rather than dispatching the earlier logical coordinate.
        fresh=driver.read()
        if fresh['identity']!=snapshot['identity'] or fresh.get('trade') or fresh.get('request')!=request:
            raise ValueError('Incoming request changed before decline')
        if requester_identity(driver.observer,name)!=identity:
            raise ValueError('Incoming requester changed before decline')
        window,logical_point=_cancel_control(driver,fresh,name)
        revision=layout.assert_current(revision)
        gui_size=tuple(revision.gui_size or driver.memory.gui.viewport_size())
        size=tuple(revision.client_size)
        point=tuple(round(value*native/logical) for value,native,logical in
                    zip(logical_point,size,gui_size))
        def before():
            controller.check();_require_no_owned_work(controller);current=driver.read()
            if current['identity']!=fresh['identity'] or current.get('trade') or current.get('request')!=request:
                raise ValueError('Incoming request changed before decline press')
            if (requester_identity(driver.observer,name)!=identity
                    or _cancel_control(driver,current,name)!=(window,logical_point)):
                raise ValueError('Incoming requester or decline control changed')
            driver.memory.gui.assert_hovered(window,'Cancel');layout.assert_current(revision)
            if manual_session:
                store,session_id=manual_session
                if store.claim_decline(session_id,current) is None:
                    raise CaptureUnavailable('Manual request decline already claimed; observation required')
            controller.journal.set(controller.character,key,{'phase':'submitted','name':name,'uid':identity['uid'],
                'identity':fresh['identity'],'inventory':_exact_inventory(fresh['inventory']),
                'booth':_exact_booth(fresh.get('booth',[])),'silver':fresh['silver'],'at':time.time()})
        foreground_click(driver.target,*point,size,require_foreground=False,
            layout_guard=lambda:layout.assert_current(revision),
            before_press=lambda:wait_hover_validation(before,controller.check))
    after=driver.wait_for(lambda s:not s.get('request') and not s.get('trade'),controller.check,seconds=3)
    pending=controller.journal.get(controller.character,key)
    return _reconcile_pending(controller,after,controller.journal.get(controller.character,key))
