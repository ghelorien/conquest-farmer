"""Independent Discord observer; never controls the game or reads screenshots."""

from conquest.character_context import state_path
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from conquest import kill_increment
from conquest.farm_telemetry import item_label

SECRET = Path(state_path(".runtime/discord-webhook.dpapi"))
STATE = Path(state_path(".runtime/discord-notifications.json"))
STATUS = Path(state_path("reports/discord-status.json"))
PAUSED = Path(state_path(".runtime/discord.paused"))


def webhook_url(value):
    parsed = urllib.parse.urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.netloc not in ("discord.com", "discordapp.com")
        or not re.fullmatch(r"/api/(?:v\d+/)?webhooks/\d+/[A-Za-z0-9_-]+", parsed.path)
        or parsed.fragment
        or parsed.query
    ):
        raise ValueError("Enter a Discord channel webhook URL")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "wait=true", "")
    )


def save_webhook(value):
    import win32crypt

    webhook_url(value)
    SECRET.parent.mkdir(parents=True, exist_ok=True)
    SECRET.write_bytes(
        win32crypt.CryptProtectData(
            value.strip().encode(), "Conquest Discord", None, None, None, 0
        )
    )
    PAUSED.unlink(missing_ok=True)


def load_webhook():
    import win32crypt

    return webhook_url(
        win32crypt.CryptUnprotectData(SECRET.read_bytes(), None, None, None, 0)[
            1
        ].decode()
    )


class DeliveryError(Exception):
    def __init__(self, note, retry_after=30):
        super().__init__(note)
        self.retry_after = retry_after


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def deliver(url, content, opener=None):
    payload = {
        "content": content[:1900],
        "username": "Conquest Farmer",
        "allowed_mentions": {"parse": []},
    }
    call = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "ConquestFarmer/1.0",
        },
        method="POST",
    )
    opener = opener or urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()
    )
    try:
        with opener.open(call, timeout=8) as response:
            result = json.load(response)
            if not result.get("id"):
                raise DeliveryError("Discord did not confirm the message")
            return result["id"]
    except urllib.error.HTTPError as error:
        delay = 300 if error.code in (401, 403, 404) else 30
        if error.code == 429:
            try:
                delay = max(1, min(3600, float(json.load(error)["retry_after"]))) + 1
            except (ValueError, KeyError, TypeError):
                delay = 60
        # Never log the request URL/token or response body.
        raise DeliveryError(f"Discord HTTP {error.code}", delay) from None
    except (OSError, ValueError):
        raise DeliveryError(
            "Discord connection failed; notification queued", 30
        ) from None


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(json.dumps(value, indent=2))
        for attempt in range(20):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.025)
    finally:
        temporary.unlink(missing_ok=True)


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False if ctypes.get_last_error() == 87 else None
    try:
        code = ctypes.c_uint32()
        return (
            code.value == 259
            if kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            else None
        )
    finally:
        kernel.CloseHandle(handle)


def live_activity(health, app):
    data = health.get("embedded_controls", {})
    life = data.get("life")
    control = data.get("control", {})
    if life and life.get("dead_candidate"):
        return "Dead — reviving and returning to the hunting area"
    if not life:
        if app.get("reconnection", {}).get("state") == "reconnect_exhausted":
            return (
                "STOPPED: automatic reconnect attempts exhausted; login needs attention"
            )
        return data.get("observation_note", "Waiting for current game memory")
    if not control.get("enabled"):
        return "Farming is off"
    window = health.get("window", {})
    if window.get("minimized") or window.get("foreground") != window.get("root_hwnd"):
        return "Paused — waiting for Conquer to regain focus"
    if control.get("execution_state") not in ("farming", "off", None):
        return control.get("note", "Waiting for the farmer")
    if app.get("navigation_blocked"):
        return "Navigation blocked — looking for another patrol path"
    text = app.get("current_activity", "")
    if text.startswith(
        (
            "Attacking",
            "Patrolling",
            "Picking up",
            "Using a healing",
            "Reloading",
            "Heading back",
            "Heading to",
        )
    ):
        return text
    # Short memory-sampling retries emit Paused/Running in the desktop log.
    # Current memory and actual window focus establish whether it is hunting.
    return (
        "Hunting Turtledoves"
        if app.get("selected_route") == "turtledove"
        else "Hunting selected monsters"
    )


