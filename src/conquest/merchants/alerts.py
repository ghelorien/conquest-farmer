"""Independent, durable #shops failure notifications; no game input or AI."""

from conquest.character_context import state_path
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from conquest.discord_notify import DeliveryError, deliver, read_json, write_json
from conquest.merchants.bridge import request
from conquest.merchants.pricing import OWNED
from conquest.merchants.sales_report import SECRET, load_webhook

STATE = Path(state_path(".runtime/merchants/shops-alerts.json"))
STATUS = Path(state_path("reports/merchants/shops-alert-status.json"))
LIFECYCLE = Path(state_path(".runtime/merchants/app-lifecycle.json"))
QUIET_WAITS = (
    "Automation stopped or manual input active",
    "Mouse control is yours",
    "Manual visitor session holds automation input",
    "Waiting for input owner",
    "Waiting for a safe farmer handoff",
    "Farmer handoff was revoked",
    "Paused; recovery will not change manual intent",
)
MONITOR_VERSION = 2


def safe_note(value):
    text = re.sub(r"https?://\S+", "[URL redacted]", str(value))
    text = re.sub(
        r"(?i)(password|token|authorization|webhook)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        text,
    )
    return text[:500]


def unavailable_owned_peer(state):
    """Only an active merchant's current native refill failure needs notice."""
    refill = state.get("refill") or {}
    current = state.get("foreground_refill_1078") or {}
    peer = current.get("unavailable_peer")
    if (
        state.get("enabled") is True
        and refill.get("enabled") is True
        and state.get("connected") is True
        and current.get("state") == "waiting"
        and current.get("blocker") == "owned_peer_observation_unavailable"
        and isinstance(peer, str)
        and peer.casefold() in OWNED
    ):
        return peer
    return None


NATIVE_TERMINAL = ("complete", "operator_overridden", "restored_observed")
NATIVE_NOTICES = {
    "logged_in_awaiting_return": (
        "logged back in",
        "Login verified after a disconnect (same character and process, two "
        "stable in-world readings). Return to Market and shop restoration are "
        "pending and not yet automated; the shop is not restored.",
    ),
    "needs_attention": (
        "disconnect recovery stopped",
        "Automatic disconnect recovery stopped and will not retry: {note}",
    ),
}


def native_incident(state):
    """An unresolved exact-1078 disconnect recovery incident, if any."""
    native = state.get("native_return_1078")
    if (
        isinstance(native, dict)
        and native.get("id")
        and native.get("phase") not in NATIVE_TERMINAL
    ):
        return native
    return None


def native_note(native):
    phase = native.get("phase")
    if phase == "needs_attention":
        return "Disconnect recovery needs attention: " + str(
            native.get("note") or "automatic recovery stopped"
        )
    if phase == "logged_in_awaiting_return":
        return (
            "Reconnected after a disconnect; login verified. Return to Market and "
            "shop restoration are pending and not yet automated."
        )
    if phase == "login_submitted":
        return (
            "Merchant was disconnected. One automatic login was submitted and is "
            "being verified; it will not be repeated."
        )
    return (
        "Merchant was disconnected and is at the login screen. One automatic "
        "login attempt is armed; no travel or shop restoration is automated yet."
    )


def login_problem(state):
    """A memory-proven login screen that automatic recovery cannot handle."""
    login = state.get("login_1078") or {}
    if login.get("at_login") and login.get("reason") and not native_incident(state):
        return (
            "Merchant is at the login screen: " + str(login["reason"]),
            60,
        )
    return None


def condition(state):
    manual = state.get("manual_session") or {}
    if manual.get("phase") == "needs_attention":
        return safe_note(
            "Manual visitor session needs attention: " + str(manual.get("reason"))
        ), 0
    if manual.get("request_state") == "decline_claimed":
        return (
            "Manual decline input was claimed; fresh unchanged ownership is required. Input will not be retried.",
            60,
        )
    attention = state.get("needs_attention")
    if attention:
        return safe_note(attention["note"]), 0
    pending = state.get("pending", [])
    if any(p["phase"] == "uncertain" for p in pending):
        return (
            "A trade or listing has an uncertain result. Reconciliation is required before retrying.",
            0,
        )
    if pending:
        return (
            "A trade or listing has not completed. Check the transaction and game connection.",
            60,
        )
    native = native_incident(state)
    if native:
        # An active disconnect incident alerts even with operations Off.
        return safe_note(native_note(native)), 0
    login = login_problem(state)
    if login:
        return safe_note(login[0]), login[1]
    if not state.get("connected"):
        return (
            "Client disconnected or memory observation unavailable. Check the client/reconnect status.",
            60,
        )
    peer = unavailable_owned_peer(state)
    if peer:
        return (
            f"Auto-refill is waiting: {peer} owned booth memory is unavailable, so owned-price safety prevents listing.",
            60,
        )
    error = state.get("error")
    if error and not str(error.get("note", "")).startswith(QUIET_WAITS):
        return safe_note(error["note"]), 60
    returning = state.get("shop_return")
    if returning and returning["phase"] not in ("complete", "operator_overridden"):
        return (
            "Reconnect is not complete: returning to Market and restoring the shop.",
            60,
        )
    return None


