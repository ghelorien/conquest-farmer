from copy import deepcopy
from types import SimpleNamespace

import pytest

from conquest.merchants import background_drag_cancel as module


def setup(monkeypatch, *, fail=None):
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay)
    )
    item = dict(
        uid=123, slot=0, bound=False, type_id=410009, plus=1, gem1=0, gem2=0, quantity=1
    )
    win = dict(
        name=module.GRID,
        address=0x100000,
        geometry=[0.0, 0.0, 400.0, 400.0],
        scroll=[0.0, 0.0],
    )
    before = dict(
        identity={"pid": 7},
        inventory=[item],
        booth=[],
        silver=0,
        position=[10, 10],
        windows=[win],
        request=None,
        trade=None,
    )
    table = dict(
        columns=[
            dict(
                content_x=float(20 + i * 40),
                minimum=float(20 + i * 40),
                maximum=float(60 + i * 40),
            )
            for i in range(5)
        ],
        outer=[20.0, 20.0, 220.0, 340.0],
        clip=[20.0, 20.0, 220.0, 340.0],
        row_height=40.0,
    )
    gui = SimpleNamespace(
        table=lambda *args: deepcopy(table), viewport_size=lambda: [400, 400]
    )
    posts = []
    target = SimpleNamespace(
        snapshot=lambda: {"client_size": [400, 400]},
        post=lambda *args: posts.append(args),
    )
    driver = SimpleNamespace(
        target=target, memory=SimpleNamespace(gui=gui), read=lambda: deepcopy(before)
    )
    neutral = dict(
        active={"id": 0},
        drag={"active": False},
        queue={"size": 0},
        backend={"buttons_down": 0},
        mouse_down=[False] * 5,
        modifiers=dict(ctrl=False, shift=False, alt=False, super=False),
        hover={"window": win["address"], "id": 888},
        want_capture_mouse=True,
        mouse_position=[40.0, 40.0],
    )
    state = deepcopy(neutral)
    session = SimpleNamespace(identity={"pid": 7}, assert_identity=lambda: None)
    reader = SimpleNamespace(session=session, snapshot=lambda: deepcopy(state))
    report = {"samples": []}
    stages = []

    def sample(stage):
        stages.append(stage)
        state.clear()
        state.update(deepcopy(neutral))
        if stage in ("drag-press", "drag-held", "drag-return"):
            state["active"]["id"] = 888
            state["mouse_down"][0] = True
            state["backend"]["buttons_down"] = 1
        if stage in ("drag-held", "drag-return"):
            state["drag"] = dict(
                active=True,
                payload_type="CQITEM",
                source_item_uid=123,
                source_id=888,
                delivery=False,
            )
        if fail == "wrong-uid" and stage == "drag-held":
            state["drag"]["source_item_uid"] = 456
        if fail == "no-press" and stage == "drag-press":
            state["active"]["id"] = 0
        if fail == "stop" and stage == "drag-held":
            raise ValueError("Manual stop")
        if fail == "identity" and stage == "drag-held":
            session.identity = {"pid": 8}
            raise ValueError("Process replaced")
        report["samples"].append({"stage": stage, "gui": deepcopy(state)})

    def check():
        pass

    return driver, before, reader, report, sample, check, posts, stages, clock


def test_proves_exact_payload_then_returns_to_source_and_releases(monkeypatch):
    args = setup(monkeypatch)
    result = module.run_drag_cancel(*args[:6])
    assert result["drag_payload"]["source_item_uid"] == 123
    assert result["drag_cancel_verified"] and result["release_verified"]
    assert args[7] == [
        "drag-prime",
        "drag-press",
        "drag-held",
        "drag-return",
        "drag-release",
    ]
    buttons = [row for row in args[6] if row[0] in (0x201, 0x202)]
    assert buttons == [(0x201, 1, 40 | 40 << 16), (0x202, 0, 40 | 40 << 16)]
    assert all(row[0] in (0x200, 0x102, 0x201, 0x202) for row in args[6])
    assert all(
        (row[2] & 0xFFFF) in (40, 41, 54, 55) for row in args[6] if row[0] == 0x200
    )


@pytest.mark.parametrize("failure", ["wrong-uid", "no-press", "stop"])
def test_failure_while_held_always_posts_identity_guarded_release(monkeypatch, failure):
    args = setup(monkeypatch, fail=failure)
    with pytest.raises(ValueError):
        module.run_drag_cancel(*args[:6])
    assert args[6][-1] == (0x202, 0, 40 | 40 << 16)
    assert args[3]["drag_emergency_release_sent"]
    assert not args[3].get("drag_cancel_verified")
    assert args[8][0] < 8


def test_pid_reuse_never_sends_release_to_replacement_process(monkeypatch):
    args = setup(monkeypatch, fail="identity")
    with pytest.raises(ValueError, match="release refused"):
        module.run_drag_cancel(*args[:6])
    assert not any(row[0] == 0x202 for row in args[6])


@pytest.mark.parametrize(
    "invalid", ["bound", "missing-slot", "trade", "dialog", "clipped"]
)
def test_ineligible_initial_state_sends_nothing(monkeypatch, invalid):
    args = setup(monkeypatch)
    driver, before = args[:2]
    if invalid == "bound":
        before["inventory"][0]["bound"] = True
    elif invalid == "missing-slot":
        before["inventory"][0]["slot"] = 40
    elif invalid == "trade":
        before["trade"] = {"participant": "Unknown"}
    elif invalid == "dialog":
        before["windows"].append({"name": "Add Item to Booth"})
    else:
        before["windows"][0]["geometry"] = [0.0, 0.0, 45.0, 45.0]
    with pytest.raises(ValueError):
        module.run_drag_cancel(*args[:6])
    assert args[6] == []


def test_changed_slot_after_hover_blocks_mouse_down(monkeypatch):
    args = setup(monkeypatch)
    original_sample = args[4]

    def changed(stage):
        original_sample(stage)
        args[1]["inventory"][0]["uid"] = 456

    with pytest.raises(ValueError, match="source, stock"):
        module.run_drag_cancel(*args[:4], changed, args[5])
    assert not any(row[0] == 0x201 for row in args[6])
