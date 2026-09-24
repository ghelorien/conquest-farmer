"""Simple merchant switch preserving independently selected permissions."""


def toggle(ui, character):
    runtime = ui.runtime
    trading = runtime.enabled(character)
    refill = runtime.refill_enabled(character)
    if trading or refill:
        runtime.journal.set(
            character, "resume_permissions", {"trading": trading, "refill": refill}
        )
        ui.pause(character)
        runtime.set_refill_enabled(character, False)
        return
    saved = runtime.journal.get(
        character, "resume_permissions", {"trading": True, "refill": True}
    )
    if saved.get("trading"):
        ui.resume(character)
    if saved.get("refill"):
        ui.resume_refill(character)


def current_refill_blocker(state):
    current = state.get("foreground_refill_1078") or {}
    if (
        (state.get("refill") or {}).get("enabled")
        and state.get("connected")
        and current.get("state") == "waiting"
        and current.get("blocker") == "owned_peer_observation_unavailable"
        and current.get("unavailable_peer") in ("Spiritual", "Dutch")
    ):
        return (
            f"Auto-refill waiting: {current['unavailable_peer']} owned booth memory "
            "is unavailable; listing prices remain deferred."
        )
    return None


def summary(state, *, now, global_stopped=False):
    from conquest.merchants.dashboard import countdown

    trading = bool(state.get("enabled"))
    refill = state.get("refill") or {}
    running = trading or refill.get("enabled", False)
    snapshot = state.get("snapshot") or {}
    refill_problem = current_refill_blocker(state) if not global_stopped else None
    if state.get("manual_only_1078"):
        title = (
            "OBSERVING ONLY — 1078 merchant input qualification pending"
            if state.get("connected")
            else "WAITING FOR 1078 CLIENT — memory observation unavailable"
        )
        stock = (
            f"Shop {len(snapshot.get('booth', []))} · Inventory {len(snapshot.get('inventory', []))}/{snapshot.get('capacity', '?')}"
            if snapshot
            else "Shop and inventory counts unavailable"
        )
        error = state.get("error") or {}
        return "\n".join(
            x for x in (title, stock, refill_problem, error.get("note")) if x
        )
    attention = state.get("needs_attention")
    uncertain = any(p.get("phase") == "uncertain" for p in state.get("pending", []))
    if global_stopped:
        title = "STOPPED — Global Stop is active"
    elif not running:
        title = "PAUSED — merchant automation is off"
    elif not state.get("connected"):
        title = "WAITING FOR CLIENT — automation enabled"
    elif uncertain:
        title = "NEEDS ATTENTION — transaction result must be reconciled"
    elif state.get("input_active"):
        title = "WORKING — " + (state.get("activity") or "updating shop")
    elif refill_problem:
        title = "WAITING — owned peer price proof unavailable"
    elif state.get("error"):
        title = "WAITING — " + state["error"].get("note", "check status details")
    else:
        title = "ACTIVE — " + (
            "ready for the next shop check"
            if not refill.get("pending")
            else "waiting for safe farmer handoff"
        )
    stock = f"Shop {len(snapshot.get('booth', []))}/32 · Inventory {len(snapshot.get('inventory', []))}/{snapshot.get('capacity', '?')}"
    modes = (
        "Trading & repricing: "
        + ("enabled" if trading else "paused")
        + " · Refill: "
        + (
            "every 15 min; next " + countdown(refill.get("next_check") or now, now)
            if refill.get("enabled")
            else "paused"
        )
    )
    warning = (
        ("Unresolved incident: " + attention.get("note", "see status details"))
        if attention
        else ""
    )
    return "\n".join(x for x in (title, stock, modes, refill_problem, warning) if x)
