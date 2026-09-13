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
