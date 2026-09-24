"""Local farming controls and progress dashboard."""

import json
import hmac
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from conquest.control import FarmingControl


def status(database):
    result = {
        "state": "Waiting for run",
        "attacks": 0,
        "heals": 0,
        "kills": 0,
        "pickups": 0,
        "reloads": 0,
        "character_level": None,
        "deaths": 0,
        "revivals": 0,
        "elapsed": 0,
        "health": None,
        "health_valid": False,
        "health_note": "Waiting for health monitor",
        "ammo": None,
        "potions": None,
        "events": [],
    }
    database = Path(database).resolve()
    if not database.is_file():
        return result
    try:
        with sqlite3.connect(
            database.as_uri() + "?mode=ro", uri=True, timeout=0.2
        ) as db:
            first = db.execute(
                "SELECT rowid,time FROM events WHERE event='trial_started' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if not first:
                return result
            rows = db.execute(
                "SELECT time,event,payload FROM events WHERE rowid>=? ORDER BY rowid",
                (first[0],),
            ).fetchall()
        result["state"] = "Running"
        ended = None
        health_time = None
        for timestamp, name, encoded in rows:
            data = json.loads(encoded)
            if name == "trial_started":
                result["monster"] = data.get("monster", "Pheasant")
            if name == "attack_attempt":
                result["attacks"] += 1
                result["state"] = "Attacking"
            if name == "healing_outcome" and data.get("outcome") == "verified":
                result["heals"] += 1
            if name == "healing_attempt":
                result["state"] = "Healing"
            if name == "movement_attempt":
                result["state"] = "Patrolling"
            if name == "death_detected":
                result["deaths"] = data["deaths"]
                result["state"] = "Waiting to revive"
            if name == "revive_calibration_required":
                result["state"] = "Waiting for Revive-button calibration"
            if name == "revival_verified":
                result["revivals"] = data["total"]
            if name == "recovery_state":
                result["state"] = data["state"].replace("_", " ").capitalize()
            if name == "recovery_complete":
                result["state"] = "Patrolling"
            if name == "reload_attempt":
                result["state"] = "Reloading arrows"
            if name == "reload_outcome" and data.get("outcome") == "verified":
                result["reloads"] += 1
            if name == "pickup_attempt":
                result["state"] = "Picking up Stancher"
            if name == "pickup_outcome" and data.get("outcome") == "verified":
                result["pickups"] = data["total"]
            if name == "paused":
                result["state"] = "Paused"
            if name == "resumed":
                result["state"] = "Running"
            if "health_ratio" in data:
                result["health"] = round(100 * data["health_ratio"], 1)
                health_time = timestamp
            if name == "kill_verified":
                result["kills"] = data["total"]
            for key in (
                "ammo",
                "potions",
                "position",
                "occupied_slots",
                "character_level",
            ):
                if key in data:
                    result[key] = data[key]
            if name == "trial_stopped":
                result["state"] = "Stopped: " + data["reason"].replace("_", " ")
                ended = timestamp
                result["kills"] = data.get("confirmed_kills")
            if name not in ("observation", "health_observation"):
                result["events"].append(
                    {"time": timestamp, "event": name.replace("_", " "), "data": data}
                )
        result["elapsed"] = round((ended or time.time()) - first[1])
        if (
            ended is None
            and result["state"] != "Paused"
            and time.time() - rows[-1][0] > 10
        ):
            result["state"] = "No recent updates"
        result["health_valid"] = (
            health_time is not None and 0 <= time.time() - health_time <= 1
        )
        if not result["health_valid"]:
            result["health"] = None
            result["health_note"] = "No fresh health observation"
        else:
            result["health_note"] = "Live Â· potion below 40%"
        result["events"] = result["events"][-12:][::-1]
    except (sqlite3.Error, ValueError, KeyError) as error:
        result["state"] = "Waiting for log: " + str(error)
    return result


PAGE = Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")


def make_dashboard_server(
    database, port=8765, *, monitor=None, control=None, runtime=None
):
    control = control or FarmingControl()
    token = secrets.token_hex(32)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def trusted_host(self):
            return self.headers.get("Host") in (
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            )

        def respond(self, content, status_code=200, html=False):
            self.send_response(status_code)
            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8" if html else "application/json",
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                f"default-src 'self'; script-src 'nonce-{token}'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if not self.trusted_host():
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            if path not in ("/", "/status"):
                self.send_error(404)
                return
            if path == "/":
                content = PAGE.replace("__CONTROL_TOKEN__", token).encode()
            else:
                data = status(database)
                if monitor is not None:
                    data.update(monitor.snapshot())
                if runtime is not None:
                    data.update(runtime.snapshot())
                else:
                    control.reconcile(
                        focused=False,
                        minimized=False,
                        observed_ids=[],
                        blockers=[
                            "Live farming controls are not connected to a worker"
                        ],
                    )
                    data.update(
                        {
                            "monsters": [],
                            "observations_available": False,
                            "observation_note": "Connect a worker to read nearby monsters",
                        }
                    )
                data["control"] = control.snapshot()
                content = json.dumps(data).encode()
            self.respond(content, html=path == "/")

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = -1
            # Drain only bounded bodies before rejecting headers. Closing a
            # Windows socket with unread request data can reset it before the
            # client receives the 403 response. Never parse/apply it before auth.
            try:
                payload = self.rfile.read(size) if 0 < size <= 4096 else b""
            except OSError:
                self.send_error(408)
                return
            origin = self.headers.get("Origin")
            if (
                not self.trusted_host()
                or (
                    origin is not None
                    and origin != "http://" + self.headers.get("Host", "")
                )
                or not hmac.compare_digest(
                    self.headers.get("X-Control-Token", ""), token
                )
            ):
                self.send_error(403)
                return
            if urlsplit(self.path).path != "/control":
                self.send_error(404)
                return
            try:
                if (
                    not 0 < size <= 4096
                    or self.headers.get_content_type() != "application/json"
                ):
                    raise ValueError("Expected a JSON control update of at most 4 KiB")
                result = control.update(json.loads(payload))
                self.respond(json.dumps(result).encode())
            except (ValueError, TypeError) as error:
                self.respond(json.dumps({"error": str(error)}).encode(), 400)
            except OSError:
                self.respond(
                    json.dumps(
                        {"error": "Could not save the control settings"}
                    ).encode(),
                    500,
                )

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_dashboard(database, port=8765, *, monitor=None, control=None, runtime=None):
    with make_dashboard_server(
        database, port, monitor=monitor, control=control, runtime=runtime
    ) as server:
        if monitor is not None:
            monitor.start()
        if runtime is not None:
            runtime.start()
        try:
            server.serve_forever()
        finally:
            if runtime is not None:
                runtime.close()
            if monitor is not None:
                monitor.close()
