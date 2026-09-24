"""Explicit supervised process-loss test, preserving stock and recovery intent."""

import threading
import time

from conquest.capture import CaptureUnavailable
from conquest.merchants.journal import character_name
from conquest.merchants.qualification import stock
from conquest.merchants.recovery import credential_path


def start(ui, character):
    character = character_name(character)
    runtime = ui.runtime
    if (
        ui.closed
        or ui.app.closing
        or not ui.safe_to_yield()
        or ui.app.control.snapshot()["enabled"]
        or runtime.enabled(character)
        or ui.coordinator.owner
        or ui.coordinator.stopped
        or runtime.connecting
        or ui.calibrating
        or getattr(runtime, "delivery_window", None)
        or getattr(runtime, "refill_window", None)
    ):
        raise ValueError(
            "Recovery trial requires a safe stopped farmer and a paused idle merchant"
        )
    if runtime.journal.pending(character) or not credential_path(character).is_file():
        raise ValueError(
            "Recovery trial needs saved credentials and reconciled transactions"
        )
    previous = runtime.journal.get(character, "recovery_trial", {})
    if previous.get("phase") in ("close_submitted", "recovering"):
        raise ValueError(
            "Reconcile the existing recovery trial before another disconnect"
        )
    driver = runtime.controllers[character].driver
    driver.require_qualified("login")
    before = driver.memory.read()
    validate_before(before)
    revision = ui.app.control.snapshot()["revision"]
    cancel = threading.Event()
    ui.calibrating.add(character)
    ui.calibration_cancel[character] = cancel
    state = dict(phase="prepared", before=before, started_at=time.time())
    runtime.journal.set(character, "recovery_trial", state)

    def check():
        import ctypes

        ui.coordinator.check()
        controls = ui.app.control.snapshot()
        if (
            time.time() - state["started_at"] >= 15
            or cancel.is_set()
            or ui.closed
            or ui.app.closing
            or controls["enabled"]
            or controls.get("paused")
            or controls["revision"] != revision
            or not ui.safe_to_yield()
            or runtime.enabled(character)
            or any(
                ctypes.windll.user32.GetAsyncKeyState(k) & 0x8000 for k in (0x7A, 0x7B)
            )
        ):
            raise CaptureUnavailable("Recovery trial cancelled by manual control")

    def work():
        try:
            from conquest.storage_halt import disconnect_exact_client

            with ui.coordinator.lease(character, purpose="recovery_test"):
                check()
                fresh = driver.memory.read()
                validate_before(fresh)
                if stock(fresh) != stock(before) or any(
                    fresh[k] != before[k]
                    for k in ("position", "character_uid", "own_booth_uid")
                ):
                    raise ValueError("Merchant changed before recovery trial")
                runtime.returns[character].remember(fresh)
                home = runtime.journal.get(character, "shop_home")
                runtime.returns[character].save(
                    dict(
                        started_at=time.time(),
                        moves=0,
                        stalls=0,
                        before=fresh,
                        home=home,
                    ),
                    "returning",
                )
                state.update(phase="close_submitted", submitted_at=time.time())
                runtime.journal.set(character, "recovery_trial", state)
                if not disconnect_exact_client(fresh["identity"]):
                    raise ValueError("Exact merchant process close was not verified")
                state.update(phase="recovering", disconnected_at=time.time())
                runtime.journal.set(character, "recovery_trial", state)
            check()
            runtime.recoveries[character].retry()
            runtime.enable(character, True)
        except Exception as error:
            state.update(error=str(error), finished_at=time.time())
            if state["phase"] == "prepared":
                state["phase"] = "cancelled_before_input"
            runtime.journal.set(character, "recovery_trial", state)
        finally:
            ui.calibrating.discard(character)

    threading.Thread(
        target=work, name="recovery-test-" + character, daemon=True
    ).start()
    return {"started": character, "phase": "prepared"}


def validate_before(snapshot):
    if (
        snapshot["map_id"] != 1036
        or not snapshot.get("own_booth_uid")
        or not snapshot.get("booth_open")
        or snapshot.get("trade")
        or snapshot.get("request")
        or snapshot["hp"] <= 0
        or len(snapshot["inventory"]) + len(snapshot["booth"]) > snapshot["capacity"]
        or any(w["name"] == "Add Item to Booth" for w in snapshot["windows"])
    ):
        raise ValueError(
            "Recovery trial needs an idle owned Market shop and space for all returned stock"
        )
