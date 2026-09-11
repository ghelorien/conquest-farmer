import pytest
from conquest import leveling_routes as lr


def test_every_level_has_one_bracket_and_boundaries_advance():
    rows=lr.presets()
    for level in range(1,141):
        assert len([r for r in rows if r['levels'][0]<=level<=r['levels'][1]])==1
    assert lr.bracket(26)['id']=='poltergeist'
    assert lr.bracket(27)['id']=='wingedsnake'
    assert lr.desired_route(26)[0].id=='poltergeist'


@pytest.mark.parametrize('level',[True,0,141,27.5])
def test_invalid_levels_do_not_select_a_route(level):
    with pytest.raises(ValueError):lr.bracket(level)


def test_level_read_rejects_stale_dead_or_changed_character(monkeypatch):
    monkeypatch.setattr(lr.time,'time',lambda:10)
    state={'embedded_controls':{'observed_at':10,'life':{'object_address':1000,'dead_candidate':False}}}
    def request(info,operation,body=None):
        if operation=='sample':return {'fields':[{'value':[27]}]}
        return state
    monkeypatch.setattr(lr,'request',request)
    assert lr.read_level(None,state)==27
    state['embedded_controls']['observed_at']=8
    with pytest.raises(ValueError):lr.read_level(None,state)
    state['embedded_controls']['observed_at']=10
    state['embedded_controls']['life']['dead_candidate']=True
    with pytest.raises(ValueError):lr.read_level(None,state)


@pytest.mark.parametrize('entry',lr.presets(),ids=lambda entry:entry['id'])
def test_every_bracket_checks_both_ends_and_saved_monster(entry):
    for level in entry['levels']:
        route,actual=lr.desired_route(level)
        assert actual['id']==entry['id']
        if route:
            assert route.recommended_levels==tuple(entry['levels'])
            assert route.monster_type_ids==tuple(m['type_id'] for m in lr.monster_family(entry['monster_type_id']))
        else:
            assert entry['status']=='needs_survey'
    if entry['levels'][1]<140:
        assert lr.bracket(entry['levels'][1]+1)['id']!=entry['id']
