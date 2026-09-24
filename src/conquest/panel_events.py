"""Typed panel outcomes shared by travel care and route reconciliation."""

PANEL_CLOSE_UNVERIFIED = "panel_close_unverified"


def panel_close_unverified(error):
    """Accept a structured bridge code, retaining legacy bridge compatibility."""
    return (
        getattr(error, "code", None) == PANEL_CLOSE_UNVERIFIED
        or str(error) == "Town panel close was not verified"
    )
