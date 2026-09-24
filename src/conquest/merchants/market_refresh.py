"""One app-owned market collector shared by both merchants; never sends game input."""

import json
from pathlib import Path
import threading
import time
from conquest.merchants.journal import CHARACTERS, character_name
from conquest.merchants.market import MarketSnapshot
from conquest.merchants.collector import collect_market


class MarketRefreshWorker:
    def __init__(self, journal, stop, path, *, collect=collect_market, clock=time.time):
        self.journal, self.stop, self.path = journal, stop, Path(path)
        self.collect, self.clock = collect, clock
        self.lock = threading.RLock()

    def state(self, character):
        return self.journal.get(character, "market_refresh", {})

    def request(self, character, request_id):
        character = character_name(character)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 120:
            raise ValueError("Invalid market refresh request")
        with self.lock:
            old = self.state(character)
            if old.get("pending") or old.get("request_id") == request_id:
                return old
            state = {
                "request_id": request_id,
                "pending": True,
                "phase": "queued",
                "requested_at": self.clock(),
                "retry_at": 0,
            }
            self.journal.set(character, "market_refresh", state)
            return state

    def snapshot(self):
        try:
            return MarketSnapshot(
                json.loads(self.path.read_text(encoding="utf-8")), now=self.clock()
            )
        except (OSError, ValueError):
            return None

    def step(self):
        market = self.snapshot()
        # Pending requests survive restart, including the old Scan now button.
        for character in CHARACTERS:
            scan = self.journal.get(character, "scan", {})
            if scan.get("pending") and market is None:
                self.request(
                    character,
                    "scan:" + scan["request_id"] + ":" + str(int(self.clock() // 120)),
                )
        with self.lock:
            waiting = {
                c: self.state(c) for c in CHARACTERS if self.state(c).get("pending")
            }
            if not waiting or any(
                v.get("retry_at", 0) > self.clock() for v in waiting.values()
            ):
                return
            for c, state in waiting.items():
                state = {
                    **state,
                    "phase": "fetching",
                    "started_at": self.clock(),
                    "error": None,
                }
                waiting[c] = state
                self.journal.set(c, "market_refresh", state)
        try:
            data = self.collect(destination=str(self.path), stop=self.stop)
            MarketSnapshot(data, now=self.clock())
        except Exception as error:
            # Browser exceptions can contain session data; never expose them.
            import traceback

            diagnostic = {
                "type": type(error).__name__,
                "frames": [
                    {
                        "file": Path(f.filename).name,
                        "function": f.name,
                        "line": f.lineno,
                    }
                    for f in traceback.extract_tb(error.__traceback__)
                ],
            }
            note = (
                str(error)
                if isinstance(error, ValueError)
                else "Market download failed."
            )
            with self.lock:
                for c, state in waiting.items():
                    self.journal.set(
                        c,
                        "market_refresh",
                        {
                            **state,
                            "phase": "failed",
                            "error": note
                            + " Retrying in 60 seconds; prices unchanged.",
                            "retry_at": self.clock() + 60,
                            "diagnostic": diagnostic,
                        },
                    )
                    self.journal.event(
                        c,
                        "market_refresh_failed",
                        note="Market download failed; retry scheduled",
                    )
            return
        with self.lock:
            for c, state in waiting.items():
                self.journal.set(
                    c,
                    "market_refresh",
                    {
                        **state,
                        "pending": False,
                        "phase": "ready",
                        "completed_at": self.clock(),
                        "observed_at": data["observed_at"],
                        "items": data["total"],
                        "retry_at": 0,
                    },
                )
                self.journal.event(c, "market_refreshed", items=data["total"])

    def run(self):
        while not self.stop.is_set():
            self.step()
            self.stop.wait(1)