def with_live_status(app):
    info = read_json(app.get("worker_info_path", ""))
    port = info.get("port")
    if not isinstance(port, int) or not 1 <= port <= 65535 or not info.get("token"):
        return app
    call = urllib.request.Request(
        f"http://127.0.0.1:{port}/health",
        data=b"{}",
        headers={"Content-Type": "application/json", "X-Conquest-Token": info["token"]},
    )
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )
        with opener.open(call, timeout=2) as response:
            health = json.load(response)
        return {
            **app,
            "live_activity": live_activity(health, app),
            "live_farming": confirmed_farming(health),
            "live_checked_at": time.time(),
            "live_position": (
                health.get("embedded_controls", {}).get("life") or {}
            ).get("position"),
            "live_map": (health.get("embedded_controls", {}).get("life") or {}).get(
                "map_id"
            ),
            "live_hp": (health.get("embedded_controls", {}).get("life") or {}).get(
                "current_hp"
            ),
            "live_max_hp": (health.get("embedded_controls", {}).get("life") or {}).get(
                "max_hp"
            ),
        }
    except (OSError, ValueError, KeyError, TypeError):
        return app


def confirmed_farming(health):
    data = health.get("embedded_controls", {})
    life = data.get("life") or {}
    control = data.get("control", {})
    window = health.get("window", {})
    return bool(
        life
        and not life.get("dead_candidate")
        and control.get("enabled")
        and data.get("external_execution")
        and control.get("execution_state") == "farming"
        and not window.get("minimized")
        and window.get("root_hwnd")
        and window.get("foreground") == window.get("root_hwnd")
    )


def status_text(app, route, now, alive=process_alive):
    phase = route.get("phase")
    if phase == "completed" and route.get("event") == "savings_complete":
        return f"Goal complete: {route['silver']:,} silver; parked in Twin City"
    active = phase in (
        "starting",
        "hunting",
        "restocking",
        "changing_route",
        "recovering_route",
        "visiting_town",
        "reloading",
    )
    age = now - route.get("updated_at", 0)
    if active and age > 15 and alive(route.get("pid")) is False:
        return "STOPPED: route process exited unexpectedly"
    if active and age > 45:
        return "WARNING: route has not reported for 45 seconds"
    if app.get("pid") and alive(app["pid"]) is False:
        return "STOPPED: farmer application closed"
    if phase == "needs_attention":
        return "STOPPED: " + route.get("detail", "Route needs attention")[:250]
    if phase == "stopped":
        return "Route stopped: " + route.get("detail", "Farming stopped")[:250]
    live = app.get("live_activity", "")
    if live.startswith(("STOPPED:", "Dead", "Disconnected")):
        return live
    if phase == "reloading" or app.get("reload_preparing"):
        return "Moving to a safe spot for app reload"
    if phase == "restocking":
        return "Restocking in town"
    text = (
        app.get("live_activity")
        or app.get("current_activity")
        or app.get("state")
        or "Waiting for farmer status"
    )
    if text.startswith("Picking up "):
        return "Picking up loot"
    return text


def major_status(app, route, now, alive=process_alive):
    """Canonical phases prevent attack/loot/vendor details from spamming Discord."""
    text = status_text(app, route, now, alive)
    lower = text.lower()
    if lower.startswith(("stopped:", "warning:", "route stopped:")):
        return text, 0
    if lower.startswith("dead") or "reviving" in lower:
        return "Dead — reviving and returning to the hunting area", 0
    if "disconnected" in lower or "reconnecting" in lower:
        return "Disconnected — reconnecting to Conquer", 0
    if lower.startswith("navigation blocked"):
        return "WARNING: navigation has been blocked for 30 seconds", 30
    if "focus" in lower or "minimized" in lower:
        return "WARNING: waiting for Conquer focus for 30 seconds", 30
    if lower == "moving to a safe spot for app reload":
        return text, 0
    if route.get("phase") == "restocking":
        return "Restocking in town", 10
    if lower.startswith(
        ("heading back", "heading to the hunting", "travelling to hunting")
    ):
        return "Returning to the hunting area", 10
    if lower == "farming is off":
        return "Farming is off", 10
    if lower.startswith(
        (
            "attacking",
            "patrolling",
            "hunting",
            "picking up",
            "using a healing",
            "reloading",
            "running",
            "farming",
        )
    ):
        group = (
            "Turtledoves"
            if app.get("selected_route") == "turtledove"
            else "selected monsters"
        )
        return "Patrolling / farming " + group, 10
    # Sampling retries and per-action details stay in the desktop UI.
    return None, 0


