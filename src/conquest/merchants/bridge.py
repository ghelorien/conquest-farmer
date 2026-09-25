"""Authenticated localhost merchant commands; no credentials or raw input API."""

from conquest.character_context import state_path
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import traceback
from conquest.discord_notify import write_json
from conquest.capture import CaptureUnavailable
from conquest.merchants.memory import (
    TransitObservationChanged as _TransitObservationChanged,
)


class MerchantRejected(ValueError):
    """A parsed bridge application rejection, not an uncertain transport loss."""


# bridge-errors.jsonl keeps only the most recent entries.
ERROR_HISTORY = 50
# Consecutive serving-loop failures before the listening server is replaced.
RECREATE_AFTER = 3
BACKOFF_FIRST, BACKOFF_MAX = 0.05, 1.0


class _Server(HTTPServer):
    bridge = None

    def handle_error(self, request, client_address):
        # socketserver's default prints the traceback to stderr; if that write
        # raises, the exception escapes handle_request.  Record instead.
        self.bridge.record("request", sys.exc_info()[1])


class MerchantBridge:
    def __init__(self, dispatch, path=state_path(".runtime/merchants/bridge.json")):
        self.path, self.dispatch = Path(path), dispatch
        self.errors = self.path.with_name("bridge-errors.jsonl")
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
                body = None
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
                except (Exception, SystemExit, KeyboardInterrupt) as error:
                    # Any other dispatch failure (sqlite3.OperationalError,
                    # RuntimeError, ...) is an app fault, not a rejection:
                    # answer 500 so the client gets a plain ValueError.
                    # SystemExit/KeyboardInterrupt raised on this worker
                    # thread cannot stop the app; socketserver would re-raise
                    # them and end the bridge, so they are answered the same.
                    result, status = bridge.failed("dispatch", error, body), 500
                try:
                    payload = json.dumps(result).encode()
                except (TypeError, ValueError) as error:
                    result, status = bridge.failed("dispatch", error, body), 500
                    payload = json.dumps(result).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.handler = Handler
        self.server = self._listen()
        self._advertise(self.server)
        self.thread = threading.Thread(
            target=self.run, daemon=True, name="merchant-bridge"
        )
        self.thread.start()

    def _listen(self):
        server = _Server(("127.0.0.1", 0), self.handler)
        server.bridge, server.timeout = self, 0.2
        return server

    def _advertise(self, server):
        write_json(
            self.path,
            {"port": server.server_port, "token": self.token, "pid": os.getpid()},
        )

    def run(self):
        # Only close() ends this loop.  With handle_error unable to raise and
        # dispatch failures answered in the handler, what can still escape
        # socketserver's handle_request is the listening socket itself:
        # gettimeout(), selector.register() (fileno() is -1 once closed:
        # ValueError) and select() (OSError); accept() errors are swallowed by
        # socketserver.  So a closed socket, or repeated consecutive escapes,
        # means the listener is unusable and is replaced.
        failures = since_recreate = 0
        try:
            while not self.stop.is_set():
                try:
                    self.server.handle_request()
                    failures = since_recreate = 0
                    continue
                except BaseException as error:
                    failures, since_recreate = failures + 1, since_recreate + 1
                    self.record("serve", error)
                unusable = since_recreate >= RECREATE_AFTER or self._closed()
                if unusable and self._recreate():
                    since_recreate = 0
                self.stop.wait(
                    min(BACKOFF_FIRST * 2 ** min(failures - 1, 10), BACKOFF_MAX)
                )
        finally:
            self.server.server_close()
            self.path.unlink(missing_ok=True)
            self.owner.close()

    def _closed(self):
        try:
            return self.server.fileno() == -1
        except Exception:
            return True

    def _recreate(self):
        """Replace the listener and advertise its port with the same token."""
        old = self.server
        try:
            server = self._listen()
        except Exception as error:
            self.record("recreate", error)
            return False
        try:
            self._advertise(server)
        except Exception as error:
            # Keep bridge.json and the served listener consistent; retry later.
            server.server_close()
            self.record("recreate", error)
            return False
        self.server = server
        try:
            old.server_close()
        except Exception:
            pass
        self._append(
            {
                "time": round(time.time(), 3),
                "where": "recreated",
                "old_port": old.server_port,
                "new_port": server.server_port,
            }
        )
        return True

    def failed(self, where, error, body=None):
        """Record a request failure and return its HTTP 500 JSON body."""
        action = body.get("action") if isinstance(body, dict) else None
        self.record(
            where, error, **({"action": action} if isinstance(action, str) else {})
        )
        try:
            message = str(error)
        except Exception:
            message = ""
        name = type(error).__name__
        return {"error": f"{name}: {message}" if message else name}

    def record(self, where, error, **extra):
        """Append a failure to bridge-errors.jsonl and stderr; never raises."""
        try:
            try:
                message = str(error)
            except Exception:
                message = "<unprintable>"
            entry = {
                "time": round(time.time(), 3),
                "where": where,
                "type": type(error).__name__,
                "message": message[:500],
                "traceback": "".join(traceback.format_exception(error))[-4000:],
                **extra,
            }
        except Exception:
            return
        self._append(entry)
        try:
            print(
                f"merchant bridge {where} error: {entry['type']}: "
                f"{entry['message']}\n{entry['traceback']}",
                file=sys.stderr,
                end="",
                flush=True,
            )
        except Exception:
            pass

    def _append(self, entry):
        try:
            try:
                lines = self.errors.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                lines = []
            lines = [*lines, json.dumps(entry)][-ERROR_HISTORY:]
            temporary = self.errors.with_name(self.errors.name + ".tmp")
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(temporary, self.errors)
        except Exception:
            pass

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
