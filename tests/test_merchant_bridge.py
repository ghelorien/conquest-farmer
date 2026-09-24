import http.client
import time
import pytest
from conquest.capture import CaptureUnavailable
from conquest.merchants.bridge import MerchantBridge, request


def test_unauthenticated_delayed_body_is_rejected_without_dispatch_or_connection_reset(
    tmp_path,
):
    calls = []
    bridge = MerchantBridge(
        lambda body: calls.append(body) or {"ok": True}, tmp_path / "bridge.json"
    )
    client = http.client.HTTPConnection(
        "127.0.0.1", bridge.server.server_port, timeout=2
    )
    try:
        body = b'{"action":"status"}'
        client.putrequest("POST", "/merchants")
        client.putheader("Content-Length", str(len(body)))
        client.endheaders()
        time.sleep(0.05)
        client.send(body)
        reply = client.getresponse()
        assert reply.status == 403
        reply.read()
        assert calls == []
        assert request({"action": "status"}, bridge.path) == {"ok": True}
        assert calls == [{"action": "status"}]
    finally:
        client.close()
        bridge.close()


def test_recoverable_input_wait_returns_error_without_breaking_authenticated_bridge(
    tmp_path,
):
    def waiting(body):
        raise CaptureUnavailable("Waiting for a safe farmer handoff")

    bridge = MerchantBridge(waiting, tmp_path / "bridge.json")
    try:
        with pytest.raises(ValueError, match="safe farmer handoff"):
            request({"action": "delivery-start"}, bridge.path)
        bridge.dispatch = lambda body: {"ok": True}
        assert request({"action": "status"}, bridge.path) == {"ok": True}
    finally:
        bridge.close()


def test_transit_observation_type_survives_the_authenticated_http_boundary(tmp_path):
    from conquest.merchants.memory import TransitObservationChanged

    bridge = MerchantBridge(
        lambda body: (_ for _ in ()).throw(
            TransitObservationChanged("Merchant position changed during observation")
        ),
        tmp_path / "bridge.json",
    )
    try:
        with pytest.raises(TransitObservationChanged, match="position changed"):
            request({"action": "delivery-source"}, bridge.path)
        bridge.dispatch = lambda body: (_ for _ in ()).throw(
            ValueError("delivery source changed")
        )
        with pytest.raises(ValueError, match="delivery source changed") as error:
            request({"action": "delivery-source"}, bridge.path)
        assert not isinstance(error.value, TransitObservationChanged)
    finally:
        bridge.close()
