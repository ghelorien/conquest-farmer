"""Read-only sales receipts: removed booth stock plus the matching silver gain."""

import json
import time
from conquest.merchants.controller import identities
from conquest.merchants.journal import CHARACTERS, character_name
from conquest.character_context import trusted_delivery


RECEIPT_WINDOW = 5


def net_bounds(items):
    """Observed America booth deduction is 3%; retain one-silver rounding bounds.

    Receipts always use the actual balance delta, never the calculated estimate.
    Historical client receipts show both rounding directions for fractional silver.
    A booth price already covers the entire stack.
    """
    return (
        sum(i["price"] * 97 // 100 for i in items),
        sum((i["price"] * 97 + 99) // 100 for i in items),
    )


def missing_stock(before, current):
    present = {i["uid"] for i in current["booth"] + current["inventory"]}
    return [i for i in before["booth"] if i["uid"] not in present]


def unrelated_request(character, snapshot):
    request = snapshot.get("request")
    if not request:
        return True
    participant = request.get("participant")
    uid = request.get("participant_uid")
    return (
        isinstance(participant, str)
        and bool(participant.strip())
        and (uid is None or type(uid) is int and uid > 0)
        and not trusted_delivery(character, participant, uid, require_uid=False)
    )


def observe(journal, snapshot):
    character = character_name(snapshot["character"])
    if snapshot.get("server") != "America":
        return
    # Keep only observation evidence; no GUI window addresses or secrets.
    current = {
        k: snapshot[k]
        for k in (
            "identity",
            "timestamp",
            "inventory",
            "booth",
            "silver",
            "request",
            "trade",
        )
    }
    at = current["timestamp"]
    with journal.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM sales_baseline WHERE character=?", (character,)
        ).fetchone()
        latest = json.loads(row["snapshot"]) if row else None
        if latest and at <= latest["timestamp"]:
            return
        before = latest.get("_sales_anchor", latest) if latest else None
        target = getattr(character, "profile_id", str(character))
        if db.execute(
            "SELECT 1 FROM state WHERE character=? AND name='manual_reader_hold' AND value!='null'",
            (character,),
        ).fetchone():
            return
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_rebaseline'"
        ).fetchone():
            if db.execute(
                "SELECT 1 FROM manual_rebaseline WHERE target_profile_id=? AND phase!='completed'",
                (character,),
            ).fetchone():
                return
        # Manual intervals include their entire stabilization window. This
        # guard also covers independent observers and app restart. The atomic
        # settlement callback installs the fresh post-session baseline.
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_sessions'"
        ).fetchone():
            if db.execute(
                "SELECT 1 FROM manual_sessions m WHERE target_profile_id=? AND created_at<=? AND (phase NOT IN ('completed','request_withdrawn','declined_verified','operator_overridden') OR COALESCE(json_extract(terminal_json,'$.settled_at'),json_extract(terminal_json,'$.at'))>? OR (json_extract(terminal_json,'$.settled_at') IS NULL AND json_extract(terminal_json,'$.at') IS NULL AND NOT EXISTS (SELECT 1 FROM events e WHERE e.character=? AND e.event='sales_observation_gap' AND json_extract(e.payload,'$.session_id')=m.id) AND NOT EXISTS (SELECT 1 FROM manual_rebaseline r WHERE r.source_session_id=m.id AND r.phase='completed' AND r.last_observed_at<=?))) LIMIT 1",
                (
                    target,
                    at,
                    before["timestamp"] if before else at,
                    target,
                    before["timestamp"] if before else at,
                ),
            ).fetchone():
                return
        # The operator handoff has a separate durable interval table.  Do not
        # infer a sale across its preparation/settlement window, including
        # after an app restart or a terminal interval with no sale receipt.
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_handoffs'"
        ).fetchone():
            if db.execute(
                "SELECT 1 FROM manual_handoffs h JOIN manual_handoff_participants p ON p.session_id=h.id WHERE p.target_profile_id=? AND h.created_at<=? AND (h.phase!='completed' OR p.last_at>?) LIMIT 1",
                (target, at, before["timestamp"] if before else at),
            ).fetchone():
                return
        if before:
            old = {i["uid"]: i for i in before["booth"]}
            booth = {i["uid"]: i for i in current["booth"]}
            inventory = identities(current["inventory"])
            missing = missing_stock(before, current)
            gap = (
                latest["identity"] != current["identity"]
                or at - latest["timestamp"] > 10
            )
            if gap:
                db.execute(
                    "INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)",
                    (
                        character,
                        "sales_observation_gap",
                        json.dumps({"from": before["timestamp"], "to": at}),
                        at,
                    ),
                )
                # A missing booth UID across an observation gap may have been
                # sold, moved, or traded. None of those outcomes is attributable
                # from these two samples. Establish a fresh baseline without
                # manufacturing even an unconfirmed sale/departure receipt.
                db.execute(
                    "INSERT OR REPLACE INTO sales_baseline VALUES(?,?,?)",
                    (character, json.dumps(current), row["started_at"]),
                )
                return
            busy = db.execute(
                "SELECT 1 FROM transactions WHERE character=? AND created<=? AND updated>=? LIMIT 1",
                (character, at, before["timestamp"]),
            ).fetchone()
            # An unrelated incoming request cannot move stock or silver. Treat
            # it as context, while a trusted delivery request or any open trade
            # still makes the causal receipt ambiguous.
            foreign_request = bool(before["request"] or current["request"])
            stable = (
                not gap
                and not busy
                and unrelated_request(character, before)
                and unrelated_request(character, current)
                and not before["trade"]
                and not current["trade"]
                and identities(before["inventory"]) == inventory
                and all(
                    uid in old
                    and old[uid]["price"] == i["price"]
                    and identities([old[uid]]) == identities([i])
                    for uid, i in booth.items()
                )
            )
            gain = current["silver"] - before["silver"]
            low, high = net_bounds(missing)
            pending_since = latest.get("_sales_pending_since", at)
            verified = (
                bool(missing)
                and stable
                and low <= gain <= high
                and at - pending_since <= RECEIPT_WINDOW
            )
            # Stock and silver can arrive in separate client updates, in either order.
            # Save the original observation across restart, with a bounded deadline.
            if (
                not verified
                and stable
                and (missing or gain > 0)
                and gain >= 0
                and at - pending_since < RECEIPT_WINDOW
            ):
                current["_sales_anchor"] = before
                current["_sales_pending_since"] = pending_since
            elif missing:
                note = (
                    "Booth removal and net silver gain after 3% deduction verified"
                    if verified
                    else "Stock disappeared without an unambiguous silver receipt"
                )
                items = [
                    {
                        k: i[k]
                        for k in (
                            "uid",
                            "name",
                            "type_id",
                            "plus",
                            "gem1",
                            "gem2",
                            "quantity",
                            "bound",
                            "price",
                        )
                    }
                    for i in missing
                ]
                db.execute(
                    "INSERT INTO sales(character,observed_at,phase,items,silver,note) VALUES(?,?,?,?,?,?)",
                    (
                        character,
                        at,
                        "verified" if verified else "unconfirmed",
                        json.dumps(items),
                        gain if verified else 0,
                        note,
                    ),
                )
                db.execute(
                    "INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)",
                    (
                        character,
                        "sale_verified" if verified else "sale_unconfirmed",
                        json.dumps(
                            {
                                "items": items,
                                "silver": gain if verified else None,
                                "note": note,
                                "before_silver": before["silver"],
                                "after_silver": current["silver"],
                                "from": before["timestamp"],
                                "gross": sum(i["price"] for i in missing),
                                "net_bounds": [low, high],
                                "deduction": sum(i["price"] for i in missing) - gain
                                if verified
                                else None,
                                "foreign_request_observed": foreign_request,
                            }
                        ),
                        at,
                    ),
                )
        db.execute(
            "INSERT OR REPLACE INTO sales_baseline VALUES(?,?,?)",
            (character, json.dumps(current), row["started_at"] if row else at),
        )


