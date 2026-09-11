"""Short-lived, authenticated localhost worker for one selected game process.

The worker exposes bounded calibration operations, never a shell or arbitrary
Python evaluation. It runs only for the requested lifetime and removes its
connection file when stopped. F12 stops the worker between requests.
"""

import hmac
import base64
import ctypes as c
import json
import secrets
import struct
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from conquest.calibration import KINDS, ObservationSet, scan
from conquest.foreground import foreground_click, foreground_key, foreground_drag
from conquest.input_probe import MessageTarget, key_probe, click_probe, positioned_click_probe
from conquest.memory import MemorySession
from conquest.win32 import bind
from conquest.capture import CaptureUnavailable


class Operations:
    def __init__(self, session, hwnd, *, read_only=False):
        self.session = session
        self.target = MessageTarget(session.pid, hwnd)
        self.stopping = False
        self.read_only = read_only

    def dispatch(self, operation, body):
        self.session.assert_identity()
        if operation == "health":
            return {"worker": "live", "protocol_version": 2, "input_revision": 9,
                    "read_only": self.read_only,
                    "memory_read_revision": 1,
                    "expected_sha256": self.session.expected_sha256,
                    "target": self.session.identity,
                    "window": self.target.snapshot(), "modules": self.session.modules[:1]}
        if operation == "shutdown":
            self.stopping = True
            return {"stopped": True}
        if operation == "read-block":
            address, size = body.get("address"), body.get("size")
            if (not isinstance(address, str) or type(size) is not int
                    or not 1 <= size <= 65536):
                raise ValueError("Provide a hexadecimal address and 1 to 65536 bytes")
            address = int(address, 0)
            if not 0x10000 <= address <= 0x7FFFFFFFFFFF - size:
                raise ValueError("Read block is outside user memory bounds")
            data = self.session.read(address, size)
            if len(data) != size:
                raise ValueError("Incomplete memory block")
            self.session.assert_identity()
            return {"address": hex(address), "size": size, "encoding": "base64",
                    "data": base64.b64encode(data).decode("ascii"),
                    "qualified": False}
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
            max_mib = body.get("max_mib", 512)
            max_candidates = body.get("max_candidates", 2000)
            if type(max_mib) is not int or not 1 <= max_mib <= 4096:
                raise ValueError("Scan byte budget must be 1 to 4096 MiB")
            if type(max_candidates) is not int or not 1 <= max_candidates <= 10000:
                raise ValueError("Scan candidate limit must be 1 to 10000")
            return scan(self.session, observation, max_seconds=20,
                        max_bytes=max_mib * 1024 * 1024, max_candidates=max_candidates)
        if operation == "background-click":
            if self.read_only:
                raise ValueError("Game input is disabled in a read-only worker")
            # One diagnostic only. Use the decoded candidate instead of the old
            # maximum-HP field; this still does not qualify autonomous attacks.
            from conquest.memory_health import HealthLayout, MemoryHealthReader
            layout = HealthLayout.model_validate(body["health_profile"])
            if layout.player.expected_sha256 != self.session.expected_sha256:
                raise ValueError("Health profile fingerprint differs from client")
            from types import SimpleNamespace
            adapter = SimpleNamespace(expected_sha256=self.session.expected_sha256,
                modules=self.session.modules, assert_identity=self.session.assert_identity,
                read=self.session.read, read_block=self.session.read)
            reading = MemoryHealthReader(adapter, layout, body["character"]).read()
            if reading.current_hp <= 0 or reading.current_hp / reading.max_hp < .4:
                raise ValueError("Candidate HP is too low for an input diagnostic")
            if type(body.get('position_cursor',False)) is not bool:
                raise ValueError('position_cursor must be a boolean')
            if body.get('position_cursor',False):
                result = positioned_click_probe(self.target,*body['point'],body['expected_size'])
            else:
                options = {'move_settle_seconds':body['move_settle_seconds']} if 'move_settle_seconds' in body else {}
                if 'control' in body:
                    options['control']=body['control']
                result = click_probe(self.target, *body["point"], body["expected_size"], **options)
            result["hp_candidate_before"] = reading.current_hp
            result["max_hp_candidate_before"] = reading.max_hp
            return result
        if operation == 'revive-click':
            if self.read_only:
                raise ValueError('Game input is disabled in a read-only worker')
            # Recovery is a separate action: a ghost may retain positive HP.
            # It must never reuse the healthy-character combat/movement guard.
            from conquest.memory_life import read_life
            from conquest.memory_health import HealthLayout
            from types import SimpleNamespace
            layout = HealthLayout.model_validate(body['health_profile'])
            adapter = SimpleNamespace(expected_sha256=self.session.expected_sha256,
                modules=self.session.modules, assert_identity=self.session.assert_identity,
                read=self.session.read, read_block=self.session.read)
            life = read_life(adapter,layout,body['character'])
            if not life.revive_ready_candidate:
                raise ValueError('The inspected ghost state is not present; no Revive click sent')
            if body.get('expected_size') != [1036,793]:
                raise ValueError('Revive button geometry has not been inspected at this size')
            if set(body)-{'health_profile','character','expected_size','expires_at','input_mode'}:
                raise ValueError('Revive does not accept arbitrary click coordinates')
            mode=body.get('input_mode','background')
            if mode=='background':
                result = click_probe(self.target,518,640,[1036,793],move_settle_seconds=.2)
            elif mode=='foreground':
                # The hosted child shares its wrapper's foreground root. Convert
                # logical calibration to physical pixels before desktop input.
                from conquest.desktop_runtime import physical_coordinates
                if self.target.snapshot()['client_size']!=[1036,793]:
                    raise ValueError('Embedded Revive calibration changed')
                with physical_coordinates():
                    size=self.target.snapshot()['client_size']
                    point=[round(518*size[0]/1036),round(640*size[1]/793)]
                    result=foreground_click(self.target,*point,size)
            else:
                raise ValueError('Unknown recovery input mode')
            result['action'] = 'revive'
            result['death_position'] = list(life.position)
            result['death_map'] = life.map_id
            return result
        if operation in ("foreground-click", "foreground-key", "background-key", "foreground-drag"):
            if self.read_only:
                raise ValueError("Game input is disabled in a read-only worker")
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


