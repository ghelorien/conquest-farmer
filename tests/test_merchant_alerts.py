import copy
import json
import pytest
from conquest.discord_notify import DeliveryError
from conquest.merchants.alerts import Alerts, condition


def healthy():
    return dict(connected=True,enabled=False,pending=[],error=None)


def test_login_alone_does_not_confirm_shop_recovery_and_pause_is_quiet():
    state={**healthy(),'enabled':True,'shop_return':{'phase':'returning'}}
    assert condition(state) is not None
    alerts=Alerts()
    alerts.observe('Dutch',('Disconnected',0),0)
    alerts.dispatch(0,load=lambda:'shops',send=lambda *args:'1')
    status={'characters':{'Dutch':state}}
    alerts.poll(status,1)
    state['enabled']=False
    alerts.poll(status,100)
    assert not alerts.state['queue']
    state['enabled']=True;state['shop_return']['phase']='complete'
    alerts.poll(status,101);alerts.poll(status,107)
    assert alerts.state['queue'][0]['kind']=='recovery'


def test_persistent_failure_survives_restart_and_sends_once_then_recovers():
    alerts=Alerts();problem=('Focus blocked',60)
    alerts.observe('Dutch',problem,0);alerts.observe('Dutch',problem,59)
    assert not alerts.state['queue']
    alerts=Alerts(json.loads(json.dumps(alerts.state)))
    alerts.observe('Dutch',problem,60)
    sent=[]
    def send(url,content):sent.append((url,content));return str(len(sent))
    alerts.dispatch(60,load=lambda:'shops',send=send)
    alerts.observe('Dutch',problem,120);alerts.dispatch(120,load=lambda:'shops',send=send)
    assert len(sent)==1 and sent[0][0]=='shops'
    alerts.observe('Dutch',None,121);assert not alerts.state['queue']
    alerts.observe('Dutch',None,126);alerts.dispatch(126,load=lambda:'shops',send=send)
    assert len(sent)==2 and 'recovery confirmed' in sent[1][1]


def test_unsent_resolved_alert_cancels_without_fake_recovery():
    alerts=Alerts();alerts.observe('Dutch',('failed',0),0)
    alerts.observe('Dutch',None,1)
    assert not alerts.state['queue'] and not alerts.state['incidents']


@pytest.mark.parametrize('note',['Automation stopped or manual input active','Waiting for input owner',
    'Waiting for a safe farmer handoff','Mouse control is yours; farming resumes after 2 seconds idle'])
def test_manual_pause_and_handoffs_are_silent(note):
    assert condition({**healthy(),'error':{'note':note}}) is None
    assert condition(healthy()) is None


def test_uncertain_transactions_and_auto_pause_are_urgent_but_normal_work_waits():
    assert condition({**healthy(),'pending':[{'phase':'uncertain'}]})[1]==0
    assert condition({**healthy(),'pending':[{'phase':'submitted'}]})[1]==60
    assert condition({**healthy(),'needs_attention':{'note':'Unexpected failure; paused'}})[1]==0
    assert condition({**healthy(),'error':{'note':'Reconnect retries exhausted'}})[1]==60
    assert condition({**healthy(),'connected':False})[1]==60


def test_app_exit_and_reporting_failure_are_monitored_independently():
    alerts=Alerts();alerts.poll(None,0);alerts.poll(None,60)
    assert alerts.state['queue'][0]['subject']=='Conquest app'
    other=Alerts();other.poll(None,0,clean_shutdown=True);other.poll(None,100,clean_shutdown=True)
    assert not other.state['queue']
    status={'characters':{'Dutch':healthy()},'sales_reporting':{'status':'needs_attention'}}
    other.poll(status,0);other.poll(status,60)
    assert other.state['queue'][0]['subject']=='Shop sales reporting'


def test_missing_observations_never_confirm_recovery():
    alerts=Alerts();alerts.observe('Dutch',('failed',0),0)
    alerts.dispatch(0,load=lambda:'shops',send=lambda *args:'id')
    alerts.poll(None,1);alerts.poll(None,10)
    assert not any(r['kind']=='recovery' for r in alerts.state['queue'])


def test_delivery_is_durable_retries_safely_and_redacts_secrets():
    alerts=Alerts();alerts.observe('Dutch',('See https://discord.com/api/webhooks/1/secret token=secret',0),0)
    assert 'secret' not in alerts.state['queue'][0]['content']
    writes=[]
    def failed(*args):raise DeliveryError('Discord HTTP 429',30)
    alerts.dispatch(0,load=lambda:'secret_url',send=failed,persist=lambda s:writes.append(copy.deepcopy(s)))
    assert len(writes)==2 and writes[0]['queue'] and alerts.state['queue'][0]['retry_at']==30
    alerts=Alerts(json.loads(json.dumps(alerts.state)))
    calls=[]
    def send(*args):calls.append(args);return 'confirmed'
    alerts.dispatch(29,load=lambda:'shops',send=send);assert not calls
    alerts.dispatch(30,load=lambda:'shops',send=send);assert len(calls)==1
    assert not alerts.state['queue'] and alerts.state['last_message_id']=='confirmed'


def test_recurrence_cancels_queued_recovery():
    alerts=Alerts();alerts.observe('Dutch',('failed',0),0)
    alerts.dispatch(0,load=lambda:'shops',send=lambda *args:'id')
    alerts.observe('Dutch',None,1);alerts.observe('Dutch',None,6)
    assert alerts.state['queue'][0]['kind']=='recovery'
    alerts.observe('Dutch',('failed again',0),7)
    assert all(r['kind']=='failure' for r in alerts.state['queue'])


def test_ui_and_reporting_worker_stalls_trigger_alerts():
    alerts=Alerts();status={'characters':{'Dutch':healthy()},'ui_health':{'tick_age_ms':16000},
                          'sales_reporting':{'status':'waiting','last_checked_at':0}}
    alerts.poll(status,100);alerts.poll(status,160)
    assert {r['subject'] for r in alerts.state['queue']}=={'Conquest app','Shop sales reporting'}
