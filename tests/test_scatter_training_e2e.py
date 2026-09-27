"""At the level goal the farmer learns Scatter in town and keeps leveling.

Before this, reaching level 23 parked the farmer in town, switched Farming
Off and logged "Learn Scatter at the Archer trainer": it stood idle until a
person came by. Alex: keep them constantly doing something; once they reach
Scatter level, farm with Scatter.

Ways this can fail:
1. The farmer parks at the target level and stays idle (Farming Off).
2. An ArcherGod option that neither names Scatter nor leads to learning
   skills is pressed (a guessed choice), or an input field is used.
3. Success is claimed without the learned-skill vector proving Scatter.
4. An unknown trainer position, an unexpected dialog or an input error
   raises out of the route and stops farming.
5. A failing trainer is retried on every town visit forever.
6. The level goal stays active after the target level (gear trips forever).

The scenario walks the real level_goal.finish_in_town and scatter_training
against a simulated ArcherGod, writes ``scatter-training.json`` and must
produce the same bytes on a second run.
"""

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from conquest import level_goal, scatter_training

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
    """ArcherGod's dialogs and the learned-skill vector."""

    def __init__(
        self,
        pages,
        *,
        teaches=True,
        locatable=True,
        open_error=None,
        hidden_until_near=False,
    ):
        self.pages = list(pages)
        self.teaches = teaches
        self.locatable = locatable and not hidden_until_near
        self.hidden_until_near = hidden_until_near
        self.open_error = open_error
        self.learned = False
        self.page = None
        self.actions = []
        self.events = []

    def town(self, action, **fields):
        self.actions.append([action, fields.get("option")])
        if action == "service-locate":
            if not self.locatable:
                raise ValueError("One memory-identified ArcherGod is required in the scene")
            return {"npc": {"position": [420, 300]}}
        if action == "service-open":
            if self.open_error:
                raise ValueError(self.open_error)
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


@pytest.fixture
def world(tmp_path, monkeypatch):
    from conquest import world_travel

    monkeypatch.setattr(level_goal, "GOAL", tmp_path / "level-goal.json")
    monkeypatch.setattr(scatter_training, "STATE", tmp_path / "scatter-training.json")
    monkeypatch.setattr(scatter_training, "TRAINERS", tmp_path / "archer-trainers.json")
    monkeypatch.setattr(world_travel, "travel_to_map", lambda loop, map_id: None)
    monkeypatch.setattr("conquest.return_scroll.return_to_town", lambda loop: False)
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


def loop_for(trainer):
    position = [466, 333]

    def travel(target, **kw):
        trainer.actions.append(["travel", list(target)])
        position[:] = list(target)
        # The simulated trainer stands at (420, 300); it enters the scene
        # when the walk ends within scene range of it.
        if trainer.hidden_until_near and max(
            abs(position[0] - 420), abs(position[1] - 300)
        ) <= 18:
            trainer.locatable = True

    loop = NS(
        phase="hunting",
        last_level=23,
        route=NS(restock_map_id=1002, restock_anchor=(466, 333)),
        town=trainer.town,
        travel=travel,
        terrain=NS(walkable=lambda p: True),
        living=lambda: {
            "embedded_controls": {
                "life": {"map_id": 1002, "position": list(position), "dead_candidate": False}
            }
        },
        record=lambda event, **fields: trainer.events.append(event),
    )
    return loop


def finish(world, trainer, monkeypatch, *, surveyed=True):
    world.root.joinpath("archer-trainers.json").write_text(
        json.dumps({"1002": {"approach": [421, 302]}} if surveyed else {}),
        encoding="utf-8",
    )
    world.root.joinpath("scatter-training.json").unlink(missing_ok=True)
    level_goal.start(23)
    monkeypatch.setattr(scatter_training, "learned", lambda loop: trainer.learned)
    loop = loop_for(trainer)
    carried_on = level_goal.finish_in_town(loop) is False
    return {
        "carried_on": carried_on,
        "goal_active": bool(level_goal.goal()),
        "learned": trainer.learned,
        "pressed": [o for a, o in trainer.actions if a == "service-select"],
        "walked_to": [t for a, t in trainer.actions if a == "travel"][-1:],
        "events": [e for e in trainer.events if e.startswith("scatter") or e.startswith("level_goal")],
        "attempts": json.loads(world.root.joinpath("scatter-training.json").read_text())["attempts"],
    }


def scenario(world, monkeypatch, root):
    root = Path(root)
    root.mkdir(parents=True)
    rows = {
        # 1, 6: learn through a "Learn skills" step, then keep leveling.
        "learns": finish(world, Trainer([LEARN, SKILLS]), monkeypatch),
        # No surveyed tile: the town grid walk finds ArcherGod, then it learns.
        "scouts": finish(
            world,
            Trainer([LEARN, SKILLS], hidden_until_near=True),
            monkeypatch,
            surveyed=False,
        ),
        # 4: not surveyed and nowhere in town: recorded, farming goes on.
        "unknown_trainer": finish(
            world, Trainer([LEARN, SKILLS], locatable=False), monkeypatch, surveyed=False
        ),
        # 2: a shop-like dialog offers nothing about learning: nothing pressed.
        "unexpected_dialog": finish(world, Trainer([SHOP]), monkeypatch),
        # 2: an input field is never used, even beside a learning option.
        "input_field": finish(world, Trainer([FIELD]), monkeypatch),
        # 3: Scatter pressed but memory never shows it: no success claimed.
        "not_taught": finish(world, Trainer([LEARN, SKILLS], teaches=False), monkeypatch),
        # 4: an input error inside the visit does not stop the route.
        "open_error": finish(
            world, Trainer([LEARN], open_error="Travel closer to service NPC"), monkeypatch
        ),
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
        assert row["carried_on"] and not row["goal_active"], name  # 1, 4, 6
    learns = rows["learns"]
    assert learns["learned"] and learns["pressed"] == ["Learn skills", "Scatter"]
    assert learns["walked_to"] == [[421, 302]]
    assert "scatter_learned" in learns["events"] and "level_goal_reached" in learns["events"]
    scouts = rows["scouts"]
    assert scouts["learned"] and scouts["pressed"] == ["Learn skills", "Scatter"]
    assert "scatter_trainer_found" in scouts["events"]
    assert rows["unknown_trainer"]["pressed"] == []
    assert "scatter_training_pending" in rows["unknown_trainer"]["events"]
    assert rows["unexpected_dialog"]["pressed"] == []
    assert rows["input_field"]["pressed"] == []
    assert "scatter_training_failed" in rows["input_field"]["events"]
    not_taught = rows["not_taught"]
    assert not not_taught["learned"] and "scatter_learned" not in not_taught["events"]
    assert "scatter_training_failed" in not_taught["events"]
    assert rows["open_error"]["pressed"] == []
    assert "scatter_training_failed" in rows["open_error"]["events"]


def test_retries_are_spaced_and_bounded(world, monkeypatch):
    # 5
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
