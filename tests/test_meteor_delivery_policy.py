from types import SimpleNamespace as NS
from conquest import banking
from conquest.merchants import delivery_journey


def test_loose_meteors_are_deposited_before_merchant_departure(monkeypatch):
    meteor={'uid':1,'type_id':1088001,'slot':0,'plus':0}
    scroll={'uid':2,'type_id':720027,'slot':1,'plus':0}
    bag=[meteor,scroll];events=[]
    monkeypatch.setattr(banking,'read_json',lambda p:{})
    def town(action,**fields):
        if action=='supplies':return {'items':list(bag)}
        if action=='warehouse-deposit':
            uid=fields['uid'];events.append(('deposit',uid));bag[:]=[i for i in bag if i['uid']!=uid]
            return {'verified_in_warehouse':True,'uid':uid}
        raise AssertionError(action)
    def start(loop):
        assert bag==[scroll];events.append(('merchant',2));bag.clear()
    monkeypatch.setattr(delivery_journey,'start',start)
    banking.stash_valuables(NS(town=town,record=lambda *a,**kw:None),deliver=True)
    assert events==[('deposit',1),('merchant',2)]


def test_production_caller_routes_completed_banked_scroll_with_no_carried_eligible_loot(monkeypatch):
    from conquest import meteor_banking
    from conquest.discord_notify import write_json
    write_json(meteor_banking.JOURNAL,{'phase':'completed','scroll_uid':99,'exchange_verified':True,
        'market_verified_at':123,'receipts':[{'stored':99,'type_id':720027,'verified_in_warehouse':True}]})
    monkeypatch.setattr(banking,'read_json',lambda p:{})
    calls=[]
    monkeypatch.setattr(delivery_journey,'start',lambda loop,**kw:calls.append(kw))
    loop=NS(town=lambda action,**fields:{'items':[]},record=lambda *a,**kw:None)
    banking.stash_valuables(loop,deliver=True)
    assert calls==[{'stored_scroll_uid':99}]


def test_unverified_or_user_resolved_consolidation_does_not_authorize_stored_scroll(monkeypatch):
    from conquest import meteor_banking
    from conquest.discord_notify import write_json
    base={'phase':'completed','scroll_uid':99,'exchange_verified':True,'market_verified_at':123,
          'receipts':[{'stored':99,'type_id':720027,'verified_in_warehouse':True}]}
    for change in ({'phase':'storing_scroll'},{'exchange_verified':False},{'receipts':[]},
                   {'user_confirmed_scroll_transfer':{'uid':99,'type_id':720027,'confirmed':True,
                     'source':'explicit user confirmation','destination':'another_character'}},
                   {'user_confirmed_scroll_consumption':{'uid':99,'confirmed':True,'source':'explicit user confirmation'}}):
        write_json(meteor_banking.JOURNAL,{**base,**change})
        assert meteor_banking.completed_stored_scroll() is None
