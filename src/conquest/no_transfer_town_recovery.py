"""Read-only proof of a cancelled, wholly unoffered native scroll delivery."""
from contextlib import closing, contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from conquest.character_context import state_path
from conquest.discord_notify import read_json

FAILURE = 'Merchant delivery needs reconciliation; valuables remain protected'


def proof(row, meteor, market, failure, target, *, allow_terminal_active=False):
    from conquest.merchants import delivery_operation, delivery_route
    from conquest.merchants.delivery import reconciliation_outcome, exact_items
    from conquest.merchants.journal import profile_row
    from conquest.merchants.sales import qualified_delivery_receipts
    from conquest.settled_delivery_town_recovery import _bag_matches_snapshot, _same_intent

    route = read_json(delivery_route.STATE)
    active=route.get('active')
    if active and not allow_terminal_active or route.get('cleanup_pending'):
        raise ValueError('Original delivery must settle before no-transfer town capture')
    origin = {k: v for k, v in (
        ('town_visit_id', row['town_visit_id']), ('visit_id', market['visit_id']),
        ('farmer_profile_id', row['farmer_profile_id']))}
    peer_path = Path(state_path('reports/merchants/journal.sqlite3'))

    class ReadOnlyJournal:
        @contextmanager
        def db(self):
            with closing(sqlite3.connect(peer_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
                db.row_factory = profile_row
                yield db

    with closing(sqlite3.connect(delivery_operation.JOURNAL.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = profile_row
        transactions = list(db.execute('SELECT * FROM transactions WHERE created>=?', (row['required_at'],)))
        admissions = list(db.execute('SELECT * FROM delivery_admissions WHERE created>=?', (row['required_at'],)))
        if len(transactions) != 1 or len(admissions) != 1:
            raise ValueError('No-transfer recovery requires one exact native delivery admission')
        tx, admission = transactions[0], admissions[0]
        key = tx['id']; before = json.loads(tx['before_json'])
        result = json.loads(tx['result_json'] or '{}')
        admitted = json.loads(admission['origin_json'])
        if (tx['kind'] != 'farmer_delivery' or tx['phase'] != 'aborted'
                or not meteor['started_at'] <= tx['created'] <= failure['time'] <= tx['updated']
                or admission['request_id'] != key or admission['phase'] != 'transaction_started'
                or admission['character'] != tx['character']
                or before.get('operation_id') != key or admitted.get('operation_id') != key
                or any(before.get(k) != v or admitted.get(k) != v for k, v in origin.items())
                or before['farmer']['identity'] != target
                or result.get('outcome') != 'no_transfer' or result.get('delivered')
                or result.get('cleanup_pending') or result.get('operator_additions')
                or not _bag_matches_snapshot(meteor['after'], before['farmer'])
                or not _bag_matches_snapshot(meteor['after'], result['farmer'])):
            raise ValueError('No-transfer recovery lacks unchanged original native ownership')
        selected = exact_items(before['items'])
        if (list(selected) != [meteor['scroll_uid']]
                or before['items'][0]['type_id'] != 720027
                or sorted(selected) != sorted(json.loads(admission['uids_json']))
                or selected != exact_items(json.loads(admission['items_json']))
                or selected != exact_items(result.get('remaining', []))):
            raise ValueError('No-transfer admission is not the exact original MeteorScroll')
        trace = [{**dict(step), 'payload': json.loads(step['payload'])} for step in db.execute(
            'SELECT stage,status,payload,timestamp FROM transaction_steps WHERE transaction_id=? ORDER BY id', (key,))]
        actions = [s for s in trace if s['status'] == 'before_action']
        closed = [s for s in trace if s['stage'] == 'cleanup_trade' and s['status'] == 'observed']
        presses = [s for s in actions if s['stage'] == 'cleanup_trade']
        if (any(s['stage'] not in ('trade_target_mode', 'trade_request', 'cleanup_trade') for s in actions)
                or any(s['stage'].startswith('offer_item') or 'confirm' in s['stage'] for s in trace)
                or len(presses) != 1 or len(closed) != 1
                or any(closed[0]['payload'].get(k) is not False for k in ('farmer_trade','merchant_trade'))
                or not failure['time'] <= presses[0]['timestamp'] <= closed[0]['timestamp'] <= tx['updated']
                or not any(s['stage'] == 'trade_request' and s['status'] == 'observed'
                           and s['payload'].get('trade_open') is True for s in trace)):
            raise ValueError('No-transfer recovery lacks an exact once-only empty cleanup')
        for role in ('farmer','merchant'):
            if role in closed[0]['payload']:
                snapshot=closed[0]['payload'][role]
                from conquest.merchants.delivery import validate_snapshot
                validate_snapshot(snapshot,before[role]['character'],closed[0]['timestamp'])
                if (snapshot.get('identity')!=before[role]['identity']
                        or snapshot.get('trade') is not None or snapshot.get('request') is not None
                        or exact_items(snapshot['inventory'])!=exact_items(before[role]['inventory'])):
                    raise ValueError('Rich cleanup observation changed exact unoffered ownership')
        for role in ('farmer', 'merchant'):
            if result[role].get('trade') is not None or result[role].get('request') is not None:
                raise ValueError('No-transfer participants still have open trade windows')
        sales = qualified_delivery_receipts(ReadOnlyJournal(), before, result['merchant'])
        proved = reconciliation_outcome(before, result['farmer'], result['merchant'],
            trace=trace, sale_receipts=sales, now=result['reconciled_at'])
        if (proved['outcome'] != 'no_transfer' or proved['cleanup_pending']
                or proved['proof_digest'] != result.get('proof_digest')
                or proved['sale_receipts'] != result.get('sale_receipts', [])):
            raise ValueError('No-transfer bilateral ownership proof differs from its receipt')
        with ReadOnlyJournal().db() as peer_db:
            peers = list(peer_db.execute('SELECT state FROM delivery_reservations WHERE request_id=?', (key,)))
        peer = json.loads(peers[0]['state']) if len(peers) == 1 else {}
        disposition = peer.get('disposition') or {}
        if (peer.get('phase') != 'no_transfer_reconciled'
                or not _same_intent(peer.get('intent') or {}, before)
                or disposition.get('outcome') != 'no_transfer'
                or disposition.get('proof_digest') != proved['proof_digest']):
            raise ValueError('Receiver no-transfer disposition is missing or changed')
        operations = [r for r in route.get('operations', []) if r.get('started_at', 0) >= row['required_at']]
        if active:
            if (operations or active.get('request_id')!=key
                    or exact_items(active.get('items',[]))!=selected
                    or any(active.get(k)!=v for k,v in origin.items())
                    or active.get('merchant_identity')!=before['merchant']['identity']
                    or active.get('merchant_uid')!=before['merchant']['character_uid']):
                raise ValueError('Active route differs from terminal no-transfer operation')
            # This internal result authorizes only read-only consumption below.
            return deepcopy(meteor['after']), []
        if len(operations) != 1:
            raise ValueError('Route no-transfer operation history is not exact')
        record = operations[0]
        if (record.get('request_id') != key or record.get('outcome') != 'no_transfer'
                or record.get('items') or record.get('proof_digest') != proved['proof_digest']
                or exact_items(record.get('remaining', [])) != selected
                or any(record.get(k) != v for k, v in origin.items())
                or record.get('verified_at', 0) < tx['updated']
                or any(r.get('request_id') == key for r in route.get('receipts', []))):
            raise ValueError('Original route has not consumed its linked no-transfer disposition')
        attempts = market.get('attempts') or []
        if attempts and (len(attempts) != 1 or attempts[0].get('outcome') != 'no_transfer'
                         or attempts[0].get('at', 0) < tx['updated']):
            raise ValueError('Other Market attempts followed the cancelled delivery')
        return deepcopy(meteor['after']), [{'request_id': key, 'outcome': 'no_transfer',
            'proof_digest': proved['proof_digest'], 'uids': sorted(selected),
            'cleanup_verified_at': closed[0]['timestamp'], 'verified_at': record['verified_at'],
            'sale_receipt_ids': [r['id'] for r in sales]}]


def settle_before_capture(loop,row,meteor,market,failure,target):
    """Consume only an already proved no-transfer route; never start/cleanup."""
    from conquest.merchants import delivery_route
    from conquest.merchants.bridge import request
    from conquest.restock_town_recovery import _ownership
    state=read_json(delivery_route.STATE)
    if not state.get('active'):return
    expected,_=proof(row,meteor,market,failure,target,allow_terminal_active=True)
    loop.check_stop();health=loop.health()
    data=health.get('embedded_controls') or {};control=data.get('control') or {}
    if (health.get('target')!=target or loop.identity!=target
            or control.get('enabled') is not False or control.get('paused')
            or data.get('manual_mouse') or data.get('manual_input_fence')
            or _ownership(loop.town('supplies'))!=_ownership(expected)):
        raise ValueError('Terminal route consumption lacks unchanged stopped Farmer ownership')
    if read_json(delivery_route.STATE)!=state:
        raise ValueError('Terminal route changed before read-only consumption')
    def read_only(body):
        loop.check_stop()
        if body.get('action') not in ('delivery-status','delivery-reconcile'):
            raise ValueError('No-transfer route consumption cannot issue gameplay commands')
        return request(body)
    delivery_route.settle(loop,read_only,state,start=False)


def warehouse_fallback_only(loop,meteor):
    """A captured no-transfer trip cannot plan a replacement trade attempt."""
    from conquest.merchants.service_visit import MarketVisit
    from conquest.restock_town_recovery import _native_tail_safe, _ownership
    visit=getattr(loop,'town_visit',None)
    if visit is None:return False
    row=visit.state();claim=row.get('pre_admission_restock_tail') or {}
    if claim.get('capture_kind')!='no_transfer':return False
    market=read_json(MarketVisit().path)
    if (claim.get('phase')!='captured' or row.get('phase')!='town_work'
            or row.get('town_work_completed_at') or meteor.get('phase')!='storing_scroll'
            or any(meteor.get(k)!=claim['meteor'].get(k) for k in
                   ('started_at','scroll_uid','meteor_uids','origin','after'))
            or any(market.get(k)!=claim['market'].get(k) for k in
                   ('visit_id','town_visit_id','farmer_profile_id','started_at','deadline'))
            or market.get('phase')!='active'):
        raise ValueError('Captured no-transfer warehouse fallback changed')
    expected,records=proof(row,meteor,market,claim['failure'],claim['target'])
    _native_tail_safe(loop,claim['target'],1036)
    if (records!=claim.get('delivery_proofs') or _ownership(expected)!=_ownership(claim['bag'])
            or _ownership(loop.town('supplies'))!=_ownership(expected)):
        raise ValueError('No-transfer ownership changed before warehouse fallback')
    return True