def notable_drop(row):
    from conquest.valuables import SPECIAL_LOOT_TYPES
    from conquest.valuables import SPECIAL_LOOT_TYPES

    kind = row.get("type_id", 0)
    # Type quality is observable; never infer a per-instance + value from it.
    return (
        kind in SPECIAL_LOOT_TYPES
        or 700000 <= kind <= 700099
        or (100000 <= kind < 600000 and kind % 10 >= 7)
        or (isinstance(row.get("plus"), int) and row["plus"] > 0)
    )


def recent_kills(path, now, seconds=900):
    """Count verified kill increments across combat-session rollovers."""
    try:
        db = sqlite3.connect(
            Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5
        )
        try:
            rows = db.execute(
                "SELECT payload FROM events WHERE event='kill_verified' AND time>? AND time<=?",
                (now - seconds, now),
            )
            total = 0
            for (payload,) in rows:
                # Same bounded rule run_trial used to qualify the increment.
                count = kill_increment.verified_kill_count(json.loads(payload))
                if count is None:
                    return None
                total += count
            return total
        finally:
            db.close()
    except (OSError, ValueError, sqlite3.Error):
        return None


class Notifications:
    def __init__(self, state=None):
        self.state = state if state is not None else {}
        self.state.setdefault("queue", [])
        if self.state.get("notification_policy") != "terminal_v3":
            # The user now wants only unrecovered stops. Retire every old
            # routine/drop/transient alert before the new policy can deliver it.
            self.state["queue"] = []
            self.state["notification_policy"] = "terminal_v3"
            for key in (
                "awaiting_farming_restart",
                "stall_reported",
                "failure_since",
                "failure_alerted",
            ):
                self.state.pop(key, None)
        self.farming_since = None

    def enqueue(self, text, now, kind=None, character=None):
        from conquest.character_context import farmer_name

        character = character or farmer_name()
        stamp = (
            datetime.fromtimestamp(now, timezone.utc)
            .astimezone()
            .strftime("%H:%M:%S %Z")
        )
        row = {
            "content": f"[{stamp}] {character} — {text}",
            "created_at": now,
            "kind": kind,
        }
        if kind == "terminal_stop":
            self.state["queue"].insert(0, row)
        else:
            self.state["queue"].append(row)

    @staticmethod
    def supplies_text(supplies):
        return ", ".join(
            f"{supplies[key]:,} {label}"
            for key, label in (
                ("arrows", "arrows"),
                ("potions", "potions"),
                ("free_slots", "free slots"),
                ("silver", "silver"),
            )
            if isinstance(supplies.get(key), int)
        )

    def updates(self, app, route, events_path, now, alive=process_alive):
        """User-requested quarter-hour summaries and durable restock events."""
        if (
            route.get("phase") == "reloading" or app.get("reload_preparing")
        ) and not self.state.get("reload_in_progress"):
            self.enqueue("Moving to a safe spot for app reload", now, "reload_started")
            self.state["reload_in_progress"] = True
        path = Path(events_path)
        if path.exists():
            size = path.stat().st_size
            if "restock_offset" not in self.state:
                self.state["restock_offset"] = (
                    size  # Never replay historical town trips.
                )
                if (
                    route.get("phase") == "restocking"
                    and not self.state.get("reload_in_progress")
                    and now - route.get("updated_at", 0) <= 15
                ):
                    self.enqueue("Restocking in town", now, "restock_started")
                    self.state["restock_in_progress"] = True
            if size < self.state["restock_offset"]:
                self.state["restock_offset"] = 0
            with path.open("rb") as source:
                source.seek(self.state["restock_offset"])
                for _ in range(200):
                    line = source.readline()
                    if not line or not line.endswith(b"\n"):
                        break
                    self.state["restock_offset"] = source.tell()
                    try:
                        row = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    event = row.get("event")
                    if event == "reload_started":
                        if not self.state.get("reload_in_progress"):
                            self.enqueue(
                                "Moving to a safe spot for app reload",
                                now,
                                "reload_started",
                            )
                        self.state["reload_in_progress"] = True
                    elif event == "storage_full_stop":
                        self.enqueue(
                            "CRITICAL: Town and Market warehouses are full. Farming stopped; closing the game client to disconnect. Automatic reconnect is disabled until you resume manually.",
                            now,
                            "terminal_stop",
                        )
                        self.state["failure_alerted"] = True
                    elif event == "storage_overflow_started":
                        self.enqueue(
                            "Town warehouse full — taking overflow valuables to the Market warehouse.",
                            now,
                            "storage_overflow_started",
                        )
                    elif event == "storage_overflow_complete":
                        self.enqueue(
                            "Overflow valuables stored in Market — returning to farming.",
                            now,
                            "storage_overflow_complete",
                        )
                    elif event == "savings_complete":
                        self.enqueue(
                            f"Savings goal complete: {row['silver']:,} silver. Parked safely in Twin City.",
                            now,
                            "savings_complete",
                        )
                        self.state["restock_in_progress"] = False
                    elif event == "restock_complete":
                        self.enqueue(
                            "Restock complete — returning to hunting. "
                            + self.supplies_text(row.get("supplies", {})),
                            now,
                            "restock_complete",
                        )
                        self.state["restock_in_progress"] = False
                    elif (
                        (event == "return_required" or row.get("phase") == "restocking")
                        and event not in ("failed", "stopped")
                        and not self.state.get("reload_in_progress")
                    ):
                        if not self.state.get("restock_in_progress"):
                            reason = {
                                "inventory_full": "inventory full",
                                "ammo_unavailable": "arrows needed",
                                "potions_exhausted": "potions needed",
                            }.get(
                                row.get("reason"),
                                "supplies or inventory need attention",
                            )
                            self.enqueue(
                                "Heading to town to restock — " + reason,
                                now,
                                "restock_started",
                            )
                            self.state["restock_in_progress"] = True
        if (
            self.state.get("reload_in_progress")
            and route.get("phase") == "hunting"
            and not app.get("reload_preparing")
            and app.get("live_farming")
            and 0 <= now - app.get("live_checked_at", 0) <= 3
            and 0 <= now - route.get("updated_at", 0) <= 15
        ):
            self.enqueue(
                "Farming resumed after app reload preparation", now, "reload_resumed"
            )
            self.state["reload_in_progress"] = False
        if (not app and not route) or now < self.state.get("next_update_at", 0):
            return
        text = status_text(app, route, now, alive)
        if (
            route.get("phase") == "hunting"
            and app.get("live_farming")
            and not app.get("navigation_blocked")
            and app.get("state") in ("Patrolling", "Farming")
        ):
            text = "Farming / patrolling"
        details = [text]
        if 0 <= now - app.get("updated_at", 0) <= 15:
            level = (app.get("experience") or {}).get("level")
            if level is not None:
                details.append(f"level {level}")
        kills = recent_kills(
            path.parent.parent / "desktop-farming" / "trial.sqlite3", now
        )
        details.append(
            f"{kills} verified kills in the last 15 minutes"
            if kills is not None
            else "15-minute kill count unavailable"
        )
        hourly = recent_kills(
            path.parent.parent / "desktop-farming" / "trial.sqlite3", now, seconds=3600
        )
        minimum, stretch = kill_rate_targets()
        details.append(
            f"{hourly:,} verified kills in the last hour (minimum: {minimum * 60:,.0f}, stretch: {stretch * 60:,.0f})"
            if hourly is not None
            else "Hourly kill count unavailable"
        )
        if kills is not None:
            details.append(f"15-minute pace: {kills * 4:,}/hour, including downtime")
        minute = recent_kills(
            path.parent.parent / "desktop-farming" / "trial.sqlite3", now, seconds=60
        )
        if minute is not None:
            details.append(
                f"last minute: {minute} kills (minimum: {minimum:g}, stretch: {stretch:g})"
            )
        if (
            abs(now - app.get("live_checked_at", 0)) <= 3
            and app.get("live_hp") is not None
        ):
            details.append(f"HP {app['live_hp']}/{app.get('live_max_hp', '?')}")
        if 0 <= now - route.get("updated_at", 0) <= 45:
            supplies = self.supplies_text(route.get("supplies", {}))
            if supplies:
                details.append(supplies)
        else:
            details.append("supply status unavailable or stale")
        # An outage keeps only the latest summary, rather than a catch-up flood.
        self.state["queue"] = [
            row for row in self.state["queue"] if row.get("kind") != "periodic_status"
        ]
        self.enqueue("15-minute status: " + " | ".join(details), now, "periodic_status")
        self.state["next_update_at"] = now + 900

    def delivered(self, row, message_id=None, now=None):
        if message_id:
            receipts = self.state.setdefault("delivery_receipts", [])
            receipts.append(
                {
                    **row,
                    "message_id": message_id,
                    "delivered_at": time.time() if now is None else now,
                }
            )
            self.state["delivery_receipts"] = receipts[-200:]
        if row.get("kind") == "terminal_stop":
            self.state["stop_delivered"] = True
        elif row.get("kind") == "farming_resumed":
            self.state["stop_delivered"] = False

    def observe(self, app, route, now, alive=process_alive):
        text = status_text(app, route, now, alive)
        terminal = text.lower().startswith(("stopped:", "route stopped:"))
        progress = {
            "position": app.get("live_position", app.get("position")),
            "map": app.get("live_map"),
            "kills": app.get("kills") or 0,
            "pickup": (app.get("last_pickup") or {}).get("timestamp") or 0,
        }

        def advanced(before):
            return bool(
                before
                and (
                    (
                        progress["position"] is not None
                        and before.get("position") is not None
                        and progress["position"] != before["position"]
                    )
                    or (
                        progress["map"] is not None
                        and before.get("map") is not None
                        and progress["map"] != before["map"]
                    )
                    or progress["kills"] > before.get("kills", 0)
                    or progress["pickup"] > before.get("pickup", 0)
                )
            )

        blocked = route.get("phase") == "hunting" and (
            app.get("navigation_blocked") is True
            or app.get("live_activity", "").lower().startswith("navigation blocked")
        )
        if blocked:
            stall = self.state.setdefault(
                "navigation_stall", {"since": now, "progress": progress}
            )
            if advanced(stall["progress"]):
                stall["since"] = now
            stall["progress"] = progress
        else:
            self.state.pop("navigation_stall", None)
        stalled = blocked and now - self.state["navigation_stall"]["since"] >= 60
        alert_progress = self.state.get("navigation_alert_progress")
        recovered_progress = advanced(alert_progress)
        # A missing heartbeat alone does not prove termination. Allow three
        # minutes for a stalled controller before reporting it as unresponsive.
        unresponsive = (
            route.get("phase")
            in (
                "starting",
                "hunting",
                "restocking",
                "changing_route",
                "recovering_route",
                "visiting_town",
                "reloading",
            )
            and now - route.get("updated_at", now) >= 180
        )
        if recovered_progress and not terminal and not unresponsive:
            self.state.pop("navigation_alert_progress", None)
            self.state.pop("failure_since", None)
            self.state.pop("failure_alerted", None)
            self.state["queue"] = [
                row for row in self.state["queue"] if row.get("kind") != "terminal_stop"
            ]
        if terminal or unresponsive or stalled:
            self.farming_since = None
            self.state["queue"] = [
                row
                for row in self.state["queue"]
                if row.get("kind") != "farming_resumed"
            ]
            since = self.state.setdefault("failure_since", now)
            if (
                (stalled or now - since >= 60)
                and not self.state.get("failure_alerted")
                and not self.state.get("stop_delivered")
            ):
                if stalled and not terminal:
                    self.enqueue(
                        "Needs attention — navigation has been blocked with no movement, kills or "
                        "verified pickups for 60 seconds; automatic path recovery has not succeeded.",
                        now,
                        "terminal_stop",
                    )
                    self.state["navigation_alert_progress"] = progress
                else:
                    detail = (
                        text.split(":", 1)[-1].strip()
                        if terminal
                        else "route controller is unresponsive"
                    )
                    self.enqueue(
                        "Needs attention — bot remains stopped; no recovery observed for 60 seconds. "
                        + detail,
                        now,
                        "terminal_stop",
                    )
                self.state["failure_alerted"] = True
            return

        # A live heartbeat or a cleared UI flag is not evidence that a stalled
        # character recovered. Wait for memory position or verified game progress.
        if blocked or (alert_progress is not None and not recovered_progress):
            self.farming_since = None
            self.state["queue"] = [
                row
                for row in self.state["queue"]
                if row.get("kind") != "farming_resumed"
            ]
            return

        # Recovery, including travel/restocking, cancels an undelivered stop.
        # Only a stop Discord actually received earns a later recovery message.
        active = (
            route.get("phase")
            in (
                "starting",
                "hunting",
                "restocking",
                "changing_route",
                "recovering_route",
                "visiting_town",
                "reloading",
            )
            and 0 <= now - route.get("updated_at", 0) <= 15
            and alive(route.get("pid")) is True
        )
        running = (
            app.get("live_farming") is True
            and abs(now - app.get("live_checked_at", 0)) <= 3
        )
        if active or running:
            self.state.pop("navigation_alert_progress", None)
            self.state.pop("failure_since", None)
            self.state.pop("failure_alerted", None)
            self.state["queue"] = [
                row for row in self.state["queue"] if row.get("kind") != "terminal_stop"
            ]
        if running:
            if self.farming_since is None:
                self.farming_since = now
        else:
            self.farming_since = None
        if (
            self.state.get("stop_delivered")
            and self.farming_since is not None
            and now - self.farming_since >= 2
            and not any(
                row.get("kind") == "farming_resumed" for row in self.state["queue"]
            )
        ):
            self.enqueue("Farming resumed — recovery confirmed", now, "farming_resumed")

    def merchants(self, path, now):
        """Replay the durable merchant outbox into the existing Discord queue."""
        if not Path(path).exists():
            return
        from conquest.merchants.journal import Journal

        journal = Journal(path)

        def failure_state(until):
            active = {}
            with journal.db() as db:
                rows = db.execute(
                    "SELECT id,character,event,payload FROM events "
                    "WHERE id<=? AND event IN ('persistent_failure','recovery_verified') ORDER BY id",
                    (until,),
                )
                for row in rows:
                    if row["event"] == "recovery_verified":
                        active.pop(row["character"], None)
                    else:
                        try:
                            note = json.loads(row["payload"])["note"]
                        except (ValueError, TypeError, KeyError):
                            continue
                        active[row["character"]] = note
            return active

        if "merchant_cursor" not in self.state:
            # First adoption establishes a durable high-water mark. Existing
            # merchant history may span months and must never become a Discord
            # catch-up queue merely because this notifier gained the feature.
            with journal.db() as db:
                cursor = db.execute(
                    "SELECT COALESCE(MAX(id),0) FROM events"
                ).fetchone()[0]
            self.state["merchant_cursor"] = cursor
            self.state["merchant_failures"] = failure_state(cursor)
            return
        cursor = self.state["merchant_cursor"]
        if "merchant_failures" not in self.state:
            self.state["merchant_failures"] = failure_state(cursor)
        failures = self.state["merchant_failures"]
        for event in journal.events(cursor, 100):
            payload = json.loads(event["payload"])
            kind, character = event["event"], event["character"]
            message = None
            if kind == "delivery_verified":
                items = payload.get("result", {}).get("items", [])
                message = "Delivery verified from Parasite: " + ", ".join(
                    f"{i['name']} ×{i['quantity']}" for i in items
                )
            elif kind == "scan_completed":
                message = f"Repricing complete: {payload['changed']} changes; {payload['deferred']} items deferred."
            elif kind == "persistent_failure":
                note = payload["note"]
                if failures.get(character) != note:
                    message = "Needs attention — " + note
                failures[character] = note
            elif kind == "recovery_verified":
                message = "Recovery confirmed by character, server, inventory and booth checks."
                failures.pop(character, None)
            if message:
                self.enqueue(
                    message, event["timestamp"], "merchant_" + kind, character=character
                )
                self.state["queue"][-1]["merchant_event_id"] = event["id"]
            # Queue and cursor are saved atomically by the existing notifier.
            self.state["merchant_cursor"] = event["id"]

    def drops(self, path, now):
        path = Path(path)
        if not path.exists():
            self.state.setdefault("drop_offset", 0)
            return
        # New installation starts at EOF; don't flood Discord with old loot.
        if "drop_offset" not in self.state:
            self.state["drop_offset"] = path.stat().st_size
            return
        if path.stat().st_size < self.state["drop_offset"]:
            self.state["drop_offset"] = 0
        with path.open("rb") as source:
            source.seek(self.state["drop_offset"])
            for _ in range(200):
                line = source.readline()
                if not line or not line.endswith(b"\n"):
                    break
                self.state["drop_offset"] = source.tell()
                try:
                    row = json.loads(line)
                    if row.get("increase", 0) > 0:
                        prefix = {
                            "recovered_inventory": "Recovered missing pickup",
                            "inventory_gain": "Acquired",
                        }.get(row.get("source"), "Picked up")
                        suffix = (
                            " (verified now; original pickup time unavailable)"
                            if row.get("source") == "recovered_inventory"
                            else ""
                        )
                        self.enqueue(
                            f"{prefix}: {item_label(row)} ×{row['increase']}{suffix}",
                            row.get("timestamp", now),
                            "pickup",
                        )
                        self.state["queue"][-1].update(
                            inventory_uid=row.get("inventory_uid"),
                            type_id=row["type_id"],
                            plus=row.get("plus"),
                        )
                except (ValueError, TypeError, KeyError):
                    continue


