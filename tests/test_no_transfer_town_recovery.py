"""Only actual terminal empty cleanup can release the original town tail."""
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace as NS
import pytest

from test_settled_delivery_town_recovery import proof as bilateral_fixture
from conquest import no_transfer_town_recovery as recovery
from conquest.merchants import delivery, delivery_route
from conquest.discord_notify import write_json


@pytest.fixture
def cancelled(bilateral_fixture,monkeypatch):
    x=bilateral_fixture
    monkeypatch.setattr(recovery,'state_path',lambda _:x.receiver)
    x.intent['items'][0]['type_id']=720027
    x.intent['farmer']['inventory'][1]['type_id']=720027
    x.meteor['after']['items'][1].update(type_id=720027,amount=1,limit=1)
    x.meteor['scroll_uid']=3
    x.farmer=deepcopy(x.intent['farmer']);x.farmer['timestamp']=867.
    x.merchant=deepcopy(x.intent['merchant']);x.merchant['timestamp']=867.
    x.trace=[dict(stage='action_trace',status='initialized',payload={'version':1},timestamp=845.),
        dict(stage='trade_target_mode',status='before_action',payload={},timestamp=846.),
        dict(stage='trade_request',status='before_action',payload={},timestamp=847.),
        dict(stage='trade_request',status='observed',payload={'trade_open':True},timestamp=850.),
        dict(stage='cleanup_trade',status='before_action',payload={},timestamp=860.),
        dict(stage='cleanup_trade',status='observed',payload={'farmer_trade':False,'merchant_trade':False},timestamp=861.)]
    x.market['attempts']=[];x.sales=[]
    with sqlite3.connect(x.receiver) as db:
        db.executescript('CREATE TABLE sales(id INTEGER,character TEXT,observed_at REAL,phase TEXT,items TEXT,silver INTEGER);'
                        'CREATE TABLE events(character TEXT,event TEXT,timestamp REAL);')
    def persist():
        x.trace=[s for s in x.trace if s['stage']!='reconciliation_observation']
        digest=delivery.ownership_digest(x.intent,x.farmer,x.merchant,x.sales)
        x.trace.extend(dict(stage='reconciliation_observation',status='observed',
            payload={'ownership_digest':digest,'observed_at':at},timestamp=at) for at in (862.,867.))
        x.result=delivery.reconciliation_outcome(x.intent,x.farmer,x.merchant,trace=x.trace,
                                               sale_receipts=x.sales,now=867.)
        with sqlite3.connect(x.source) as db:
            db.execute("UPDATE transactions SET phase='aborted',updated=868,before_json=?,result_json=?",
                       (json.dumps(x.intent),json.dumps(x.result)))
            db.execute('UPDATE delivery_admissions SET items_json=?',(json.dumps(x.intent['items']),))
            db.execute('DELETE FROM transaction_steps')
            db.executemany('INSERT INTO transaction_steps VALUES(?,?,?,?,?,?)',
                [(i,'delivery',s['stage'],s['status'],json.dumps(s['payload']),s['timestamp']) for i,s in enumerate(x.trace)])
        with sqlite3.connect(x.receiver) as db:
            db.execute('UPDATE delivery_reservations SET state=?',(json.dumps(dict(
                phase='no_transfer_reconciled',intent=x.intent,disposition=x.result)),))
            db.execute('DELETE FROM sales')
            db.executemany('INSERT INTO sales VALUES(?,?,?,?,?,?)',[(r['id'],'Dutch',r['observed_at'],r['phase'],
                json.dumps(r['items']),r['silver']) for r in x.sales])
        x.route=dict(active=None,receipts=[],operations=[dict(request_id='delivery',outcome='no_transfer',
            started_at=845.,items=[],remaining=x.intent['items'],proof_digest=x.result['proof_digest'],
            verified_at=870.,**x.origin)])
        write_json(delivery_route.STATE,x.route)
    x.persist=persist;persist()
    x.run=lambda:recovery.proof(x.row,x.meteor,x.market,x.failure,x.target)
    return x


def test_exact_cancelled_no_transfer_preserves_every_original_asset(cancelled):
    x=cancelled;expected,proof=x.run()
    assert expected==x.meteor['after']
    assert proof[0]['outcome']=='no_transfer' and proof[0]['uids']==[3]
    assert proof[0]['cleanup_verified_at']==861.


@pytest.mark.parametrize('change',['uncertain','no_cleanup','duplicate_cleanup','offered','confirmed',
    'receiver','active','route_digest','lost_asset','unexplained_silver','short_settlement'])
def test_incomplete_changed_or_uncertain_delivery_cannot_release_town(cancelled,change):
    x=cancelled
    with sqlite3.connect(x.source) as db:
        if change=='uncertain':db.execute("UPDATE transactions SET phase='uncertain'")
        if change=='no_cleanup':db.execute("DELETE FROM transaction_steps WHERE stage='cleanup_trade'")
        if change=='duplicate_cleanup':db.execute("INSERT INTO transaction_steps SELECT id+100,transaction_id,stage,status,payload,timestamp FROM transaction_steps WHERE stage='cleanup_trade'")
        if change in ('offered','confirmed'):
            db.execute('INSERT INTO transaction_steps VALUES(?,?,?,?,?,?)',(99,'delivery',
                'offer_item:3' if change=='offered' else 'farmer_confirm','before_action','{}',851.))
        if change=='short_settlement':
            db.execute("DELETE FROM transaction_steps WHERE stage='reconciliation_observation' AND timestamp=862")
        if change in ('lost_asset','unexplained_silver'):
            result=deepcopy(x.result)
            if change=='lost_asset':result['farmer']['inventory'].pop(0)
            else:result['merchant']['silver']+=1
            db.execute('UPDATE transactions SET result_json=?',(json.dumps(result),))
    if change=='receiver':
        with sqlite3.connect(x.receiver) as db:db.execute('UPDATE delivery_reservations SET state=?',('{}',))
    if change=='active':x.route['active']={'request_id':'delivery'}
    if change=='route_digest':x.route['operations'][0]['proof_digest']='forged'
    write_json(delivery_route.STATE,x.route)
    with pytest.raises(ValueError):x.run()


