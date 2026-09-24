"""Unknown-origin observation is not a transaction or an ownership override."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import pytest

from conquest import restock_supply_observation as observation
from conquest import restock_town_recovery as recovery


@pytest.fixture
def rig(monkeypatch):
    from conquest import memory_health
    from conquest.merchants import bridge, trade_reader_1078
    from conquest.character_context import farmer_name
    x=NS(now=1000.,saved=[],reads=0)
    monkeypatch.setattr(observation.time,'time',lambda:x.now)
    monkeypatch.setattr(recovery,'_other_holds',lambda:False)
    monkeypatch.setattr(recovery,'_save_tail',lambda _,row:x.saved.append(deepcopy(row)))
    monkeypatch.setattr(memory_health,'HealthWorkerSession',lambda *_:NS())
    x.target=dict(pid=7,creation_time_100ns=80,path='game.exe')
    potion=dict(uid=1,type_id=1000020,amount=1,limit=1,plus=0,slot=0)
    x.arrow=dict(uid=4,type_id=1050002,amount=4,limit=5000,plus=0,slot=1)
    x.expected=dict(items=[potion],silver=200,capacity=40,
                    equipped_ammo=dict(uid=2,type_id=1050002,amount=1207,limit=5000,plus=0,slot=None))
    x.bag=deepcopy(x.expected);x.bag['items'].append(deepcopy(x.arrow))
    original=deepcopy(x.expected)
    original['items'].append(dict(uid=3,type_id=720027,amount=1,limit=1,plus=0,slot=1))
    x.claim=dict(phase='captured',captured_at=900.,target=x.target,bag=original,
                 delivery_proofs=[dict(uids=[5])])
    x.row=dict(town_visit_id='town',pre_admission_restock_tail=x.claim)
    x.meteor=dict(phase='completed',after=deepcopy(original),market_verified_at=910.,
                  return_submitted_at=920.,completed_at=930.,return_before=deepcopy(x.bag))
    x.health=dict(target=x.target,profile_id='farmer',embedded_controls=dict(
        observed_at=1000.,control=dict(enabled=False,paused=False,revision=2),
        life=dict(map_id=1011,dead_candidate=False,current_hp=100),external_execution=False))
    x.status=dict(input_owner=None,handoff_granted=False)
    x.manual=dict(farmer={},sessions=[],handoff=None)
    monkeypatch.setattr(bridge,'request',lambda body:deepcopy(x.status if body['action']=='status' else x.manual))
    x.rich=dict(identity=x.target,character=farmer_name(),character_uid=99,server='America',
        map_id=1011,position=[226,259],hp=100,timestamp=999.,canonical_manual_ownership=True,
        inventory=[dict(uid=i['uid'],type_id=i['type_id'],plus=0,gem1=0,gem2=0,bound=False,
                        quantity=i['amount'],slot=i['slot']) for i in x.bag['items']],
        booth=[],booth_open=False,own_booth_uid=0,trade=None,request=None,silver=200,capacity=40)
    def read(*_):
        x.reads+=1;x.now+=.1
        result=deepcopy(x.rich);result['timestamp']=x.now
        if getattr(x,'change_second',False) and x.reads==2:result['inventory'][0]['quantity']+=1
        return result
    monkeypatch.setattr(trade_reader_1078,'manual_ownership',read)
    x.loop=NS(info='isolated',identity=x.target,town_visit=NS(profile='farmer'),
        route=NS(restock_map_id=1011),check_stop=lambda:None,health=lambda:deepcopy(x.health),
        town=lambda action:deepcopy(x.bag))
    x.run=lambda:observation.observe_addition(x.loop,x.row,x.claim,x.meteor,x.expected)
    return x


def test_positive_supply_records_unknown_origin_and_preserves_original_claim(rig):
    x=rig;original=deepcopy(x.claim['bag'])
    result=x.run();proof=x.claim['unexpected_supply_addition']
    assert result==x.bag and x.claim['bag']==original
    assert proof['origin']=='unknown' and proof['is_transaction_receipt'] is False
    assert len(proof['observations'])==2 and len(x.saved)==1
    assert proof['observations'][0]['ownership']['timestamp']<proof['observations'][1]['ownership']['timestamp']


@pytest.mark.parametrize('change',['missing','amount','silver','ammo','capacity','limit'])
def test_any_changed_original_asset_is_not_explained_by_addition(rig,change):
    x=rig
    if change=='missing':x.bag['items'].pop(0)
    if change=='amount':x.bag['items'][0]['amount']=2
    if change=='silver':x.bag['silver']+=1
    if change=='ammo':x.bag['equipped_ammo']['amount']-=1
    if change=='capacity':x.bag['capacity']=39
    if change=='limit':x.bag['items'][0]['limit']=2
    with pytest.raises(ValueError):x.run()
    assert not x.saved


@pytest.mark.parametrize('change',['unknown_type','plus','zero','over_limit','wrong_limit','gem','bound','unknown_bound','duplicate_addition','delivered_uid'])
def test_only_one_known_plain_normal_arrow_is_eligible(rig,change):
    x=rig
    if change=='gem':x.rich['inventory'][1]['gem1']=1
    elif change=='bound':x.rich['inventory'][1]['bound']=True
    elif change=='unknown_bound':del x.rich['inventory'][1]['bound']
    else:
        item=x.meteor['return_before']['items'][1]
        if change=='unknown_type':item['type_id']=1088000
        if change=='plus':item['plus']=1
        if change=='zero':item['amount']=0
        if change=='over_limit':item['amount']=5001
        if change=='wrong_limit':item['limit']=4999
        if change=='duplicate_addition':x.meteor['return_before']['items'].append(dict(item,uid=6))
        if change=='delivered_uid':item['uid']=5
    with pytest.raises(ValueError):x.run()
    assert not x.saved


@pytest.mark.parametrize('hold',['stop','identity','mouse','trade','handoff','owner','unstable'])
def test_fresh_closed_exclusive_same_identity_proof_is_mandatory(rig,hold):
    x=rig
    if hold=='stop':x.loop.check_stop=lambda:(_ for _ in ()).throw(ValueError('Manual Stop'))
    if hold=='identity':x.health['target']=dict(x.target,pid=8)
    if hold=='mouse':x.health['embedded_controls']['manual_mouse']=True
    if hold=='trade':x.rich['trade']={'participant':'visitor'}
    if hold=='handoff':x.manual['handoff']={'phase':'active'}
    if hold=='owner':x.status['input_owner']='Dutch'
    if hold=='unstable':x.change_second=True
    with pytest.raises(ValueError):x.run()
    assert not x.saved


def test_restart_revalidates_same_json_roundtripped_evidence_without_absorbing_more(rig):
    x=rig;x.run()
    x.row=json.loads(json.dumps(x.row));x.claim=x.row['pre_admission_restock_tail']
    original=deepcopy(x.claim['unexpected_supply_addition'])
    assert x.run()==x.bag and len(x.saved)==1
    assert x.claim['unexpected_supply_addition']==original
    x.bag['items'].append(dict(x.arrow,uid=9))
    with pytest.raises(ValueError):x.run()
    assert len(x.saved)==1 and x.claim['unexpected_supply_addition']==original
