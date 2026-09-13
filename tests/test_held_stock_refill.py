from types import SimpleNamespace as NS
import copy
import time
import pytest
from conquest.merchants.journal import Journal
from conquest.merchants.held_stock_refill import allowed


def fixture(tmp_path):
    def item(uid): return dict(uid=uid,type_id=1088001,plus=0,gem1=0,gem2=0,quantity=1)
    j=Journal(tmp_path/'j.db');identity={'pid':10,'creation_time_100ns':20}
    incident={'phase':'needs_attention','note':'Stock missing','before':{'inventory':[item(1)],'booth':[]}}
    j.set('Dutch','shop_return',incident)
    r=NS(journal=j,refill_enabled=lambda c:True,returns={'Dutch':NS(state=lambda:j.get('Dutch','shop_return'))},
         observers={'Dutch':NS(adapter=NS(identity=identity))})
    snapshot=dict(identity=identity,map_id=1036,booth_open=True,own_booth_uid=123,
                  timestamp=time.time()-.5,inventory=[item(2)],booth=[])
    return r,snapshot,incident


def test_current_refill_preserves_original_incident_and_missing_ids(tmp_path):
    r,s,incident=fixture(tmp_path)
    assert not allowed(r,'Dutch',s)
    s['timestamp']=time.time()
    assert allowed(r,'Dutch',s)
    assert r.journal.get('Dutch','shop_return')==incident
    e=r.journal.get('Dutch','held_stock_refill')
    assert e['incident_unresolved'] and e['missing_uids']==[1] and e['new_uids']==[2]
    assert r.journal.get('Dutch','enabled') is None


@pytest.mark.parametrize('change',[{'map_id':1002},{'booth_open':False},{'own_booth_uid':None},
    {'trade':{'uid':1}},{'request':{'uid':2}},{'timestamp':0},{'identity':{'pid':99}}])
def test_unsafe_or_unqualified_refill_is_rejected(tmp_path,change):
    r,s,_=fixture(tmp_path);assert not allowed(r,'Dutch',s)
    s.update(timestamp=time.time(),**{k:v for k,v in change.items() if k!='timestamp'})
    if 'timestamp' in change:s['timestamp']=change['timestamp']
    assert not allowed(r,'Dutch',s)


def test_pending_transaction_blocks_current_stock_refill(tmp_path):
    r,s,_=fixture(tmp_path);allowed(r,'Dutch',s)
    r.journal.begin('pending','Dutch','trade',{})
    s['timestamp']=time.time();assert not allowed(r,'Dutch',s)


def test_new_delivery_readiness_keeps_loss_incident_and_rechecks_live_safety(tmp_path):
    from conquest.merchants.held_stock_refill import market_ready
    r,s,incident=fixture(tmp_path)
    assert not market_ready(r,'Dutch',s)
    allowed(r,'Dutch',s);s['timestamp']=time.time();assert allowed(r,'Dutch',s)
    assert market_ready(r,'Dutch',s)
    assert r.journal.get('Dutch','shop_return')==incident
    s['trade']={'peer':77};assert not market_ready(r,'Dutch',s)
    s.pop('trade');s['map_id']=1002;assert not market_ready(r,'Dutch',s)
