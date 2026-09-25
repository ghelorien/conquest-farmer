"""One bounded rule for qualifying kill-counter increments.

run_trial reads the character kill counter once per combat loop. An increment
between two uninterrupted reads is a verified kill batch only when it is at most

    min(HARD_MAX, max(base, ceil(elapsed * MAX_KILLS_PER_SECOND)))

where ``base`` is 32 for Scatter (right-click) and 10 for single shots, and
``elapsed`` is the time since the previous counter read. ``elapsed`` is None
when the interval included a pause or wait; only the fixed base applies then.

Constants, from the Parasite journal (2026-09-07..18, 562,125 verified kills in
141,667 kill_verified events, all qualified under the old fixed cap):

* MAX_KILLS_PER_SECOND = 5. Best verified rates were 2.58/s over an hour
  (9,276 kills), 4.23/s over five minutes, 6.08/s over one minute, 7.43/s over
  30 s and 11.3/s over 10 s. At 5/s the allowance for any interval longer than
  6.4 s stays below the verified peak for a window of that length, so the rule
  admits nothing the journal has not already shown to be real. It is roughly
  twice the densest hour and covers 6,000-10,000 kills/h routes whose reads are
  delayed. A 50-kill increment needs at least 10 s between reads.
* HARD_MAX = 128 (four full Scatter base caps, reached after 25.6 s). Longer
  gaps between reads are themselves abnormal (fresh observations expire after
  0.85 s), so larger increments stay unverified gaps whatever the interval.

Readers re-check each kill_verified payload with verified_kill_count. Events
written before this rule carry no elapsed/base/limit fields; they keep the old
reader rule (1 <= count <= 32).
"""

import math

SCATTER_BASE_LIMIT = 32
SINGLE_BASE_LIMIT = 10
MAX_KILLS_PER_SECOND = 5
HARD_MAX = 128
BASE_LIMITS = (SINGLE_BASE_LIMIT, SCATTER_BASE_LIMIT)
# Pre-rule events had no evidence fields and were qualified up to 32.
HISTORICAL_LIMIT = SCATTER_BASE_LIMIT
EVIDENCE_FIELDS = ("counter_elapsed_seconds", "increment_base", "increment_limit")


def base_limit(attack_button):
    return SCATTER_BASE_LIMIT if attack_button == "right" else SINGLE_BASE_LIMIT


def _valid_elapsed(elapsed):
    return elapsed is None or (
        type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0
    )


def allowed_increment(elapsed, base):
    """Largest increment that may be verified after ``elapsed`` seconds."""
    if type(base) is not int or base not in BASE_LIMITS:
        raise ValueError("Unknown kill increment base")
    if not _valid_elapsed(elapsed):
        raise ValueError("Invalid kill counter interval")
    if elapsed is None:
        return base
    return min(HARD_MAX, max(base, math.ceil(elapsed * MAX_KILLS_PER_SECOND)))


def evidence(elapsed, base):
    """Fields recorded with every kill_verified/gap event for re-checking."""
    return {
        "counter_elapsed_seconds": elapsed,
        "increment_base": base,
        "increment_limit": allowed_increment(elapsed, base),
    }


def verified_kill_count(payload):
    """The qualified count of a kill_verified payload, or None if it is invalid."""
    if not isinstance(payload, dict):
        return None
    count = payload.get("count")
    if type(count) is not int or not 1 <= count <= HARD_MAX:
        return None
    present = [field in payload for field in EVIDENCE_FIELDS]
    if not any(present):
        return count if count <= HISTORICAL_LIMIT else None
    if not all(present):
        return None
    try:
        limit = allowed_increment(
            payload["counter_elapsed_seconds"], payload["increment_base"]
        )
    except ValueError:
        return None
    recorded = payload["increment_limit"]
    if type(recorded) is not int or recorded != limit or count > limit:
        return None
    return count
