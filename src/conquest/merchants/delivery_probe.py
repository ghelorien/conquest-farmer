"""Explicit live qualification of a farmer trade request; no item confirmation."""
from pathlib import Path
import hashlib
import json
import os
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


def write_probe(path,state):
    """Persist each one-shot input boundary before its native action."""
    write_json(path,state)
    with Path(path).open('r+b') as stream:
        stream.flush()
        os.fsync(stream.fileno())


def previous_probe():
    # read_json intentionally tolerates malformed diagnostic files. A missing
    # transaction receipt is different: corruption must never authorize input.
    try:
        state=json.loads(JOURNAL.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError,ValueError) as error:
        raise ValueError('Trade probe evidence is unreadable; reconcile before input') from error
    if not isinstance(state,dict) or state.get('phase') not in TERMINAL:
        raise ValueError('Reconcile existing trade request probe before further input')
    return state


def archive_probe(state):
    """Keep terminal proof when a supervised operator starts a fresh probe."""
    if state is None:return
    raw=json.dumps(state,sort_keys=True,separators=(',',':')).encode('utf-8')
    digest=hashlib.sha256(raw).hexdigest()
    path=JOURNAL.parent/'delivery-request-probe-audit'/f'{digest}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('xb') as out:
            out.write(raw);out.flush();os.fsync(out.fileno())
    except FileExistsError:
        if path.read_bytes()!=raw:
            raise ValueError('Trade probe archive differs from its historical receipt')


def selected_intent(farmer,merchant,uids):
    """The operator selects one ordinary +1; never infer a test batch/value."""
    if (not isinstance(uids,list) or len(uids)!=1
            or type(uids[0]) is not int or uids[0]<=0):
        raise ValueError('Select exactly one carried +1 equipment UID for qualification')
    if any(item.get('type_id')==1088001 for item in farmer['inventory']):
        raise ValueError('Bank loose Meteors before the supervised trade probe')
    items=[item for item in farmer['inventory'] if item.get('uid')==uids[0]]
    if len(items)!=1:
        raise ValueError('The selected qualification item is no longer carried')
    item=items[0];kind=item.get('type_id')
    if (not eligible(item) or type(kind) is not int or not 100000<=kind<600000
            or kind%10==9 or type(item.get('plus')) is not int or item['plus']!=1
            or item.get('gem1')!=0 or item.get('gem2')!=0 or item.get('quantity')!=1):
        raise ValueError('Qualification requires one unbound, unsocketed, non-Super +1 equipment item')
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
    from conquest.merchants.farmer_preferences import permits_new_delivery
    from conquest.merchants.farmer_identity import ui_character
    permits_new_delivery(ui_character(ui))
    character=character_name(character)
    if getattr(ui,'delivery_probe_thread',None) and ui.delivery_probe_thread.is_alive():
        raise ValueError('Trade request probe is running')
    old=previous_probe()
    ui.coordinator.check()
    if not ui.safe_to_yield() or ui.app.control.snapshot()['enabled']:
        raise ValueError('Trade probe requires stopped farming and released input')
    f,m=pair(ui,character)
    intent=selected_intent(f,m,uids)
    if max(abs(a-b) for a,b in zip(f['position'],m['position']))>12:
        raise ValueError('Approach the memory-identified merchant before the trade probe')
    revision=ui.app.control.snapshot()['revision']
    state={'phase':'prepared','character':character,'intent':intent,'started_at':time.time(),
           'selected_uids':list(uids)}
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
    from conquest.merchants.farmer_trade import recipient_record
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
    with ui.coordinator.lease('Farmer'),physical_coordinates():
        done,result=threading.Event(),{}
        ui.ui_requests.put((ui.app.show_game,done,result))
        if not done.wait(3):
            result['expired']=True
            raise ValueError('Farmer window did not become available')
        if result.get('error'):raise ValueError(result['error'])
        check()
        size=observer.operations.target.snapshot()['client_size']
        gui=memory.gui.viewport_size()
        if size!=gui:raise ValueError('Trade probe requires matching native and GUI dimensions')
        profile=read_json(state_path('reports/merchants/trade-layout-candidate.json'))
        profile={**profile,'gui_size':gui}
        if profile.get('client_sha256')!=observer.adapter.expected_sha256:
            raise ValueError('Trade layout build changed')
        f,m=pair(ui,character);unchanged(intent,f,m)
        recipient=recipient_record(observer,profile,m)
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
        recipient=recipient_record(observer,profile,m,targeting=True)
        def before_peer():
            check()
            f,m=pair(ui,character);unchanged(intent,f,m)
            if recipient_record(observer,profile,m,targeting=True)!=recipient:
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
