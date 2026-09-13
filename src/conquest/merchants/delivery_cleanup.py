"""Synchronous, fence-bound cleanup of one exact empty delivery trade."""
import time

from conquest.capture import CaptureUnavailable
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.driver import wait_hover_validation
from conquest.merchants.empty_delivery_cancel import control,unchanged


def cleanup_empty_trade(ui,character,expected,*,operation=None,timeout=3,check=None):
    """Close and verify an exact bilateral empty/unaccepted trade.

    ``expected`` is a freshly reconciled ``{'farmer': ..., 'merchant': ...}``
    pair. The caller's grant fence must already be bound to this worker.
    """
    from conquest.merchants.journal import character_name
    character=character_name(character)
    if set(expected)!={'farmer','merchant'}:
        raise ValueError('Empty trade cleanup requires exact bilateral evidence')
    unchanged(expected,expected)
    if not expected['farmer'].get('trade') or not expected['merchant'].get('trade'):
        raise ValueError('Empty trade cleanup requires both trade windows')
    driver=ui.runtime.controllers[character].driver
    revision=ui.app.control.snapshot()['revision'];deadline=time.monotonic()+timeout
    def permitted():
        ui.coordinator.check()
        state=ui.app.control.snapshot()
        grant=getattr(ui,'grant',None)
        if (ui.closed or getattr(ui.app,'closing',False) or state['enabled'] or state.get('paused')
                or state['revision']!=revision or not grant or grant['expires_at']<=time.time()
                or time.monotonic()>=deadline):
            raise CaptureUnavailable('Empty trade cleanup permission changed or expired')
        if check is not None:check()
    from conquest.desktop_runtime import physical_coordinates
    from conquest.focus_recovery import activate_client
    from conquest.foreground import foreground_click
    with ui.coordinator.lease(character,purpose='delivery_cleanup'),physical_coordinates():
        permitted();farmer,merchant=pair(ui,character)
        current={'farmer':farmer,'merchant':merchant};unchanged(expected,current)
        if not farmer.get('trade') or not merchant.get('trade'):
            raise ValueError('Bilateral empty trade changed before cleanup')
        window,point=control(driver,merchant)
        size=tuple(driver.target.snapshot()['client_size'])
        if list(size)!=list(driver.memory.gui.viewport_size()):
            raise ValueError('Trade viewport changed before cleanup')
        if not activate_client(driver.target.hwnd,merchant['identity']):
            raise CaptureUnavailable('Merchant focus unavailable; no cleanup input sent')
        layout=driver.layout_revision();layout_revision=layout.stable()
        def before():
            permitted();fresh_farmer,fresh_merchant=pair(ui,character)
            unchanged(expected,{'farmer':fresh_farmer,'merchant':fresh_merchant})
            if not fresh_farmer.get('trade') or not fresh_merchant.get('trade'):
                raise ValueError('Bilateral empty trade changed before cleanup press')
            if control(driver,fresh_merchant)!=(window,point):
                raise ValueError('Trade cleanup control moved')
            driver.memory.gui.assert_hovered(window,'#CLOSE')
            layout.assert_current(layout_revision)
            if operation is not None:operation.before_action('cleanup_trade')
        foreground_click(driver.target,*point,size,require_foreground=False,
            layout_guard=lambda:layout.assert_current(layout_revision),
            before_press=lambda:wait_hover_validation(before,permitted))
        while time.monotonic()<deadline:
            permitted();farmer,merchant=pair(ui,character)
            after={'farmer':farmer,'merchant':merchant};unchanged(expected,after)
            if not farmer.get('trade') and not merchant.get('trade'):
                evidence={'farmer_trade':False,'merchant_trade':False}
                if operation is not None:operation.action_observed('cleanup_trade',evidence=evidence)
                return {'cleaned':True,'evidence':evidence}
            time.sleep(.05)
    raise ValueError('Empty trade cleanup unverified; reconcile before any further input')
