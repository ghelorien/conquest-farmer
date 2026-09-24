"""Read-only presentation of the existing engines' observations."""

import threading
import time


class CapabilityView:
    def __init__(self):
        self.value = {}
        self.key = None
        self.thread = None
        self.next_read = 0

    def refresh(self, observer, context):
        key = (
            (context.profile.id, repr(observer.adapter.identity))
            if observer and context
            else None
        )
        if self.key != key:
            self.key = key
            self.value = {}
            self.next_read = 0
        if (
            not key
            or (self.thread and self.thread.is_alive())
            or time.monotonic() < self.next_read
        ):
            return
        self.next_read = time.monotonic() + 5

        def read():
            result = {"profile_id": context.profile.id, "observed_at": time.monotonic()}
            try:
                if not observer.lock.acquire(timeout=0.1):
                    return
                try:
                    from conquest.client_attachment import verify_observer

                    verify_observer(context, observer)
                    from conquest.equipment import read_equipment

                    result["equipment"] = read_equipment(observer)
                    from conquest.combat_ranges import read_combat_ranges

                    try:
                        result["combat"] = read_combat_ranges(
                            observer, require_scatter=False
                        )
                    except ValueError:
                        result["combat_unavailable"] = (
                            "Learned Scatter / bow range unavailable through the existing engine"
                        )
                finally:
                    observer.lock.release()
            except (OSError, ValueError, AttributeError):
                result = {"unavailable": True}
            if self.key == key:
                self.value = result

        self.thread = threading.Thread(
            target=read, daemon=True, name="profile-capabilities"
        )
        self.thread.start()

    def text(self):
        value = self.value
        if not value or time.monotonic() - value.get("observed_at", 0) > 10:
            return "Observed level, class, skills and equipment: unavailable (no fresh memory observation)."
        equipment = value.get("equipment", {})
        combat = value.get("combat", {})
        return (
            f"Observed level: {equipment.get('level', 'unavailable')} · Class ID: {equipment.get('profession', 'unavailable')}\n"
            f"Equipment: {len(equipment.get('equipment', []))} observed slots\n"
            f"Learned Scatter: {combat.get('scatter', value.get('combat_unavailable', 'unavailable'))}\n"
            "Other learned skills: not exposed by the current engine. These observations are never exported."
        )
