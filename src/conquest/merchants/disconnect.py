"""Protective account disconnect through the existing privileged app."""

import time
from conquest.merchants.journal import character_name
from conquest.storage_halt import disconnect_exact_client


def disconnect(runtime, character, *, close=disconnect_exact_client):
    if not runtime.coordinator.lock.acquire(blocking=False):
        raise ValueError("Wait for current input to release before disconnecting")
    try:
        return _disconnect_locked(runtime, character, close=close)
    finally:
        runtime.coordinator.lock.release()


def _disconnect_locked(runtime, character, *, close):
    character = character_name(character)
    if runtime.coordinator.owner is not None:
        raise ValueError("Wait for current input to release before disconnecting")
    if runtime.journal.pending(character):
        raise ValueError(
            "Reconcile the pending merchant transaction before disconnecting"
        )
    identity = runtime.journal.get(character, "last_identity")
    if not identity:
        raise ValueError("No verified merchant process identity is available")
    runtime.enable(character, False)
    runtime.set_refill_enabled(character, False)
    runtime.journal.set(character, "connect_hold", True)
    runtime.journal.set(
        character,
        "attention",
        {
            "kind": "protective_disconnect",
            "note": "Merchant stopped for protection; stock or life-state discrepancy requires reconciliation before reconnecting.",
        },
    )
    # This helper checks path and creation time on the same process handle;
    # stale or reused PIDs cannot close a different client.
    closed = close(identity)
    result = {
        "character": character,
        "disconnected": closed is True,
        "time": time.time(),
        "identity": identity,
    }
    runtime.journal.set(character, "protective_disconnect", result)
    runtime.journal.event(
        character, "protective_disconnect", disconnected=closed is True
    )
    return result