class Alerts:
    def __init__(self, state=None):
        self.state = state if state is not None else {}
        self.state.setdefault("incidents", {})
        self.state.setdefault("queue", [])

    def observe(self, subject, problem, now):
        incident = self.state["incidents"].get(subject)
        if problem:
            note, delay = problem
            self.state["queue"] = [
                r
                for r in self.state["queue"]
                if not (r["subject"] == subject and r["kind"] == "recovery")
            ]
            if incident is None:
                incident = {"id": uuid.uuid4().hex, "since": now, "sent": False}
                self.state["incidents"][subject] = incident
            incident.update(note=safe_note(note), healthy_since=None)
            if (
                now - incident["since"] >= delay
                and not incident.get("queued")
                and not incident["sent"]
            ):
                self.state["queue"].append(
                    {
                        "id": incident["id"],
                        "subject": subject,
                        "kind": "failure",
                        "content": f"**{subject} — needs attention**\n{incident['note']}",
                        "created": now,
                        "retry_at": 0,
                    }
                )
                incident["queued"] = True
            return
        if not incident:
            return
        if not incident["sent"]:
            self.state["queue"] = [
                r for r in self.state["queue"] if r["id"] != incident["id"]
            ]
            self.state["incidents"].pop(subject)
            return
        if incident.get("healthy_since") is None:
            incident["healthy_since"] = now
        if now - incident["healthy_since"] >= 5:
            self.state["queue"].append(
                {
                    "id": uuid.uuid4().hex,
                    "subject": subject,
                    "kind": "recovery",
                    "content": f"**{subject} — recovery confirmed**\nFresh status checks confirm the reported issue has cleared.",
                    "created": now,
                    "retry_at": 0,
                }
            )
            self.state["incidents"].pop(subject)

    def native_notice(self, subject, state, now):
        """One durable #shops notice per incident milestone, never repeated."""
        native = state.get("native_return_1078")
        if not isinstance(native, dict) or not native.get("id"):
            return
        notice = NATIVE_NOTICES.get(native.get("phase"))
        if notice is None:
            return
        key = f"{native['id']}:{native['phase']}"
        sent = self.state.setdefault("native_notices", [])
        if key in sent:
            return
        title, body = notice
        self.state["queue"].append(
            {
                "id": uuid.uuid4().hex,
                "subject": f"{subject} recovery",
                "kind": "notice",
                "content": safe_note(
                    f"**{subject} — {title}**\n"
                    + body.format(note=native.get("note") or "see the app")
                ),
                "created": now,
                "retry_at": 0,
            }
        )
        sent.append(key)
        del sent[:-100]

    def poll(self, status, now, *, clean_shutdown=False):
        if status is None:
            if not clean_shutdown:
                self.observe(
                    "Conquest app",
                    (
                        "Conquest is closed or not responding. Shop automation and sales observation may be interrupted.",
                        60,
                    ),
                    now,
                )
            return  # Missing observations can never confirm merchant recovery.
        stale_ui = status.get("ui_health", {}).get("tick_age_ms", 0) > 15000
        self.observe(
            "Conquest app",
            ("Conquest UI stopped responding; merchant input may be blocked.", 60)
            if stale_ui
            else None,
            now,
        )
        handoff = status.get("manual_handoff") or {}
        # Preparation, bot drain, ordinary reader retries and settlement are
        # deliberately quiet operator waits. Only a real identity/uncertainty
        # disposition is urgent.
        handoff_reason = str(handoff.get("reason") or "")
        handoff_problem = (
            ("Operator manual handoff needs attention: " + handoff_reason, 0)
            if handoff.get("phase") == "needs_attention"
            else (
                "Operator manual handoff has not received fresh memory evidence: "
                + handoff_reason,
                60,
            )
            if handoff.get("phase") in ("preparing", "ready", "ending")
            and handoff_reason.startswith("Waiting for fresh ")
            else None
        )
        self.observe("Operator manual handoff", handoff_problem, now)
        # The outbox is committed by the completed native bank visit.  Polling
        # it here keeps network I/O outside gameplay and survives app restart.
        try:
            from conquest.merchants.bank_stock_alerts import pending as pending_stock

            queued = {row["id"] for row in self.state["queue"]}
            for stock in pending_stock():
                if stock["id"] in queued:
                    continue
                items = json.loads(stock["warehouse_json"])
                names = ", ".join(
                    f"{item.get('name') or item['type_id']} ×{item.get('quantity', 1)}"
                    for item in items[:8]
                )
                source = json.loads(stock["source_json"])
                location = (
                    "Market"
                    if source.get("map_id") == 1036
                    else "map " + str(source.get("map_id", "unknown"))
                )
                self.state["queue"].append(
                    {
                        "id": stock["id"],
                        "subject": "Verified bank stock",
                        "kind": "bank_stock",
                        "content": f"**Manual handoff available — verified bank stock**\nFarmer: {source.get('character', 'verified farmer')}\n{names}\nLocation: {location} warehouse (native memory verified).\nUse Manual handoff before any trade.",
                        "created": now,
                        "retry_at": 0,
                    }
                )
            from conquest.merchants.bank_stock_alerts import issue as stock_issue

            self.observe(
                "Verified bank stock observation",
                (
                    "Verified bank-stock notification observation needs attention: "
                    + stock_issue(),
                    60,
                )
                if stock_issue()
                else None,
                now,
            )
        except Exception:
            # The normal monitor-health alert covers a persistent worker issue;
            # bank completion itself remains successful and never retries input.
            pass
        for character, state in status["characters"].items():
            manual = state.get("manual_session") or {}
            self.native_notice(character, state, now)
            # A disconnect recovery incident (or a login screen it cannot
            # handle) is never a quiet manual wait, even with operations Off.
            disconnect = bool(native_incident(state) or login_problem(state))
            if (
                state.get("manual_input_fence")
                and manual.get("phase") != "needs_attention"
                and manual.get("request_state") != "decline_claimed"
                and not state.get("needs_attention")
                and not disconnect
            ):
                continue  # A manual wait neither alerts nor confirms recovery.
            returning = state.get("shop_return")
            if (
                returning
                and returning["phase"] not in ("complete", "operator_overridden")
                and not state.get("enabled")
                and not state.get("needs_attention")
                and not disconnect
            ):
                continue  # Manual pause neither alerts nor confirms unfinished recovery.
            if (
                not state.get("enabled")
                and not state.get("connected")
                and not state.get("needs_attention")
                and not any(
                    p.get("phase") in ("submitted", "uncertain")
                    for p in state.get("pending", [])
                )
                and manual.get("phase") != "needs_attention"
                and not disconnect
            ):
                continue  # An operations-Off login/disconnect is a manual wait.
            problem = condition(state)
            prior = self.state["incidents"].get(character) or {}
            if prior.get("owned_peer") and problem is None:
                # A restarted app has no current refill result yet. Neither
                # that gap nor a stale last-completed refill proves recovery.
                peer = status["characters"].get(prior["owned_peer"]) or {}
                current = state.get("foreground_refill_1078") or {}
                own_snapshot = state.get("snapshot") or {}
                peer_snapshot = peer.get("snapshot") or {}
                priced_after_peer = (
                    current.get("state")
                    in ("listing_pending", "unknown_prices_deferred")
                    or current.get("blocker") == "waiting_farmer_handoff"
                )
                if (
                    not priced_after_peer
                    or state.get("enabled") is not True
                    or (state.get("refill") or {}).get("enabled") is not True
                    or not state.get("connected")
                    or not peer.get("connected")
                    or not 0 <= now - own_snapshot.get("timestamp", 0) <= 5
                    or not 0 <= now - peer_snapshot.get("timestamp", 0) <= 5
                ):
                    continue
            self.observe(character, problem, now)
            peer = unavailable_owned_peer(state)
            if peer and character in self.state["incidents"]:
                self.state["incidents"][character]["owned_peer"] = peer
        report = status.get("sales_reporting", {})
        failed = report.get("status") in (
            "worker_error",
            "needs_attention",
            "needs_configuration",
            "retry_later",
        )
        stale_report = (
            report.get("last_checked_at") is not None
            and now - report["last_checked_at"] > 60
        )
        problem = (
            ("The four-hour sales-report worker stopped responding.", 60)
            if stale_report
            else (
                (
                    "The four-hour Discord report needs attention: "
                    + str(report.get("status")),
                    60,
                )
                if failed
                else None
            )
        )
        self.observe("Shop sales reporting", problem, now)

    def dispatch(
        self, now, *, send=deliver, load=load_webhook, persist=lambda state: None
    ):
        due = next((r for r in self.state["queue"] if r["retry_at"] <= now), None)
        if due is None:
            return
        if due["kind"] == "bank_stock" and due.get("confirmed_receipt"):
            # Network delivery already succeeded. Persisted ack-only retries
            # must never re-send that Discord message after restart.
            try:
                from conquest.merchants.bank_stock_alerts import acknowledge

                acknowledge(due["id"], due["confirmed_receipt"])
            except Exception:
                due["retry_at"] = now + 60
                persist(self.state)
                return
            self.state["queue"].remove(due)
            persist(self.state)
            return
        # Persist the queue before network I/O. Like the farmer notifier, an
        # ambiguous network receipt can cause a duplicate on a later retry.
        persist(self.state)
        try:
            receipt = send(load(), due["content"])
            if not receipt:
                raise DeliveryError("Discord did not confirm the message")
        except Exception as error:
            delay = error.retry_after if isinstance(error, DeliveryError) else 60
            due["retry_at"] = now + max(2, min(3600, delay))
            self.state["delivery_error"] = (
                "Discord alert delivery failed; queued for retry"
            )
        else:
            incident = self.state["incidents"].get(due["subject"])
            if incident and due["kind"] == "failure" and incident["id"] == due["id"]:
                incident["sent"] = True
            if due["kind"] == "bank_stock":
                due["confirmed_receipt"] = str(receipt)
                due["retry_at"] = now
                persist(self.state)  # receipt is durable before local ack.
                try:
                    from conquest.merchants.bank_stock_alerts import acknowledge

                    acknowledge(due["id"], receipt)
                except Exception:
                    return
            self.state["queue"].remove(due)
            self.state.update(last_message_id=str(receipt), last_sent_at=now)
            self.state.pop("delivery_error", None)
        persist(self.state)