def serve(pid, hwnd, expected_sha256, info_path, lifetime=1800, *, read_only=False):
    if not 30 <= lifetime <= 14400:
        raise ValueError("Worker lifetime must be 30 to 14400 seconds")
    info_path = Path(info_path)
    if info_path.exists():
        raise ValueError("Worker connection file already exists; check that worker before starting another")
    with MemorySession(pid, expected_sha256) as session:
        operations = Operations(session, hwnd, read_only=read_only)
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
                    if isinstance(error, CaptureUnavailable):
                        result['code'] = 'foreground_unavailable'
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
                                            "target_pid": pid, "read_only": read_only,
                                            "expires_at": time.time() + lifetime}), encoding="utf-8")
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
    if operation not in ("health", "shutdown", "sample", "sample-npcs", "town", "read-block", "inspect-object", "scan", "foreground-click", "foreground-key", "background-key", "background-click", "foreground-drag",'controls','reload-app','revive-click','native-window-mode','route-jump','reconnect-retry'):
        raise ValueError("Unsupported worker operation")
    call = urllib.request.Request(f"http://127.0.0.1:{port}/{operation}",
                                  data=json.dumps(body or {}).encode("utf-8"),
                                  headers={"Content-Type": "application/json", "X-Conquest-Token": info["token"]})
    try:
        # The authenticated bridge is HTTP on loopback only. Building the
        # default HTTPS handler loads Windows certificate stores on every
        # memory read and can make a multi-read scene stale before validation.
        opener = urllib.request.OpenerDirector()
        for handler in (urllib.request.HTTPHandler(),urllib.request.HTTPDefaultErrorHandler(),
                        urllib.request.HTTPErrorProcessor()):
            opener.add_handler(handler)
        with opener.open(call, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        error_code = None
        try:
            payload = json.load(error)
            detail, error_code = payload.get('error',str(error)), payload.get('code')
        except (ValueError, AttributeError):
            detail = str(error)
        if error_code == 'town_observation_unavailable':
            from conquest.town_trade import TownObservationUnavailable
            raise TownObservationUnavailable(detail) from error
        if error_code == 'foreground_unavailable':
            raise CaptureUnavailable(detail) from error
        raise ValueError(detail) from error
