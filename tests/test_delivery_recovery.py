import copy
import time
import pytest
from conquest.merchants.delivery_recovery import partial_result

def setup():
    def item(uid):return dict(uid=uid,type_id=720027,plus=0,gem1=0,gem2=0,quantity=1,bound=False,slot=0)
    def snap(name,uid,items):return dict(character=name,character_uid=uid,identity={'pid':uid},
        server='America',timestamp=time.time(),map_id=1036,hp=100,silver=100,capacity=40,
        inventory=items,booth=[],trade=None,request=None)
    one,two,three=item(10),item(11),item(12)
    f=snap('Parasite',1,[one,two]);m=snap('Spiritual',2,[three])
    intent={'farmer':copy.deepcopy(f),'merchant':copy.deepcopy(m),'items':[one,two]}
    f['inventory']=[two];m['inventory']=[three,one]
    return intent,f,m

def test_partial_result_preserves_remainder_and_separate_empty_cleanup():
    i,f,m=setup()
    m['trade']=dict(participant='Parasite',participant_uid=1,own_items=[],items=[],
        own_silver=0,other_silver=0,accepted=False,other_accepted=False)
    r=partial_result(i,f,m)
    assert [x['uid'] for x in r['delivered']]==[10]
    assert [x['uid'] for x in r['remaining']]==[11]
    assert r['cleanup_pending']==['Spiritual']

@pytest.mark.parametrize('change',['lost','duplicate','extra','currency','identity','booth','offer','accepted'])
def test_ambiguous_partial_outcome_never_reconciles(change):
    i,f,m=setup()
    if change=='lost':m['inventory']=m['inventory'][:1]
    if change=='duplicate':f['inventory']+=i['items'][:1]
    if change=='extra':m['inventory']=m['inventory'][1:]
    if change=='currency':f['silver']+=1
    if change=='identity':m['identity']={'pid':9}
    if change=='booth':m['booth']=[m['inventory'].pop()]
    if change in ('offer','accepted'):
        m['trade']=dict(participant='Parasite',participant_uid=1,own_items=[],items=[],
            own_silver=0,other_silver=0,accepted=False,other_accepted=False)
        if change=='offer':m['trade']['items']=[i['items'][1]]
        else:m['trade']['accepted']=True
    with pytest.raises(ValueError):partial_result(i,f,m)