def ensure_monitor():
    if not SECRET.exists():
        return False
    from conquest.application_layout import RuntimeLayout

    layout = RuntimeLayout.resolve()
    script = layout.script("run_shop_notifications.py")
    python = layout.python(windowed=True)
    subprocess.Popen(
        [str(python), str(script)],
        cwd=layout.root,
        env=layout.environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return True


def run():
    import msvcrt

    STATE.parent.mkdir(parents=True, exist_ok=True)
    lock = STATE.with_suffix(".lock").open("a+b")
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    try:
        for attempt in range(10):
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if attempt == 9:
                    raise
                time.sleep(0.1)
    except OSError:
        lock.close()
        return
    alerts = Alerts(read_json(STATE))
    try:
        while True:
            try:
                from conquest.discord_notify import PAUSED

                if PAUSED.exists():
                    write_json(
                        STATUS,
                        {
                            "pid": os.getpid(),
                            "version": MONITOR_VERSION,
                            "updated_at": time.time(),
                            "state": "Paused",
                            "queued": len(alerts.state["queue"]),
                        },
                    )
                    time.sleep(0.5)
                    continue
                try:
                    status = request({"action": "status"})
                except Exception:
                    status = None
                now = time.time()
                lifecycle = read_json(LIFECYCLE)
                alerts.poll(
                    status, now, clean_shutdown=lifecycle.get("state") == "closed"
                )
                write_json(STATE, alerts.state)
                alerts.dispatch(now, persist=lambda state: write_json(STATE, state))
                write_json(
                    STATUS,
                    {
                        "pid": os.getpid(),
                        "version": MONITOR_VERSION,
                        "updated_at": time.time(),
                        "state": "watching",
                        "queued": len(alerts.state["queue"]),
                        "open_incidents": list(alerts.state["incidents"]),
                        "last_sent_at": alerts.state.get("last_sent_at"),
                        "last_message_id": alerts.state.get("last_message_id"),
                        "error": alerts.state.get("delivery_error"),
                    },
                )
            except Exception:
                # Never include raw exception text: it could contain secrets.
                pass
            time.sleep(5)
    finally:
        lock.close()
