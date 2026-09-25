"""Fair, never-blocking admission to the single 1078 refill check.

``runtime.listing1078_lock`` still admits every refill_1078.step, so two
checks never run at once. This gate only adds refusals: the merchant that
held the lock last yields its next turn while another merchant was refused
within ``WAIT_TTL``. A check that is slow or fails on every tick therefore
cannot starve the other merchant (live 2026-09-25 14:18-14:53). The merchant
holding the live listing grant never yields its bounded window.

The holder, its stage inside step and each refused merchant's
``blocked_since`` are kept for a read-only status. Nothing here waits.
"""

import threading
import time

# A refused merchant that has not retried for this long (its observer stopped
# or stalled) no longer makes the last holder yield.
WAIT_TTL = 10.0
REFUSED = "other_refill_check_active"
YIELDED = "refill_turn_yielded"

_CREATE = threading.Lock()


class RefillTurns:
    def __init__(self, lock):
        self.lock = lock  # the runtime's listing1078_lock, the only admission
        self._meta = threading.Lock()
        self.holder = None
        self.last_holder = None
        self.last_released_at = None
        self.waiting = {}
        self.counts = {}

    def _count(self, name, key):
        row = self.counts.setdefault(name, {"admitted": 0, "refused": 0, "yielded": 0})
        row[key] += 1

    def enter(self, character, *, grant_character=None):
        """Admit (return None) or refuse at once with a diagnostic dict."""
        name = str(character)
        wall, now = time.time(), time.monotonic()
        with self._meta:
            rival = any(
                other != name and now - row["attempt_monotonic"] <= WAIT_TTL
                for other, row in self.waiting.items()
            )
            if rival and self.last_holder == name and grant_character != name:
                blocker = YIELDED
            elif self.lock.acquire(blocking=False):
                try:
                    self.waiting.pop(name, None)
                    self._count(name, "admitted")
                    self.holder = {
                        "character": name,
                        "thread": threading.current_thread().name,
                        "acquired_at": wall,
                        "stage": "acquired",
                        "stage_at": wall,
                    }
                except BaseException:
                    self.holder = None
                    self.lock.release()
                    raise
                return None
            else:
                blocker = REFUSED
            row = self.waiting.setdefault(name, {"blocked_since": wall})
            row.update(last_attempt_at=wall, attempt_monotonic=now, blocker=blocker)
            self._count(name, "yielded" if blocker == YIELDED else "refused")
            holder = self.holder
            return {
                "blocker": blocker,
                "blocked_since": row["blocked_since"],
                "lock_holder": None
                if holder is None
                else {
                    key: holder[key] for key in ("character", "stage", "acquired_at")
                },
            }

    def stage(self, character, label):
        with self._meta:
            holder = self.holder
            if holder is not None and holder["character"] == str(character):
                holder.update(stage=label, stage_at=time.time())

    def leave(self, character):
        with self._meta:
            self.holder = None
            self.last_holder = str(character)
            self.last_released_at = time.time()
            self.lock.release()

    def status(self):
        with self._meta:
            return {
                "holder": dict(self.holder) if self.holder else None,
                "last_holder": self.last_holder,
                "last_released_at": self.last_released_at,
                "waiting": {
                    name: {
                        key: row[key]
                        for key in ("blocked_since", "last_attempt_at", "blocker")
                    }
                    for name, row in self.waiting.items()
                },
                "counts": {name: dict(row) for name, row in self.counts.items()},
                "wait_ttl_seconds": WAIT_TTL,
            }


def turns(runtime):
    """The runtime's gate; created once for runtimes built without one."""
    gate = getattr(runtime, "refill1078_turns", None)
    if gate is None:
        with _CREATE:
            gate = getattr(runtime, "refill1078_turns", None)
            if gate is None:
                gate = RefillTurns(runtime.listing1078_lock)
                runtime.refill1078_turns = gate
    return gate
