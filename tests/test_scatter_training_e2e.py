"""At the level goal the farmer learns Scatter from ArcherGod and keeps leveling.

Before this, reaching level 23 parked the farmer in town, switched Farming
Off and logged "Learn Scatter at the Archer trainer": it stood idle until a
person came by. Alex: keep them constantly doing something; once they reach
Scatter level, farm with Scatter; at 23 "why are you not heading to archer god
for a new skill?"

ArcherGod stands in his own building (map 1004). Twin City portal 2 lands at
(51, 70), 18 tiles from him and off screen (live 16:14 the dialog click missed
the client), so the farmer walks up to (37, 55) first and reads a TwinCityGate
scroll back out afterwards.

Ways this can fail:
1. The farmer parks at the target level and stays idle (Farming Off).
2. An ArcherGod option that neither names Scatter nor leads to learning
   skills is pressed (a guessed choice), or an input field is used.
3. Success is claimed without the learned-skill vector proving Scatter.
4. An unexpected dialog or an input error inside the building leaves the
   farmer there: every visit must read the scroll back out. The dialog is
   opened from the landing, 18 tiles away, instead of beside him.
5. The farmer enters without a scroll to leave by (it cannot walk out).
6. A failing trainer is retried on every town visit forever.
7. The level goal stays active after the target level (gear trips forever).
8. The old town-grid scouting walk strays into the building (13:02 the same
   day) instead of using the known door.

The scenario walks the real level_goal.finish_in_town and scatter_training
against a simulated ArcherGod, writes ``scatter-training.json`` and must
produce the same bytes on a second run.
"""

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from conquest import level_goal, scatter_training

SCROLL = 1060020
LEARN = [
    {"kind": 0, "option": 255, "text": "I can teach archers their skills."},
    {"kind": 1, "option": 0, "text": "Learn skills"},
    {"kind": 1, "option": 1, "text": "Just passing by."},
]
SKILLS = [
    {"kind": 0, "option": 255, "text": "Which skill?"},
    {"kind": 1, "option": 0, "text": "Scatter"},
    {"kind": 1, "option": 1, "text": "Leave"},
]
SHOP = [
    {"kind": 0, "option": 255, "text": "Arrows for sale."},
    {"kind": 1, "option": 0, "text": "Buy arrows"},
    {"kind": 1, "option": 1, "text": "Just passing by."},
]
FIELD = [
    {"kind": 0, "option": 255, "text": "Say the password."},
    {"kind": 2, "option": 0, "text": ""},
    {"kind": 1, "option": 1, "text": "Learn skills"},
]


class Trainer:
    """Twin City, ArcherGod's building, his dialogs and the learned skills."""

    def __init__(
        self, pages, *, teaches=True, open_error=None, scrolls=1, silver=500, exits=True
    ):
        self.pages = list(pages)
        self.teaches = teaches
        self.open_error = open_error
        self.scrolls, self.silver, self.exits = scrolls, silver, exits
        self.map = 1002
        self.position = [466, 333]
        self.learned = False
        self.page = None
        self.actions = []
        self.events = []

    def town(self, action, **fields):
        self.actions.append([action, fields.get("option")])
        if action == "supplies":
            items = [{"type_id": SCROLL, "amount": 1}] * self.scrolls
            return {"items": items, "silver": self.silver}
        if action == "shop":
            return {"products": [{"type_id": SCROLL, "price": 200}]}
        if action == "buy":
            assert fields == {"vendor_type": 3, "type_id": SCROLL} and self.map == 1002
            self.scrolls, self.silver = self.scrolls + 1, self.silver - 200
            return {"bought": SCROLL, "amount": 1, "price": 200}
        if action.startswith("service-") and action != "service-close-panel":
            assert self.map == 1004, "ArcherGod is only in his building"
        if action == "service-locate":
            return {"npc": {"position": [33, 53]}}
        if action == "service-open":
            if self.open_error:
                raise ValueError(self.open_error)
            x, y = self.position
            if max(abs(x - 33), abs(y - 53)) > 8:
                raise ValueError("Point is outside the game client")  # 16:14
            self.page = 0
            return {"interacted": True}
        if action == "service-dialog":
            if self.page is None or self.page >= len(self.pages):
                raise ValueError("NPC dialog is absent")
            return {"records": self.pages[self.page], "viewport": [1024, 768]}
        if action == "service-select":
            assert fields["records"] == self.pages[self.page]
            if fields["option"] == "Scatter" and self.teaches:
                self.learned = True
            self.page += 1
            return {"interacted": True, "option": fields["option"]}
        return {"closed": True}

    def enter(self, loop, portal_id, expected_map):
        assert (portal_id, expected_map) == (2, 1004)
        assert self.scrolls, "never enter without the scroll that leads out"
        self.actions.append(["enter", portal_id])
        self.map, self.position = 1004, [51, 70]

    def read_scroll(self, loop):
        # The real return_to_town refuses inside Twin City town.
        x, y = self.position
        in_town = self.map == 1002 and 348 <= x <= 507 and 209 <= y <= 433
        if in_town or not self.scrolls or (self.map == 1004 and not self.exits):
            return False
        self.actions.append(["scroll", None])
        self.scrolls -= 1
        self.map, self.position = 1002, [430, 380]
        return True


