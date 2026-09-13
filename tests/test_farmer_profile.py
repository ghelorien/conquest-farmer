import pytest
from conquest.farmer_profile import CombatSpeed,load_combat_speed

def test_each_farmer_has_independent_speed_parameters(tmp_path):
    (tmp_path/'First.yaml').write_text('character: First\ncombat_speed:\n  jump_arrival_seconds: 0.3\n')
    (tmp_path/'Second.yaml').write_text('character: Second\ncombat_speed:\n  jump_arrival_seconds: 0.7\n')
    assert load_combat_speed('First',tmp_path).jump_arrival_seconds==.3
    assert load_combat_speed('Second',tmp_path).jump_arrival_seconds==.7
    assert load_combat_speed('Third',tmp_path)==CombatSpeed()

def test_profile_cannot_be_applied_to_another_farmer(tmp_path):
    (tmp_path/'First.yaml').write_text('character: Second\n')
    with pytest.raises(ValueError,match='another character'):load_combat_speed('First',tmp_path)

@pytest.mark.parametrize('name',['../Other','a/b','a\\b','..'])
def test_profile_name_cannot_escape_directory(tmp_path,name):
    with pytest.raises(ValueError):load_combat_speed(name,tmp_path)
