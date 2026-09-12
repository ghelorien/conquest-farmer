from types import SimpleNamespace as NS
import threading
import pytest
from conquest.merchants import delivery_operation as operation
from conquest.merchants.journal import Journal


def test_readiness_checks_qualification_without_input_or_journal(monkeypatch):
    calls=[]
    monkeypatch.setattr(operation,'FarmerTradeDriver',lambda ui:NS(require_qualified=lambda:calls.append('qualification')))
    assert operation.dispatch(NS(),{'action':'delivery-readiness'})=={'qualified':True}
    assert calls==['qualification']
    def absent():raise ValueError('No live qualification')
    monkeypatch.setattr(operation,'FarmerTradeDriver',lambda ui:NS(require_qualified=absent))
    assert operation.dispatch(NS(),{'action':'delivery-readiness'})=={'qualified':False}


def test_town_source_reader_requires_farmer_identity(monkeypatch):
    from conquest.merchants import delivery_bridge
    monkeypatch.setattr(delivery_bridge,'MerchantMemory',lambda observer:pytest.fail('Wrong character must not be read'))
    ui=NS(app=NS(observer=NS(character='Dutch')))
    with pytest.raises(ValueError,match='verified farmer'):
        delivery_bridge.dispatch(ui,{'action':'delivery-source'})


def test_unknown_route_submission_blocks_reload_before_source_receipt(monkeypatch):
    from conquest.merchants import delivery_route
    from conquest.discord_notify import write_json
    write_json(delivery_route.STATE,{'active':{'request_id':'unknown'}})
    with pytest.raises(ValueError,match='pending farmer delivery'):operation.guard_reload()


def test_disabled_rollout_cannot_construct_native_input_driver(tmp_path,monkeypatch):
    monkeypatch.setattr(operation,'JOURNAL',tmp_path/'source.sqlite3')
    monkeypatch.setattr(operation,'read_json',lambda path:{'enabled':False})
    monkeypatch.setattr(operation,'FarmerTradeDriver',lambda *a:pytest.fail('Native input must remain gated'))
    ui=NS()
    with pytest.raises(ValueError,match='not enabled'):
        operation.dispatch(ui,{'action':'delivery-start','request_id':'one','character':'Dutch','uids':[10]})
    status=operation.dispatch(ui,{'action':'delivery-status','request_id':'one'})
    assert status['receipt'] is None and not status['running']


def test_restart_only_reconciles_and_repeated_request_reuses_live_worker(tmp_path,monkeypatch):
    path=tmp_path/'source.sqlite3';monkeypatch.setattr(operation,'JOURNAL',path)
    journal=Journal(path)
    journal.begin('one','Dutch','farmer_delivery',{'items':[{'uid':10}]})
    monkeypatch.setattr(operation,'FarmerTradeDriver',lambda *a:pytest.fail('Recovery must not construct an input driver'))
    entered=threading.Event();release=threading.Event();calls=[]
    class Recovery:
        def __init__(self,journal,driver):pass
        def recover(self,key):
            calls.append(key);entered.set();assert release.wait(3)
    monkeypatch.setattr(operation,'DeliveryTransaction',Recovery)
    ui=NS(runtime=NS(journal=Journal(tmp_path/'merchant.sqlite3')))
    command={'action':'delivery-start','request_id':'one','character':'Dutch','uids':[10]}
    try:
        operation.dispatch(ui,command);assert entered.wait(1)
        worker=ui.delivery_workers['one']
        assert operation.dispatch(ui,command)['running']
        assert ui.delivery_workers['one'] is worker and calls==['one']
        with pytest.raises(ValueError,match='reused'):
            operation.dispatch(ui,{**command,'uids':[11]})
    finally:
        release.set();ui.delivery_workers['one'].join(2)
    assert not ui.delivery_workers['one'].is_alive()


def test_reload_waits_for_submitted_delivery_reconciliation(tmp_path,monkeypatch):
    path=tmp_path/'source.sqlite3';monkeypatch.setattr(operation,'JOURNAL',path)
    operation.guard_reload()
    journal=Journal(path);journal.begin('one','Dutch','farmer_delivery',{'items':[{'uid':10}]})
    for phase in ('submitted','uncertain'):
        journal.transition('one',phase)
        with pytest.raises(ValueError,match='before reloading'):operation.guard_reload()
    journal.transition('one','verified')
    operation.guard_reload()