@pytest.fixture
def world(tmp_path, monkeypatch):
    from conquest import world_travel

    monkeypatch.setattr(level_goal, "GOAL", tmp_path / "level-goal.json")
    monkeypatch.setattr(scatter_training, "STATE", tmp_path / "scatter-training.json")
    monkeypatch.setattr(world_travel, "travel_to_map", lambda loop, map_id: None)
    monkeypatch.setattr("conquest.banking.ensure_transport", lambda loop, minimum: None)
    monkeypatch.setattr(world_travel, "read_terrain", lambda root, map_id: NS(map_id=map_id))
    monkeypatch.setattr(level_goal, "back2classic", lambda: True)
    monkeypatch.setattr("conquest.dialog_geometry.scroll_direction", lambda *a: 0)
    clock = NS(now=1000.0)

    def sleep(seconds):
        clock.now += seconds

    monkeypatch.setattr(
        scatter_training,
        "time",
        NS(time=lambda: clock.now, monotonic=lambda: clock.now, sleep=sleep),
    )
    return NS(root=tmp_path, clock=clock)


def loop_for(trainer, monkeypatch):
    monkeypatch.setattr("conquest.world_travel.cross_portal", trainer.enter)
    monkeypatch.setattr("conquest.return_scroll.return_to_town", trainer.read_scroll)

    def travel(target, **kw):
        trainer.actions.append(["travel", [trainer.map, *target]])
        trainer.position[:] = list(target)

    return NS(
        phase="hunting",
        last_level=23,
        route=NS(restock_map_id=1002, restock_anchor=(466, 333)),
        town=trainer.town,
        travel=travel,
        living=lambda: {
            "embedded_controls": {
                "life": {
                    "map_id": trainer.map,
                    "position": list(trainer.position),
                    "dead_candidate": False,
                }
            }
        },
        record=lambda event, **fields: trainer.events.append(event),
    )


def finish(world, trainer, monkeypatch):
    world.root.joinpath("scatter-training.json").unlink(missing_ok=True)
    level_goal.start(23)
    monkeypatch.setattr(scatter_training, "learned", lambda loop: trainer.learned)
    loop = loop_for(trainer, monkeypatch)
    carried_on = level_goal.finish_in_town(loop) is False
    return {
        "carried_on": carried_on,
        "goal_active": bool(level_goal.goal()),
        "learned": trainer.learned,
        "pressed": [o for a, o in trainer.actions if a == "service-select"],
        "entered": any(a == "enter" for a, _ in trainer.actions),
        "walked_inside": [t for a, t in trainer.actions if a == "travel" and t[0] == 1004],
        "map_after": trainer.map,
        "scrolls_after": trainer.scrolls,
        "events": [
            e for e in trainer.events if e.startswith("scatter") or e.startswith("level_goal")
        ],
        "attempts": (
            json.loads(state.read_text())["attempts"]
            if (state := world.root.joinpath("scatter-training.json")).exists()
            else 0
        ),
    }


