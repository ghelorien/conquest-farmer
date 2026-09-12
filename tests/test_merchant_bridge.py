import http.client
import time
import pytest
from conquest.capture import CaptureUnavailable
from conquest.merchants.bridge import MerchantBridge,request


def test_unauthenticated_delayed_body_is_rejected_without_dispatch_or_connection_reset(tmp_path):
    calls=[]
    bridge=MerchantBridge(lambda body:calls.append(body) or {'ok':True},tmp_path/'bridge.json')
    client=http.client.HTTPConnection('127.0.0.1',bridge.server.server_port,timeout=2)
    try:
        body=b'{"action":"status"}'
        client.putrequest('POST','/merchants')
        client.putheader('Content-Length',str(len(body)));client.endheaders()
        time.sleep(.05)
        client.send(body)
        reply=client.getresponse()
        assert reply.status==403
        reply.read()
        assert calls==[]
        assert request({'action':'status'},bridge.path)=={'ok':True}
        assert calls==[{'action':'status'}]
    finally:
        client.close();bridge.close()


def test_recoverable_input_wait_returns_error_without_breaking_authenticated_bridge(tmp_path):
    def waiting(body):raise CaptureUnavailable('Waiting for a safe farmer handoff')
    bridge=MerchantBridge(waiting,tmp_path/'bridge.json')
    try:
        with pytest.raises(ValueError,match='safe farmer handoff'):
            request({'action':'delivery-start'},bridge.path)
        bridge.dispatch=lambda body:{'ok':True}
        assert request({'action':'status'},bridge.path)=={'ok':True}
    finally:bridge.close()
