from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from conquest.merchants import booth_panel_probe as probe
from conquest.merchants.journal import Journal
from conquest.merchants.memory import HoverNotReady


@pytest.mark.parametrize(
    "case",
    [
        "closed",
        "foreign",
        "stock_before",
        "stock_after",
        "moved",
        "hover_missing",
        "stopped",
        "pending",
        "modal",
    ],
)
def test_panel_close_preserves_shop_and_journals_before_input(
    tmp_path, monkeypatch, case
):
    journal = Journal(tmp_path / "state.sqlite3")
    presses = []
    reads = 0
    item = dict(uid=77, type_id=1088001, plus=0, gem1=0, gem2=0, quantity=1, price=100)
    before = dict(
        identity={"pid": 1},
        map_id=1036,
        position=[271, 174],
        own_booth_uid=103064,
        booth_open=True,
        inventory=[],
        booth=[item],
        silver=100,
        trade=None,
        request=None,
        windows=[{"name": "Booth", "address": 500, "geometry": [600, 200, 620, 300]}],
    )
    if case == "modal":
        before["windows"].append({"name": "Add Item to Booth"})
    if case == "pending":
        journal.set("Spiritual", "booth_panel_probe", {"phase": "close_submitted"})
    state = deepcopy(before)

    def read():
        nonlocal reads
        reads += 1
        if reads > 1:
            if case == "stock_before":
                state["booth"] = []
            if case == "foreign":
                state["own_booth_uid"] = 999
            if case == "moved":
                state["position"] = [272, 174]
        return deepcopy(state)

    def check():
        if case == "stopped" and reads:
            raise ValueError("Manual Stop")

    def hovered(w, label):
        assert label == "#CLOSE"
        if case == "hover_missing":
            raise HoverNotReady("No native hover")

    monkeypatch.setattr(
        probe, "close_point", lambda d, s: ((1196, 212), s["windows"][0])
    )
    if case == "hover_missing":
        ticks = iter([0, 1])
        monkeypatch.setattr(probe.time, "monotonic", lambda: next(ticks))
    driver = NS(
        observer=NS(character="Spiritual"),
        memory=NS(read=read, gui=NS(assert_hovered=hovered)),
    )

    def click(point, check, *, before_press):
        before_press()
        assert (
            journal.get("Spiritual", "booth_panel_probe")["phase"] == "close_submitted"
        )
        presses.append(point)
        state["booth_open"] = False
        if case == "stock_after":
            state["booth"] = []

    if case == "closed":
        receipt = probe.close_owned_panel(driver, NS(click=click), journal, check)
        assert receipt["phase"] == "closed_verified" and receipt["stock_unchanged"]
        assert receipt["own_booth_uid"] == 103064 and state["booth"] == before["booth"]
        assert presses == [(1196, 212)]
    else:
        with pytest.raises(ValueError):
            probe.close_owned_panel(driver, NS(click=click), journal, check)
        assert len(presses) == (1 if case == "stock_after" else 0)
        if case == "stock_after":
            assert (
                journal.get("Spiritual", "booth_panel_probe")["phase"]
                == "close_submitted"
            )
