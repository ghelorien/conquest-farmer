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


@pytest.mark.parametrize('jump_scatter,isolated,expected', [
    (True, True, 'right'),
    (True, False, 'right'),
    (False, True, 'left'),
])
def test_force_jump_scatter_precedes_isolated_and_is_disabled_with_missing_skill(jump_scatter, isolated, expected):
    from types import SimpleNamespace
    from conquest.trial import scatter_attack_mode, ammunition_per_attack
    config=SimpleNamespace(jump_scatter=jump_scatter,attack_button='left')
    strategy=SimpleNamespace(button=lambda name:'left')
    assert scatter_attack_mode(config,CombatSpeed(force_jump_scatter=True),strategy,isolated,'Bandit')==expected
    assert ammunition_per_attack(config)==(3 if jump_scatter else 1)


def test_force_jump_scatter_is_enabled_for_all_profiles():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'profiles'/'farmers'
    assert load_combat_speed('Parasite',root).force_jump_scatter is True
    assert load_combat_speed('Kilhiam',root).force_jump_scatter is True
    assert load_combat_speed('UnconfiguredFarmer',root).force_jump_scatter is True


def test_force_jump_scatter_can_be_disabled_per_profile(tmp_path):
    (tmp_path/'First.yaml').write_text('character: First\ncombat_speed:\n  force_jump_scatter: false\n')
    assert load_combat_speed('First',tmp_path).force_jump_scatter is False


def test_force_jump_scatter_applies_without_adaptive_strategy():
    from types import SimpleNamespace
    from conquest.trial import scatter_attack_mode
    config=SimpleNamespace(jump_scatter=True,attack_button='left')
    assert scatter_attack_mode(config,CombatSpeed(force_jump_scatter=True),None,True,'Bandit')=='right'


def test_non_forced_mode_preserves_configured_button_without_adaptive_strategy():
    from types import SimpleNamespace
    from conquest.trial import scatter_attack_mode
    config=SimpleNamespace(jump_scatter=True,attack_button='right')
    speed=CombatSpeed(force_jump_scatter=False)
    assert scatter_attack_mode(config,speed,None,True,'Bandit')=='right'
    strategy=SimpleNamespace(button=lambda name:'right')
    assert scatter_attack_mode(config,speed,strategy,True,'Bandit')=='left'


def test_missing_scatter_disables_jump_route_setting():
    from types import SimpleNamespace
    from conquest.combat_ranges import route_combat_settings
    route=SimpleNamespace(jump_scatter=True,attack_range_tiles=15)
    settings=route_combat_settings(route,{'bow':{'range':12},'scatter':None})
    assert settings['jump_scatter'] is False and settings['attack_button']=='left'


@pytest.mark.parametrize('option', ['cross_region_scatter','regional_search_expansion',
                                    'cluster_lookahead','counter_gap_recovery'])
def test_saved_search_and_counter_options_apply_only_to_parasite(option):
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'profiles'/'farmers'
    assert getattr(load_combat_speed('Parasite',root),option) is True
    assert getattr(load_combat_speed('Kilhiam',root),option) is False
    assert getattr(load_combat_speed('UnconfiguredFarmer',root),option) is False
