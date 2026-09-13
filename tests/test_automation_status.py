import pytest
from conquest.farm_telemetry import automation_status

@pytest.mark.parametrize('phase',['restocking','visiting_town','merchant_handoff','reloading'])
def test_combat_off_does_not_mean_route_stopped(phase):
 route={'phase':phase,'updated_at':99,'activity':'Heading to Spiritual with 2 Meteors'}
 label,detail=automation_status(route,{}, {'enabled':False},None,now=100)
 assert label=='Running'
 assert 'Farming is off' not in detail

def test_manual_off_does_not_claim_reviving_or_stale_work():
 label,detail=automation_status({}, {'automation_work':{'revision':1,'state':'running','at':99,'activity':'Trading'}},
                               {'enabled':False,'revision':2},{'dead_candidate':True},now=100)
 assert label=='Stopped' and 'reviving' not in detail

def test_stale_travel_is_not_reported_running():
 label,detail=automation_status({'phase':'restocking','updated_at':50}, {},{'enabled':False},None,now=100)
 assert label.startswith('Waiting') and 'not confirmed' in detail

def test_transfer_steps_and_failure_override_combat_off():
 work={'state':'running','at':99,'revision':2,'activity':'Placing Meteor in trade with Spiritual'}
 app={'automation_work':work};control={'enabled':False,'revision':2}
 assert automation_status({},app,control,None,now=100)==('Running',work['activity'])
 work['state']='attention';work['activity']='Transfer to Spiritual paused: focus lost'
 assert automation_status({},app,control,None,now=100)==('Stopped · needs attention',work['activity'])
 control['paused']=True
 assert automation_status({},app,control,None,now=100)[0]=='Paused'

def test_focus_recovery_is_distinct_from_complete_stop():
 label,_=automation_status({}, {},{'enabled':True,'execution_state':'waiting_focus','note':'Restoring client focus'},None,now=100)
 assert label=='Waiting · automatic recovery'

def test_explicit_stop_overrides_fresh_travel_and_trade_status():
 route={'phase':'restocking','updated_at':99,'activity':'Heading to warehouse'}
 app={'manual_stop_revision':7,'automation_work':{'state':'running','revision':7,'at':99,'activity':'Trading'}}
 assert automation_status(route,app,{'enabled':False,'revision':7},None,now=100)[0]=='Stopped'
 assert automation_status(route,app,{'enabled':True,'revision':8},None,now=100)[0]=='Running'
