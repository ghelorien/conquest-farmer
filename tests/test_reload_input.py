from contextlib import contextmanager
from types import SimpleNamespace as NS
import pytest
from conquest import native_farm as n


@pytest.mark.parametrize('fails',[False,True])
def test_reload_and_inventory_cleanup_use_logical_coordinates(monkeypatch,fails):
    active=[False];calls=[]
    @contextmanager
    def logical():
        active[0]=True
        try:yield
        finally:active[0]=False
    monkeypatch.setattr(n,'logical_coordinates',logical)
    def trade(body):
        assert active[0]
        calls.append(body)
        if fails and body['action']=='equip-arrows':raise ValueError('Uncertain receipt')
        return {'equipped':7}
    s=n.NativeFarmSupervisor.__new__(n.NativeFarmSupervisor)
    s.observer=NS(town_trade=trade);s.dispatch=lambda callback:callback()
    bag=NS(items=[NS(uid=7,type_id=1050001,amount=1000)])
    if fails:
        with pytest.raises(ValueError,match='Uncertain receipt'):s.reload_arrows(bag,1050001)
    else:assert s.reload_arrows(bag,1050001)=={'equipped':7}
    assert calls==[{'action':'equip-arrows','uid':7},{'action':'close','window':'Inventory'}]
    assert not active[0]


@pytest.mark.parametrize('failure',[None,'Equipment changed before equip input','Uncertain receipt'])
def test_cleanup_failure_preserves_reload_result_and_retries_cleanup(monkeypatch,failure):
    from contextlib import nullcontext
    from conquest.capture import CaptureUnavailable
    monkeypatch.setattr(n,'logical_coordinates',nullcontext)
    calls=[]
    def trade(body):
        calls.append(body)
        if body['action']=='close':raise ValueError('Panel moved')
        if failure:raise ValueError(failure)
        return {'equipped':7}
    s=n.NativeFarmSupervisor.__new__(n.NativeFarmSupervisor)
    s.observer=NS(town_trade=trade);s.dispatch=lambda callback:callback()
    bag=NS(items=[NS(uid=7,type_id=1050001,amount=1000)])
    if failure:
        expected=CaptureUnavailable if failure.startswith('Equipment changed') else ValueError
        with pytest.raises(expected,match=failure):s.reload_arrows(bag,1050001)
    else:assert s.reload_arrows(bag,1050001)=={'equipped':7}
    assert s.supply_panel_pending
    assert len(calls)==2


def test_reload_after_guard_rejection_selects_fresh_reserve(monkeypatch):
    from contextlib import nullcontext
    from conquest.capture import CaptureUnavailable
    monkeypatch.setattr(n,'logical_coordinates',nullcontext)
    attempts=[]
    def trade(body):
        if body['action']=='close':return {}
        attempts.append(body['uid'])
        if len(attempts)==1:raise ValueError('Equipment changed before equip input')
        return {'equipped':body['uid']}
    s=n.NativeFarmSupervisor.__new__(n.NativeFarmSupervisor)
    s.observer=NS(town_trade=trade);s.dispatch=lambda callback:callback()
    with pytest.raises(CaptureUnavailable):
        s.reload_arrows(NS(items=[NS(uid=7,type_id=1050001,amount=1000)]),1050001)
    assert s.reload_arrows(NS(items=[NS(uid=8,type_id=1050001,amount=900)]),1050001)=={'equipped':8}
    assert attempts==[7,8]
