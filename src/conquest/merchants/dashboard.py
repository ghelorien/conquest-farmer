"""Display durable merchant receipts and schedules without driving automation."""

import math
import json
from conquest.merchants.journal import CHARACTERS


def merchant_text(state, *, now, waiting_items=None, global_stopped=False):
    from conquest.merchants.simple_controls import current_refill_blocker

    snapshot = state.get("snapshot") or {}
    scan = state.get("scan", {})
    market = state.get("market_refresh", {})
    progress = state.get("batch_progress", {})
    refill = state.get("refill", {})
    pending = [
        r
        for r in state.get("pending", [])
        if r.get("phase") == "uncertain" or not state.get("input_active")
    ]
    error = state.get("error") or {}
    title = "Connected" if state.get("connected") else "Waiting for client"
    stock = (
        f"Inventory {len(snapshot.get('inventory', []))}/{snapshot.get('capacity', '?')} · Shop {len(snapshot.get('booth', []))}/32"
        if snapshot
        else "Inventory and shop counts unavailable"
    )
    action = ""
    if pending:
        before = json.loads(pending[0].get("before_json", "{}"))
        name = (before.get("item") or {}).get("name", "operation")
        batch = f"Needs attention: interrupted {name}; automatic retry is blocked."
        detail = json.loads(pending[0].get("result_json") or "{}").get(
            "note", "Result needs reconciliation"
        )
        action = (
            "Reason: "
            + detail
            + " Check Logs; verify the item and silver before retrying."
        )
    elif market.get("pending"):
        if market.get("phase") == "failed":
            batch = "Market download failed · retry in " + countdown(
                market.get("retry_at", now), now
            )
            action = market.get("error", "Prices unchanged; retry is automatic.")
        else:
            batch = "Downloading America market prices…"
            action = (
                "Shop update starts after the download."
                if scan.get("pending")
                else "Downloading price data only. Your shop listings stay unchanged."
            )
    elif market.get("phase") == "needs_setup":
        batch = "Market prices unavailable: browser setup needed"
        action = market.get("error", "Install the market browser, then refresh.")
    elif scan.get("pending"):
        if not state.get("enabled"):
            batch = "Shop update paused"
            action = "Click Resume shop update to finish this request."
        elif error:
            batch = "Shop update waiting: " + error["note"]
            action = (
                "Continues when your mouse is idle and the farmer can safely share control."
                if "input" in error["note"].lower()
                or "handoff" in error["note"].lower()
                else "The update stays queued until this problem is resolved."
            )
        elif progress.get("request_id") and progress["request_id"] == scan.get(
            "request_id"
        ):
            batch = f"Updating shop: {progress.get('changed', 0)} done · {progress.get('remaining', 0)} remaining"
            action = (
                "Current item: " + progress["item"]
                if progress.get("item")
                else "Preparing the next item."
            )
        else:
            batch = "Shop update queued · checking item prices"
    elif error:
        batch = "Waiting: " + error["note"]
    elif scan.get("completed_at"):
        batch = f"Last shop update finished: {scan.get('changed', 0)} listings added or repriced."
        if waiting_items:
            unpriced = sum(item.get("price") is None for item in waiting_items)
            reasons = []
            if unpriced:
                reasons.append(f"{unpriced} need a safe price")
            if len(waiting_items) > unpriced:
                reasons.append(
                    f"{len(waiting_items) - unpriced} need shop space or safe input"
                )
            action = (
                "Waiting items: "
                + ", ".join(reasons)
                + ". Open the Waiting items tab for reasons."
            )
        elif waiting_items is None and scan.get("deferred"):
            action = f"{scan['deferred']} items were left waiting at that update. Open Waiting items for current reasons."
    else:
        batch = "Ready for a shop update."
    if state.get("delivery"):
        batch = state["delivery"]["state"].capitalize()
        action = state["delivery"]["reason"]
    if refill.get("enabled"):
        peer_blocker = current_refill_blocker(state) if not global_stopped else None
        if peer_blocker:
            refill_note = peer_blocker
        elif refill.get("status") == "paused_budget" and refill.get("pending"):
            refill_note = f"safely deferred · {len(refill.get('cursor', []))} queued · {refill.get('listed', 0)} listed this check"
        else:
            refill_note = (
                "waiting for safe input"
                if refill.get("pending")
                else "next check in " + countdown(refill.get("next_check", now), now)
            )
        timer = (
            f"Auto-refill ON: fills empty shop slots using saved prices · {refill_note}"
        )
    else:
        timer = "Auto-refill OFF: empty shop slots stay empty until you update the shop or enable refill."
    if scan.get("pending") and scan.get("one_time"):
        mode = "One-time shop update: stops when finished. Incoming trades are paused."
    elif state.get("enabled"):
        mode = "Auto-manage ON: incoming trades, scheduled price updates and reconnect recovery."
    else:
        mode = "Auto-manage OFF: incoming trades, scheduled price updates and reconnect recovery are paused."
    if global_stopped:
        mode = (
            "STOP ALL is active: farming, merchant actions and auto-refill are stopped."
        )
        timer = "Enable only the controls you want to restart."
    login = (
        "Reconnect setup needed: More / help → Set up automatic login"
        if state.get("credentials_saved") is False
        else ""
    )
    return "\n".join(
        line
        for line in (title + " · " + stock, batch, action, mode, timer, login)
        if line
    )


