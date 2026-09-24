import copy
import time
import pytest
from conquest.merchants.empty_delivery_cancel import unchanged


@pytest.mark.parametrize(
    "phase,marker",
    [("cancel_submitted", True), ("prepared", True), ("cancel_verified", True)],
)
def test_durable_cancel_marker_blocks_new_input_after_restart(
    tmp_path, monkeypatch, phase, marker
):
    from types import SimpleNamespace as NS
    import threading
    from conquest.merchants import (
        empty_delivery_cancel as cancel,
        delivery_probe as probe,
    )

    state = {"phase": "trade_open_verified", "character": "Spiritual"}
    monkeypatch.setattr(probe, "JOURNAL", tmp_path / "probe.json")
    probe.write_probe(probe.JOURNAL, state)
    monkeypatch.setattr(cancel, "state_path", lambda _: tmp_path / "cancel.json")
    cancel.save(
        {
            "phase": phase,
            "character": "Spiritual",
            "probe_digest": cancel.digest(state),
            "submitted_at": time.time(),
            "point": [10, 10],
        }
    )
    monkeypatch.setattr(
        cancel,
        "pair",
        lambda *a: pytest.fail("Submitted cleanup cannot enter input preparation"),
    )
    ui = NS(
        coordinator=NS(lock=threading.RLock(), check=lambda: None),
        runtime=NS(enabled=lambda _: False, manual_handoff_status=lambda: None),
        safe_to_yield=lambda: True,
        app=NS(control=NS(snapshot=lambda: {"enabled": False, "paused": False})),
    )
    with pytest.raises(ValueError, match="one-shot|terminal receipt"):
        cancel.start(ui, "Spiritual")
    assert cancel.read()["submitted_at"] > 0


def test_prepared_cancel_cannot_reconcile_as_submitted(monkeypatch):
    from types import SimpleNamespace as NS
    import threading
    from conquest.merchants import empty_delivery_cancel as cancel

    monkeypatch.setattr(
        cancel,
        "pair",
        lambda *a: pytest.fail("No ownership publication before submission"),
    )
    with pytest.raises(ValueError, match="No submitted"):
        cancel._reconcile(
            NS(coordinator=NS(lock=threading.RLock())), {"phase": "prepared"}
        )


def pair():
    def snapshot(name, uid):
        return dict(
            character=name,
            character_uid=uid,
            identity={"pid": uid},
            server="America",
            timestamp=time.time(),
            map_id=1036,
            hp=100,
            silver=100,
            capacity=40,
            inventory=[],
            booth=[],
            position=[1, 2],
            request=None,
            trade=None,
        )

    f = snapshot("Parasite", 1)
    m = snapshot("Spiritual", 2)
    m["trade"] = dict(
        participant="Parasite",
        participant_uid=1,
        own_items=[],
        items=[],
        own_silver=0,
        other_silver=0,
        accepted=False,
        other_accepted=False,
    )
    return {"farmer": f, "merchant": m}


def test_empty_stale_recipient_window_and_verified_close():
    b = pair()
    unchanged(b, b)
    a = copy.deepcopy(b)
    a["merchant"]["trade"] = None
    unchanged(b, a)


@pytest.mark.parametrize(
    "field,value",
    [
        ("items", [{"uid": 99}]),
        ("own_items", [{"uid": 99}]),
        ("own_silver", 1),
        ("other_silver", 1),
        ("accepted", True),
        ("other_accepted", True),
        ("participant", "Stranger"),
        ("participant_uid", 3),
        ("accepted", None),
    ],
)
def test_never_cancel_nonempty_or_accepted_trade(field, value):
    b = pair()
    a = copy.deepcopy(b)
    a["merchant"]["trade"][field] = value
    with pytest.raises(ValueError):
        unchanged(b, a)


@pytest.mark.parametrize("role", ["farmer", "merchant"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("silver", 101),
        ("position", [2, 3]),
        ("identity", {"pid": 99}),
        ("request", {"participant": "Stranger"}),
    ],
)
def test_reject_changes_between_observation_and_click(role, field, value):
    b = pair()
    a = copy.deepcopy(b)
    a[role][field] = value
    with pytest.raises(ValueError):
        unchanged(b, a)
