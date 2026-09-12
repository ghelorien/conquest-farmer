import json
import threading
from pathlib import Path
from conquest.merchants.journal import Journal
from conquest.merchants.market_refresh import MarketRefreshWorker
from conquest.merchants.market import SOURCE


def snapshot(now):
    return {'source':SOURCE,'server':'America','complete':True,'observed_at':now,'total':1,
        'listings':[{'name':'Coat','category':'Trojan Armor','quality':'Normal','plus':1,
                     'sockets':['No socket','No socket'],'price':100,'seller':'Outside','server':'America'}]}


def test_two_merchants_share_download_and_restart_keeps_result(tmp_path):
    journal=Journal(tmp_path/'j.db');calls=[]
    def collect(**kwargs):
        calls.append(kwargs);data=snapshot(100)
        Path(kwargs['destination']).write_text(json.dumps(data));return data
    worker=MarketRefreshWorker(journal,threading.Event(),tmp_path/'market.json',collect=collect,clock=lambda:100)
    worker.request('Spiritual','a');worker.request('Dutch','b')
    assert worker.request('Dutch','duplicate')['request_id']=='b'
    worker.step();assert len(calls)==1
    assert all(worker.state(c)['phase']=='ready' and not worker.state(c)['pending'] for c in ('Spiritual','Dutch'))
    restarted=MarketRefreshWorker(journal,threading.Event(),worker.path,collect=collect,clock=lambda:101)
    restarted.step();assert len(calls)==1


def test_failed_download_keeps_work_pending_and_retries_without_secret_text(tmp_path):
    journal=Journal(tmp_path/'j.db');now=[100];calls=[]
    def collect(**kwargs):
        calls.append(1)
        if len(calls)==1:raise RuntimeError('secret browser session')
        return snapshot(now[0])
    worker=MarketRefreshWorker(journal,threading.Event(),tmp_path/'market.json',collect=collect,clock=lambda:now[0])
    worker.request('Dutch','once');worker.step()
    assert worker.state('Dutch')['phase']=='failed' and worker.state('Dutch')['pending']
    assert 'secret' not in json.dumps(worker.state('Dutch'))
    worker.step();assert len(calls)==1
    now[0]=161;worker.step();assert worker.state('Dutch')['phase']=='ready' and len(calls)==2


def test_old_scan_with_expired_data_fetches_without_resuming_paused_operations(tmp_path):
    journal=Journal(tmp_path/'j.db');calls=[]
    journal.request_scan('Dutch','legacy',now=1);journal.set('Dutch','enabled',False)
    def collect(**kwargs):
        calls.append(1);data=snapshot(1000)
        Path(kwargs['destination']).write_text(json.dumps(data));return data
    worker=MarketRefreshWorker(journal,threading.Event(),tmp_path/'market.json',collect=collect,clock=lambda:1000)
    worker.step()
    assert len(calls)==1 and not journal.get('Dutch','enabled')
    assert journal.get('Dutch','scan')['pending']
    worker.step();assert len(calls)==1


def test_resume_legacy_batch_is_listing_only_and_completion_counts_survive_restart(tmp_path):
    journal=Journal(tmp_path/'j.db');journal.request_scan('Dutch','legacy',now=1)
    resumed=journal.resume_batch('Dutch')
    assert resumed['one_time'] and journal.get('Dutch','enabled')
    journal.complete_scan('Dutch','legacy',changed=4,deferred=2,now=10)
    restarted=Journal(journal.path)
    assert restarted.get('Dutch','scan')['changed']==4
    assert restarted.get('Dutch','scan')['deferred']==2 and not restarted.get('Dutch','enabled')


def test_collector_uses_configured_browser_instead_of_wrong_account_cache(tmp_path):
    import pytest
    from conquest.merchants.collector import browser_launch_options
    settings=tmp_path/'browser.json'
    assert browser_launch_options(settings)=={'headless':True}
    executable=tmp_path/'chrome.exe';executable.write_bytes(b'test executable fixture')
    settings.write_text(json.dumps({'executable_path':str(executable)}))
    assert browser_launch_options(settings)=={'headless':True,'executable_path':str(executable)}
    executable.unlink()
    with pytest.raises(ValueError,match='browser is missing'):browser_launch_options(settings)
