"""Town batch vs historical shop-return records.

Written before the fix (AGENTS.md testing rule). Failure modes:

1. A shop_return left over from an earlier game process (live 2026-09-24:
   Spiritual pid 9560 and Dutch pid 16676 from 09-20, both merchants now run
   as pids 632952/635124) would block every town batch forever, although
   ordinary refill already lists past it.
2. A shop_return bound to the merchant's current process is a live incident
   and must still block.
3. A shop_return without a usable identity cannot be proved stale: block.
4. A merchant whose current identity is unreadable cannot be compared: block.
5. Terminal shop_return phases never block (unchanged).
"""

import pytest

from conquest.merchants.town_batch import merchant_blockers

CURRENT = {"pid": 632952, "creation_time_100ns": 134345856627182944, "path": "x"}
OLD = {"pid": 9560, "creation_time_100ns": 134341000000000000, "path": "x"}


def status(shop_return, snapshot_identity=CURRENT):
    character = {
        "connected": True,
        "pending": [],
        "needs_attention": None,
        "manual_input_fence": False,
        "recovery_safety": None,
        "shop_return": shop_return,
        "refill": {"enabled": True, "pending": True, "listing1078_request": None},
        "foreground_refill_1078": {"state": "capacity_checked"},
        "snapshot": None
        if snapshot_identity is None
        else {"identity": snapshot_identity},
    }
    return {
        "characters": {"Spiritual": character},
        "input_owner": None,
        "handoff_granted": False,
        "host_request": None,
    }


def shop_blocked(value):
    return any(r.endswith(":shop_return") for r in merchant_blockers(value))


def test_record_from_an_earlier_process_does_not_block():
    assert not shop_blocked(status({"phase": "returning", "before": {"identity": OLD}}))


def test_record_for_the_current_process_blocks():
    assert shop_blocked(status({"phase": "returning", "before": {"identity": CURRENT}}))


@pytest.mark.parametrize(
    "record",
    [
        {"phase": "returning"},
        {"phase": "returning", "before": None},
        {"phase": "returning", "before": {"identity": None}},
        {"phase": "returning", "before": {"identity": {"pid": 9560}}},
    ],
)
def test_record_without_a_provable_identity_blocks(record):
    assert shop_blocked(status(record))


def test_unreadable_current_identity_blocks():
    assert shop_blocked(
        status({"phase": "returning", "before": {"identity": OLD}}, None)
    )


@pytest.mark.parametrize("phase", [None, "complete", "operator_overridden"])
def test_terminal_records_never_block(phase):
    record = (
        None if phase is None else {"phase": phase, "before": {"identity": CURRENT}}
    )
    assert not shop_blocked(status(record))