def qualified_delivery_receipts(journal, intent, merchant):
    """Return exact verified sale receipts wholly inside one delivery interval."""
    character = character_name(merchant["character"])
    started = max(intent["farmer"]["timestamp"], intent["merchant"]["timestamp"])
    ended = merchant["timestamp"]
    if ended < started:
        return []
    with journal.db() as db:
        if db.execute(
            "SELECT 1 FROM events WHERE character=? AND event='sales_observation_gap' AND timestamp>? AND timestamp<=? LIMIT 1",
            (character, started, ended),
        ).fetchone():
            raise ValueError("Sales observation gap overlaps delivery reconciliation")
        rows = list(
            db.execute(
                "SELECT id,observed_at,phase,items,silver FROM sales WHERE character=? AND phase='verified' AND observed_at>? AND observed_at<=? ORDER BY id",
                (character, started, ended),
            )
        )
    return [
        {
            "id": row["id"],
            "observed_at": row["observed_at"],
            "phase": row["phase"],
            "items": json.loads(row["items"]),
            "silver": row["silver"],
        }
        for row in rows
    ]


def summary(journal, *, now=None, since=None):
    now = time.time() if now is None else now
    since = now - 14400 if since is None else since
    result = {"at": now, "since": since, "characters": {}}
    with journal.db() as db:
        for character in CHARACTERS:
            baseline = db.execute(
                "SELECT * FROM sales_baseline WHERE character=?", (character,)
            ).fetchone()
            rows = list(
                db.execute(
                    "SELECT * FROM sales WHERE character=? AND observed_at<=?",
                    (character, now),
                )
            )

            def totals(selected):
                return {
                    "items": sum(
                        sum(i["quantity"] for i in json.loads(r["items"]))
                        for r in selected
                    ),
                    "silver": sum(r["silver"] for r in selected),
                }

            verified = [r for r in rows if r["phase"] == "verified"]
            recovered = list(
                db.execute(
                    "SELECT * FROM sales_reconciliations WHERE character=? AND until_at<=?",
                    (character, now),
                )
            )
            total = totals(verified)
            period = totals([r for r in verified if r["observed_at"] > since])
            for receipt in recovered:
                for dest in (
                    [total, period] if receipt["first_sale_at"] > since else [total]
                ):
                    dest["silver"] += receipt["silver"]
                    dest["items"] += receipt["items"]
            saved = json.loads(baseline["snapshot"]) if baseline else {}
            pending = (
                len(missing_stock(saved["_sales_anchor"], saved))
                if "_sales_anchor" in saved
                else 0
            )
            gaps = db.execute(
                "SELECT COUNT(*) FROM events WHERE character=? AND event='sales_observation_gap' AND timestamp>? AND timestamp<=?",
                (character, since, now),
            ).fetchone()[0]
            result["characters"][character] = {
                "started_at": baseline["started_at"] if baseline else None,
                "last_observed_at": json.loads(baseline["snapshot"])["timestamp"]
                if baseline
                else None,
                "period": period,
                "total": total,
                "gaps": gaps,
                "recovered_silver": sum(r["silver"] for r in recovered),
                "period_incomplete": any(
                    r["first_sale_at"] <= since < r["until_at"] for r in recovered
                ),
                "pending": pending,
                "unconfirmed": pending
                + sum(
                    len(json.loads(r["items"]))
                    for r in rows
                    if r["phase"] not in ("verified", "reconciled")
                ),
            }
    return result


