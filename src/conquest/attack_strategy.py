"""Remember Scatter breakpoints using fresh, per-entity HP observations."""

import hashlib
import json
from pathlib import Path


def equipment_context(state, scatter):
    # Stack UID, ammunition count and durability change during ordinary farming.
    # Only character/skill levels and equipped combat properties reset a trial.
    keys = (
        "type_id",
        "plus",
        "gem1",
        "gem2",
        "attack_min",
        "attack_max",
        "defense",
        "dodge",
    )
    gear = {
        slot: {key: item.get(key) for key in keys}
        for slot, item in state["equipment"].items()
    }
    payload = {
        "level": state["level"],
        "profession": state["profession"],
        "gear": gear,
        "scatter": scatter,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class AttackStrategy:
    """A living target after three damaging casts warrants single attacks.

    Missing entities, global kill counts and unconfirmed clicks provide no
    per-target evidence. HP decreases are temporal feedback, not a claim that
    other players could not have contributed damage.
    """

    def __init__(self, path=None, notify=lambda *args: None):
        self.path = Path(path) if path else None
        self.notify = notify
        self.context = None
        self.saved = {}
        self.history = {}
        self.pending = None
        if self.path:
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if saved.get("version") == 1 and isinstance(saved.get("groups"), dict):
                    self.saved = saved["groups"]
            except (OSError, ValueError, AttributeError):
                pass

    def set_context(self, context):
        if context != self.context:
            previous = self.context
            self.context = context
            self.history.clear()
            self.pending = None
            if previous is not None:
                self.notify(
                    "attack_strategy_recheck",
                    {
                        "activity": "Level or equipment changed; checking Scatter breakpoint again"
                    },
                )

    def button(self, name):
        entry = self.saved.get(name, {})
        return (
            "left"
            if self.context
            and entry.get("context") == self.context
            and entry.get("button") == "left"
            else "right"
        )

    @staticmethod
    def key(target):
        if (
            not target.entity_id
            or not target.object_address
            or type(target.current_hp) is not int
            or target.current_hp <= 0
        ):
            return None
        return target.name, target.entity_id, target.object_address

    def issued(self, target, button, now):
        key = self.key(target)
        self.pending = (
            (key, target.current_hp, now)
            if key and button == "right" and self.context
            else None
        )

    def observe(self, targets, now):
        self.history = {
            key: value
            for key, value in self.history.items()
            if 0 <= now - value[1] <= 15
        }
        if not self.pending:
            return
        key, before, issued = self.pending
        age = now - issued
        if not 0 <= age <= 2:
            self.pending = None
            return
        target = next((t for t in targets if self.key(t) == key), None)
        if target is None:
            return
        if target.current_hp > before:
            self.history.pop(key, None)
            self.pending = None
            return
        if age < 0.35 or target.current_hp == before:
            return
        count = self.history.get(key, (0, now))[0] + 1
        self.history[key] = (count, now)
        self.pending = None
        if count < 3 or self.button(target.name) == "left":
            return
        self.saved[target.name] = {
            "context": self.context,
            "button": "left",
            "reason": "survived_three_damaging_scatters",
            "entity_id": target.entity_id,
            "object_address": target.object_address,
            "remaining_hp": target.current_hp,
        }
        if self.path:
            try:
                from conquest.discord_notify import write_json

                write_json(self.path, {"version": 1, "groups": self.saved})
            except OSError:
                pass  # Keep the in-memory decision even if persistence is unavailable.
        self.notify(
            "attack_strategy_changed",
            {
                "monster": target.name,
                "button": "left",
                "entity_id": target.entity_id,
                "remaining_hp": target.current_hp,
                "activity": f"Using single attacks: {target.name} survived three Scatters",
            },
        )


def nearby_group_size(targets, position, scatter_range):
    # Count only fresh selected living entities within the actual skill range.
    return len(
        {
            (t.entity_id, t.object_address)
            for t in targets
            if t.entity_id
            and t.object_address
            and t.world_position is not None
            and type(t.current_hp) is int
            and t.current_hp > 0
            and max(abs(a - b) for a, b in zip(t.world_position, position))
            <= scatter_range
        }
    )
