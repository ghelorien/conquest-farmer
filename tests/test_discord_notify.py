import io
import json
import urllib.error

import pytest

from conquest.discord_notify import Notifications,DeliveryError,deliver,notable_drop,status_text,webhook_url


def test_notification_targets_follow_changed_route_policy(tmp_path,monkeypatch):
    from conquest import discord_notify as module
    policy=tmp_path/'policy.json'
    monkeypatch.setattr(module,'KILL_RATE_POLICY',policy)
    assert module.kill_rate_targets()==(40,50)
    policy.write_text(json.dumps({'target_kills_per_minute':60,'stretch_kills_per_minute':80}))
    assert module.kill_rate_targets()==(60,80)
    policy.write_text(json.dumps({'target_kills_per_minute':60,'stretch_kills_per_minute':50}))
    assert module.kill_rate_targets()==(60,60)
    policy.write_text(json.dumps({'target_kills_per_minute':False,'stretch_kills_per_minute':-1}))
    assert module.kill_rate_targets()==(40,50)


def test_full_storage_alert_is_immediate_prioritized_and_not_repeated(tmp_path):
    events=tmp_path/'events.jsonl';events.write_text('')
    n=Notifications({'next_update_at':9999})
    n.updates({}, {'phase':'hunting'},events,100,lambda pid:True)
    n.enqueue('routine update',100,'periodic_status')
    events.write_text(json.dumps({'event':'storage_full_stop','phase':'stopped'})+'\n')
    route={'phase':'stopped','detail':'Town and Market warehouses are full'}
    n.updates({},route,events,101,lambda pid:True)
    assert n.state['queue'][0]['kind']=='terminal_stop'
    assert 'Automatic reconnect is disabled' in n.state['queue'][0]['content']
    n.observe({},route,101,lambda pid:True)
    n.observe({},route,200,lambda pid:True)
    n.updates({},route,events,200,lambda pid:True)
    assert sum(row['kind']=='terminal_stop' for row in n.state['queue'])==1


@pytest.mark.parametrize('value',['http://discord.com/api/webhooks/123/abc',
    'https://discord.com.evil.test/api/webhooks/123/abc','https://evil.test/api/webhooks/123/abc',
    'https://discord.com/api/webhooks/123/abc?redirect=elsewhere'])
def test_webhook_secret_cannot_be_sent_to_other_hosts(value):
    with pytest.raises(ValueError):webhook_url(value)


def test_webhook_requires_server_confirmation():
    assert webhook_url('https://discord.com/api/webhooks/123/abc_DEF-123').endswith('?wait=true')












def test_dead_process_or_stale_heartbeat_does_not_report_hunting():
    route={'phase':'hunting','pid':5,'updated_at':50}
    assert 'exited' in status_text({},route,70,lambda pid:False)
    assert '45 seconds' in status_text({},route,100,lambda pid:True)




@pytest.mark.parametrize('kind,expected',[(1088000,True),(1088001,True),(700001,True),
    (421009,True),(121007,True),(421005,False),(1000020,False),(1090000,False)])
def test_notable_drops_do_not_infer_plus_from_plain_item_type(kind,expected):
    assert notable_drop({'type_id':kind}) is expected




def test_delivery_disables_mentions_and_never_exposes_http_error_url():
    class Opener:
        def open(self,call,timeout):
            assert json.loads(call.data)['allowed_mentions']=={'parse':[]}
            raise urllib.error.HTTPError(call.full_url,429,'secret in URL',{},io.BytesIO(b'{"retry_after":12}'))
    with pytest.raises(DeliveryError) as error:
        deliver('https://discord.com/api/webhooks/123/SECRET','@everyone hi',Opener())
    assert str(error.value)=='Discord HTTP 429'
    assert error.value.retry_after==13


def test_delivery_requires_discord_message_id():
    class Opener:
        def open(self,call,timeout):return io.BytesIO(b'{"id":"12345"}')
    assert deliver('https://discord.com/api/webhooks/123/test','Status changed',Opener())=='12345'


