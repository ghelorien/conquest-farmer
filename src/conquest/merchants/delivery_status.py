"""Project the durable source outcome into UI and notification status."""


def describe(receipt, *, running):
    if not receipt:
        return {'state':'verifying' if running else 'stopped requiring attention',
                'reason':'Waiting for the durable delivery admission receipt','requires_attention':not running}
    outcome=receipt['outcome'];action=receipt['next_action']
    if action=='release_route' and outcome=='transferred':
        state='returning';reason='Both inventories reconciled; merchant delivery complete'
    elif outcome in ('retryable_before_input','no_transfer','operator_overridden') and action in ('release_route','retry_delivery'):
        state='safely deferred';reason=('Operator closed this incident without asserting a transfer outcome; '
                                       'fresh stock is eligible after recheck' if outcome=='operator_overridden'
                                       else 'No items transferred; another merchant or warehouse may be used')
    elif running:
        state='verifying' if action in ('reconcile_bilateral_ownership','finalize_receiver_receipt','cleanup_trade_modal') else 'trading'
        reason='Checking the exact delivery and both participants'
    else:
        state='stopped requiring attention';reason=receipt.get('reason') or 'Delivery ownership or panel cleanup needs reconciliation'
    return {'state':state,'reason':reason,'requires_attention':state=='stopped requiring attention',
            'request_id':receipt['request_id'],'visit_id':receipt.get('visit_id'),
            'town_visit_id':receipt.get('town_visit_id'),'outcome':outcome,'next_action':action}


def enrich(ui, states):
    import time
    from conquest.discord_notify import read_json
    from conquest.merchants.delivery_route import STATE
    from conquest.merchants import delivery_operation as operation
    active=read_json(STATE).get('active')
    if not active or active.get('merchant') not in states:return states
    key=active['request_id'];worker=getattr(ui,'delivery_workers',{}).get(key)
    receipt=operation.status(operation.Journal(operation.JOURNAL),key)
    awaiting_admission=receipt is None and 0<=time.time()-active.get('started_at',0)<30
    delivery=describe(receipt,running=bool(worker and worker.is_alive()) or awaiting_admission)
    state=states[active['merchant']]
    state['delivery']=delivery;state['activity']=delivery['state'].capitalize()+': '+delivery['reason']
    if delivery['requires_attention']:
        state['needs_attention']={'kind':'farmer_delivery','request_id':key,'note':delivery['reason']}
        state['ready']=False
    return states
