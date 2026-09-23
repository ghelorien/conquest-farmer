"""One-shot recovery of an interrupted urgent bank visit.

The legacy claim is an explicit operator action. Route startup may consume a
claim once, but cannot create one from stocked supplies or a terminal journal.
"""
import json
import math
from pathlib import Path
import time

from conquest.character_context import state_path
from conquest.discord_notify import read_json
from conquest.town_visit import _process_identity


EVENTS=Path(state_path('reports/overnight/events.jsonl'))
MONEY=Path(state_path('reports/banking/transfers.jsonl'))


def _rows(path):
    if not path.is_file():raise ValueError('Required recovery audit is unavailable')
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def transaction_holds():
    from conquest.meteor_banking import pending as meteor_pending
    from conquest.merchants.delivery_journey import pending as journey_pending
    from conquest.merchants.delivery_route import pending as trade_pending
    from conquest.merchants.delivery_operation import pending as delivery_pending
    from conquest.protected_withdrawal import pending as withdrawal_pending
    from conquest.storage_overflow import pending as overflow_pending
    from conquest.storage_halt import active as storage_halt
    return any((meteor_pending(),journey_pending(),trade_pending(),delivery_pending(),
                bool(withdrawal_pending()),overflow_pending(),storage_halt()))


def _terminal_meteor(visit,events):
    from conquest.meteor_banking import JOURNAL,SCROLL
    from conquest.merchants.delivery_route import receipt_for
    meteor=read_json(JOURNAL)
    started=meteor.get('started_at');completed=meteor.get('completed_at')
    verified=meteor.get('market_verified_at')
    if (meteor.get('phase')!='completed' or meteor.get('exchange_verified') is not True
            or type(started) not in (int,float) or started<visit['required_at']
            or type(completed) not in (int,float) or completed<=started
            or type(verified) not in (int,float) or not started<=verified<=completed
            or meteor.get('origin')!=1011
            or meteor.get('user_confirmed_scroll_consumption')
            or meteor.get('user_confirmed_scroll_transfer')):
        raise ValueError('Meteor consolidation lacks exact terminal proof')
    failures=[row for row in events if row.get('event')=='failed'
              and type(row.get('time')) in (int,float) and started<=row['time']<completed
              and row.get('town_visit_id')==visit['town_visit_id']
              and any(Path(frame.get('file','')).name=='meteor_banking.py'
                      for frame in row.get('failure_trace',[]))]
    if len(failures)!=1:
        raise ValueError('Interrupted Meteor call is not proven by one visit failure')
    receipts={row.get('stored'):row for row in meteor.get('receipts',[])
              if row.get('verified_in_warehouse') is True}
    scroll=meteor.get('scroll_uid')
    if (type(scroll) is not int or scroll<=0 or receipts.get(scroll,{}).get('type_id')!=SCROLL
            or receipt_for(scroll,SCROLL)):
        raise ValueError('Exact MeteorScroll storage receipt is missing')
    return meteor,failures[0],receipts


