from types import SimpleNamespace as NS
import json
import numpy as np
import pytest
from conquest.runback_monitor import RunbackMonitor,escape_step
from conquest.navigation import TerrainMap


def make(tmp_path):
    now=[0.]
    monitor=RunbackMonitor((30,10),1011,'test',clock=lambda:now[0],wall=lambda:now[0],output=tmp_path)
    def sample(t,x=10,hp=100,paused=False,world=1011):
        now[0]=t
        monitor.observe({'map_id':world,'position':[x,10],'current_hp':hp,'max_hp':100,
                         'dead_candidate':hp==0,'timestamp':t},paused=paused)
    return monitor,sample


def test_stationary_damage_becomes_urgent_before_normal_stall(tmp_path):
    m,s=make(tmp_path);s(0);s(.2,hp=95);s(.6,hp=85)
    assert m.urgent and m.stalls==0
    s(1.1,hp=80);s(1.3,hp=70)
    assert m.stalls==1 and m.damage==30
    s(1.4,x=12,hp=70)
    assert not m.urgent


def test_manual_motion_and_focus_gaps_do_not_inflate_bot_speed_or_stalls(tmp_path):
    m,s=make(tmp_path);s(0);s(.5,x=20,paused=True);s(10,x=25,paused=True);s(10.5,x=26)
    assert m.distance==0 and m.stalls==0 and not m.urgent
    s(11,x=28)
    assert m.distance==2 and m.active_seconds==.5


def test_death_is_counted_once_and_finish_preserves_the_failure(tmp_path):
    m,s=make(tmp_path);s(0);s(.5,hp=0);s(1,hp=0);s(1.5,hp=100)
    m.finish('arrived');m.finish('arrived')
    rows=[json.loads(x) for x in (tmp_path/'history.jsonl').read_text().splitlines()]
    assert len(rows)==1 and rows[0]['deaths']==1 and rows[0]['minimum_hp_percent']==0


def test_map_change_does_not_count_teleport_as_travel(tmp_path):
    m,s=make(tmp_path);s(0);s(.5,x=1000,world=1036)
    assert m.finished and m.distance==0
    assert json.loads((tmp_path/'test.json').read_text())['result']=='map_changed'


def test_escape_moves_away_from_attackers_and_never_crosses_walls():
    blocked=np.zeros((40,40),dtype=bool);blocked[20,21]=True
    terrain=TerrainMap(1011,40,40,blocked,'',(),())
    enemies=[{'position':[21,20],'alive':True,'current_hp':10}]
    point=escape_step(terrain,(20,20),(30,20),(518,396),enemies)
    assert point is not None and point[0]<=20
    assert max(abs(a-b) for a,b in zip(point,(20,20)))>=8
    assert escape_step(terrain,(20,20),(30,20),(518,396),[]) is None


def test_recording_failure_cannot_interrupt_movement(tmp_path):
    file=tmp_path/'not-a-directory';file.write_text('x')
    m=RunbackMonitor((1,1),1011,'test',output=file)
    m.finish('arrived')
    assert m.io_error and m.finished
