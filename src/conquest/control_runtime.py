"""Live memory observations for persistent UI controls and exact-ID targeting."""
import threading
import time
from dataclasses import asdict
from pathlib import Path

import yaml

from conquest.addressing import WorkerPointerSession
from conquest.memory_entities import EntityLayout, MemoryEntityReader
from conquest.memory_health import HealthLayout, HealthWorkerSession, MemoryHealthReader
from conquest.kill_loot import KillLootCycle


class ControlRuntime:
    def __init__(self, control, worker_info, health_profile, entity_profile, character,
                 *, observer=None, dispatcher=None, pickup_dispatcher=None, recovery=None, clock=time.monotonic,
                 disable_on_close=True):
        self.control = control
        self.worker_info = worker_info
        self.health_profile, self.entity_profile, self.character = health_profile, entity_profile, character
        self.observer, self.dispatcher = observer or self._observe, dispatcher
        self.pickup_dispatcher, self.clock = pickup_dispatcher, clock
        self.recovery = recovery
        self.disable_on_close=bool(disable_on_close)
        self.external_execution = False
        self.external_failure = None
        self.encounter = KillLootCycle()
        self.encounter_revision = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.latest = {"monsters": [], "observations_available": False,
                       "observation_note": "Connecting to memory worker"}
        self.health_reader = self.entity_reader = None

    def _connect(self):
        health = HealthLayout.model_validate(yaml.safe_load(Path(self.health_profile).read_text()))
        entities = EntityLayout.model_validate(yaml.safe_load(Path(self.entity_profile).read_text()))
        self.health_reader = MemoryHealthReader(HealthWorkerSession(self.worker_info, health.player.expected_sha256), health, self.character)
        self.entity_reader = MemoryEntityReader(WorkerPointerSession(self.worker_info, entities.expected_sha256), entities)

    def _observe(self):
        if self.health_reader is None:
            self._connect()
        entities = self.entity_reader.read()
        health = self.health_reader.read()
        worker = self.health_reader.session.request("health")
        window = worker["window"]
        return {"monsters": [asdict(entity) for entity in entities.monsters],
                "observations_available": True, "observation_note": "Live memory candidates",
                "hp_candidate": health.current_hp, "max_hp_candidate": health.max_hp,
                "focused": window["foreground"] == window["hwnd"],
                "minimized": window["minimized"], "observed_at": time.time(),
                "blockers": ["Current HP and monster life state need live validation",
                             "Background attacks have not been verified"],
                "read_only_worker": worker.get("read_only", False)}

    def step(self):
        try:
            data = self.observer()
            intent = self.control.snapshot()
            if self.external_execution:
                with self.control.lock:
                    self.control.resolved_ids=sorted(m['entity_id'] for m in data['monsters']
                        if m['entity_id'] in intent['target_ids'] or m['type_id'] in intent['target_type_ids'])
                with self.lock:
                    self.latest=data
                return
            if self.recovery:
                recovery=self.recovery.step(data.get('life'),data.get('focused',False))
                if recovery:
                    self.encounter.reset()
                    self.control.publish(intent['revision'],recovery['state'],recovery['note'])
                    with self.lock:
                        self.latest={**data,'recovery':recovery}
                    return
            if self.external_failure and intent['enabled'] and self.external_failure[0]==intent['revision']:
                self.control.publish(intent['revision'],'runner_stopped','Farm runner stopped: '+self.external_failure[1])
                with self.lock:
                    self.latest=data
                return
            if not intent['enabled'] or intent['revision'] != self.encounter_revision:
                self.encounter.reset()
                self.encounter_revision = intent['revision']
            ids = [monster["entity_id"] for monster in data["monsters"]]
            blockers = list(data.get("blockers", []))
            if self.dispatcher is None:
                blockers.append("Attack execution is not connected")
            if self.pickup_dispatcher is None:
                blockers.append('Loot pickup execution is not connected')
            loot = data.get('loot_observation')
            try:
                self.encounter.validate(loot, self.clock())
            except ValueError as error:
                blockers.append(str(error))
            current = self.control.reconcile(focused=data.get("focused", False),
                minimized=data.get("minimized", False), observed_ids=ids, blockers=blockers,
                observed_types={m['entity_id']:m.get('type_id') for m in data['monsters']},
                pending_target=self.encounter.target)
            if current["execution_state"] == "ready":
                action = self.encounter.plan(data['monsters'], current['resolved_target_ids'], loot, self.clock())
                if action:
                    def execute():
                        try:
                            # Recheck freshness after acquiring the input gate.
                            issued = self.clock()
                            self.encounter.validate(loot, issued)
                            if action.kind == 'attack':
                                self.dispatcher(action.monster, data, current['input_mode'])
                            else:
                                self.pickup_dispatcher(action.drop, data, current['input_mode'])
                            self.encounter.committed(action, loot, issued)
                        except Exception:
                            self.encounter.fault('Input outcome is uncertain; holding next target')
                            raise
                    self.control.dispatch(current['revision'], action.monster['entity_id'], execute,
                                          type_id=action.monster.get('type_id'))
                self.control.publish(current['revision'], self.encounter.state, self.encounter.note)
            # Keep typed observations private; the UI/HTTP snapshot stays JSON.
            data = {key:value for key,value in data.items() if key != 'loot_observation'}
            data['encounter'] = self.encounter.snapshot()
            with self.lock:
                self.latest = data
        except Exception as error:
            self.health_reader = self.entity_reader = None
            current = self.control.snapshot()
            self.control.publish(current["revision"], "waiting_for_observation", str(error))
            with self.lock:
                self.latest = {"monsters": [], "observations_available": False,
                               "observation_note": str(error)}

    def snapshot(self):
        with self.lock:
            result = dict(self.latest)
        result["control"] = self.control.snapshot()
        result['external_execution'] = self.external_execution
        return result

    def _run(self):
        while not self.stop.is_set():
            self.step()
            self.stop.wait(.15)  # Life/recovery checks should not wait on a slow combat cadence.

    def start(self):
        self.thread = threading.Thread(target=self._run, name="farming-controls", daemon=True)
        self.thread.start()

    def close(self):
        if self.disable_on_close:
            self.control.update({"enabled": False})
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
