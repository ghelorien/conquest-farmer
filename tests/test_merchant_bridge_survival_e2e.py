"""End-to-end: the merchant bridge keeps serving until close(), and says why.

Live incident 2026-09-25 ~10:19: the controller's MerchantBridge serving loop
exited during a burst of disk/CPU load (a SQLite "database is locked" on another
DB in the same minute). run()'s finally deleted bridge.json while
app-lifecycle.json still said "running" and merchant threads kept working, so
every client (shop-alert, deploy/park tooling) saw "Conquest is closed or not
responding". Nothing recorded the exception.

Real code under test: conquest.merchants.bridge.MerchantBridge (its HTTPServer,
Handler.do_POST, the run() loop and close()) listening on 127.0.0.1, the real
request() client reading bridge.json, and the bridge-errors.jsonl diagnostic
file written next to bridge.json.

Faults are injected only where the incident points:
- the dispatch callable (the app's command handler) raising;
- socketserver.BaseServer.handle_request raising OSError (select under load);
- the listening socket closed underneath the server;
- a handler failure reaching socketserver's handle_error while sys.stderr
  raises on write.

Failure modes, written before the fix:
 F1  dispatch raises sqlite3.OperationalError("database is locked"): the
     connection is dropped with no HTTP response, nothing is recorded, or the
     next request is not served.
 F2  dispatch raises RuntimeError: same as F1.
 F3  dispatch raises SystemExit: socketserver re-raises it past handle_error,
     run() exits and deletes bridge.json; threading.excepthook ignores
     SystemExit, so the bridge dies silently.
 F4  dispatch raises KeyboardInterrupt: same, the loop dies.
 F5  the fault response is not a clean HTTP 500 {"error": "<Type>: <message>"}
     JSON body, or request() hangs, or raises MerchantRejected (a 400-only
     application rejection) instead of a plain ValueError.
 F6  handle_request raises OSError twice (transient select failure): the loop
     exits and bridge.json is deleted; or a transient fault needlessly moves
     the server to another port.
 F7  handle_request raises OSError persistently: the loop exits, or spins
     without backoff, or bridge.json disappears / changes token while it
     fails, or requests are not served once the fault clears.
 F8  the listening socket is closed underneath the server: the loop dies, or
     keeps failing without a new server; bridge.json is not rewritten with the
     new port, the same token and pid.
 F9  a handler failure reaches handle_error and writing its traceback to
     stderr raises: the exception escapes handle_request and kills the loop.
 F10 failures are not recorded (where, type, message, traceback, time) in
     bridge-errors.jsonl next to bridge.json, or that file grows unbounded
     (it keeps only the last 50 entries).
 F11 close() no longer stops the loop, removes bridge.json and releases the
     app lock, including while the loop is backing off from a persistent fault.
 F12 regression: unauthorized 403 (without dispatch), the 400 responses
     (ValueError/KeyError/TypeError/OSError/CaptureUnavailable, unknown path,
     bad JSON, non-object) and the 409 transit response change bytes or client
     exception type, or get written to the diagnostic file.
 F13 the artifact is not repeatable.

Each scenario writes <tmp>/<run>/<scenario>/bridge-survival-e2e.json, is
re-read from disk for the assertions, and runs twice to prove the artifact is
byte-identical.
"""

import http.client
import json
import socketserver
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from conquest.capture import CaptureUnavailable
from conquest.merchants.bridge import MerchantBridge, MerchantRejected, request
from conquest.merchants.memory import TransitObservationChanged

ARTIFACT = "bridge-survival-e2e.json"
ORIGINAL_HANDLE_REQUEST = socketserver.BaseServer.handle_request
ORIGINAL_FINISH_REQUEST = socketserver.BaseServer.finish_request
SERVED = {"result": {"ok": "status"}}
STATUS = {"action": "status"}


class Dispatch:
    """The app's command handler; raises the queued faults, then answers."""

    def __init__(self):
        self.faults, self.calls = [], 0

    def __call__(self, body):
        self.calls += 1
        if self.faults:
            raise self.faults.pop(0)()
        return {"ok": body.get("action")}


class BrokenStream:
    def write(self, *args):
        raise OSError(22, "Invalid argument")

    flush = write


