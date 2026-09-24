"""Thread-safe user intent for the local farming controls.

Focus and observation failures change execution status, never the on/off switch.
Restarting the service starts off; saved target selections remain available.
"""

import json
import os
import threading
from pathlib import Path


def target_ids(values):
    if not isinstance(values, list) or len(values) > 128:
        raise ValueError("Select at most 128 monster IDs")
    if any(type(value) is not int or not 1 <= value <= 0xFFFFFFFF for value in values):
        raise ValueError("Monster IDs must be integers from 1 to 4294967295")
    return sorted(set(values))


class FarmingControl:
    def __init__(self, settings_path=None):
        self.lock = threading.RLock()
        self.path = Path(settings_path) if settings_path is not None else None
        self.enabled, self.ids, self.mode, self.revision = False, [], "background", 0
        self.type_ids, self.resolved_ids = [], []
        self.execution_state = "off"
        self.note = "Farming is off"
        if self.path is not None and self.path.exists():
            if self.path.stat().st_size > 8192:
                raise ValueError("Control settings exceed 8 KiB")
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.ids = target_ids(saved.get("target_ids", []))
            self.type_ids = target_ids(saved.get("target_type_ids", []))
            self.mode = self._mode(saved.get("input_mode", "background"))

    @staticmethod
    def _mode(value):
        if value not in ("foreground", "background"):
            raise ValueError("Choose foreground or background input")
        return value

    def snapshot(self):
        with self.lock:
            return {
                "enabled": self.enabled,
                "target_ids": list(self.ids),
                "target_type_ids": list(self.type_ids),
                "resolved_target_ids": list(self.resolved_ids),
                "input_mode": self.mode,
                "revision": self.revision,
                "execution_state": self.execution_state,
                "note": self.note,
            }

    def update(self, body):
        if (
            not isinstance(body, dict)
            or not body
            or set(body) - {"enabled", "target_ids", "target_type_ids", "input_mode"}
        ):
            raise ValueError("Unsupported control update")
        with self.lock:
            enabled = body.get("enabled", self.enabled)
            if type(enabled) is not bool:
                raise ValueError("On/off must be a boolean")
            ids = target_ids(body["target_ids"]) if "target_ids" in body else self.ids
            types = (
                target_ids(body["target_type_ids"])
                if "target_type_ids" in body
                else self.type_ids
            )
            mode = self._mode(body.get("input_mode", self.mode))
            # Persist selections before changing in-memory intent. Never save an
            # enabled flag that could cause unattended actions after a restart.
            if self.path is not None and (
                ids != self.ids or types != self.type_ids or mode != self.mode
            ):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_name(self.path.name + ".tmp")
                temporary.write_text(
                    json.dumps(
                        {
                            "target_ids": ids,
                            "target_type_ids": types,
                            "input_mode": mode,
                        }
                    ),
                    encoding="utf-8",
                )
                os.replace(temporary, self.path)
            if (enabled, ids, types, mode) != (
                self.enabled,
                self.ids,
                self.type_ids,
                self.mode,
            ):
                self.revision += 1
                self.resolved_ids = []
            self.enabled, self.ids, self.mode = enabled, list(ids), mode
            self.type_ids = list(types)
            self.execution_state = "checking" if enabled else "off"
            self.note = (
                "Checking selected targets and input readiness"
                if enabled
                else "Farming is off"
            )
            return self.snapshot()

    def finish_session(self, revision, reason):
        """An old runner may not erase a newer On/target selection."""
        with self.lock:
            if reason == "emergency_stop" or (
                reason == "requested_stop" and self.revision == revision
            ):
                self.update({"enabled": False})
            return self.enabled and reason in ("control_changed", "requested_stop")

    def publish(self, revision, state, note):
        with self.lock:
            if revision == self.revision and self.enabled:
                self.execution_state, self.note = state, note

    def dispatch(self, revision, entity_id, callback, *, type_id=None):
        """Serialize stopping/changing IDs with the final input dispatch."""
        with self.lock:
            if (
                not self.enabled
                or revision != self.revision
                or not (entity_id in self.ids or type_id in self.type_ids)
            ):
                return False
            callback()
            return True

    def dispatch_recovery(self, revision, callback):
        """Stopping or changing selection also cancels pending recovery input."""
        with self.lock:
            if not self.enabled or revision != self.revision:
                return False
            callback()
            return True

    def reconcile(
        self,
        *,
        focused,
        minimized,
        observed_ids,
        blockers=(),
        observed_types=None,
        pending_target=None,
    ):
        with self.lock:
            return self._reconcile(
                focused=focused,
                minimized=minimized,
                observed_ids=observed_ids,
                blockers=blockers,
                observed_types=observed_types or {},
                pending_target=pending_target,
            )

    def _reconcile(
        self,
        *,
        focused,
        minimized,
        observed_ids,
        blockers,
        observed_types,
        pending_target,
    ):
        self.resolved_ids = sorted(
            {
                uid
                for uid in observed_ids
                if uid in self.ids or observed_types.get(uid) in self.type_ids
            }
        )
        current = self.snapshot()
        if not current["enabled"]:
            return current
        if not current["target_ids"] and not current["target_type_ids"]:
            state, note = (
                "waiting_for_targets",
                "Select a monster group or individual ID",
            )
        elif blockers:
            state, note = "blocked", "; ".join(blockers)
        elif minimized:
            state, note = (
                "waiting_for_window",
                "Restore the game window; your switch stays on",
            )
        elif current["input_mode"] == "foreground" and not focused:
            state, note = (
                "waiting_for_focus",
                "Waiting for game focus; your switch stays on",
            )
        elif not self.resolved_ids and not (
            pending_target
            and (
                pending_target["entity_id"] in self.ids
                or pending_target.get("type_id") in self.type_ids
            )
        ):
            state, note = (
                "waiting_for_targets",
                "Waiting for a selected monster ID to appear",
            )
        else:
            state, note = "ready", "Ready to attack a selected monster"
        self.publish(current["revision"], state, note)
        return self.snapshot()
