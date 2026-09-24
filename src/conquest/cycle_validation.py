"""Evaluate uninterrupted cycles from durable native receipts, never UI counters."""

MINIMUM_KILLS_PER_MINUTE=60
STRETCH_KILLS_PER_MINUTE=75


def exact_batch(items):
    fields=('uid','type_id','plus','gem1','gem2','quantity','bound')
    if not isinstance(items,list) or not items:return None
    if any(not isinstance(item,dict) or any(key not in item for key in fields) for item in items):return None
    if len({item['uid'] for item in items})!=len(items):return None
    return sorted(tuple(item[key] for key in fields) for item in items)


def evaluate(config, *, now, kill_events, visits, deliveries, refill_events, interruptions=()):
    start=config['started_at'];duration=config.get('duration_seconds',7200);end=start+duration
    elapsed=max(0,min(now,end)-start)
    kills=sum(count for at,count in kill_events if start<=at<=min(now,end))
    rate=kills*60/elapsed if elapsed else 0
    covered=[];incomplete=[];excluded=[]
    for visit in visits:
        vid=visit.get('town_visit_id')
        if ('merchant_acceptance' in visit.get('reasons',[]) or visit.get('validation_cycle')
                or visit.get('acceptance_scope')):
            excluded.append(vid)
            continue
        if (visit.get('phase')!='complete' or not vid or visit.get('required_at',0)<start
                or visit.get('completed_at',end+1)>min(now,end)
                or not visit.get('first_verified_resume_kill')):continue
        resumed=visit['first_verified_resume_kill'];returned=visit.get('return_started_at')
        if (not returned or not resumed.get('time') or resumed.get('count',0)<=0
                or resumed.get('rowid',0)<=0 or not visit.get('farmer_profile_id')
                or not visit['required_at']<=returned<=resumed['time']<=visit['completed_at']):continue
        transfers=[d for d in deliveries if d.get('town_visit_id')==vid and d.get('phase')=='verified'
                   and d.get('outcome')=='transferred' and d.get('next_action')=='release_route']
        matches=[];unmatched=[]
        for delivery in transfers:
            verified=delivery.get('verified_at');batch=exact_batch(delivery.get('items'))
            eligible=(delivery.get('farmer_profile_id')==visit['farmer_profile_id']
                and delivery.get('operation_id')==delivery.get('request_id')
                and delivery.get('visit_id') and delivery.get('character')
                and verified is not None and delivery.get('started_at') is not None
                and visit['required_at']<=delivery['started_at']<=verified<=returned
                and delivery.get('proof_digest') and not delivery.get('cleanup_pending')
                and batch is not None and batch==exact_batch(delivery.get('delivered')))
            refills=[r for r in refill_events if eligible and r.get('town_visit_id')==vid
                and r.get('visit_id')==delivery['visit_id'] and r.get('character')==delivery['character']
                and r.get('status') in ('completed','no_stock','booth_full')
                and r.get('observed_at') is not None and verified<=r['observed_at']<=returned]
            if refills:
                matches.append({'request_id':delivery['request_id'],
                    'merchant':delivery.get('merchant_identity',{'record_id':delivery['character']}),
                    'farmer':delivery.get('farmer_identity',{'profile_id':delivery['farmer_profile_id']}),
                    'refill':min(refills,key=lambda row:row['observed_at'])})
            else:unmatched.append(delivery.get('request_id'))
        if unmatched:incomplete.append({'town_visit_id':vid,'unmatched_transfers':unmatched})
        if matches and not unmatched:
            covered.append({'town_visit_id':vid,'elapsed_seconds':visit.get('elapsed_seconds'),
                            'farmer_profile_id':visit['farmer_profile_id'],
                            'transfers':[d['request_id'] for d in transfers],
                            'transfer_refill_matches':matches,
                            'refill_results':[m['refill'] for m in matches],'resumed_kill':resumed})
    unresolved=[d.get('request_id') for d in deliveries if d.get('started_at',0)>=start and
                (d.get('phase') not in ('verified','aborted','operator_overridden') or d.get('cleanup_pending')
                 or d.get('phase')=='verified' and d.get('next_action')!='release_route')]
    farmers=sorted({cycle['farmer_profile_id'] for cycle in covered})
    merchants=sorted({match['refill']['character'] for cycle in covered for match in cycle['transfer_refill_matches']})
    missing_farmers=sorted(set(config.get('required_farmer_profile_ids',[]))-set(farmers))
    missing_merchants=sorted(set(config.get('required_merchant_record_ids',[]))-set(merchants))
    failures=list(dict.fromkeys(interruptions))
    if duration<7200:failures.append('Configured observation is shorter than two hours')
    if now<end:failures.append('Two-hour observation is still running')
    if not covered:failures.append('No natural complete trade/refill/return/verified-combat cycle')
    if incomplete:failures.append('A completed town visit lacks matching subsequent recipient refill evidence')
    if missing_farmers or missing_merchants:failures.append('Required farmer or merchant coverage is incomplete')
    if unresolved:failures.append('New transactions remain unresolved')
    if rate<MINIMUM_KILLS_PER_MINUTE:
        failures.append(f'Overall verified kill rate is below {MINIMUM_KILLS_PER_MINUTE} per minute')
    return {'qualified':not failures,'limitations':failures,'elapsed_seconds':elapsed,
            'total_kills':kills,'overall_kills_per_minute':round(rate,2),
            'covered_cycles':covered,'unresolved_operations':unresolved,
            'incomplete_cycles':incomplete,'covered_farmer_profile_ids':farmers,
            'excluded_forced_visit_ids':excluded,
            'covered_merchant_record_ids':merchants,
            'missing_farmer_profile_ids':missing_farmers,'missing_merchant_record_ids':missing_merchants,
            'coverage_scope':'Only the identities in covered_cycles were validated live',
            'minimum_kills_per_minute':MINIMUM_KILLS_PER_MINUTE,
            'stretch_kills_per_minute':STRETCH_KILLS_PER_MINUTE}