def claim_legacy(visit,*,info_path,visit_id,original_target,reference):
    """Audit the exact interrupted visit and bind a supplied original identity.

    `original_target` must come from independently retained live supervision;
    the old town-visit record did not persist it. This function does not obtain
    or infer that historical identity and never sends game input.
    """
    from conquest.worker import request
    health=request(info_path,'health')
    data=health.get('embedded_controls') or {}
    life=data.get('life') or {}
    row=visit.state()
    if (not isinstance(reference,str) or not reference.strip()
            or row.get('town_visit_id')!=visit_id or row.get('reasons')!=['urgent_banking']
            or row.get('phase')!='town_work' or not _process_identity(original_target)
            or original_target!=health.get('target')
            or not 0<=time.time()-data.get('observed_at',0)<=1
            or life.get('map_id')!=1011 or life.get('dead_candidate') is not False
            or life.get('current_hp',0)<=0 or transaction_holds()):
        raise ValueError('Legacy urgent recovery is not explicitly qualified')
    events=_rows(EVENTS)
    starts=[event for event in events if event.get('event')=='urgent_banking_started'
            and event.get('town_visit_id')==visit_id
            and type(event.get('time')) in (int,float)
            and event['time']>=row['required_at']]
    if len(starts)!=1 or not isinstance(starts[0].get('uids'),list):
        raise ValueError('Exact urgent bank start event is absent or ambiguous')
    uids=starts[0]['uids']
    if (not uids or len(set(uids))!=len(uids)
            or any(type(uid) is not int or uid<=0 for uid in uids)):
        raise ValueError('Urgent start item IDs are invalid')
    meteor,failure,receipts=_terminal_meteor(row,events)
    if not starts[0]['time']<=meteor['started_at']<=failure['time']:
        raise ValueError('Meteor interruption predates the urgent visit')
    if any(receipts.get(uid,{}).get('type_id') is None for uid in uids):
        raise ValueError('Urgent items lack exact terminal storage receipts')
    if any(event.get('event')=='urgent_banking_complete' and
           event.get('town_visit_id')==visit_id for event in events):
        raise ValueError('Urgent bank already reported completion without its marker')
    # The original after_shopping() was blocked inside Meteor consolidation.
    # A money receipt after the visit would contradict that source-order proof.
    if any(type(receipt.get('time')) not in (int,float)
           or receipt['time']>=row['required_at'] for receipt in _rows(MONEY)):
        raise ValueError('Post-visit or unreadable money transfer needs reconciliation')
    intent=[{'uid':uid,'type_id':receipts[uid]['type_id'],'storage_map_id':1036}
            for uid in uids]
    return visit.claim_urgent_recovery(visit_id=visit_id,target=original_target,
        intent=intent,evidence={'reference':reference,'urgent_start_at':starts[0]['time'],
            'meteor_started_at':meteor['started_at'],'meteor_completed_at':meteor['completed_at'],
            'interrupted_at':failure['time'],'scroll_uid':meteor['scroll_uid']})


