"""Show the farmer tab without changing the farming intent."""

import time


def _intent(snapshot):
    """Exclude observation/execution telemetry that does not authorize input."""
    return (
        snapshot.get("enabled"),
        snapshot.get("paused"),
        snapshot.get("revision"),
        tuple(snapshot.get("target_ids", ())),
        tuple(snapshot.get("target_type_ids", ())),
        snapshot.get("input_mode"),
    )


def focus_idle_farmer(ui, *, queued_at):
    if time.monotonic() - queued_at > 2.5:
        raise ValueError("Farmer focus request expired")
    ui.coordinator.check()
    if ui.closed or ui.app.closing or ui.coordinator.owner or not ui.safe_to_yield():
        raise ValueError("Release active input before focusing the farmer")
    before = ui.app.control.snapshot()
    if before["enabled"] or before.get("paused"):
        raise ValueError("Farmer focus preparation requires idle farming")
    if not ui.app.show_game():
        raise ValueError("Farmer surface could not receive focus")
    after = ui.app.control.snapshot()
    ui.coordinator.check()
    if _intent(after) != _intent(before):
        raise ValueError("Farmer intent changed during focus preparation")
    return {"focused": True, "farming_enabled": False}
