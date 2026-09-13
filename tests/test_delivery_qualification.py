from copy import deepcopy
import json

import pytest

from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.delivery import prepare
from conquest.merchants.delivery_qualification import promote


def evidence():
    item=dict(uid=10,type_id=720027,plus=0,gem1=0,gem2=0,quantity=1,bound=False,slot=0)
    def account(name,uid,items):
        return dict(character=name,character_uid=uid,identity={'pid':uid,'created':100},server='America',
            timestamp=100,map_id=1036,hp=100,silver=200,capacity=40,inventory=items,booth=[],
            trade=None,request=None,position=[100,100],windows=[])
    f=account('Parasite',1,[item]);m=account('Spiritual',2,[])
    intent=prepare(f,m,[item],now=100)
    f,m=deepcopy(f),deepcopy(m);f['inventory']=[];m['inventory']=[deepcopy(item)]
    f['windows']=[{'name':'Inventory/##ItemGrid_A800F95C','geometry':[100,100,400,200]},
                  {'name':'##Control','geometry':[100,500,600,100]}]
    return dict(phase='delivery_verified',intent=intent,farmer_after=f,merchant_after=m,verified_at=100,
                recipient={'uid':2,'name':'Spiritual'},accept_point=[10,10],confirm_point=[20,20],offered_uids=[10])


@pytest.mark.parametrize('failure',[None,'phase','missing_item','currency','identity','recipient','unplaced','wrong_peer'])
def test_promotion_needs_complete_two_account_receipt_and_preserves_peer_controls(tmp_path,failure):
    state=evidence()
    if failure=='phase':state['phase']='merchant_confirm_submitted'
    if failure=='missing_item':state['merchant_after']['inventory']=[]
    if failure=='currency':state['merchant_after']['silver']+=1
    if failure=='identity':state['merchant_after']['identity']={'pid':3}
    if failure=='recipient':state['recipient']['uid']=3
    if failure=='unplaced':state['offered_uids']=[]
    paths=[tmp_path/name for name in ('receipt.json','candidate.json','farmer.json','peer.json')]
    peer={'character':'Dutch' if failure=='wrong_peer' else 'Spiritual','client_sha256':CLIENT_SHA256,
          'capabilities':{'booth_input':True,'booth_setup':False},'controls':{'old_control':{'verified':True}}}
    paths[0].write_text(json.dumps(state))
    paths[1].write_text(json.dumps({'client_sha256':CLIENT_SHA256,'recipient':{'draw_format':'i32'},
                                   'target_mode':{'rva':0x699290,'value':19}}))
    paths[3].write_text(json.dumps(peer));original=paths[3].read_bytes()
    if failure:
        with pytest.raises(ValueError):promote(*paths)
        assert not paths[2].exists() and paths[3].read_bytes()==original
    else:
        assert promote(*paths)=={'farmer':'Parasite','merchant':'Spiritual','items':1}
        farmer=json.loads(paths[2].read_text());updated=json.loads(paths[3].read_text())
        assert farmer['capabilities']=={'farmer_delivery':True}
        assert farmer['controls']['trade_drop']['mode']=='native_trade_drop'
        assert updated['controls']['old_control']==peer['controls']['old_control']
        assert updated['capabilities']=={'booth_input':True,'booth_setup':False,'trade_request':True,'trade':True}