def resume_claimed(loop):
    """Consume a reviewed claim once; uncertain tail actions can never replay."""
    from conquest.banking import after_shopping,close_warehouse,open_warehouse,urgent_valuables
    from conquest.merchants.farmer_preferences import enabled as delivery_enabled
    from conquest.merchants.handoff import service_window
    from conquest.merchants.delivery_route import receipt_for
    from conquest.meteor_banking import JOURNAL,METEOR
    from conquest.overnight import needs_town,supply_counts
    from conquest.town_trade import stash_candidate

    visit=loop.town_visit
    row=visit.state();claim=row.get('urgent_recovery_claim')
    if not claim:return False
    if row.get('phase')!='town_work' or row.get('reasons')!=['urgent_banking']:
        return False
    completed=row.get('town_work_completed_at')
    if completed is not None:
        if (type(completed) not in (int,float) or not math.isfinite(completed)
                or completed<=0 or row.get('town_work_completed_kind')!='urgent_banking'
                or not row.get('urgent_recovery_attempted_at')
                or not row.get('urgent_banking_tail_completed_at')
                or not row.get('urgent_followup_completed_at')
                or not _process_identity(row.get('urgent_target'))
                or row['urgent_target']!=loop.identity):
            raise ValueError('Completed urgent recovery marker is inconsistent')
        return False  # TownVisit.returning() still establishes the hunt baseline.
    if row.get('urgent_recovery_attempted_at'):
        raise ValueError('Urgent recovery tail was already attempted; reconcile before any retry')
    health=loop.health();target=health.get('target')
    life=(health.get('embedded_controls') or {}).get('life') or {}
    if (target!=row.get('urgent_target') or target!=loop.identity
            or not _process_identity(target) or life.get('map_id')!=1011
            or life.get('dead_candidate') is not False or life.get('current_hp',0)<=0
            or transaction_holds() or delivery_enabled()):
        raise ValueError('Urgent recovery process, town, safety, or transaction hold changed')
    # A restarted route begins with its saved arrow tier. The ordinary town
    # path selects the currently equipped/owned usable tier before checking
    # supplies; recovery must do the same or SpeedArrows look like zero IronArrows.
    # This is memory-only and occurs before opening the warehouse or any input.
    loop.adopt_ammunition()
    bag=loop.town('supplies')
    if (urgent_valuables(bag['items']) or any(stash_candidate(item) for item in bag['items'])
            or needs_town(supply_counts(bag,loop.route),loop.route)
            or any(item['uid'] in {wanted['uid'] for wanted in row['urgent_intent']}
                   for item in bag['items'])):
        raise ValueError('Urgent recovery bag or supplies changed')
    meteor=read_json(JOURNAL)
    if (meteor.get('phase')!='completed' or meteor.get('completed_at')!=claim['meteor_completed_at']
            or meteor.get('scroll_uid')!=claim['scroll_uid']
            or not meteor.get('market_verified_at')
            or meteor.get('user_confirmed_scroll_consumption')
            or meteor.get('user_confirmed_scroll_transfer')
            or receipt_for(claim['scroll_uid'],720027)
            or any(item['uid']==claim['scroll_uid'] for item in bag['items'])):
        raise ValueError('Claimed Meteor terminal journal changed')
    # An explicit restart can have the combat controller enabled. Town travel
    # and warehouse input must own the lane exclusively, just as the normal
    # urgent-bank path does after its return-to-town transition.
    loop.stop_farm()
    # Only the original skipped tail is permitted. A further ten-Meteor batch
    # would be a new fare/exchange and belongs to a separately reviewed visit.
    open_warehouse(loop)
    stored=loop.town('warehouse-items');fresh_pre=loop.town('supplies')
    keys=('items','equipped_ammo','capacity','silver')
    if any(fresh_pre.get(key)!=bag.get(key) for key in keys):
        raise ValueError('Bag changed during recovery warehouse approach')
    phoenix={item['uid']:item['type_id'] for item in stored['items']}
    # The scroll and receipt-backed urgent items were stored in Market. They
    # must remain out of the Phoenix bag; they are not Phoenix bank entries.
    if (any((phoenix.get(wanted['uid'])!=wanted['type_id']
             if wanted.get('storage_map_id',1011)==1011 else wanted['uid'] in phoenix)
            for wanted in row['urgent_intent'])
            or claim['scroll_uid'] in phoenix
            or sum(item['type_id']==METEOR and item['amount']==item['limit']==1
                   for item in fresh_pre['items']+stored['items'])>=10):
        raise ValueError('Fresh warehouse ownership or new Meteor batch differs; no tail submitted')
    # Warehouse opening is non-transactional. Consume the one-shot only after
    # its fresh native ownership read has passed, before any tail transaction.
    visit.start_urgent_recovery_once(target=target)
    if not after_shopping(loop):
        raise ValueError('Urgent banking tail is disabled')
    open_warehouse(loop)
    fresh_bag=loop.town('supplies');fresh_bank=loop.town('warehouse-items')
    if (urgent_valuables(fresh_bag['items'])
            or needs_town(supply_counts(fresh_bag,loop.route),loop.route)
            or transaction_holds()):
        raise ValueError('Urgent banking tail did not settle')
    banked={item['uid']:item['type_id'] for item in fresh_bank['items']}
    if any((banked.get(item['uid'])!=item['type_id']
             if item.get('storage_map_id',1011)==1011 else item['uid'] in banked)
           for item in row['urgent_intent']) or claim['scroll_uid'] in banked:
        raise ValueError('Urgent item ownership changed after banking tail')
    close_warehouse(loop)
    visit.record_urgent_tail('banking',target=target)
    service_window(loop,town=True)
    visit.record_urgent_tail('followup',target=target)
    # Recheck exact stored ownership after the optional handoff, then close
    # the panel before certifying the visit. A failed read leaves the claim held.
    open_warehouse(loop)
    final=loop.town('supplies');final_bank=loop.town('warehouse-items')
    if (urgent_valuables(final['items']) or transaction_holds()
            or receipt_for(claim['scroll_uid'],720027)
            or not visit.reconcile_urgent_town_work(target=target,bag=final,
                warehouse=final_bank,meteor=read_json(JOURNAL),
                transaction_holds=False,
                needs_town=needs_town(supply_counts(final,loop.route),loop.route))):
        raise ValueError('Urgent recovery changed after the merchant handoff')
    close_warehouse(loop)
    return True
