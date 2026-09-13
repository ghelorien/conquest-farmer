import copy
import time
import pytest
from conquest.merchants.delivery_probe import unchanged
from conquest.merchants.delivery import prepare


@pytest.mark.parametrize('change',[None,'position','currency','identity','inventory','request'])
def test_request_probe_rechecks_both_participants_before_input(change):
    item=dict(uid=10,type_id=1088001,plus=0,gem1=0,gem2=0,quantity=1,bound=False,slot=0)
    def snapshot(name,uid,items):
        return dict(character=name,character_uid=uid,identity={'pid':uid},server='America',
            timestamp=time.time(),map_id=1036,hp=100,silver=200,capacity=40,
            position=[10,10],inventory=items,booth=[],trade=None,request=None)
    f=snapshot('Parasite',1,[item]);m=snapshot('Spiritual',2,[])
    intent=copy.deepcopy(prepare(f,m,[item]))
    if change=='position':m['position']=[11,10]
    if change=='currency':f['silver']=199
    if change=='identity':m['identity']={'pid':3}
    if change=='inventory':f['inventory']=[]
    if change=='request':m['request']={'participant':'SomeoneElse'}
    if change:
        with pytest.raises(ValueError):unchanged(intent,f,m)
    else:unchanged(intent,f,m)