@pytest.mark.parametrize('focused,dead,expected',[(True,False,'Hunting Turtledoves'),
    (False,False,'Paused'),(True,True,'Dead')])
def test_current_memory_overrides_transient_desktop_pause_messages(focused,dead,expected):
    from conquest.discord_notify import live_activity
    health={'window':{'foreground':1 if focused else 2,'root_hwnd':1},
            'embedded_controls':{'life':{'dead_candidate':dead},
                                 'control':{'enabled':True,'execution_state':'farming'}}}
    assert live_activity(health,{'current_activity':'Running','selected_route':'turtledove'}).startswith(expected)



def test_live_hunting_flag_cannot_mask_navigation_blockage():
    from conquest.discord_notify import live_activity
    health={'window':{'foreground':1,'root_hwnd':1},'embedded_controls':{
        'life':{'dead_candidate':False},'control':{'enabled':True,'execution_state':'farming'}}}
    assert live_activity(health,{'navigation_blocked':True,'current_activity':'Running'}).startswith('Navigation blocked')









def test_confirmed_farming_requires_active_focused_live_worker():
    from conquest.discord_notify import confirmed_farming
    health={'window':{'foreground':1,'root_hwnd':1},'embedded_controls':{
        'life':{'dead_candidate':False},'control':{'enabled':True,'execution_state':'farming'},
        'external_execution':True}}
    assert confirmed_farming(health)
    for key,value in [('external_execution',False),('life',None)]:
        changed=json.loads(json.dumps(health));changed['embedded_controls'][key]=value
        assert not confirmed_farming(changed)
    health['window']['foreground']=2
    assert not confirmed_farming(health)



def stopped(n,now,detail='movement_failure_limit'):
    n.observe({}, {'phase':'needs_attention','detail':detail},now,lambda pid:True)


def farming(n,now):
    n.observe({'live_farming':True,'live_checked_at':now,'current_activity':'Attacking'},
              {'phase':'hunting','updated_at':now},now,lambda pid:True)


def test_unrecovered_stop_waits_sixty_seconds_and_deduplicates_changing_details():
    n=Notifications()
    stopped(n,100)
    stopped(n,159)
    assert not n.state['queue']
    stopped(n,160)
    stopped(n,170,'route process exited')
    assert len(n.state['queue'])==1
    assert 'Needs attention' in n.state['queue'][0]['content']


def test_short_stop_and_recovery_are_completely_silent():
    n=Notifications()
    stopped(n,100)
    farming(n,140)
    farming(n,142)
    assert not n.state['queue']
    stopped(n,150)
    stopped(n,209)
    assert not n.state['queue']


def test_recovery_during_delivery_outage_discards_unsent_stop_without_resume_message():
    n=Notifications()
    stopped(n,100);stopped(n,160)
    assert n.state['queue']
    farming(n,170);farming(n,172)
    assert not n.state['queue']


def test_delivered_stop_gets_one_confirmed_recovery_even_after_notifier_restart():
    n=Notifications();stopped(n,100);stopped(n,160)
    n.delivered(n.state['queue'].pop(0))
    n=Notifications(json.loads(json.dumps(n.state)))
    farming(n,170);farming(n,172);farming(n,175)
    assert len(n.state['queue'])==1
    assert 'Farming resumed' in n.state['queue'][0]['content']
    n.delivered(n.state['queue'].pop(0))
    farming(n,180)
    assert not n.state['queue']


def test_pending_failure_timer_survives_notifier_restart():
    n=Notifications();stopped(n,100)
    n=Notifications(json.loads(json.dumps(n.state)))
    stopped(n,160)
    assert len(n.state['queue'])==1


@pytest.mark.parametrize('activity',['Restocking in town','Dead — reviving','Disconnected — reconnecting',
    'Paused — waiting for Conquer focus','Patrolling','Farming is off'])
def test_recovering_and_routine_states_stay_quiet(activity):
    n=Notifications()
    for now in (100,160,300):
        n.observe({'live_activity':activity},{'phase':'hunting','updated_at':now},now,lambda pid:True)
    assert not n.state['queue']