def scenario(world, monkeypatch, root):
    root = Path(root)
    root.mkdir(parents=True)
    rows = {
        # 1, 7: learn through a "Learn skills" step, read out, keep leveling.
        "learns": finish(world, Trainer([LEARN, SKILLS]), monkeypatch),
        # 5: the goal's return read the last scroll: buy one, then visit.
        "buys_exit_scroll": finish(world, Trainer([LEARN, SKILLS], scrolls=0), monkeypatch),
        # 5: no scroll and no silver for one: never enter, farming goes on.
        "no_way_out": finish(
            world, Trainer([LEARN, SKILLS], scrolls=0, silver=50), monkeypatch
        ),
        # 2, 4: a shop-like dialog offers nothing about learning.
        "unexpected_dialog": finish(world, Trainer([SHOP]), monkeypatch),
        # 2, 4: an input field is never used, even beside a learning option.
        "input_field": finish(world, Trainer([FIELD]), monkeypatch),
        # 3: Scatter pressed but memory never shows it: no success claimed.
        "not_taught": finish(world, Trainer([LEARN, SKILLS], teaches=False), monkeypatch),
        # 4: an input error inside the building still reads the scroll out.
        "open_error": finish(
            world, Trainer([LEARN], open_error="Travel closer to service NPC"), monkeypatch
        ),
        # 4: a scroll that fails inside is reported, never hidden.
        "stuck_inside": finish(world, Trainer([LEARN, SKILLS], exits=False), monkeypatch),
    }
    path = root / "scatter-training.json"
    path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_scatter_training_e2e(world, monkeypatch):
    first = scenario(world, monkeypatch, world.root / "pc-a")
    second = scenario(world, monkeypatch, world.root / "pc-b")
    assert first.read_bytes() == second.read_bytes()
    rows = json.loads(first.read_text(encoding="utf-8"))
    for name, row in rows.items():
        assert row["carried_on"] and not row["goal_active"], name  # 1, 7
        if name != "stuck_inside":
            assert row["map_after"] == 1002, name  # 4: always back out
    learns = rows["learns"]
    assert learns["learned"] and learns["pressed"] == ["Learn skills", "Scatter"]
    assert learns["entered"] and learns["scrolls_after"] == 0
    assert learns["walked_inside"] == [[1004, 37, 55]]  # 4: beside him, on screen
    assert "scatter_learned" in learns["events"] and "level_goal_reached" in learns["events"]
    bought = rows["buys_exit_scroll"]
    assert bought["learned"] and bought["entered"] and bought["scrolls_after"] == 0
    no_way_out = rows["no_way_out"]
    assert not no_way_out["entered"] and no_way_out["pressed"] == []
    assert "scatter_training_pending" in no_way_out["events"]
    assert rows["unexpected_dialog"]["pressed"] == []
    assert rows["input_field"]["pressed"] == []
    assert "scatter_training_failed" in rows["input_field"]["events"]
    not_taught = rows["not_taught"]
    assert not not_taught["learned"] and "scatter_learned" not in not_taught["events"]
    assert "scatter_training_failed" in not_taught["events"]
    assert rows["open_error"]["pressed"] == []
    assert "scatter_training_failed" in rows["open_error"]["events"]
    stuck = rows["stuck_inside"]
    assert stuck["map_after"] == 1004 and "scatter_training_failed" in stuck["events"]


def test_retries_are_spaced_and_bounded(world, monkeypatch):
    # 6
    loop = NS(last_level=23)
    assert scatter_training.due(loop)
    scatter_training.write_json(
        scatter_training.STATE, {"attempts": 1, "last_attempt": world.clock.now}
    )
    assert not scatter_training.due(loop)  # tried moments ago
    world.clock.now += scatter_training.RETRY_SECONDS
    assert scatter_training.due(loop)
    scatter_training.write_json(
        scatter_training.STATE, {"attempts": scatter_training.MAX_ATTEMPTS, "last_attempt": 0}
    )
    assert not scatter_training.due(loop)  # bounded
    scatter_training.write_json(scatter_training.STATE, {"learned_at": 1})
    assert not scatter_training.due(loop)  # already learned
    scatter_training.write_json(scatter_training.STATE, {})
    assert not scatter_training.due(NS(last_level=22))  # below the level
    level_goal.start(23)
    assert not scatter_training.due(loop)  # the goal's own finish handles it
    level_goal.stop()
    monkeypatch.setattr(level_goal, "back2classic", lambda: False)
    assert not scatter_training.due(loop)  # America farmers are untouched


