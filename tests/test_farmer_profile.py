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


def test_receipt_cost_is_isolated_from_other_farmers_and_attack_stock(tmp_path):
    from conquest.trial import ammunition_per_attack
    from types import SimpleNamespace
    (tmp_path/'First.yaml').write_text('character: First\ncombat_speed:\n  scatter_receipt_arrows: 2\n')
    speed=load_combat_speed('First',tmp_path)
    assert speed.scatter_receipt_arrows==2
    assert load_combat_speed('Second',tmp_path).scatter_receipt_arrows==3
    assert ammunition_per_attack(SimpleNamespace(attack_button='right',combat_speed=speed))==3
    with pytest.raises(ValueError):CombatSpeed(scatter_receipt_arrows=1)


@pytest.mark.parametrize('option', ['cross_region_scatter','regional_search_expansion',
                                    'cluster_lookahead','counter_gap_recovery'])
def test_saved_search_and_counter_options_apply_only_to_parasite(option):
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'profiles'/'farmers'
    assert getattr(load_combat_speed('Parasite',root),option) is True
    assert getattr(load_combat_speed('Kilhiam',root),option) is False
    assert getattr(load_combat_speed('UnconfiguredFarmer',root),option) is False