def test_policy_migration_discards_old_alert_and_drop_backlog():
    n=Notifications({'notification_policy':'major_v2','queue':[{'content':'STOPPED: old'},
        {'content':'Notable drop: Meteor'},{'content':'Farming resumed'}]})
    assert not n.state['queue']


def test_drop_notifications_do_not_replay_history_on_first_install(tmp_path):
    p=tmp_path/'pickups.jsonl'
    p.write_text(json.dumps({'type_id':1088001,'increase':1})+'\n')
    n=Notifications();n.drops(p,100)
    assert not n.state['queue']
    assert n.state['drop_offset']==p.stat().st_size


def test_restocking_cancels_pending_failure_but_does_not_claim_farming_resumed():
    n=Notifications();stopped(n,100);stopped(n,160)
    n.delivered(n.state['queue'].pop(0))
    n.observe({}, {'phase':'restocking','updated_at':170},170,lambda pid:True)
    assert not n.state['queue']
    assert n.state['stop_delivered']
    assert 'failure_since' not in n.state


def navigation(n,now,*,blocked=True,position=(603,576),kills=15,pickup=0):
    n.observe({'live_activity':'Navigation blocked' if blocked else 'Hunting',
        'navigation_blocked':blocked,'live_farming':True,'live_checked_at':now,
        'live_position':list(position),'live_map':1002,'kills':kills,
        'last_pickup':{'timestamp':pickup}},
        {'phase':'hunting','pid':5,'updated_at':now},now,lambda pid:True)


def test_blocked_navigation_alerts_despite_fresh_heartbeat_and_farming_flag():
    n=Notifications()
    navigation(n,100);navigation(n,159)
    assert not n.state['queue']
    navigation(n,160);navigation(n,180)
    assert len(n.state['queue'])==1
    assert 'navigation has been blocked' in n.state['queue'][0]['content']
    assert '60 seconds' in n.state['queue'][0]['content']


def test_brief_detour_and_recovery_are_silent():
    n=Notifications()
    navigation(n,100);navigation(n,159)
    navigation(n,160,blocked=False,position=(604,576))
    navigation(n,162,blocked=False,position=(605,576))
    assert not n.state['queue']


@pytest.mark.parametrize('change',[{'position':(604,576)},{'kills':16},{'pickup':120}])
def test_actual_progress_resets_blockage_timer(change):
    n=Notifications();navigation(n,100)
    navigation(n,150,**change);navigation(n,209,**change)
    assert not n.state['queue']
    navigation(n,210,**change)
    assert len(n.state['queue'])==1


def test_blockage_timer_survives_notifier_restart_and_counter_reset():
    n=Notifications();navigation(n,100)
    n=Notifications(json.loads(json.dumps(n.state)))
    navigation(n,160,kills=0)
    assert len(n.state['queue'])==1


def test_stall_recovery_requires_progress_not_merely_cleared_flag():
    n=Notifications();navigation(n,100);navigation(n,160)
    n.delivered(n.state['queue'].pop(0))
    n=Notifications(json.loads(json.dumps(n.state)))
    navigation(n,170,blocked=False);navigation(n,172,blocked=False)
    assert not n.state['queue']
    navigation(n,180,blocked=False,position=(604,576))
    navigation(n,182,blocked=False,position=(604,576))
    assert len(n.state['queue'])==1 and n.state['queue'][0]['kind']=='farming_resumed'


def test_stall_recovered_before_delivery_cancels_alert_without_resume():
    n=Notifications();navigation(n,100);navigation(n,160)
    navigation(n,170,blocked=False,position=(604,576))
    navigation(n,172,blocked=False,position=(604,576))
    assert not n.state['queue']
    assert 'failure_since' not in n.state


def test_progress_cancels_unsent_stall_alert_even_before_ui_flag_clears():
    n=Notifications();navigation(n,100);navigation(n,160)
    navigation(n,170,position=(604,576))
    assert not n.state['queue']
    navigation(n,229,position=(604,576))
    assert not n.state['queue']
    navigation(n,230,position=(604,576))
    assert len(n.state['queue'])==1