def test_a_character_can_opt_out_of_the_visit(world, monkeypatch):
    # 8: no scouting walk exists any more; opting out never enters either.
    assert not hasattr(scatter_training, "scout")
    scatter_training.write_json(scatter_training.STATE, {"auto_visit": False})
    trainer = Trainer([LEARN, SKILLS])
    level_goal.start(23)
    monkeypatch.setattr(scatter_training, "learned", lambda loop: trainer.learned)
    assert level_goal.finish_in_town(loop_for(trainer, monkeypatch)) is False
    assert not level_goal.goal() and not trainer.learned
    assert not any(a == "enter" for a, _ in trainer.actions)
    assert "scatter_training_manual" in trainer.events
    assert not scatter_training.due(NS(last_level=23))


def test_a_visit_due_in_the_field_goes_to_town_first(world, monkeypatch):
    # The route restarted in the field at level 23 (a deploy): scroll to
    # town, buy the scroll that leads out, visit, read out, keep leveling.
    trainer = Trainer([LEARN, SKILLS], scrolls=1, silver=500)
    trainer.position = [300, 605]  # the Apparition field
    monkeypatch.setattr(scatter_training, "learned", lambda loop: trainer.learned)
    loop = loop_for(trainer, monkeypatch)
    assert scatter_training.due(loop)
    assert scatter_training.attempt(loop) is True
    order = [a for a, _ in trainer.actions if a in ("scroll", "buy", "enter")]
    assert order == ["scroll", "buy", "enter", "scroll"]
    assert trainer.map == 1002 and trainer.scrolls == 0 and trainer.silver == 300


def test_a_farmer_left_in_the_building_reads_its_way_back_to_twin_city(monkeypatch):
    from conquest import world_travel

    life = {"map_id": 1004, "position": [37, 55]}
    reads = []

    def read_scroll(loop):
        reads.append(life["map_id"])
        life.update(map_id=1002, position=[430, 380])
        return True

    monkeypatch.setattr("conquest.return_scroll.return_to_town", read_scroll)
    monkeypatch.setattr(world_travel, "read_terrain", lambda root, map_id: NS(map_id=map_id))
    loop = NS(living=lambda: {"embedded_controls": {"life": dict(life)}})
    world_travel.travel_to_map(loop, 1002)
    assert reads == [1004] and loop.terrain.map_id == 1002
    # No scroll in the building: reported, not hidden.
    life.update(map_id=1004)
    monkeypatch.setattr("conquest.return_scroll.return_to_town", lambda loop: False)
    with pytest.raises(ValueError, match="needed to leave this building"):
        world_travel.travel_to_map(loop, 1002)


def test_option_choice_never_guesses():
    assert scatter_training.choose(SKILLS, []) == "Scatter"
    assert scatter_training.choose(LEARN, []) == "Learn skills"
    assert scatter_training.choose(LEARN, ["Learn skills"]) is None
    assert scatter_training.choose(SHOP, []) is None
    assert scatter_training.choose(FIELD, []) is None
    confirm = [
        {"kind": 0, "option": 255, "text": "Learn Scatter for 1000 silver?"},
        {"kind": 1, "option": 0, "text": "Yes."},
        {"kind": 1, "option": 1, "text": "No, thanks."},
    ]
    # A confirmation is taken only straight after choosing Scatter.
    assert scatter_training.choose(confirm, ["Learn skills", "Scatter"]) == "Yes."
    assert scatter_training.choose(confirm, ["Learn skills"]) is None
    assert scatter_training.choose(confirm, []) is None
