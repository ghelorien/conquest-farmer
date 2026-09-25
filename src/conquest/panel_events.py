"""Typed panel outcomes shared by travel care and route reconciliation."""

PANEL_CLOSE_UNVERIFIED = "panel_close_unverified"


def panel_close_unverified(error):
    """Accept a structured bridge code, retaining legacy bridge compatibility."""
    return (
        getattr(error, "code", None) == PANEL_CLOSE_UNVERIFIED
        or str(error) == "Town panel close was not verified"
    )


# panel_close.click_close raises these from its pre-press hover wait, before
# any mouse button event.  The bridge forwards them as plain ValueError text.
HOVER_NOT_READY = (
    "Pointer is not over the memory-identified merchant control",
    "Merchant hover changed during observation",
)
NOTHING_SENT = ("no button pressed", "no input sent", "no click sent")


def panel_close_not_ready(error):
    """A display-panel close that proved no button was pressed; safe to retry.

    Anything else, including an unverified submitted close, stays uncertain.
    """
    from conquest.merchants.coordination import InputAcquisitionBusy
    from conquest.town_trade import TownObservationUnavailable

    if not isinstance(error, ValueError) or panel_close_unverified(error):
        return False
    if isinstance(error, (TownObservationUnavailable, InputAcquisitionBusy)):
        return True
    text = str(error)
    return text in HOVER_NOT_READY or any(part in text for part in NOTHING_SENT)