def test_stop_recurring_before_queued_recovery_is_delivered_stays_one_open_alert():
    n=Notifications();stopped(n,100);stopped(n,160)
    n.delivered(n.state['queue'].pop(0))
    farming(n,170);farming(n,172)
    assert n.state['queue'][0]['kind']=='farming_resumed'
    stopped(n,173);stopped(n,233)
    assert not n.state['queue']
    assert n.state['stop_delivered']


def test_quarter_hour_summary_is_immediate_then_persistent_and_coalesced(tmp_path,monkeypatch):
    monkeypatch.setattr('conquest.discord_notify.recent_kills',lambda path,now,seconds=900:{900:42,60:20,3600:1400}[seconds])
    n=Notifications();path=tmp_path/'events.jsonl';path.write_text('')
    app={'updated_at':100,'state':'Farming','live_farming':True,'live_checked_at':100,
         'live_hp':317,'live_max_hp':329,'kills':31,'experience':{'level':17}}
    route={'phase':'hunting','updated_at':100,'supplies':{'arrows':1679,'potions':15,'free_slots':13}}
    n.updates(app,route,path,100,lambda pid:True)
    assert len(n.state['queue'])==1
    content=n.state['queue'][0]['content']
    assert 'level 17' in content and 'HP 317/329' in content and '1,679 arrows' in content
    assert '42 verified kills in the last 15 minutes' in content
    assert '1,400 verified kills in the last hour (minimum: 2,400, stretch: 3,000)' in content
    assert '15-minute pace: 168/hour, including downtime' in content
    assert 'last minute: 20 kills (minimum: 40, stretch: 50)' in content
    assert n.state['next_update_at']==1000
    n=Notifications(json.loads(json.dumps(n.state)))
    n.updates(app,route,path,999,lambda pid:True)
    assert len(n.state['queue'])==1 and n.state['queue'][0]['created_at']==100
    n.updates(app,route,path,1000,lambda pid:True)
    assert len(n.state['queue'])==1 and n.state['queue'][0]['created_at']==1000
    assert 'HP 317' not in n.state['queue'][0]['content']
    assert 'stale' in n.state['queue'][0]['content']


def test_restock_log_events_survive_short_trip_restart_and_partial_write(tmp_path):
    path=tmp_path/'events.jsonl'
    path.write_text(json.dumps({'event':'restock_complete','phase':'restocking'})+'\n')
    n=Notifications({'notification_policy':'terminal_v3','next_update_at':10000})
    route={'phase':'hunting','updated_at':100}
    n.updates({},route,path,100,lambda pid:True)
    assert not n.state['queue']  # Historical restocking is not replayed.
    with path.open('a') as out:
        out.write(json.dumps({'event':'return_required','phase':'hunting','reason':'inventory_full'})+'\n')
        out.write(json.dumps({'event':'town_activity','phase':'restocking'})+'\n')
        out.write('{"event":"restock_complete",')
    n.updates({},route,path,101,lambda pid:True)
    assert [r['kind'] for r in n.state['queue']]==['restock_started']
    assert 'inventory full' in n.state['queue'][0]['content']
    n=Notifications(json.loads(json.dumps(n.state)))
    with path.open('a') as out:out.write('"phase":"restocking","supplies":{"arrows":1700,"potions":15}}\n')
    n.updates({},route,path,102,lambda pid:True)
    n.updates({},route,path,103,lambda pid:True)
    assert [r['kind'] for r in n.state['queue']]==['restock_started','restock_complete']
    assert '1,700 arrows' in n.state['queue'][-1]['content']


