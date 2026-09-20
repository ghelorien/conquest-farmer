"""Close a memory-identified display panel only after exact widget hover."""
from conquest.capture import CaptureUnavailable
from conquest.merchants.memory import GuiReader,GuiObservationChanged
from conquest.merchants.driver import wait_hover_validation


def click_close(trade,name,*,validate=None,before_mouse_down=None):
    if name not in ('Inventory','Shop','Warehouse'):raise ValueError('Unsupported display panel')
    gui=GuiReader(trade.observer.adapter)
    def read_windows():
        # These reads run during preparation or before_press, never after a
        # button event. Town input marks intent early, so classify this race
        # explicitly rather than relying on the outer input_attempted flag.
        try:return gui.windows()
        except GuiObservationChanged as error:
            from conquest.town_trade import TownObservationUnavailable
            raise TownObservationUnavailable('Panel layout changed; reobserving before close; no button pressed') from error
    target=getattr(getattr(trade.observer,'operations',None),'target',None)
    layout=revision=None
    if target is not None:
        from conquest.layout_revision import SharedLayoutRevision
        layout=SharedLayoutRevision(target,windows=read_windows,gui_size=gui.viewport_size)
        revision=layout.stable()
    def current():
        matches=[w for w in read_windows() if w['name']==name]
        if len(matches)!=1:raise CaptureUnavailable('Display panel absent or ambiguous')
        return matches[0]
    window=current();x,y,width,height=window['geometry']
    point=(round(x+width-23.5),round(y+12))
    if not x<point[0]<x+width or not y<point[1]<y+height:raise ValueError('Invalid close-button geometry')
    def guard():
        check_input=getattr(trade,'check_input',None)
        if check_input is not None:check_input()
        if layout is not None:layout.assert_current(revision)
        fresh=current()
        if any(fresh[k]!=window[k] for k in ('address','geometry')):
            raise CaptureUnavailable('Display panel moved before closing')
        if validate is not None:validate()
        gui.assert_hovered(fresh,'#CLOSE')
    def check():
        check_input=getattr(trade,'check_input',None)
        if check_input is not None:check_input()
        if layout is not None:layout.assert_current(revision)
    trade.click(point,before_press=lambda:wait_hover_validation(guard,check),
                before_mouse_down=before_mouse_down)
