"""Short-lived, authenticated localhost worker for one selected game process.

The worker exposes bounded calibration operations, never a shell or arbitrary
Python evaluation. It runs only for the requested lifetime and removes its
connection file when stopped. F12 stops the worker between requests.
"""

import hmac
import ctypes as c
import json
import secrets
import struct
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from conquest.calibration import KINDS, ObservationSet, scan
from conquest.foreground import foreground_click, foreground_key, foreground_drag
from conquest.input_probe import MessageTarget, key_probe
from conquest.memory import MemorySession
from conquest.win32 import bind


class Operations:
    def __init__(self, session, hwnd):
        self.session = session
        self.target = MessageTarget(session.pid, hwnd)
        self.stopping = False

    def dispatch(self, operation, body):
        self.session.assert_identity()
        if operation == "health":
            return {"worker": "live", "protocol_version": 2, "input_revision": 6,
                    "expected_sha256": self.session.expected_sha256,
                    "target": self.session.identity,
                    "window": self.target.snapshot(), "modules": self.session.modules[:1]}
        if operation == "shutdown":
            self.stopping = True
            return {"stopped": True}
        if operation == "sample":
            fields = body.get("fields")
            if not isinstance(fields, list) or not 1 <= len(fields) <= 64:
                raise ValueError("Provide 1 to 64 fields")
            result = []
            for field in fields:
                kind = field["kind"]
                address = int(field["address"], 0)
                if kind in KINDS:
                    fmt = "<" + KINDS[kind]
                    value = list(struct.unpack(fmt, self.session.read(address, struct.calcsize(fmt))))
                elif kind == "utf8":
                    value = self.session.read(address, 64).split(b"\0", 1)[0].decode("utf-8", errors="replace")
                else:
                    raise ValueError("Unsupported field kind")
                result.append({"name": field.get("name"), "address": hex(address), "value": value})
            return {"sampled_at": time.time(), "fields": result, "qualified": False}
        if operation == "inspect-object":
            anchor = int(body["anchor"], 0)
            span = int(body.get("span", 256))
            if not 8 <= span <= 1024:
                raise ValueError("Object inspection span exceeds bounds")
            start = max(0x10000, (anchor - span) & ~7)
            data = self.session.read(start, anchor - start)
            module_pointers = []
            for offset in range(0, len(data) - 7, 8):
                value = struct.unpack_from("<Q", data, offset)[0]
                for module in self.session.modules:
                    if module["base"] <= value < module["base"] + module["size"]:
                        module_pointers.append({"address": hex(start + offset), "value": hex(value),
                                                "module": module["name"], "rva": hex(value - module["base"])})
                        break
            return {"module_pointers_before_anchor": module_pointers, "qualified": False}
        if operation == "scan":
            observation = ObservationSet.model_validate(body["observations"])
            if observation.expected_sha256 != self.session.expected_sha256:
                raise ValueError("Observation fingerprint differs from target")
            return scan(self.session, observation, max_seconds=20, max_bytes=512 * 1024 * 1024)
        if operation in ("foreground-click", "foreground-key", "background-key", "foreground-drag"):
            # This endpoint is a baseline diagnostic, not a background fallback.
            guard = body.get("guard")
            if not isinstance(guard, dict):
                raise ValueError("A live character-name and HP guard is required")
            name = self.session.read(int(guard["name_address"], 0), 64).split(b"\0", 1)[0].decode("utf-8")
            hp = struct.unpack("<I", self.session.read(int(guard["hp_address"], 0), 4))[0]
            if name != guard["name"] or not 0 < hp <= guard["max_hp"]:
                raise ValueError("Character identity or HP guard failed")
            if operation == "foreground-key":
                return foreground_key(self.target, body["vk"], body["expected_size"], body.get("control", False), body.get("require_foreground", False))
            if operation == "foreground-drag":
                return foreground_drag(self.target, body["source"], body["destination"], body["expected_size"])
            if operation == "background-key":
                vk = body["vk"]
                if type(vk) is not int or not 0x70 <= vk <= 0x7A:
                    raise ValueError("Key diagnostic supports F1 through F11 only")
                control = body.get("control", False)
                if type(control) is not bool:
                    raise ValueError("control must be a boolean")
                map_key = bind(self.target.backend.user, "MapVirtualKeyW", [c.c_uint, c.c_uint], c.c_uint)
                return key_probe(self.target, vk, map_key(vk, 0), body["expected_size"],
                                 control_scan=map_key(0x11, 0) if control else None)
            return foreground_click(self.target, *body["point"], body["expected_size"],
                                    body.get("button", "left"), body.get("control", False),
                                    body.get("require_foreground", False), body.get("expected_origin"))
        raise ValueError("Unsupported worker operation")


def serve(pid, hwnd, expected_sha256, info_path, lifetime=1800):
    if not 30 <= lifetime <= 14400:
        raise ValueError("Worker lifetime must be 30 to 14400 seconds")
    info_path = Path(info_path)
    if info_path.exists():
        raise ValueError("Worker connection file already exists; check that worker before starting another")
    with MemorySession(pid, expected_sha256) as session:
        operations = Operations(session, hwnd)
        token = secrets.token_hex(32)
        deadline = time.monotonic() + lifetime

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_):
                pass  # Never log authentication headers or memory payloads.

            def do_POST(self):
                if not hmac.compare_digest(self.headers.get("X-Conquest-Token", ""), token):
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 65536:
                        raise ValueError("Invalid request size")
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError("Expected a JSON object")
                    result = operations.dispatch(self.path.strip("/"), body)
                    status = 200
                except (ValueError, OSError, KeyError, TypeError, struct.error) as error:
                    result, status = {"error": str(error)}, 400
                payload = json.dumps(result, ensure_ascii=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 0.2
        key_state = bind(operations.target.backend.user, "GetAsyncKeyState", [c.c_int], c.c_short)
        try:
            info_path.parent.mkdir(parents=True, exist_ok=True)
            info_path.write_text(json.dumps({"port": server.server_port, "token": token,
                                            "target_pid": pid, "expires_at": time.time() + lifetime}), encoding="utf-8")
            while not operations.stopping and time.monotonic() < deadline:
                if key_state(0x7B) & 0x8000:  # F12 emergency stop.
                    break
                server.handle_request()
        finally:
            server.server_close()
            info_path.unlink(missing_ok=True)


def request(info_path, operation, body=None):
    import urllib.request
    import urllib.error

    info = json.loads(Path(info_path).read_text(encoding="utf-8"))
    port = info["port"]
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Invalid local worker port")
    if operation not in ("health", "shutdown", "sample", "inspect-object", "scan", "foreground-click", "foreground-key", "background-key", "foreground-drag"):
        raise ValueError("Unsupported worker operation")
    call = urllib.request.Request(f"http://127.0.0.1:{port}/{operation}",
                                  data=json.dumps(body or {}).encode("utf-8"),
                                  headers={"Content-Type": "application/json", "X-Conquest-Token": info["token"]})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(call, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error).get("error", str(error))
        except (ValueError, AttributeError):
            detail = str(error)
        raise ValueError(detail) from error
