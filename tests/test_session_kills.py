import json
from contextlib import closing
import sqlite3

import pytest

from conquest.session_kills import SessionKills


def append(path, at, count, total=1, event='kill_verified'):
    with closing(sqlite3.connect(path / 'trial.sqlite3')) as db:
        db.execute('CREATE TABLE IF NOT EXISTS events(time REAL,event TEXT,payload TEXT)')
        db.execute('INSERT INTO events VALUES (?,?,?)',
                   (at, event, json.dumps(dict(count=count, total=total))))
        db.commit()


def test_banking_and_runner_restart_preserve_kills_and_include_downtime(tmp_path):
    now=[100]
    stats=SessionKills(tmp_path,clock=lambda:now[0]);stats.begin()
    append(tmp_path,110,10,total=10)
    now[0]=120
    assert stats.refresh()['kills']==10
    now[0]=200  # a town trip with no kills
    stats.begin()  # internal combat runner restart
    append(tmp_path,201,5,total=5)
    now[0]=220
    assert stats.refresh()['kills']==15
    assert stats.snapshot()['kills_per_hour']==450
    assert stats.refresh()['kills']==15


def test_reload_reconciles_committed_events_without_loss_or_double_count(tmp_path):
    stats=SessionKills(tmp_path,clock=lambda:100);stats.begin()
    append(tmp_path,110,10);stats.refresh()
    append(tmp_path,120,3)  # committed before the old UI processes it
    restored=SessionKills(tmp_path,clock=lambda:160)
    restored.begin()
    assert restored.refresh()['kills']==13
    assert restored.refresh()['kills_per_hour']==780


def test_explicit_stop_zeros_rate_then_new_start_resets_session(tmp_path):
    now=[100];stats=SessionKills(tmp_path,clock=lambda:now[0]);stats.begin()
    append(tmp_path,110,4)
    now[0]=115;stats.stop()
    append(tmp_path,116,8)  # old runner finished after Stop
    assert stats.refresh()['kills']==4
    assert stats.snapshot()['kills_per_hour']==0
    now[0]=120;stats.begin()
    append(tmp_path,121,2)
    now[0]=180
    assert stats.refresh()['kills']==2
    assert stats.snapshot()['kills_per_hour']==120


@pytest.mark.parametrize('count',[0,-1,33,True,2.5,None])
def test_ambiguous_increments_never_become_verified_kills(tmp_path,count):
    stats=SessionKills(tmp_path,clock=lambda:100);stats.begin()
    append(tmp_path,101,3);stats.refresh()
    append(tmp_path,102,count)
    result=stats.refresh()
    assert result['kills']==3 and result['kills_per_hour'] is None
    assert result['kill_metrics_note']


def test_database_busy_defers_metrics_without_dropping_events(tmp_path):
    stats=SessionKills(tmp_path,clock=lambda:100);stats.begin()
    append(tmp_path,101,3)
    with closing(sqlite3.connect(tmp_path/'trial.sqlite3')) as db:
        db.execute('BEGIN EXCLUSIVE')
        assert stats.refresh()['kills_per_hour'] is None
    assert stats.refresh()['kills']==3
    assert stats.snapshot()['kill_metrics_note'] is None


def test_corrupt_checkpoint_does_not_silently_reset_session(tmp_path):
    (tmp_path/'kill-session.json').write_text('{broken')
    stats=SessionKills(tmp_path)
    with pytest.raises(ValueError):stats.begin()
    assert stats.snapshot()['kills_per_hour'] is None


def test_missing_log_keeps_count_and_marks_rate_unavailable(tmp_path):
    stats=SessionKills(tmp_path,clock=lambda:100);stats.begin()
    append(tmp_path,101,3);stats.refresh()
    (tmp_path/'trial.sqlite3').unlink()
    assert stats.refresh()['kills']==3
    assert stats.snapshot()['kills_per_hour'] is None


def test_ui_keeps_whole_session_rate_while_combat_is_off_for_town():
    from conquest.farm_telemetry import farm_stats
    value=dict(kills=10,kills_per_hour=600,kill_session_active=True)
    assert '600/h' in farm_stats(value,False)
    value['kill_session_active']=False
    assert '0/h' in farm_stats(value,False)
