import copy
import time
import pytest
from conquest.merchants.empty_delivery_cancel import unchanged

def pair():
    def snapshot(name,uid):
        return dict(character=name,character_uid=uid,identity={'pid':uid},server='America',
            timestamp=time.time(),map_id=1036,hp=100,silver=100,capacity=40,
            inventory=[],booth=[],position=[1,2],request=None,trade=None)
    f=snapshot('Parasite',1);m=snapshot('Spiritual',2)
    m['trade']=dict(participant='Parasite',participant_uid=1,own_items=[],items=[],
        own_silver=0,other_silver=0,accepted=False,other_accepted=False)
    return {'farmer':f,'merchant':m}

def test_empty_stale_recipient_window_and_verified_close():
    b=pair();unchanged(b,b)
    a=copy.deepcopy(b);a['merchant']['trade']=None;unchanged(b,a)

@pytest.mark.parametrize('field,value',[
    ('items',[{'uid':99}]),('own_items',[{'uid':99}]),('own_silver',1),
    ('other_silver',1),('accepted',True),('other_accepted',True),
    ('participant','Stranger'),('participant_uid',3),('accepted',None)])
def test_never_cancel_nonempty_or_accepted_trade(field,value):
    b=pair();a=copy.deepcopy(b);a['merchant']['trade'][field]=value
    with pytest.raises(ValueError):unchanged(b,a)

@pytest.mark.parametrize('role',['farmer','merchant'])
@pytest.mark.parametrize('field,value',[('silver',101),('position',[2,3]),('identity',{'pid':99}),
    ('request',{'participant':'Stranger'})])
def test_reject_changes_between_observation_and_click(role,field,value):
    b=pair();a=copy.deepcopy(b);a[role][field]=value
    with pytest.raises(ValueError):unchanged(b,a)
