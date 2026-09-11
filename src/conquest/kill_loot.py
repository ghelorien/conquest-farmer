"""Keep one monster encounter open until its attributed drops are collected.

The observer must supply confirmed, player-attributed kills and ground-item
identities. Neither XP changes, disappearance, nor proximity establishes that
link. The current candidate memory reader cannot supply these observations yet.
"""
from dataclasses import dataclass

from conquest.memory_inventory import InventorySnapshot


@dataclass(frozen=True)
class ConfirmedKill:
    event_id: str
    monster_id: int
    object_address: int
    timestamp: float


@dataclass(frozen=True)
class GroundLoot:
    uid: int
    type_id: int
    kill_event_id: str
    point: tuple[int, int]
    amount: int = 1
    silver: bool = False

    def count(self, inventory):
        return inventory.silver if self.silver else inventory.count(self.type_id)


@dataclass(frozen=True)
class LootObservation:
    # Includes process creation identity and map; changes cancel old attribution.
    context: str
    timestamp: float
    drops: tuple[GroundLoot, ...]
    kills: tuple[ConfirmedKill, ...]
    inventory: InventorySnapshot


@dataclass(frozen=True)
class EncounterAction:
    kind: str
    monster: dict
    drop: GroundLoot | None = None


class KillLootCycle:
    def __init__(self, *, spawn_wait=3.0, pickup_timeout=2.0, pickup_limit=2,
                 attack_interval=1.0, stale_after=.5):
        self.spawn_wait, self.pickup_timeout = spawn_wait, pickup_timeout
        self.pickup_limit, self.attack_interval = pickup_limit, attack_interval
        self.stale_after = stale_after
        self.reset()

    def reset(self):
        self.target = self.context = self.kill = self.pending = None
        self.started = self.last_attack = self.quiet_since = self.last_sample = None
        self.preexisting = set()
        self.claimed, self.collected, self.attempts = {}, set(), {}
        self.state, self.note = 'idle', 'Waiting for a selected monster'

    def fault(self, note):
        self.state, self.note = 'blocked', note

    def snapshot(self):
        return {'state': self.state, 'note': self.note,
                'monster_id': self.target['entity_id'] if self.target else None,
                'kill_event_id': self.kill.event_id if self.kill else None,
                'collected_drop_ids': sorted(self.collected),
                'pending_drop_ids': sorted(set(self.claimed) - self.collected)}

    def validate(self, observation, now):
        if not isinstance(observation, LootObservation):
            raise ValueError('Monster-to-loot observations are not connected')
        if (not observation.context or not 0 <= now - observation.timestamp <= self.stale_after
                or not 0 <= observation.timestamp - observation.inventory.timestamp <= self.stale_after):
            raise ValueError('Loot or inventory observation is stale')
        if len({drop.uid for drop in observation.drops}) != len(observation.drops):
            raise ValueError('Duplicate ground-item IDs')
        if any(drop.uid <= 0 or drop.amount <= 0 for drop in observation.drops):
            raise ValueError('Invalid ground-item identity or quantity')
        if self.context is not None and self.context != observation.context:
            self.fault('Client or map changed during the encounter; switch Off to reset')
        if self.last_sample is not None and observation.timestamp < self.last_sample:
            raise ValueError('Loot observations arrived out of order')

    def plan(self, monsters, selected_ids, observation, now):
        self.validate(observation, now)
        if self.state == 'blocked':
            return None
        # A gap in observation must not count as time spent watching for drops.
        if self.last_sample is not None and observation.timestamp - self.last_sample > 2:
            self.quiet_since = observation.timestamp
        self.last_sample = observation.timestamp
        if self.target is None:
            target = next((m for m in monsters if m['entity_id'] in selected_ids
                           and m.get('alive') is True and m.get('object_address')), None)
            if target is None:
                self.state, self.note = 'waiting_for_target', 'Waiting for a verified living target'
                return None
            return EncounterAction('attack', dict(target))

        if self.kill is None:
            matches = [k for k in observation.kills if k.monster_id == self.target['entity_id']
                       and k.object_address == self.target['object_address']
                       and self.started <= k.timestamp <= observation.timestamp and k.event_id]
            if len({k.event_id for k in matches}) > 1:
                self.fault('Ambiguous kill identity; switch Off to reset')
                return None
            if matches:
                self.kill = matches[0]
                self.quiet_since = observation.timestamp
                self.state = 'waiting_for_drops'
            else:
                target = next((m for m in monsters if m['entity_id'] == self.target['entity_id']
                               and m.get('object_address') == self.target['object_address']
                               and m.get('alive') is True), None)
                self.state, self.note = 'attacking', f"Waiting for confirmed kill of ID {self.target['entity_id']}"
                if target and now - self.last_attack >= self.attack_interval:
                    return EncounterAction('attack', dict(target))
                return None

        drops = {d.uid: d for d in observation.drops}
        for drop in observation.drops:
            if drop.kill_event_id != self.kill.event_id or drop.uid in self.preexisting:
                continue
            previous = self.claimed.get(drop.uid)
            if previous and (previous.type_id, previous.amount, previous.silver) != (drop.type_id, drop.amount, drop.silver):
                self.fault('Ground-item identity changed during pickup')
                return None
            if not previous:
                self.quiet_since = observation.timestamp
            self.claimed[drop.uid] = drop

        if self.pending:
            drop, before, issued = self.pending
            # A vanished label alone is insufficient, as is inventory growth
            # while the same ground item still exists. Stacked items are valid.
            if (drop.uid not in drops and observation.inventory.timestamp > issued
                    and drop.count(observation.inventory) >= before + drop.amount):
                self.collected.add(drop.uid)
                self.pending = None
                self.quiet_since = observation.timestamp
            elif now - issued < self.pickup_timeout:
                self.state, self.note = 'waiting_for_pickup', f'Confirming pickup of drop ID {drop.uid}'
                return None
            else:
                self.pending = None
                if drop.uid not in drops or self.attempts[drop.uid] >= self.pickup_limit:
                    self.fault(f'Pickup of drop ID {drop.uid} was not confirmed; holding next target')
                    return None

        remaining = sorted(set(self.claimed) - self.collected)
        if remaining:
            uid = remaining[0]
            if uid not in drops or drops[uid].kill_event_id != self.kill.event_id:
                self.fault(f'Drop ID {uid} disappeared without a confirmed pickup; holding next target')
                return None
            drop = drops[uid]
            inventory = observation.inventory
            if (not drop.silver and len(inventory.items) >= inventory.capacity
                    and not any(i.type_id == drop.type_id and i.limit-i.amount >= drop.amount for i in inventory.items)):
                self.fault('Inventory is full; holding loot and the next target')
                return None
            self.state, self.note = 'looting', f"Picking up drop ID {uid} from monster ID {self.target['entity_id']}"
            return EncounterAction('pickup', self.target, drop)

        self.state, self.note = 'waiting_for_drops', f"Checking drops from monster ID {self.target['entity_id']}"
        if observation.timestamp - self.quiet_since >= self.spawn_wait:
            self.reset()
        return None

    def committed(self, action, observation, now):
        """Only record an attempt after the enabled/revision dispatch gate."""
        if action.kind == 'attack':
            if self.target is None:
                self.target, self.context = action.monster, observation.context
                self.started = now
                self.preexisting = {drop.uid for drop in observation.drops}
            self.last_attack = now
            self.state, self.note = 'attacking', f"Attacking monster ID {self.target['entity_id']}"
        else:
            drop = action.drop
            self.attempts[drop.uid] = self.attempts.get(drop.uid, 0) + 1
            self.pending = (drop, drop.count(observation.inventory), now)
            self.state, self.note = 'waiting_for_pickup', f'Confirming pickup of drop ID {drop.uid}'
