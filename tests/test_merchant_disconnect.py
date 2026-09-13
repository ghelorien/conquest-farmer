from types import SimpleNamespace as NS
import pytest
import threading
from conquest.merchants.disconnect import disconnect
from conquest.merchants.journal import Journal


def fixture(tmp_path):
    journal=Journal(tmp_path/'j.sqlite3');identity={'pid':7,'path':'C:/game/ImConquer.exe','creation_time_100ns':99}
    journal.set('Dutch','last_identity',identity)
    r=NS(journal=journal,coordinator=NS(owner=None,lock=threading.RLock()),enable=lambda n,on:journal.set(n,'enabled',on),
         set_refill_enabled=lambda n,on:journal.set(n,'refill_enabled',on))
    return r,identity


def test_disconnect_pins_identity_and_persists_stop(tmp_path):
    r,identity=fixture(tmp_path);calls=[]
    result=disconnect(r,'Dutch',close=lambda value:calls.append(value) or True)
    assert calls==[identity] and result['disconnected']
    assert r.journal.get('Dutch','enabled') is False
    assert r.journal.get('Dutch','refill_enabled') is False
    assert r.journal.get('Dutch','connect_hold') is True
    assert r.journal.get('Dutch','attention')['kind']=='protective_disconnect'


def test_failed_close_preserves_pause_and_does_not_claim_success(tmp_path):
    r,_=fixture(tmp_path)
    def fail(identity):raise ValueError('Process identity changed')
    with pytest.raises(ValueError,match='identity'):disconnect(r,'Dutch',close=fail)
    assert r.journal.get('Dutch','enabled') is False
    assert r.journal.get('Dutch','protective_disconnect') is None


@pytest.mark.parametrize('case',['owner','pending','identity'])
def test_disconnect_refuses_unsettled_input_or_missing_identity(tmp_path,case):
    r,_=fixture(tmp_path)
    if case=='owner':r.coordinator.owner='Spiritual'
    if case=='pending':r.journal.begin('t','Dutch','delivery',{})
    if case=='identity':r.journal.set('Dutch','last_identity',None)
    calls=[]
    with pytest.raises(ValueError):disconnect(r,'Dutch',close=lambda i:calls.append(i))
    assert not calls
