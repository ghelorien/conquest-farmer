"""Authenticated localhost merchant commands; no credentials or raw input API."""

from conquest.character_context import state_path
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from conquest.discord_notify import write_json
from conquest.capture import CaptureUnavailable
from conquest.merchants.memory import (
    TransitObservationChanged as _TransitObservationChanged,
)


class MerchantRejected(ValueError):
    """A parsed bridge application rejection, not an uncertain transport loss."""


class MerchantBridge:
    def __init__(self, dispatch, path=state_path(".runtime/merchants/bridge.json")):
        self.path, self.dispatch = Path(path), dispatch
        self.token, self.stop = secrets.token_hex(32), threading.Event()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Keep one app owner, even if a previous crash left connection metadata.
        import msvcrt

        self.owner = self.path.with_suffix(".lock").open("a+b")
        self.owner.write(b"0")
        self.owner.flush()
        self.owner.seek(0)
        try:
            msvcrt.locking(self.owner.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.owner.close()
            raise ValueError("A merchant app is already running") from None
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def log_message(self, *args):
                pass

            def do_POST(self):
                self.close_connection = True
                if not hmac.compare_digest(
                    self.headers.get("X-Conquest-Token", ""), bridge.token
                ):
                    # Drain a bounded body without parsing or dispatching it.
                    # Closing with unread request bytes can reset the Windows
                    # socket before the client receives the rejection status.
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                        if 0 < size <= 65536:
                            self.rfile.read(size)
                    except (ValueError, OSError):
                        pass
                    self.send_error(403)
                    return
                try:
                    if self.path != "/merchants":
                        raise ValueError("Unknown operation")
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 65536:
                        raise ValueError("Invalid request length")
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError("Expected an object")
                    result, status = bridge.dispatch(body), 200
                except _TransitObservationChanged as error:
                    # HTTP discards Python exception identity.  Preserve only
                    # the bounded, read-only transit race used by the
                    # acceptance observer; all other failures remain ordinary
                    # terminal bridge errors.
                    result, status = (
                        {
                            "error": str(error),
                            "code": "merchant_transit_observation_changed",
                        },
                        409,
                    )
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    OSError,
                    CaptureUnavailable,
                ) as error:
                    result, status = {"error": str(error)}, 400
                payload = json.dumps(result).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.timeout = 0.2
        write_json(
            self.path,
            {"port": self.server.server_port, "token": self.token, "pid": os.getpid()},
        )
        self.thread = threading.Thread(
            target=self.run, daemon=True, name="merchant-bridge"
        )
        self.thread.start()

    def run(self):
        try:
            while not self.stop.is_set():
                self.server.handle_request()
        finally:
            self.server.server_close()
            self.path.unlink(missing_ok=True)
            self.owner.close()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=4)


def request(body, path=state_path(".runtime/merchants/bridge.json")):
    import urllib.request
    import urllib.error

    info = json.loads(Path(path).read_text())
    call = urllib.request.Request(
        f"http://127.0.0.1:{int(info['port'])}/merchants",
        data=json.dumps(body).encode(),
        headers={"X-Conquest-Token": info["token"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            call, timeout=5
        ) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        payload = None
        try:
            payload = json.load(error)
            detail, code = (
                payload.get("error", "Merchant request failed"),
                payload.get("code"),
            )
        except (ValueError, AttributeError):
            detail, code = "Merchant request failed", None
        if code == "merchant_transit_observation_changed":
            from conquest.merchants.memory import TransitObservationChanged

            raise TransitObservationChanged(detail) from error
        if (
            error.code == 400
            and isinstance(payload, dict)
            and set(payload) == {"error"}
            and isinstance(payload["error"], str)
            and payload["error"]
        ):
            raise MerchantRejected(payload["error"]) from error
        raise ValueError(detail) from error
