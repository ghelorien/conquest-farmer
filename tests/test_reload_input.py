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