def format_summary(data):
    lines = ["**Shop sales — 4-hour update**"]
    lines.append(
        "Reporting through "
        + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data["at"]))
    )
    lines.append("Silver totals are net proceeds after booth deductions.")
    total_items = total_silver = 0
    period_items = period_silver = 0
    for character, row in data["characters"].items():
        period, total = row["period"], row["total"]
        if row["started_at"] is None:
            lines.append(f"**{character}:** sales tracking unavailable")
            continue
        period_note = (
            "incomplete; confirmed within window "
            if row.get("period_incomplete")
            else ""
        )
        lines.append(
            f"**{character}:** last 4h {period_note}{period['items']} items / {period['silver']:,} silver; "
            f"since tracking began {total['items']} items / {total['silver']:,} silver."
        )
        period_items += period["items"]
        period_silver += period["silver"]
        total_items += total["items"]
        total_silver += total["silver"]
        if row["unconfirmed"]:
            lines.append(
                f"  {row['unconfirmed']} unconfirmed stock departures excluded from sales totals."
            )
        if row.get("recovered_silver"):
            lines.append(
                f"  Includes {row['recovered_silver']:,} silver recovered by reconciling historical stock and balances."
            )
        if row.get("period_incomplete"):
            lines.append(
                "  A recovered batch crosses this reporting boundary; its silver is included only in the cumulative total."
            )
        if row["gaps"] or data["at"] - (row["last_observed_at"] or 0) > 10:
            lines.append(
                "  Observation gaps/offline time: verified totals may be incomplete."
            )
    period_note = (
        "incomplete; confirmed within window "
        if any(r.get("period_incomplete") for r in data["characters"].values())
        else ""
    )
    lines.append(
        f"**Combined verified:** last 4h {period_note}{period_items} items / {period_silver:,} silver; "
        f"cumulative {total_items} items / {total_silver:,} silver."
    )
    starts = [
        r["started_at"]
        for r in data["characters"].values()
        if r["started_at"] is not None
    ]
    if starts:
        lines.append(
            "Tracking started "
            + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(min(starts)))
            + ". Earlier sales are unavailable; listings and reprices are not sales."
        )
    return "\n".join(lines)
