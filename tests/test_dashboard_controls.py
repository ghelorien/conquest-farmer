import http.client
import json
import re
import threading

import pytest

from conquest.control import FarmingControl
from conquest.dashboard import make_dashboard_server


@pytest.fixture
def dashboard(tmp_path):
    control = FarmingControl()
    server = make_dashboard_server(tmp_path / "missing.sqlite3", 0, control=control)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, control
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def call(port, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        return response.status, response.read().decode()
    finally:
        connection.close()


def token(port):
    status, page = call(port, "GET", "/")
    assert status == 200
    return re.search(r"token='([0-9a-f]{64})'", page)[1]


def test_control_post_and_status_reflect_same_state(dashboard):
    port, control = dashboard
    status, body = call(port, "POST", "/control", json.dumps({"enabled":True,"target_ids":[77]}),
        {"Content-Type":"application/json", "X-Control-Token":token(port)})
    assert status == 200
    assert json.loads(body)["target_ids"] == [77]
    _, status_body = call(port, "GET", "/status")
    assert json.loads(status_body)["control"]["enabled"]
    assert json.loads(status_body)["control"]["execution_state"] == "blocked"


def test_other_website_cannot_switch_farming_on(dashboard):
    port, control = dashboard
    for headers in [{}, {"X-Control-Token":token(port), "Origin":"https://example.com"},
                    {"X-Control-Token":token(port), "Host":"attacker.example"}]:
        status, _ = call(port,"POST","/control",'{"enabled":true}',
            {"Content-Type":"application/json", **headers})
        assert status == 403
        assert not control.snapshot()["enabled"]


def test_bad_ids_cannot_partially_enable_farming(dashboard):
    port, control = dashboard
    status, _ = call(port,"POST","/control",'{"enabled":true,"target_ids":[-1]}',
        {"Content-Type":"application/json", "X-Control-Token":token(port)})
    assert status == 400
    assert not control.snapshot()["enabled"]
