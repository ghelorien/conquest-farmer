"""Close a memory-identified display panel only after exact widget hover."""
from conquest.capture import CaptureUnavailable
from conquest.merchants.memory import GuiReader
from conquest.merchants.driver import wait_hover_validation


def click_close(trade,name):
    if name not in ('Inventory','Shop','Warehouse'):raise ValueError('Unsupported display panel')
    gui=GuiReader(trade.observer.adapter)
    def current():
        matches=[w for w in gui.windows() if w['name']==name]
        if len(matches)!=1:raise CaptureUnavailable('Display panel absent or ambiguous')
        return matches[0]
    window=current();x,y,width,height=window['geometry']
    point=(round(x+width-23.5),round(y+12))
    if not x<point[0]<x+width or not y<point[1]<y+height:raise ValueError('Invalid close-button geometry')
    def guard():
        fresh=current()
        if any(fresh[k]!=window[k] for k in ('address','geometry')):
            raise CaptureUnavailable('Display panel moved before closing')
        gui.assert_hovered(fresh,'#CLOSE')
    trade.click(point,before_press=lambda:wait_hover_validation(guard,lambda:None))
