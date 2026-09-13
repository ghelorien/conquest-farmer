"""Read-only ownership reconciliation for an interrupted, partially delivered batch."""
import time
from conquest.merchants.delivery import exact_items,validate_snapshot


def partial_result(intent,farmer,merchant,*,now=None):
    now=time.time() if now is None else now
    wanted=exact_items(intent['items']);before_f=exact_items(intent['farmer']['inventory'])
    before_m=exact_items(intent['merchant']['inventory'])
    current={}
    cleanup=[]
    for role,snapshot,other in (('farmer',farmer,merchant),('merchant',merchant,farmer)):
        before=intent[role]
        current[role]=validate_snapshot(snapshot,before['character'],now)
        if any(snapshot[k]!=before[k] for k in ('identity','character_uid','silver')):
            raise ValueError('Partial delivery identity or currency changed')
        if exact_items(snapshot.get('booth',[]))!=exact_items(before.get('booth',[])):
            raise ValueError('Partial delivery booth stock changed')
        if snapshot.get('request'):raise ValueError('An incoming request remains unresolved')
        trade=snapshot.get('trade')
        if trade:
            if (trade.get('participant')!=other['character'] or trade.get('participant_uid')!=other['character_uid'] or
                trade.get('own_items') or trade.get('items') or trade.get('own_silver')!=0 or
                trade.get('other_silver')!=0 or trade.get('accepted') is not False or trade.get('other_accepted') is not False):
                raise ValueError('Offered items or acceptance must reconcile before a partial abort')
            cleanup.append(snapshot['character'])
    f,m=current['farmer'],current['merchant']
    moved={uid:details for uid,details in wanted.items() if uid not in f and m.get(uid)==details}
    remaining={uid:details for uid,details in wanted.items() if f.get(uid)==details and uid not in m}
    if (set(moved)|set(remaining)!=set(wanted) or not moved or not remaining or
        f!={uid:details for uid,details in before_f.items() if uid not in moved} or
        m!={**before_m,**moved}):
        raise ValueError('The exact partial batch and all surrounding stock have not reconciled')
    return {'outcome':'partial_delivery_aborted','delivered':[i for i in intent['items'] if i['uid'] in moved],
        'remaining':[i for i in intent['items'] if i['uid'] in remaining],
        'cleanup_pending':cleanup,'farmer':farmer,'merchant':merchant,'reconciled_at':now}
