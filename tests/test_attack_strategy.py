from dataclasses import replace
from conquest.attack_strategy import AttackStrategy, equipment_context
from conquest.vision import Target


def monster(hp=100, uid=10, address=1000):
    return Target("Poltergeist", 600, 400, 1, uid, address, (10, 10), hp)


def damaging_cast(strategy, before, after, now):
    strategy.issued(before, "right", now)
    strategy.observe([after], now + 0.8)


def test_surviving_third_damaging_cast_selects_left_and_persists(tmp_path):
    path = tmp_path / "decision.json"
    events = []
    s = AttackStrategy(path, lambda *args: events.append(args))
    s.set_context("level27-bow")
    for index in range(3):
        assert s.button("Poltergeist") == "right"
        damaging_cast(s, monster(100 - index * 20), monster(80 - index * 20), index)
    assert s.button("Poltergeist") == "left"
    assert s.button("Pheasant") == "right"
    assert events[-1][1]["remaining_hp"] == 40
    restored = AttackStrategy(path)
    restored.set_context("level27-bow")
    assert restored.button("Poltergeist") == "left"
    restored.set_context("level28-bow")
    assert restored.button("Poltergeist") == "right"


def test_misses_missing_targets_recycled_ids_and_early_samples_do_not_count():
    s = AttackStrategy()
    s.set_context("gear")
    for index in range(6):
        s.issued(monster(), "right", index)
        s.observe([monster(50, address=2000)], index + 0.8)
        s.observe([], index + 0.9)
        s.observe([monster()], index + 1.0)
    assert s.button("Poltergeist") == "right" and not s.history
    s.observe([monster(40)], 20)
    assert not s.history
    s.issued(monster(), "right", 21)
    s.observe([monster(40)], 21.1)
    assert not s.history


def test_kill_or_left_click_cannot_count_as_survived_scatter():
    s = AttackStrategy()
    s.set_context("gear")
    for i in range(4):
        damaging_cast(s, monster(), monster(0), i)
        s.issued(monster(), "left", i + 0.1)
        s.observe([monster(20)], i + 0.9)
    assert not s.history and s.button("Poltergeist") == "right"


def test_healing_reset_and_old_evidence_expiration():
    s = AttackStrategy()
    s.set_context("gear")
    damaging_cast(s, monster(), monster(90), 0)
    damaging_cast(s, monster(90), monster(80), 1)
    damaging_cast(s, monster(80), monster(100), 2)
    damaging_cast(s, monster(), monster(90), 3)
    assert s.button("Poltergeist") == "right"
    damaging_cast(s, monster(90), monster(80), 30)
    assert s.button("Poltergeist") == "right"


def test_gear_identity_ignores_consumption_but_detects_plus_gems_and_level():
    state = {
        "level": 27,
        "profession": 41,
        "equipment": {
            "bow": {
                "type_id": 500035,
                "uid": 100,
                "plus": 0,
                "gem1": 0,
                "gem2": 0,
                "attack_max": 30,
            },
            "arrows": {"type_id": 1050000, "uid": 2, "amount": 100},
        },
    }
    context = lambda: equipment_context(state, {"level": 0, "range": 8})
    before = context()
    state["equipment"]["arrows"]["amount"] = 1
    state["equipment"]["bow"]["uid"] = 200
    assert context() == before
    state["equipment"]["bow"]["plus"] = 1
    assert context() != before
    upgraded = context()
    state["level"] = 28
    assert context() != upgraded


def test_arrow_tier_change_retests_scatter_but_stack_consumption_does_not():
    state = {
        "level": 32,
        "profession": 41,
        "equipment": {
            "arrows": {
                "type_id": 1050000,
                "attack_min": 10,
                "attack_max": 10,
                "uid": 1,
                "amount": 10,
            }
        },
    }
    first = equipment_context(state, None)
    state["equipment"]["arrows"].update(uid=2, amount=200)
    assert equipment_context(state, None) == first
    state["equipment"]["arrows"].update(type_id=1050001, attack_min=50, attack_max=50)
    assert equipment_context(state, None) != first


def test_group_size_excludes_dead_far_and_duplicate_entities():
    from conquest.attack_strategy import nearby_group_size

    near = monster()
    second = monster(uid=11, address=2000)
    far = replace(monster(uid=12), world_position=(30, 30))
    dead = monster(hp=0, uid=13)
    assert nearby_group_size([near, near, far, dead], (10, 10), 8) == 1
    assert nearby_group_size([near, second, far, dead], (10, 10), 8) == 2
