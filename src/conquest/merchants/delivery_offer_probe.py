"""Journaled native item placement for an explicitly authorized live trade."""
import ctypes
import time
from contextlib import contextmanager
from conquest.capture import CaptureUnavailable
from conquest.merchants.delivery_probe import JOURNAL,write_probe as write_json
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.delivery import exact_items,validate_offers
from conquest.merchants.farmer_trade import partial_offer
from conquest.merchants.delivery_trade_controls import endpoints


def run(ui,state):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_drag
    from conquest.focus_recovery import activate_client
    from conquest.merchants.memory import unpack,HoverNotReady
    from conquest.merchants.delivery_bridge import source_memory
    from conquest.merchants.driver import wait_hover_validation
    from conquest.merchants.farmer_preferences import permits_new_delivery
    character=state['character'];intent=state['intent'];revision=ui.app.control.snapshot()['revision']
    observer=ui.app.observer;memory=source_memory(observer);target=observer.operations.target
    deadline=time.monotonic()+15
    def manual_fence():
        if any(ui.coordinator.manual_session_blocked(owner) for owner in
               (state.get('farmer_profile_id','Farmer'),state.get('target_profile_id',character))):
            raise CaptureUnavailable('Manual visitor session holds a delivery participant')
    def check():
        permits_new_delivery(intent['farmer']['character']);ui.coordinator.check()
        manual_fence()
        c=ui.app.control.snapshot()
        if (ui.closed or ui.app.closing or c['enabled'] or c.get('paused') or c['revision']!=revision
                or not ui.safe_to_yield() or time.monotonic()>=deadline
                or any(ctypes.windll.user32.GetAsyncKeyState(k)&0x8000 for k in (0x7a,0x7b))):
            raise CaptureUnavailable('Trade placement was stopped or expired')
    def save(phase,**fields):
        state.update(phase=phase,updated_at=time.time(),error=None,**fields);write_json(JOURNAL,state)
    @contextmanager
    def lease():
        with ui.coordinator.lock:
            f,m=pair(ui,character)
            if not ui.runtime.reconcile_probe_pair(character,f,m):
                raise CaptureUnavailable('Delivery placement needs fresh bilateral probe reconciliation')
            manual_fence()
            with ui.coordinator.lease('Farmer',purpose='delivery_offer_probe'),physical_coordinates():yield
    with lease():
        from conquest.merchants.delivery_farmer_surface import prepare
        presentation=prepare(ui,state,purpose='delivery_offer_probe',revision=revision,deadline=deadline)
        check();f,m=pair(ui,character);offered=partial_offer(intent,f,m)
        if any(s['trade']['accepted'] or s['trade']['other_accepted'] for s in (f,m)):
            raise ValueError('Trade was accepted before placement activation')
        presentation()
        if not activate_client(target.hwnd,f['identity']):raise ValueError('Farmer focus unavailable; no drag sent')
        size=target.snapshot()['client_size']
        if size!=memory.gui.viewport_size():raise ValueError('Native and GUI sizes differ')
        for wanted in intent['items']:
            check();f,m=pair(ui,character);offered=partial_offer(intent,f,m)
            if wanted['uid'] in exact_items(offered):continue
            if deadline-time.monotonic()<1.5:
                save('trade_open_verified',offered_uids=list(exact_items(offered)),needs_more=True);return
            if any(s['trade']['accepted'] or s['trade']['other_accepted'] for s in (f,m)):
                raise ValueError('Trade was accepted before placement completed')
            item=next((i for i in f['inventory'] if i['uid']==wanted['uid']),None)
            if not item or exact_items([item])!=exact_items([wanted]):raise ValueError('Reserved item changed')
            inventory,trade,source,destination=endpoints(memory.gui,f,item,offered)
            def before():
                check();a,b=pair(ui,character)
                if exact_items(partial_offer(intent,a,b))!=exact_items(offered):raise ValueError('Offer changed before drag')
                if any(s['trade']['accepted'] or s['trade']['other_accepted'] for s in (a,b)):
                    raise ValueError('Trade was accepted during placement')
                current=next((i for i in a['inventory'] if i['uid']==wanted['uid']),None)
                if (not current or exact_items([current])!=exact_items([item]) or current['slot']!=item['slot']
                        or endpoints(memory.gui,a,current,offered)!=(inventory,trade,source,destination)):
                    raise ValueError('Item or grid changed before drag')
                context=unpack(observer.adapter,memory.gui.base+memory.gui.context_rva,'<Q')[0]
                if unpack(observer.adapter,context+0x3ec0,'<Q')[0]!=inventory['address']:
                    raise HoverNotReady('Inventory cell is covered')
            save('placement_submitted',placing_uid=item['uid'],source=source,destination=destination)
            foreground_drag(target,source,destination,tuple(size),
                before_press=lambda:wait_hover_validation(before,check))
            # Reconcile a submitted drag even after the input window expires.
            until=time.monotonic()+3
            while True:
                try:
                    a,b=pair(ui,character);received=partial_offer(intent,a,b)
                    if item['uid'] in exact_items(received):break
                except (OSError,ValueError):
                    # The sender and receiver acknowledge on separate frames.
                    # Reads may retry; never repeat the submitted drag here.
                    pass
                if time.monotonic()>=until:raise ValueError('Placement unverified; reconcile before another drag')
                time.sleep(.05)
            f,m=a,b
            offered=received
            save('trade_open_verified',offered_uids=list(exact_items(received)),needs_more=True)
        f,m=pair(ui,character);validate_offers(intent,f,m)
        save('offer_verified',farmer_after=f,merchant_after=m,needs_more=False)
