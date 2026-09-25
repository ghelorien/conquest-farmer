"""ProfileName values must survive copying (written before the fix).

Live 2026-09-25 03:19-05:15 (r41/r42): every healthy 1078 Market tick raised
TypeError in return_1078.record_baseline -> copy.deepcopy(snapshot), because
the ownership snapshot's "character" is a ProfileName (str subclass with a
required profile_id) that copy/pickle rebuilt as ProfileName(str) only. The
runtime's catch-all hid it as "Unexpected merchant observer failure", which
stopped sales observation, trades and refill for both merchants, and left the
disconnect-recovery baseline unwritten. The M1 tests used plain str names.

Failure modes:
P1 copy.deepcopy of a ProfileName raises or loses profile_id.
P2 pickle round-trip raises or loses profile_id.
P3 copy.copy raises or loses profile_id.
P4 A snapshot dict holding a ProfileName character cannot be deep-copied.
P5 record_baseline with a live-shaped ProfileName snapshot raises instead of
   persisting the baseline (the live failure).
P6 The persisted baseline no longer compares equal to the profile name.
"""

import copy
import pickle

from conquest.character_context import ProfileName
from conquest.merchants import return_1078

import test_merchant_recovery_1078 as m1

NAME = ProfileName("Spiritual", "c95dd7d9-9b52-4f24-88ed-622741adb18b")


def same(value):
    return (
        isinstance(value, ProfileName)
        and str(value) == "Spiritual"
        and value.profile_id == NAME.profile_id
    )


def test_p1_deepcopy_keeps_profile_id():
    assert same(copy.deepcopy(NAME))


def test_p2_pickle_round_trip_keeps_profile_id():
    assert same(pickle.loads(pickle.dumps(NAME)))


def test_p3_shallow_copy_keeps_profile_id():
    assert same(copy.copy(NAME))


def test_p4_snapshot_with_profile_name_deep_copies():
    snapshot = {"character": NAME, "booth": [{"uid": 1}]}
    copied = copy.deepcopy(snapshot)
    assert same(copied["character"]) and copied == snapshot


def test_p5_p6_record_baseline_with_live_profile_name(rt):
    snapshot = m1.market(character=NAME)
    assert return_1078.record_baseline(rt, NAME, snapshot) is True
    saved = return_1078.baseline(rt, NAME)
    assert saved is not None
    assert saved["character"] == "Spiritual"
    assert saved["snapshot"]["character"] == "Spiritual"


rt = m1.rt
