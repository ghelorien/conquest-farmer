from types import SimpleNamespace
import struct
import pytest
from conquest import town_corner as tc

@pytest.mark.parametrize('change',['character','map','hash','source','dead','blocked',None])
def test_only_pinned_verified_corner_can_issue_one_walking_input(monkeypatch,change):
    now=[10.];calls=[];events=[];position=[195,227]
    monkeypatch.setattr(tc.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(tc.time,'sleep',lambda t:now.__setitem__(0,now[0]+t))
    terrain=SimpleNamespace(map_id=1002 if change=='map' else 1011,
        source_sha256='other' if change=='hash' else tc.TERRAIN_SHA256,
        walkable=lambda p:change!='blocked',travel_path=lambda *a:[tc.DESTINATION,(227,243)])
    def living():
        now[0]+=.01
        life=dict(character='Other' if change=='character' else 'Parasite',map_id=terrain.map_id,
            position=[194,227] if change=='source' else list(position),dead_candidate=change=='dead',
            object_address=0x600000,max_hp=1274,timestamp=now[0])
        return {'embedded_controls':{'life':life,'manual_mouse':False,'control':{'enabled':False}},
                'window':{'client_size':[1416,907]}}
    loop=SimpleNamespace(terrain=terrain,living=living,info='test',town=lambda *a:{'closed_panel':None},
        check_stop=lambda:None,record=lambda *a,**kw:events.append(a[0]))
    class Session:
        def __init__(self,*a):pass
        def read_block(self,*a):return struct.pack('<II8xii',195,227,700,434)
    monkeypatch.setattr(tc,'HealthWorkerSession',Session)
    monkeypatch.setattr(tc,'resolve_player',lambda *a:{'name':0x700000,'max_hp':0x700100})
    def send(info,operation,body):
        assert operation=='foreground-click' and body['button']=='left' and not body['control']
        assert body['point']==[700,466] and body['require_foreground']
        calls.append(body);position[:]=tc.DESTINATION
        return {}
    monkeypatch.setattr(tc,'request',send)
    assert tc.recover_corner(loop,(227,243))==(change is None)
    assert len(calls)==(1 if change is None else 0)
    if change is None:assert events==['town_corner_recovery','town_corner_recovered']