def test_real_durable_sale_receipt_can_explain_only_receiver_booth_and_silver(cancelled):
    from conquest.merchants.sales import net_bounds
    x=cancelled
    listed=dict(uid=99,type_id=130923,plus=1,gem1=0,gem2=0,bound=False,quantity=1,price=100000)
    silver=net_bounds([listed])[0]
    x.intent['merchant']['booth']=[listed]
    x.merchant['silver']+=silver
    x.sales=[dict(id=1,phase='verified',observed_at=858.,items=[listed],silver=silver)]
    x.persist()
    expected,proof=x.run()
    assert expected==x.meteor['after'] and proof[0]['sale_receipt_ids']==[1]
    with sqlite3.connect(x.receiver) as db:db.execute('DELETE FROM sales')
    with pytest.raises(ValueError):x.run()


def test_active_route_can_only_be_prevalidated_for_read_only_settlement(cancelled):
    x=cancelled
    x.route['operations']=[]
    x.route['active']=dict(request_id='delivery',items=x.intent['items'],
        merchant_identity=x.intent['merchant']['identity'],merchant_uid=x.intent['merchant']['character_uid'],**x.origin)
    write_json(delivery_route.STATE,x.route)
    expected,proof=recovery.proof(x.row,x.meteor,x.market,x.failure,x.target,allow_terminal_active=True)
    assert expected==x.meteor['after'] and proof==[]
    with pytest.raises(ValueError):x.run()


def test_rich_cleanup_payload_is_verified_without_treating_close_as_transfer(cancelled):
    x=cancelled
    payload=dict(farmer_trade=False,merchant_trade=False,historical_delivery_outcome='unknown',
                 farmer=deepcopy(x.farmer),merchant=deepcopy(x.merchant),verified_new_sales=[])
    for role in ('farmer','merchant'):payload[role]['timestamp']=861.
    with sqlite3.connect(x.source) as db:
        db.execute("UPDATE transaction_steps SET payload=? WHERE stage='cleanup_trade' AND status='observed'",
                   (json.dumps(payload),))
    assert x.run()[1][0]['outcome']=='no_transfer'
    payload['farmer']['inventory']=[]
    with sqlite3.connect(x.source) as db:
        db.execute("UPDATE transaction_steps SET payload=? WHERE stage='cleanup_trade' AND status='observed'",
                   (json.dumps(payload),))
    with pytest.raises(ValueError,match='cleanup'):x.run()


def test_captured_no_transfer_selects_only_unchanged_warehouse_fallback(cancelled,monkeypatch,tmp_path):
    from conquest import restock_town_recovery
    from conquest.merchants import service_visit
    x=cancelled
    expected,records=x.run()
    x.meteor['phase']='storing_scroll'
    x.market.update(phase='active',town_visit_id='town',farmer_profile_id='farmer',deadline=900.)
    x.row.update(phase='town_work',pre_admission_restock_tail=dict(capture_kind='no_transfer',
        phase='captured',meteor=deepcopy(x.meteor),market=deepcopy(x.market),failure=x.failure,
        target=x.target,bag=expected,delivery_proofs=records))
    path=tmp_path/'market.json';write_json(path,x.market)
    monkeypatch.setattr(service_visit,'MarketVisit',lambda:NS(path=path))
    monkeypatch.setattr(restock_town_recovery,'_native_tail_safe',lambda *_:None)
    loop=NS(town_visit=NS(state=lambda:deepcopy(x.row)),town=lambda action:deepcopy(expected))
    assert recovery.warehouse_fallback_only(loop,x.meteor) is True
    expected['silver']+=1
    with pytest.raises(ValueError,match='ownership'):recovery.warehouse_fallback_only(loop,x.meteor)


@pytest.mark.parametrize('command',['delivery-status','delivery-cleanup','delivery-start'])
def test_pre_capture_settlement_cannot_issue_gameplay_commands(cancelled,monkeypatch,command):
    from conquest.merchants import bridge
    x=cancelled;calls=[]
    x.route['operations']=[]
    x.route['active']=dict(request_id='delivery',items=x.intent['items'],
        merchant_identity=x.intent['merchant']['identity'],merchant_uid=x.intent['merchant']['character_uid'],**x.origin)
    write_json(delivery_route.STATE,x.route)
    loop=NS(identity=x.target,check_stop=lambda:None,
        health=lambda:dict(target=x.target,embedded_controls=dict(control=dict(enabled=False))),
        town=lambda action:deepcopy(x.meteor['after']))
    monkeypatch.setattr(bridge,'request',lambda body:calls.append(body))
    def settle(loop,send,state,*,start):
        assert start is False
        send({'action':command,'request_id':'delivery'})
    monkeypatch.setattr(delivery_route,'settle',settle)
    if command=='delivery-status':
        recovery.settle_before_capture(loop,x.row,x.meteor,x.market,x.failure,x.target)
        assert len(calls)==1
    else:
        with pytest.raises(ValueError,match='gameplay'):
            recovery.settle_before_capture(loop,x.row,x.meteor,x.market,x.failure,x.target)
        assert not calls
