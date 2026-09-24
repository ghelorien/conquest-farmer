import threading
import time
from types import SimpleNamespace

import pytest

from conquest.merchants.service_visit import MarketVisit
from conquest.merchants.handoff import WorkWindows
from conquest.merchants.refill import RefillSchedule
from conquest.merchants.journal import Journal


@pytest.mark.parametrize('map_id,hp,age,accepted', [
    (1036, 100, 0, True), (1011, 100, 0, False),
    (1036, 0, 0, False), (1036, 100, 3, False),
])
def test_1078_market_grant_uses_build_aware_farmer_memory(
        tmp_path, monkeypatch, map_id, hp, age, accepted):
    from conquest.merchants import delivery_bridge, service_visit, trade_reader_1078
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    from conquest.discord_notify import write_json

    now=time.time()
    path=tmp_path/'market-visit.json'
    row={'phase':'active','visit_id':'same-visit','farmer_profile_id':'farmer-test',
         'started_at':now-30,'deadline':now+30}
    write_json(path,row)
    monkeypatch.setattr(service_visit,'state_path',lambda _:path)
    monkeypatch.setattr(service_visit,'farmer_id',lambda:'farmer-test')
    monkeypatch.setattr(service_visit,'farmer_name',lambda:'Farmer')
    calls=[]
    class TradeReader:
        def __init__(self, observer):
            calls.append(observer)
        def read(self):
            return {'map_id':map_id,'hp':hp,'timestamp':now-age}
    monkeypatch.setattr(trade_reader_1078,'TradeMemory1078',TradeReader)
    monkeypatch.setattr(delivery_bridge,'MerchantMemory',
                        lambda _:pytest.fail('1078 must not use the legacy reader'))
    observer=SimpleNamespace(character='Farmer',lock=threading.RLock(),
                             adapter=SimpleNamespace(expected_sha256=CLIENT_SHA256_1078))
    ui=SimpleNamespace(app=SimpleNamespace(observer=observer))
    body={'visit_id':'same-visit','expires_at':row['deadline']}
    if accepted:
        assert service_visit.validate_grant(ui,body)==row
    else:
        with pytest.raises(ValueError,match='fresh living farmer in Market'):
            service_visit.validate_grant(ui,body)
    assert calls==[observer]


def test_visit_deadline_and_hunting_cadence_survive_batches_restart(tmp_path):
    now=[1000]
    visit=MarketVisit(tmp_path/'visit.json',clock=lambda:now[0])
    first=visit.begin(parent='trip:1',profile='farmer-a')
    windows=WorkWindows(tmp_path/'windows.json',clock=lambda:now[0])
    assert windows.reserve('batch1',town=True,visit=first)
    assert windows.started()==1060
    now[0]=1030
    restarted=MarketVisit(visit.path,clock=lambda:now[0])
    assert restarted.begin(parent='trip:1',profile='farmer-a')==first
    assert windows.reserve('batch2',town=True,visit=first)
    assert windows.started()==1060 and windows.state()['next_check']==1900
    now[0]=1061
    assert not windows.reserve('batch3',town=True,visit=first)
    assert not windows.due()
    restarted.departed(1011)
    now[0]=1100
    assert restarted.begin(parent='trip:2',profile='farmer-a')['deadline']==1160


def test_budget_pause_preserves_queue_and_completed_check(tmp_path):
    now=[1000];j=Journal(tmp_path/'state.sqlite3')
    s=RefillSchedule('Dutch',j,clock=lambda:now[0])
    s.state();s.start();s.checkpoint([21,22],listed=2)
    old=s.state()['next_check'];s.pause_budget()
    state=s.state()
    assert state['pending'] and state['cursor']==[21,22]
    assert state['last_checked'] is None and state['next_check']==old
    assert state['status']=='paused_budget' and state['listed']==2
    now[0]=2000;s.start()
    assert s.state()['cursor']==[21,22] and s.state()['original_due_at']==old
    s.complete('completed',listed=4)
    assert s.state()['last_completed_check_at']==2000
    assert not s.state()['pending'] and s.state()['next_check']==2900
