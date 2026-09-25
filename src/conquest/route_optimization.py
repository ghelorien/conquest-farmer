"""Persistent area discovery and fair, memory-verified route comparisons.

The scheduled optimization monitor conducts the tests. This module never sends
game input or changes farming intent; discovering an area only queues work.
"""

from conquest import kill_increment
from conquest.character_context import state_path
from pathlib import Path
import time

from conquest.discord_notify import read_json, write_json

POLICY = Path("profiles/route-optimization.json")
STATE = Path(state_path(".runtime/route-optimization-state.json"))


def area_key(route):
    # Alternative patrols within one monster family belong to one experiment.
    return f"{route.map_id}:{min(route.monster_type_ids)}"


def fixed_route_windows(kills, *, started_at, now, window_seconds=900, retain=4):
    """Completed, non-overlapping elapsed-time windows anchored to activation.

    Input is (timestamp, kill_verified payload), covering every returned
    interval; a bare integer is a pre-rule historical count. Both are qualified
    by the shared kill_increment rule. These are throughput measurements, not
    safety or route qualification. Empty periods count as zero; a partially
    elapsed interval never counts as complete.
    """
    if window_seconds <= 0 or retain < 1 or now < started_at:
        raise ValueError("Invalid measurement interval")
    completed = int((now - started_at) // window_seconds)
    windows = []
    verified = []
    for t, value in kills:
        count = kill_increment.verified_kill_count(
            value if isinstance(value, dict) else {"count": value}
        )
        if count is not None:
            verified.append((t, count))
    for index in range(max(0, completed - retain), completed):
        start = started_at + index * window_seconds
        end = start + window_seconds
        count = sum(n for t, n in verified if start <= t < end)
        windows.append(
            {
                "start": start,
                "end": end,
                "seconds": window_seconds,
                "kills": count,
                "kills_per_minute": count * 60 / window_seconds,
            }
        )
    return windows


def queue_area(route, *, path=STATE, policy_path=POLICY, now=None):
    policy = read_json(policy_path)
    if not policy.get("enabled"):
        return None
    now = time.time() if now is None else now
    state = read_json(path)
    areas = state.setdefault("areas", {})
    key = area_key(route)
    if key in areas:
        return areas[key]
    entry = {
        "area_key": key,
        "map_id": route.map_id,
        "monster_type_ids": list(route.monster_type_ids),
        "baseline_route_id": route.id,
        "baseline_route": route.model_dump(mode="json"),
        "phase": "needs_survey",
        "queued_at": now,
        "minimum_minutes": policy["minimum_minutes"],
        "maximum_minutes": policy["maximum_minutes"],
        "samples": [],
        "winner": None,
    }
    areas[key] = entry
    write_json(path, state)
    return entry


def comparison_plan(candidate_ids, *, policy_path=POLICY):
    policy = read_json(policy_path)
    candidates = list(candidate_ids)
    if len(set(candidates)) != len(candidates) or not 2 <= len(candidates) <= 4:
        raise ValueError("Choose two to four distinct saved routes")
    if policy["rounds"] != 2:
        raise ValueError("Each candidate requires two separate samples")
    sample = policy["sample_minutes"]
    # Reverse and rotate the second round; avoid adjacent duplicate samples.
    reverse = candidates[::-1]
    second = reverse[1:] + reverse[:1]
    stages = [
        {"kind": "sample", "route_id": rid, "minutes": sample}
        for rid in candidates + second
    ]
    minutes = sum(s["minutes"] for s in stages)
    if minutes + policy["validation_minutes"] <= policy["maximum_minutes"]:
        stages.append(
            {"kind": "validate_winner", "minutes": policy["validation_minutes"]}
        )
        minutes += policy["validation_minutes"]
    if not policy["minimum_minutes"] <= minutes <= policy["maximum_minutes"]:
        raise ValueError("Comparison must fit the authorized one-to-two-hour budget")
    return {"stages": stages, "minutes": minutes, "candidate_ids": candidates}


def rank_candidates(samples, candidate_ids, *, policy_path=POLICY):
    """Rank only repeated complete samples; retain downtime in the denominator.

    Caller must verify memory provenance, label intervals at actual activation,
    and mark interrupted or changed-gear samples non-comparable. Unsafe evidence
    disqualifies a route even if its interrupted sample cannot otherwise count.
    """
    policy = read_json(policy_path)
    candidates = list(candidate_ids)
    if len(set(candidates)) != len(candidates) or len(candidates) < 2:
        raise ValueError("Distinct candidates required")
    ids = [s["sample_id"] for s in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate samples cannot establish repeatability")
    results = []
    pending = []
    for rid in candidates:
        rows = [s for s in samples if s["route_id"] == rid]
        if any(s.get("deaths", 0) > 0 or s.get("unsafe", False) for s in rows):
            results.append({"route_id": rid, "eligible": False, "reason": "unsafe"})
            continue
        valid = [
            s
            for s in rows
            if s.get("memory_verified") is True
            and s.get("comparable") is True
            and s["elapsed_seconds"] >= policy["sample_minutes"] * 60
            and type(s["verified_kills"]) is int
            and s["verified_kills"] >= 0
        ]
        if len(valid) < policy["rounds"]:
            pending.append(rid)
            continue
        seconds = sum(s["elapsed_seconds"] for s in valid)
        kills = sum(s["verified_kills"] for s in valid)
        results.append(
            {
                "route_id": rid,
                "eligible": True,
                "verified_kills": kills,
                "elapsed_seconds": seconds,
                "kills_per_hour": kills * 3600 / seconds,
            }
        )
    eligible = sorted(
        (r for r in results if r["eligible"]), key=lambda r: -r["kills_per_hour"]
    )
    return {
        "winner": eligible[0]["route_id"] if eligible and not pending else None,
        "pending_candidates": pending,
        "ranking": eligible,
        "rejected": [r for r in results if not r["eligible"]],
    }
