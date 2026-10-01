"""Close a memory-identified display panel only after exact widget hover."""

from conquest.capture import CaptureUnavailable
from conquest.merchants.memory import GuiReader, GuiObservationChanged, HoverNotReady
from conquest.merchants.driver import wait_hover_validation

# A town panel is dragged back this far inside the viewport's edge.
VIEW_MARGIN = 8
# Where its title bar is grabbed: right of the visible left edge, clear of the
# close button at the bar's right end, inside the bar's height.
GRAB_INSET = 40
TITLE_BAR = 20


def bring_into_view(trade, name):
    """Drag a town panel that sticks out past the viewport back inside it.

    The client keeps each window where it was last dragged. Suicide's Phoenix
    Shop sat at x = -35 on 2026-10-01 06:03, and every strict read of it
    failed ("GUI window geometry is invalid"): its opening, the sale and its
    close. Every restock retry stopped there. Moving a display panel has no
    game effect. The press and the release each wait for the pointer to be
    over this exact window, and memory must show the move. True once moved.
    """
    if name not in ("Inventory", "Shop", "Warehouse"):
        raise ValueError("Unsupported display panel")
    from conquest.foreground import foreground_drag
    from conquest.merchants.memory import unpack
    from conquest.viewport import size_for

    gui = GuiReader.for_session(trade.observer.adapter)

    def current():
        matches = [w for w in gui.windows() if w["name"] == name]
        if len(matches) != 1:
            raise CaptureUnavailable("Display panel absent or ambiguous")
        return matches[0]

    window = current()
    x, y, width, height = window["geometry"]
    view_width, view_height = gui.viewport_size()
    dx = VIEW_MARGIN - x if x < 0 else min(0, view_width - VIEW_MARGIN - (x + width))
    dy = VIEW_MARGIN - y if y < 0 else min(0, view_height - VIEW_MARGIN - (y + height))
    if not dx and not dy:
        return False
    grab = (round(max(x, 0) + GRAB_INSET), round(max(y, 0) + TITLE_BAR / 2))
    if not (x < grab[0] < x + width - 2 * GRAB_INSET and y <= grab[1] < y + TITLE_BAR):
        raise ValueError("No visible title bar to drag the panel by")
    drop = (round(grab[0] + dx), round(grab[1] + dy))

    def over_panel():
        context = unpack(gui.session, gui.base + gui.context_rva, "<Q")[0]
        if unpack(gui.session, context + 0x3EC0, "<Q")[0] != window["address"]:
            raise HoverNotReady("Pointer is not over the panel to move")

    def hovered():
        check_input = getattr(trade, "check_input", None)
        if check_input is not None:
            check_input()
        wait_hover_validation(over_panel, lambda: None)

    trade.input_attempted = True
    foreground_drag(
        trade.observer.operations.target,
        grab,
        drop,
        size_for(trade.observer),
        before_press=hovered,
        before_release=over_panel,
        activate=True,
    )
    moved = current()["geometry"]
    if abs(moved[0] - (x + dx)) > 2 or abs(moved[1] - (y + dy)) > 2:
        raise ValueError(f"Panel move was not verified ({name} at {moved[:2]})")
    return True


def click_close(trade, name, *, validate=None, before_mouse_down=None):
    if name not in ("Inventory", "Shop", "Warehouse"):
        raise ValueError("Unsupported display panel")
    gui = GuiReader.for_session(trade.observer.adapter)

    def read_windows():
        # These reads run during preparation or before_press, never after a
        # button event. Town input marks intent early, so classify this race
        # explicitly rather than relying on the outer input_attempted flag.
        try:
            return gui.windows()
        except GuiObservationChanged as error:
            from conquest.town_trade import TownObservationUnavailable

            raise TownObservationUnavailable(
                "Panel layout changed; reobserving before close; no button pressed"
            ) from error

    target = getattr(getattr(trade.observer, "operations", None), "target", None)
    layout = revision = None
    if target is not None:
        from conquest.layout_revision import SharedLayoutRevision

        layout = SharedLayoutRevision(
            target, windows=read_windows, gui_size=gui.viewport_size
        )
        revision = layout.stable()

    def current():
        matches = [w for w in read_windows() if w["name"] == name]
        if len(matches) != 1:
            raise CaptureUnavailable("Display panel absent or ambiguous")
        return matches[0]

    window = current()
    x, y, width, height = window["geometry"]
    point = (round(x + width - 23.5), round(y + 12))
    if not x < point[0] < x + width or not y < point[1] < y + height:
        raise ValueError("Invalid close-button geometry")

    def guard():
        check_input = getattr(trade, "check_input", None)
        if check_input is not None:
            check_input()
        if layout is not None:
            layout.assert_current(revision)
        fresh = current()
        if any(fresh[k] != window[k] for k in ("address", "geometry")):
            raise CaptureUnavailable("Display panel moved before closing")
        if validate is not None:
            validate()
        gui.assert_hovered(fresh, "#CLOSE")

    def check():
        check_input = getattr(trade, "check_input", None)
        if check_input is not None:
            check_input()
        if layout is not None:
            layout.assert_current(revision)

    # A display panel's close has no game effect, so it is allowed while dead:
    # an open Inventory covers the Revive button (2026-09-29).
    trade.click(
        point,
        before_press=lambda: wait_hover_validation(guard, check),
        before_mouse_down=before_mouse_down,
        allow_dead=True,
    )
