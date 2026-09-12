import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from conquest.merchants.collector import collect_pages,stable_metadata,matching_count
from conquest.merchants.market import browser_pages
from conquest.merchants.relist import run_cycle


def snapshot():
    return dict(source='https://conqueronline.net/market',server='America',complete=True,
                observed_at=time.time(),total=1,listings=[dict(name='Hat',category='Helmet',
                quality='Normal',plus=1,sockets=['No socket','No socket'],seller='Seller',
                price=100,quantity=1,server='America')])


@pytest.mark.parametrize('mode',['paused','not_due','one_time','due','pending','pause_during_collection','denied'])
def test_script_schedule_preserves_intent_and_requires_complete_fresh_data(tmp_path,mode):
    now = time.time()
    state = {'characters':{name:{'enabled':False,'scan':{}} for name in ('Spiritual','Dutch')}}
    merchant = state['characters']['Dutch']
    merchant['enabled'] = mode!='paused'
    if mode=='not_due':merchant['scan']={'next_scan':now+1}
    if mode=='one_time':merchant['scan']={'pending':True,'one_time':True}
    if mode=='pending':merchant['scan']={'pending':True,'request_id':'old'}
    calls = []
    def bridge(body):
        calls.append(body)
        return copy.deepcopy(state) if body['action']=='status' else {}
    def collect(**kwargs):
        calls.append({'action':'collect'})
        if mode=='denied':raise ValueError('Website denied access')
        if mode=='pause_during_collection':merchant['enabled']=False
        return snapshot()
    args = dict(now=now,bridge=bridge,collect=collect,verify=lambda:None,market_path=tmp_path/'market.json')
    if mode=='denied':
        with pytest.raises(ValueError):run_cycle(**args)
    else:
        result = run_cycle(**args)
        assert result['requested']==(['Dutch'] if mode in ('due','pending') else [])
    requests = [c for c in calls if c['action']=='scan']
    assert len(requests)==(1 if mode in ('due','pending') else 0)
    if requests:assert requests[0]['request_id']==f'12h:{int(now//43200)}'
    if mode in ('paused','not_due','one_time'):assert not any(c['action']=='collect' for c in calls)


def test_rollout_failure_prevents_network_and_requests(tmp_path):
    def verify():raise ValueError('Missing receipts')
    bridge,collect = Mock(),Mock()
    with pytest.raises(ValueError,match='Missing receipts'):
        run_cycle(bridge=bridge,collect=collect,verify=verify,market_path=tmp_path/'missing.json')
    bridge.assert_not_called();collect.assert_not_called()


def test_recent_market_is_shared_between_merchants(tmp_path):
    market = tmp_path/'market.json';market.write_text(json.dumps(snapshot()))
    statuses = {'characters':{c:{'enabled':True,'scan':{}} for c in ('Spiritual','Dutch')}}
    bridge = Mock(return_value=statuses);collect = Mock()
    result = run_cycle(bridge=bridge,collect=collect,verify=lambda:None,market_path=market)
    assert result['requested']==['Spiritual','Dutch'] and not result['collected']
    collect.assert_not_called()


class TablePage:
    """Only the observable DOM contract: pagination, filters and table cells."""
    def __init__(self, *, changed=False, filtered=False):
        self.number=1
        self.changed,self.filtered=changed,filtered
        self.row=['Hat\nHelmet','Normal','+1','No socket\nNo socket','Seller','America','100']

    def locator(self,*args):return object()

    def get_by_text(self,pattern):
        return SimpleNamespace(inner_text=lambda:'51 matching items' if 'matching' in pattern.pattern else
            'Last booth change: '+('new' if self.changed and self.number==2 else 'same'))

    def get_by_role(self,role,name=None,exact=False):
        locator = Mock()
        locator.and_.return_value=locator
        locator.input_value.return_value=''
        locator.inner_text.return_value=('America' if name=='Server' else
            'Super' if self.filtered and name=='Quality' else '\u200b')
        locator.is_enabled.side_effect=lambda:(self.number==1 if name=='Go to next page' else self.number>1)
        locator.click.side_effect=lambda:setattr(self,'number',self.number+1)
        locator.get_by_role.return_value=locator
        locator.evaluate_all.side_effect=lambda expression:[self.row]*(50 if self.number==1 else 1)
        return locator


def test_browser_collection_normalizes_placeholders_and_collects_every_page():
    data = collect_pages(TablePage())
    result = browser_pages(data,[{'id':111005,'name':'Hat'}])
    assert result['total']==51 and len(data['pages'])==2


def test_changed_or_filtered_market_is_rejected():
    with pytest.raises(ValueError,match='changed'):
        collect_pages(TablePage(changed=True))
    with pytest.raises(ValueError,match='filters'):
        collect_pages(TablePage(filtered=True))


def test_partial_market_is_never_normalized_as_complete():
    data = collect_pages(TablePage())
    data['pages'].pop()
    with pytest.raises(ValueError,match='pagination'):
        browser_pages(data,[])


def test_progressive_count_must_settle_before_pagination():
    ticks=[0.0]
    def sleep(seconds):ticks[0]+=seconds
    def read():return min(3,int(ticks[0]*5))*400,'unchanged'
    result = stable_metadata(read,lambda:None,clock=lambda:ticks[0],sleep=sleep)
    assert result==(1200,'unchanged') and ticks[0]>=1.6


def test_continuously_changing_count_is_bounded():
    ticks=[0.0]
    def sleep(seconds):ticks[0]+=seconds
    with pytest.raises(ValueError,match='did not settle'):
        stable_metadata(lambda:(ticks[0],'same'),lambda:None,clock=lambda:ticks[0],sleep=sleep)
    assert ticks[0]<15.2


@pytest.mark.parametrize('raw', ['3,327', '3\u202f327', '3\u00a0327', '3 327', '3327'])
def test_market_count_preserves_localized_thousands(raw):
    assert matching_count(raw+' matching items')==3327


@pytest.mark.parametrize('raw', ['3x327', '3,32', 'prefix 327', '3\n327'])
def test_market_count_rejects_partial_matches(raw):
    with pytest.raises(ValueError,match='format'):
        matching_count(raw+' matching items')


@pytest.mark.parametrize('raw', ['20,000', '20\u202f000', '20\u00a0000', '20 000'])
def test_localized_price_is_preserved_in_comparisons(raw):
    page = TablePage()
    page.row[-1]=raw
    result=browser_pages(collect_pages(page),[{'id':111005,'name':'Hat'}])
    assert all(row['price']==20000 for row in result['listings'])