def test_reload_notices_do_not_claim_town_trip_and_resume_only_after_live_farming(tmp_path):
    path=tmp_path/'events.jsonl';path.write_text('')
    n=Notifications({'notification_policy':'terminal_v3','next_update_at':10000})
    route={'phase':'reloading','updated_at':100}
    n.updates({},route,path,100,lambda pid:True)
    with path.open('a') as f:
        for event in ('reload_started','travel_heal','xp_fly_verified'):
            f.write(json.dumps({'event':event,'phase':'reloading'})+'\n')
    n=Notifications(json.loads(json.dumps(n.state)))
    n.updates({},route,path,101,lambda pid:True)
    assert [r['kind'] for r in n.state['queue']]==['reload_started']
    assert 'safe spot for app reload' in n.state['queue'][0]['content']
    n.updates({}, {'phase':'hunting','updated_at':102},path,102,lambda pid:True)
    assert len(n.state['queue'])==1
    app={'live_farming':True,'live_checked_at':103}
    n.updates(app,{'phase':'hunting','updated_at':103},path,103,lambda pid:True)
    n.updates(app,{'phase':'hunting','updated_at':103},path,104,lambda pid:True)
    assert [r['kind'] for r in n.state['queue']]==['reload_started','reload_resumed']


def test_monitor_started_during_restock_announces_it_once(tmp_path):
    path=tmp_path/'events.jsonl';path.write_text('')
    n=Notifications({'notification_policy':'terminal_v3','next_update_at':10000})
    route={'phase':'restocking','updated_at':100}
    n.updates({},route,path,100,lambda pid:True)
    n=Notifications(json.loads(json.dumps(n.state)))
    n.updates({},route,path,101,lambda pid:True)
    assert [r['kind'] for r in n.state['queue']]==['restock_started']


def test_critical_failure_precedes_queued_routine_updates():
    n=Notifications();n.enqueue('status',100,'periodic_status')
    n.enqueue('restocking',101,'restock_started')
    stopped(n,102);stopped(n,162)
    assert n.state['queue'][0]['kind']=='terminal_stop'


def test_rolling_kills_uses_verified_increments_across_session_resets(tmp_path):
    import sqlite3
    from conquest.discord_notify import recent_kills
    path=tmp_path/'trial.sqlite3';db=sqlite3.connect(path)
    db.execute('create table events(time real,event text,payload text)')
    rows=[(99,'kill_verified',{'count':9,'total':99}),
          (100,'kill_verified',{'count':8,'total':107}),
          (101,'kill_verified',{'count':2,'total':109}),
          (500,'trial_started',{}),(501,'kill_verified',{'count':1,'total':1}),
          (999,'attack_attempt',{'number':55}),(1000,'kill_verified',{'count':1,'total':2}),
          (1001,'kill_verified',{'count':5,'total':7})]
    db.executemany('insert into events values(?,?,?)',[(t,e,json.dumps(p)) for t,e,p in rows])
    db.commit();db.close()
    assert recent_kills(path,1000)==4
    assert recent_kills(path,2000)==0
    assert recent_kills(tmp_path/'missing.sqlite3',1000) is None
    assert not (tmp_path/'missing.sqlite3').exists()



def test_savings_completion_is_announced_once(tmp_path):
    path=tmp_path/'events.jsonl';path.write_text('')
    n=Notifications({'notification_policy':'terminal_v3','next_update_at':10000})
    n.updates({}, {'phase':'hunting'},path,100,lambda pid:True)
    path.write_text(json.dumps({'event':'savings_complete','phase':'completed','silver':50021})+'\n')
    n.updates({}, {'phase':'completed'},path,101,lambda pid:True)
    n.updates({}, {'phase':'completed'},path,102,lambda pid:True)
    assert [r['kind'] for r in n.state['queue']]==['savings_complete']
    assert '50,021' in n.state['queue'][0]['content']

def test_status_write_retries_sharing_violation_with_unique_temporary(tmp_path,monkeypatch):
    from conquest import discord_notify as d
    from pathlib import Path
    target=tmp_path/'state.json';target.write_text('{"old":true}')
    original=Path.replace;calls=[]
    def replace(source,destination):
        calls.append(source)
        if len(calls)<3:raise PermissionError('File temporarily open')
        return original(source,destination)
    monkeypatch.setattr(Path,'replace',replace);monkeypatch.setattr(d.time,'sleep',lambda _:None)
    d.write_json(target,{'new':True})
    assert d.read_json(target)=={'new':True}
    assert len(calls)==3 and calls[0]!=target.with_suffix('.tmp')
    assert not list(tmp_path.glob('*.tmp'))


