"""Close identified display panels before farmer movement or combat."""
import time
from types import SimpleNamespace
from conquest.capture import CaptureUnavailable
from conquest.merchants.memory import GuiReader,GuiObservationChanged
from conquest.merchants.booth_panel_probe import close_point
from conquest.merchants.driver import wait_hover_validation
from conquest.character_context import farmer_name

PANELS=('Booth','Shop','Warehouse','Dialog','Inventory')
TRANSACTIONS={'Trade##TradeWindow','Add Item to Booth','###Confirm'}

def close_one(trade,check=lambda:None):
    try:return _close_one(trade,check)
    except GuiObservationChanged as error:
        raise CaptureUnavailable('GUI observation changed; reobserve panels before movement') from error


def _close_one(trade,check):
    # This helper is called only by farmer travel/combat, never by sellers.
    if trade.observer.character!=farmer_name():raise ValueError('Panel cleanup requires the farmer')
    check();gui=GuiReader(trade.observer.adapter);windows=gui.windows()
    if any(w['name'] in TRANSACTIONS for w in windows):
        raise CaptureUnavailable('A transaction dialog needs reconciliation before movement')
    chosen=next((name for name in PANELS if any(w['name']==name for w in windows)),None)
    if chosen is None:return None
    if chosen!='Booth':
        action='service-close-panel' if chosen=='Dialog' else 'close'
        trade({'action':action,'window':chosen})
        return chosen
    snapshot={'windows':windows}
    driver=SimpleNamespace(memory=SimpleNamespace(gui=gui))
    point,window=close_point(driver,snapshot,display_only=True)
    def guard():
        check();fresh={'windows':gui.windows()}
        if any(w['name'] in TRANSACTIONS for w in fresh['windows']):
            raise CaptureUnavailable('Transaction appeared before closing booth view')
        if close_point(driver,fresh,display_only=True)!=(point,window):
            raise CaptureUnavailable('Booth view moved before close')
        gui.assert_hovered(window,'#CLOSE')
    trade.input_attempted=True
    trade.click(point,before_press=lambda:wait_hover_validation(guard,check))
    until=time.monotonic()+2
    while time.monotonic()<until:
        check()
        if not any(w['name']=='Booth' for w in gui.windows()):return 'Booth'
        time.sleep(.05)
    raise CaptureUnavailable('Booth view close unverified; reobserve before movement')
