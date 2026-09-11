import json
import pytest
from conquest.farm_telemetry import PickupHistory,pickup_values,activity_text
from conquest.overnight import needs_town
from conquest.routes import RouteLibrary


def test_level_eta_uses_remaining_experience_and_net_hourly_rate():
    from conquest.farm_telemetry import farm_stats
    app={'kills':1234,'kills_per_hour':1800,'experience_observed_at':100,
         'experience':{'level':55,'experience_candidate':250,'experience_required':1000,'xp_per_hour_candidate':500}}
    assert farm_stats(app,True,now=101)=='Kills 1,234  ·  1,800/h\nLevel 55  ·  Next level ~1h 30m'


@pytest.mark.parametrize('speed',[None,0,-100,float('nan')])
def test_eta_does_not_invent_time_without_positive_experience_gain(speed):
    from conquest.farm_telemetry import farm_stats
    app={'experience_observed_at':100,'experience':{'level':55,'experience_candidate':250,
         'experience_required':1000,'xp_per_hour_candidate':speed}}
    assert 'Estimating…' in farm_stats(app,True,now=101)


def test_paused_stale_and_disconnected_eta_do_not_count_down():
    from conquest.farm_telemetry import farm_stats
    app={'experience_observed_at':100,'experience':{'level':55,'experience_candidate':250,
         'experience_required':1000,'xp_per_hour_candidate':500}}
    assert 'Next level Paused' in farm_stats(app,False,now=101)
    assert 'Estimating…' in farm_stats(app,True,now=120)
    app['state']='Reconnecting'
    assert 'Level —  ·  Next level —' in farm_stats(app,True,now=101)


def test_pickup_history_survives_reload_and_keeps_only_recent_rows_in_ui(tmp_path):
    path=tmp_path/'pickups.jsonl'
    history=PickupHistory(path,limit=2)
    for i in range(3):
        history.add({'uid':i,'type_id':1090000,'silver':True,'increase':25,'timestamp':1700000000+i})
    restored=PickupHistory(path,limit=2)
    assert [r['uid'] for r in restored.rows]==[1,2]
    assert len(path.read_text().splitlines())==3
    assert pickup_values(restored.rows[-1])[1:]==('Silver','+25')
    assert pickup_values(restored.rows[-1])[0].endswith(':22')


def test_history_rejects_unverified_zero_increase_and_skips_corrupt_line(tmp_path):
    path=tmp_path/'pickups.jsonl'
    path.write_text('incomplete record\n')
    history=PickupHistory(path)
    assert not history.rows
    with pytest.raises(ValueError):
        history.add({'increase':0})


def test_travel_activity_overrides_farming_off_during_town_input():
    text=activity_text({'phase':'restocking','updated_at':99,'activity':'Heading to Pharmacist to buy Painkiller'},
                       {'state':'Stopped'},{'enabled':False},None,now=100)
    assert text=='Heading to Pharmacist to buy Painkiller'


def test_reload_activity_overrides_restocks_and_intermediate_care_messages():
    for phase,app in [('reloading',{}),('restocking',{'reload_preparing':True})]:
        assert activity_text({'phase':phase,'updated_at':99,'activity':'Fly active; continuing combat'},
            app,{'enabled':False},None,now=100)=='Moving to a safe spot for app reload'


def test_dead_character_overrides_stale_travel_description():
    text=activity_text({'phase':'restocking','updated_at':99,'activity':'Heading to Blacksmith'},
                       {},{'enabled':False},{'dead_candidate':True},now=100)
    assert text.startswith('Dead')


def test_stale_route_status_does_not_claim_live_travel():
    assert activity_text({'phase':'restocking','updated_at':50,'activity':'Heading to Blacksmith'},
                         {},{'enabled':False},None,now=100)=='Waiting for a current route update'


def test_recent_healing_event_has_readable_activity():
    assert activity_text({'phase':'hunting'}, {'activity':'Using a healing potion','activity_at':99},
                         {'enabled':True,'execution_state':'farming'},None,now=100)=='Using a healing potion'


@pytest.mark.parametrize('free_slots',[1,2,3,4])
def test_a_single_pickup_does_not_trigger_a_town_trip(free_slots):
    assert not needs_town({'arrows':1000,'potions':10,'free_slots':free_slots},RouteLibrary().load('turtledove'))



def test_navigation_failure_is_not_reported_as_lost_focus():
    from conquest.farm_telemetry import pause_message
    assert pause_message('Waiting for a traversable patrol step').startswith('Navigation blocked')
    assert pause_message('Game lost focus; no input sent')=='Waiting for Conquer to regain focus'
    assert pause_message('Life state changed during observation').startswith('Waiting: Life state')
    assert pause_message(None)=='Paused with F11'


@pytest.mark.parametrize('phase',['changing_route','recovering_route','visiting_town'])
def test_city_transition_activity_does_not_look_like_farming_off(phase):
    assert activity_text(dict(phase=phase,updated_at=99,activity='Heading to PhoenixCity town'),
        {},{'enabled':False},None,now=100)=='Heading to PhoenixCity town'


def test_history_preserves_item_enhancement_and_quality(tmp_path):
    path=tmp_path/'pickups.jsonl';history=PickupHistory(path)
    history.add({'uid':7,'type_id':500089,'plus':2,'silver':False,'increase':1,
                 'timestamp':1700000000,'position':[341,441],'map_id':1011})
    row=PickupHistory(path).rows[0]
    assert row['plus']==2 and row['position']==[341,441]
    assert 'Super' in pickup_values(row)[1] and '+2' in pickup_values(row)[1]


def test_user_stop_clears_display_rate_without_deleting_kill_history(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from conquest.desktop_app import DesktopApp
    from conquest.control import FarmingControl
    monkeypatch.chdir(tmp_path)
    app=DesktopApp.__new__(DesktopApp);app.control=FarmingControl()
    app.control.update({'enabled':True});app.last={'kills_per_hour':1900,'kills':390}
    app.record=lambda **fields:app.last.update(fields)
    app.thread=None;app.runtime=None;app.memory_text=SimpleNamespace(set=lambda value:None)
    app.update_ids(False)
    assert app.last['kills_per_hour']==0 and app.last['kills']==390
    assert not app.control.snapshot()['enabled']
    assert (tmp_path/'.runtime/overnight.stop').read_text()=='Stopped by user: Farming Off'


def test_recovered_inventory_history_is_labeled_and_deduplicated_across_reload(tmp_path):
    path=tmp_path/'pickups.jsonl';history=PickupHistory(path)
    fields={'uid':7,'inventory_uid':7,'type_id':530013,'plus':2,'silver':False,'increase':1,
            'timestamp':1700000000,'source':'recovered_inventory','timestamp_kind':'verified_at'}
    row=history.add(fields)
    assert 'time unknown' in pickup_values(row)[1]
    restored=PickupHistory(path)
    assert restored.add(fields) is None
    assert len(path.read_text().splitlines())==1
    assert restored.rows[0]['timestamp_kind']=='verified_at'