def wait_until(check, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return bool(check())


def read_info(path):
    for _ in range(40):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except PermissionError:
            time.sleep(0.01)
        except ValueError:
            return "invalid"
    return "unreadable"


def snapshot(path, before):
    info = read_info(path)
    if not isinstance(info, dict):
        return {"exists": False, "same_port": None, "same_token": None}
    return {
        "exists": True,
        "same_port": info.get("port") == before["port"],
        "same_token": info.get("token") == before["token"],
    }


def call(path, body=STATUS):
    try:
        return {"result": request(body, path)}
    except TransitObservationChanged as error:
        return {"raised": "TransitObservationChanged", "message": str(error)}
    except MerchantRejected as error:
        return {"raised": "MerchantRejected", "message": str(error)}
    except ValueError as error:
        return {"raised": "ValueError", "message": str(error)}
    except OSError:
        return {"raised": "transport"}


def raw(port, token, body, target="/merchants"):
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Conquest-Token"] = token
        client.request("POST", target, body=body, headers=headers)
        reply = client.getresponse()
        return {
            "status": reply.status,
            "content_type": reply.getheader("Content-Type"),
            "body": reply.read().decode(),
        }
    except OSError:
        return {"raw": "transport"}
    finally:
        client.close()


def log_rows(path):
    errors = Path(path).with_name("bridge-errors.jsonl")
    if not errors.exists():
        return []
    return [
        json.loads(line)
        for line in errors.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def error_rows(rows):
    return [row for row in rows if row.get("where") != "recreated"]


def complete(rows):
    return all(
        isinstance(row.get("time"), (int, float))
        and all(isinstance(row.get(k), str) and row[k] for k in ("where", "type"))
        and isinstance(row.get("message"), str)
        and isinstance(row.get("traceback"), str)
        and row["type"] in row["traceback"]
        for row in error_rows(rows)
    )


def normal(rows):
    return [
        {k: row[k] for k in ("where", "type", "message", "action") if k in row}
        for row in rows
    ]


def lock_released(path):
    try:
        MerchantBridge(lambda body: {}, path).close()
        return True
    except ValueError:
        return False


def fault_handle_request(m, error, fail):
    """Make every server's handle_request raise while fail() says so."""
    count = {"calls": 0}

    def faulty(self):
        if fail():
            count["calls"] += 1
            raise error()
        return ORIGINAL_HANDLE_REQUEST(self)

    m.setattr(socketserver.BaseServer, "handle_request", faulty)
    return count


def dispatch_fault(make):
    def scenario(path, m):
        dispatch = Dispatch()
        bridge = MerchantBridge(dispatch, path)
        try:
            before = read_info(path)
            dispatch.faults.append(make)
            reply = raw(before["port"], before["token"], json.dumps(STATUS).encode())
            dispatch.faults.append(make)
            client = call(path)
            served = call(path)
            rows = log_rows(path)
            return {
                "raw": reply,
                "client": client,
                "served_after": served,
                "bridge_json": snapshot(path, before),
                "alive": bridge.thread.is_alive(),
                "rows": normal(rows),
                "rows_complete": complete(rows),
            }
        finally:
            bridge.close()

    return scenario


def serve_transient(path, m):
    bridge = MerchantBridge(Dispatch(), path)
    try:
        before = read_info(path)
        left = {"n": 2}

        def fail():
            if left["n"]:
                left["n"] -= 1
                return True
            return False

        fault_handle_request(m, lambda: OSError(10038, "injected select failure"), fail)
        wait_until(lambda: left["n"] == 0)
        served = call(path)
        rows = log_rows(path)
        return {
            "served_after": served,
            "bridge_json": snapshot(path, before),
            "alive": bridge.thread.is_alive(),
            "rows": normal(rows),
            "rows_complete": complete(rows),
        }
    finally:
        bridge.close()


def serve_persistent(path, m):
    bridge = MerchantBridge(Dispatch(), path)
    try:
        before = read_info(path)
        state = {"fail": True}
        count = fault_handle_request(
            m, lambda: OSError(10055, "injected no buffer space"), lambda: state["fail"]
        )
        valid, start = True, time.monotonic()
        while time.monotonic() - start < 1.5:
            info = read_info(path)
            valid = (
                valid and isinstance(info, dict) and info["token"] == before["token"]
            )
            time.sleep(0.05)
        calls = count["calls"]
        state["fail"] = False
        served = call(path)
        rows = log_rows(path)
        return {
            "bridge_json_valid_throughout": valid,
            # Unbounded retries would reach thousands of calls in 1.5 s.
            "faulted_calls_bounded": 3 <= calls <= 20,
            "served_after": served,
            "same_token": snapshot(path, before)["same_token"],
            "alive": bridge.thread.is_alive(),
            "rows_where": sorted({row["where"] for row in rows}),
            "rows_types": sorted({row["type"] for row in error_rows(rows)}),
            "rows_complete": complete(rows),
        }
    finally:
        bridge.close()


def socket_broken(path, m):
    bridge = MerchantBridge(Dispatch(), path)
    try:
        before = read_info(path)
        served_before = call(path)
        bridge.server.socket.close()

        def moved():
            info = read_info(path)
            return isinstance(info, dict) and info["port"] != before["port"]

        has_moved = wait_until(moved)
        after = read_info(path) if has_moved else {}
        served = call(path)
        rows = log_rows(path)
        recreated = [row for row in rows if row.get("where") == "recreated"]
        return {
            "served_before": served_before,
            "moved": has_moved,
            "same_token": after.get("token") == before["token"],
            "same_pid": after.get("pid") == before["pid"],
            "served_after": served,
            "alive": bridge.thread.is_alive(),
            "rows_where": sorted({row["where"] for row in rows}),
            "error_types_known": {row["type"] for row in error_rows(rows)}
            <= {"OSError", "ValueError"},
            "recreated_row_matches": bool(recreated)
            and recreated[-1].get("old_port") == before["port"]
            and recreated[-1].get("new_port") == after.get("port"),
            "rows_complete": complete(rows),
        }
    finally:
        bridge.close()


def handle_error_stderr_broken(path, m):
    bridge = MerchantBridge(Dispatch(), path)
    try:
        before = read_info(path)
        left = {"n": 1}

        def failing_finish(self, request_socket, client_address):
            if left["n"]:
                left["n"] -= 1
                raise RuntimeError("injected handler failure")
            return ORIGINAL_FINISH_REQUEST(self, request_socket, client_address)

        m.setattr(socketserver.BaseServer, "finish_request", failing_finish)
        m.setattr(sys, "stderr", BrokenStream())
        first = call(path)
        served = call(path)
        m.undo()
        rows = log_rows(path)
        return {
            "first": first,
            "served_after": served,
            "bridge_json": snapshot(path, before),
            "alive": bridge.thread.is_alive(),
            "rows": normal(rows),
            "rows_complete": complete(rows),
        }
    finally:
        bridge.close()


def error_log_bounded(path, m):
    dispatch = Dispatch()
    bridge = MerchantBridge(dispatch, path)
    try:
        answers = []
        for index in range(60):
            dispatch.faults.append(lambda index=index: RuntimeError(f"fault {index}"))
            answers.append(call(path))
        served = call(path)
        rows = log_rows(path)
        errors = Path(path).with_name("bridge-errors.jsonl")
        return {
            "client_answers_ok": answers
            == [
                {"raised": "ValueError", "message": f"RuntimeError: fault {index}"}
                for index in range(60)
            ],
            "rows": len(rows),
            "first_message": rows[0]["message"] if rows else None,
            "last_message": rows[-1]["message"] if rows else None,
            "size_bounded": errors.exists() and errors.stat().st_size < 300_000,
            "served_after": served,
            "alive": bridge.thread.is_alive(),
        }
    finally:
        bridge.close()


def close_normal(path, m):
    bridge = MerchantBridge(Dispatch(), path)
    served = call(path)
    bridge.close()
    return {
        "served_before": served,
        "alive_after_close": bridge.thread.is_alive(),
        "bridge_json_exists": Path(path).exists(),
        "lock_released": lock_released(path),
        "stale_client": call(path),
    }


def close_during_fault(path, m):
    bridge = MerchantBridge(Dispatch(), path)
    count = fault_handle_request(
        m, lambda: OSError(10055, "injected no buffer space"), lambda: True
    )
    wait_until(lambda: count["calls"] >= 4)
    existed, alive = Path(path).exists(), bridge.thread.is_alive()
    start = time.monotonic()
    bridge.close()
    elapsed = time.monotonic() - start
    return {
        "bridge_json_existed_before_close": existed,
        "alive_before_close": alive,
        "closed_promptly": elapsed < 1.5,
        "alive_after_close": bridge.thread.is_alive(),
        "bridge_json_exists": Path(path).exists(),
        "lock_released": lock_released(path),
    }


def contract(path, m):
    dispatch = Dispatch()
    bridge = MerchantBridge(dispatch, path)
    try:
        info = read_info(path)
        port, token = info["port"], info["token"]
        body = json.dumps(STATUS).encode()
        forbidden = raw(port, "0" * 64, body)
        forbidden_calls = dispatch.calls
        out = {
            "forbidden": {
                "status": forbidden.get("status"),
                "content_type": forbidden.get("content_type"),
                "body_has_code": "Error code: 403" in forbidden.get("body", ""),
                "dispatched": forbidden_calls != 0,
            }
        }
        faults = {
            "value_error": lambda: ValueError("bad thing"),
            "key_error": lambda: KeyError("slot"),
            "type_error": lambda: TypeError("wrong type"),
            "os_error": lambda: FileNotFoundError("missing file"),
            "capture_unavailable": lambda: CaptureUnavailable(
                "Waiting for a safe farmer handoff"
            ),
            "transit": lambda: TransitObservationChanged("position changed"),
        }
        for name, make in faults.items():
            dispatch.faults.append(make)
            reply = raw(port, token, body)
            dispatch.faults.append(make)
            out[name] = {"raw": reply, "client": call(path)}
        out["unknown_path"] = raw(port, token, body, "/other")
        out["bad_json"] = raw(port, token, b"{")
        out["non_object"] = raw(port, token, b"[]")
        out["served_after"] = call(path)
        out["rows"] = log_rows(path)
        return out
    finally:
        bridge.close()


SCENARIOS = {
    "dispatch_sqlite_locked": dispatch_fault(
        lambda: sqlite3.OperationalError("database is locked")
    ),
    "dispatch_runtime_error": dispatch_fault(lambda: RuntimeError("injected fault")),
    "dispatch_system_exit": dispatch_fault(lambda: SystemExit(3)),
    "dispatch_keyboard_interrupt": dispatch_fault(
        lambda: KeyboardInterrupt("injected interrupt")
    ),
    "serve_transient": serve_transient,
    "serve_persistent": serve_persistent,
    "socket_broken": socket_broken,
    "handle_error_stderr_broken": handle_error_stderr_broken,
    "error_log_bounded": error_log_bounded,
    "close_normal": close_normal,
    "close_during_fault": close_during_fault,
    "contract": contract,
}


def dispatch_expected(type_name, message):
    error = f"{type_name}: {message}"
    row = {
        "where": "dispatch",
        "type": type_name,
        "message": message,
        "action": "status",
    }
    return {
        "raw": {
            "status": 500,
            "content_type": "application/json",
            "body": json.dumps({"error": error}),
        },
        "client": {"raised": "ValueError", "message": error},
        "served_after": SERVED,
        "bridge_json": {"exists": True, "same_port": True, "same_token": True},
        "alive": True,
        "rows": [row, row],
        "rows_complete": True,
    }


def rejected(status, body, client):
    return {
        "raw": {"status": status, "content_type": "application/json", "body": body},
        "client": client,
    }


# F12 bodies are the exact bytes the pre-fix bridge produced.
EXPECTED = {
    "dispatch_sqlite_locked": dispatch_expected(
        "OperationalError", "database is locked"
    ),
    "dispatch_runtime_error": dispatch_expected("RuntimeError", "injected fault"),
    "dispatch_system_exit": dispatch_expected("SystemExit", "3"),
    "dispatch_keyboard_interrupt": dispatch_expected(
        "KeyboardInterrupt", "injected interrupt"
    ),
    "serve_transient": {
        "served_after": SERVED,
        "bridge_json": {"exists": True, "same_port": True, "same_token": True},
        "alive": True,
        "rows": [
            {
                "where": "serve",
                "type": "OSError",
                "message": "[Errno 10038] injected select failure",
            }
        ]
        * 2,
        "rows_complete": True,
    },
    "serve_persistent": {
        "bridge_json_valid_throughout": True,
        "faulted_calls_bounded": True,
        "served_after": SERVED,
        "same_token": True,
        "alive": True,
        "rows_where": ["recreated", "serve"],
        "rows_types": ["OSError"],
        "rows_complete": True,
    },
    "socket_broken": {
        "served_before": SERVED,
        "moved": True,
        "same_token": True,
        "same_pid": True,
        "served_after": SERVED,
        "alive": True,
        "rows_where": ["recreated", "serve"],
        "error_types_known": True,
        "recreated_row_matches": True,
        "rows_complete": True,
    },
    "handle_error_stderr_broken": {
        "first": {"raised": "transport"},
        "served_after": SERVED,
        "bridge_json": {"exists": True, "same_port": True, "same_token": True},
        "alive": True,
        "rows": [
            {
                "where": "request",
                "type": "RuntimeError",
                "message": "injected handler failure",
            }
        ],
        "rows_complete": True,
    },
    "error_log_bounded": {
        "client_answers_ok": True,
        "rows": 50,
        "first_message": "fault 10",
        "last_message": "fault 59",
        "size_bounded": True,
        "served_after": SERVED,
        "alive": True,
    },
    "close_normal": {
        "served_before": SERVED,
        "alive_after_close": False,
        "bridge_json_exists": False,
        "lock_released": True,
        "stale_client": {"raised": "transport"},
    },
    "close_during_fault": {
        "bridge_json_existed_before_close": True,
        "alive_before_close": True,
        "closed_promptly": True,
        "alive_after_close": False,
        "bridge_json_exists": False,
        "lock_released": True,
    },
    "contract": {
        "forbidden": {
            "status": 403,
            "content_type": "text/html;charset=utf-8",
            "body_has_code": True,
            "dispatched": False,
        },
        "value_error": rejected(
            400,
            '{"error": "bad thing"}',
            {"raised": "MerchantRejected", "message": "bad thing"},
        ),
        "key_error": rejected(
            400,
            '{"error": "\'slot\'"}',
            {"raised": "MerchantRejected", "message": "'slot'"},
        ),
        "type_error": rejected(
            400,
            '{"error": "wrong type"}',
            {"raised": "MerchantRejected", "message": "wrong type"},
        ),
        "os_error": rejected(
            400,
            '{"error": "missing file"}',
            {"raised": "MerchantRejected", "message": "missing file"},
        ),
        "capture_unavailable": rejected(
            400,
            '{"error": "Waiting for a safe farmer handoff"}',
            {
                "raised": "MerchantRejected",
                "message": "Waiting for a safe farmer handoff",
            },
        ),
        "transit": rejected(
            409,
            '{"error": "position changed", '
            '"code": "merchant_transit_observation_changed"}',
            {"raised": "TransitObservationChanged", "message": "position changed"},
        ),
        "unknown_path": {
            "status": 400,
            "content_type": "application/json",
            "body": '{"error": "Unknown operation"}',
        },
        "bad_json": {
            "status": 400,
            "content_type": "application/json",
            "body": '{"error": "Expecting property name enclosed in double quotes: '
            'line 1 column 2 (char 1)"}',
        },
        "non_object": {
            "status": 400,
            "content_type": "application/json",
            "body": '{"error": "Expected an object"}',
        },
        "served_after": SERVED,
        "rows": [],
    },
}


def run_scenario(root, name):
    base = root / name
    base.mkdir(parents=True)
    with pytest.MonkeyPatch.context() as m:
        artifact = {"scenario": name, **SCENARIOS[name](base / "bridge.json", m)}
    path = base / ARTIFACT
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_bridge_keeps_serving_until_close(tmp_path, name):
    first = run_scenario(tmp_path / "first", name)
    second = run_scenario(tmp_path / "second", name)
    artifact = json.loads(first.read_text(encoding="utf-8"))
    assert artifact == {"scenario": name, **EXPECTED[name]}
    # F13: deterministic, repeatable artifact.
    assert first.read_bytes() == second.read_bytes()