def test_failed_status_write_preserves_existing_file(tmp_path,monkeypatch):
    from conquest import discord_notify as d
    from pathlib import Path
    target=tmp_path/'state.json';target.write_text('{"old":true}')
    def denied(*a):raise PermissionError('Locked')
    monkeypatch.setattr(Path,'replace',denied);monkeypatch.setattr(d.time,'sleep',lambda _:None)
    import pytest
    with pytest.raises(PermissionError):d.write_json(target,{'new':True})
    assert d.read_json(target)=={'old':True} and not list(tmp_path.glob('*.tmp'))


def test_every_verified_pickup_is_notified_once_with_plus_and_timestamp(tmp_path):
    path=tmp_path/'pickups.jsonl';path.write_text('')
    n=Notifications();n.drops(path,100)
    rows=[{'type_id':421015,'plus':1,'increase':1,'timestamp':101},
          {'type_id':1088001,'increase':1,'timestamp':102},
          {'type_id':1088000,'increase':1,'timestamp':103},
          {'type_id':500089,'increase':1,'timestamp':104},
          {'type_id':1088001,'increase':0,'timestamp':105}]
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    n.drops(path,110);n.drops(path,111)
    assert len(n.state['queue'])==4
    assert '+1' in n.state['queue'][0]['content']
    assert 'Meteor' in n.state['queue'][1]['content']
    assert 'DragonBall' in n.state['queue'][2]['content']
    assert 'Super' in n.state['queue'][3]['content']
    assert [r['created_at'] for r in n.state['queue']]==[101,102,103,104]
    assert all(r['kind']=='pickup' for r in n.state['queue'])


def test_recovered_drop_notification_does_not_invent_pickup_time(tmp_path):
    path=tmp_path/'pickups.jsonl';path.write_text('')
    n=Notifications();n.drops(path,100)
    row={'type_id':530013,'plus':2,'increase':1,'timestamp':101,'source':'recovered_inventory'}
    path.write_text(json.dumps(row)+'\n')
    n.drops(path,102)
    message=n.state['queue'][0]['content']
    assert '+2' in message and 'Recovered missing pickup' in message
    assert 'original pickup time unavailable' in message


def test_pickup_delivery_receipt_links_exact_item_to_discord_message(tmp_path):
    path=tmp_path/'pickups.jsonl';path.write_text('')
    n=Notifications();n.drops(path,100)
    path.write_text(json.dumps({'type_id':530013,'plus':2,'increase':1,
        'inventory_uid':1234,'timestamp':101})+'\n')
    n.drops(path,102)
    n.delivered(n.state['queue'].pop(0),'discord-message-id',103)
    restored=Notifications(json.loads(json.dumps(n.state)))
    receipt=restored.state['delivery_receipts'][-1]
    assert receipt['inventory_uid']==1234 and receipt['plus']==2
    assert receipt['message_id']=='discord-message-id' and receipt['delivered_at']==103
    assert receipt['created_at']==101 and not restored.state['queue']


def test_hourly_count_accepts_qualified_scatter_batches_but_rejects_discontinuities(tmp_path):
    import sqlite3
    from conquest.discord_notify import recent_kills
    path=tmp_path/'trial.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('create table events(time real,event text,payload text)')
        db.execute('insert into events values(?,?,?)',(500,'kill_verified',json.dumps({'count':20})))
        db.execute('insert into events values(?,?,?)',(600,'kill_verified',json.dumps({'count':32})))
    assert recent_kills(path,1000,3600)==52
    with sqlite3.connect(path) as db:
        db.execute('insert into events values(?,?,?)',(700,'kill_verified',json.dumps({'count':33})))
    assert recent_kills(path,1000,3600) is None