def countdown(due, now):
    seconds = max(0, math.ceil(due - now))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def on_sale_text(characters, *, now):
    totals = {}
    for character in CHARACTERS:
        state = characters.get(character, {})
        snapshot = state.get("snapshot") or {}
        booth = snapshot.get("booth")
        if (
            state.get("connected")
            and snapshot.get("booth_open")
            and 0 <= now - snapshot.get("timestamp", 0) <= 10
            and isinstance(booth, list)
            and all(
                type(item.get("price")) is int and item["price"] > 0 for item in booth
            )
        ):
            # The booth price is the total asking price for the listing/stack.
            totals[character] = sum(item["price"] for item in booth)
    complete = len(totals) == len(CHARACTERS)
    total = f"{sum(totals.values()):,} silver" if totals else "unavailable"
    parts = [
        f"Currently on sale: {total}"
        + (" (partial)" if totals and not complete else "")
    ]
    parts.extend(
        f"{c}: {totals[c]:,}" if c in totals else f"{c}: unavailable"
        for c in CHARACTERS
    )
    return "     |     ".join(parts)


def header_text(sales, reporting, characters, *, now):
    state = reporting.get("status", "unavailable")
    due = reporting.get("next_due")
    retry = reporting.get("retry_at") or 0
    if state not in ("waiting", "delivered", "already_delivered"):
        report = state.replace("_", " ")
        if retry > now:
            report += f" · retry {countdown(retry, now)}"
    elif not due:
        report = "not scheduled"
    elif due <= now:
        report = "due now"
    else:
        report = countdown(due, now)
    scans = []
    for character in CHARACTERS:
        status = characters.get(character, {})
        scan = status.get("scan", {})
        if not status.get("enabled"):
            text = "paused"
        elif scan.get("pending"):
            text = "queued / awaiting safe input"
        elif scan.get("next_scan"):
            text = countdown(scan["next_scan"], now)
        else:
            text = "not scheduled"
        scans.append(f"{character}: {text}")
    timers = f"Discord #shops (4h): {report}     |     Reprice (12h) — " + " · ".join(
        scans
    )

    rows = sales.get("characters", {})
    tracked = [r for r in rows.values() if r.get("started_at") is not None]
    total = sum(r["total"]["silver"] for r in tracked)
    amount = f"{total:,}" if tracked else "unavailable"
    parts = [f"Net silver earned: {amount} (since tracking began)"]
    for character in CHARACTERS:
        row = rows.get(character, {})
        amount = (
            f"{row['total']['silver']:,}"
            if row.get("started_at") is not None
            else "unavailable"
        )
        parts.append(f"{character}: {amount}")
    if any(r.get("recovered_silver") for r in tracked):
        parts.append("Includes reconciled sales history")
    uncertain = sum(r.get("unconfirmed", 0) for r in tracked)
    if uncertain:
        parts.append(f"{uncertain} stock departures awaiting confirmation")
    if len(tracked) != len(CHARACTERS) or any(
        r.get("gaps")
        or r.get("unconfirmed")
        or now - (r.get("last_observed_at") or 0) > 10
        for r in tracked
    ):
        parts.append("Coverage incomplete")
    return {
        "timers": timers,
        "silver": "     |     ".join(parts),
        "on_sale": on_sale_text(characters, now=now),
    }