KILL_RATE_POLICY = Path("profiles/route-optimization.json")


def kill_rate_targets():
    """Use the same current policy as route assessment, without imposing a cap."""
    import math

    policy = read_json(KILL_RATE_POLICY)

    def positive(name, default):
        value = policy.get(name, default)
        return (
            value
            if type(value) in (int, float) and math.isfinite(value) and value > 0
            else default
        )

    minimum = positive("target_kills_per_minute", 40)
    return minimum, max(minimum, positive("stretch_kills_per_minute", 50))


def ensure_monitor():
    if not SECRET.exists():
        return False
    from conquest.application_layout import RuntimeLayout

    layout = RuntimeLayout.resolve()
    script = layout.script("run_discord_notifications.py")
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
    # Windows releases this lock even if the notifier crashes.
    lock = (STATE.parent / "discord-notifications.lock").open("a+b")
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return
    notifications = Notifications(read_json(STATE))
    try:
        while True:
            now = time.time()
            status = {
                "pid": os.getpid(),
                "updated_at": now,
                "state": "Watching status changes",
                "monitor_revision": "storage_overflow_v6",
            }
            try:
                if PAUSED.exists() or not SECRET.exists():
                    status["state"] = (
                        "Paused" if PAUSED.exists() else "Webhook not configured"
                    )
                else:
                    app = with_live_status(
                        read_json(state_path("reports/desktop-farming/app-state.json"))
                    )
                    route = read_json(state_path("reports/overnight/status.json"))
                    now = (
                        time.time()
                    )  # The memory check completed after this loop began.
                    notifications.observe(app, route, now)
                    notifications.updates(
                        app, route, state_path("reports/overnight/events.jsonl"), now
                    )
                    notifications.drops(
                        state_path("reports/desktop-farming/pickups.jsonl"), now
                    )
                    notifications.merchants(
                        state_path("reports/merchants/journal.sqlite3"), now
                    )
                    write_json(STATE, notifications.state)  # durable before sending
                    if notifications.state["queue"] and now >= notifications.state.get(
                        "retry_at", 0
                    ):
                        try:
                            message_id = deliver(
                                load_webhook(),
                                notifications.state["queue"][0]["content"],
                            )
                            notifications.delivered(
                                notifications.state["queue"].pop(0), message_id
                            )
                            notifications.state.update(
                                last_sent_at=time.time(),
                                last_message_id=message_id,
                                retry_at=time.time() + 2,
                            )
                            notifications.state.pop("delivery_error", None)
                        except DeliveryError as error:
                            notifications.state.update(
                                delivery_error=str(error),
                                retry_at=time.time() + error.retry_after,
                            )
                        write_json(STATE, notifications.state)
                    status.update(
                        queued=len(notifications.state["queue"]),
                        last_sent_at=notifications.state.get("last_sent_at"),
                        error=notifications.state.get("delivery_error"),
                    )
            except Exception:
                status.update(
                    state="Notifier error; retrying",
                    error="Could not read configuration or monitoring files",
                )
            try:
                write_json(STATUS, status)
            except OSError:
                pass
            time.sleep(0.5)
    finally:
        lock.close()
