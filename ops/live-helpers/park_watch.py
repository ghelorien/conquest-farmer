"""Catch the farmer in Phoenix after a town batch and park it in Market.

The route's town batch walks into Phoenix and then returns to hunting (09-26:
its 240 s parking budget is spent by the walk, so it gives up in the city).
Waiting for merchant_town_batch_finished lets park_market.py start from the
city instead of walking in from the field. park_market.main() re-checks every
gate itself; a gate failure raises before anything is paused, so it is simply
retried for a short window. Anything after pausing is never retried.
Run elevated. Usage: python park_watch.py [wait-seconds]
"""

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import park_market  # noqa: E402  (sets the pinned release environment)

EVENTS = park_market.CHAR / "reports/overnight/events.jsonl"
LOG = HERE / "park-watch.log"
WAIT = float(sys.argv[1]) if len(sys.argv) > 1 else 1500
# The farmer is next to Phoenix right after either event: a town batch ends
# in the city, and a town visit completes at its first kill outside the gate.
TRIGGERS = tuple(
    sys.argv[2].split(",")
    if len(sys.argv) > 2
    else ("merchant_town_batch_finished", "merchant_work_finished")
)


def log(text):
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(time.strftime("%H:%M:%S ") + text + "\n")


def batch_finished(since, offset):
    with EVENTS.open("rb") as stream:
        stream.seek(offset)
        data = stream.read()
    found = False
    for line in data.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("time", 0) >= since and row.get("event") in TRIGGERS:
            found = True
    return found, offset + len(data)


def main():
    started = time.time()
    offset = max(0, EVENTS.stat().st_size - 200_000)
    log(f"watching for the town batch to finish (up to {WAIT:.0f} s)")
    while True:
        found, offset = batch_finished(started, offset)
        if found:
            break
        if time.time() - started > WAIT:
            log("no town batch finish seen; nothing done")
            return
        time.sleep(0.5)
    log("town batch finished; trying Market parking while in the city")
    until = time.monotonic() + 90
    while True:
        try:
            park_market.main()
            log(f"parked in Market: {park_market.STATE}")
            return
        except Exception as error:
            if park_market.STATE.get("phase"):
                restored = park_market.restore_prior_intent()
                park_market.mark(
                    "failed",
                    error_type=type(error).__name__,
                    error=str(error),
                    restored=restored,
                )
                log(f"parking failed after pausing: {error}; {restored}")
                raise
            if time.monotonic() > until:
                log(f"gates never passed: {error}")
                return
            time.sleep(0.5)


if __name__ == "__main__":
    main()
