"""End-to-end: a broke farmer sells a TwinCityGate for the arrow pack an empty
quiver needs instead of failing every restock retry in town.

Live 2026-09-27 17:19 (Toxic, level 27, Phoenix City): 128 silver, an empty
bank, one LuckyArrow left and two TwinCityGates (bought for 200, sold back for
a third). The 200-silver pack was refused before input on every restock retry.

Runs the real restock through test_arrow_tier_fallback_e2e's game-process
fake, extended with the worker's "sell-scroll" action.
"""

import json

import pytest

import test_arrow_tier_fallback_e2e as arrows

SCROLL = 1060020
RESALE = 66


class ScrollGame(arrows.Game):
    def request(self, info, operation, body=None):
        if operation == "town" and body.get("action") == "sell-scroll":
            assert {k for k in body if k != "expires_at"} == {"action", "vendor_type", "uid"}
            assert body["vendor_type"] == 5
            self.clock.now += 0.05
            item = next(i for i in self.items if i["uid"] == body["uid"])
            assert item["type_id"] == SCROLL
            self.items.remove(item)
            self.silver += RESALE
            self.add("sell-scroll", silver=self.silver)
            return {"sold": item["uid"], "type_id": SCROLL, "plus": 0, "silver_gained": RESALE}
        return super().request(info, operation, body)


def scroll(uid, slot):
    return {"uid": uid, "type_id": SCROLL, "amount": 1, "limit": 1, "plus": 0, "slot": slot}


def scenario(scrolls):
    return {
        "route": "wingedsnake",
        "map_id": 1011,
        "level": 27,
        "equipped": {"uid": 9, "type_id": arrows.LUCKY, "amount": 1, "limit": 200},
        "bag": [scroll(30 + n, 1 + n) for n in range(scrolls)],
        # A full potion stock skips the Pharmacist: the case is the arrows.
        "potions": 20,
        "silver": 128,
        "stored": 0,
    }


@pytest.mark.parametrize("scrolls", [2, 1, 0])
def test_empty_quiver_sells_scrolls_for_the_pack(tmp_path, monkeypatch, scrolls):
    name = f"broke_l27_{scrolls}_scrolls"
    monkeypatch.setattr(arrows, "Game", ScrollGame)
    monkeypatch.setitem(arrows.SCENARIOS, name, scenario(scrolls))
    monkeypatch.setattr(
        arrows, "KEPT_EVENTS", arrows.KEPT_EVENTS + ("scroll_sold_for_arrows",)
    )
    artifact = json.loads(
        arrows.run_scenario(tmp_path / "run", monkeypatch, name).read_text(encoding="utf-8")
    )
    trace, final = artifact["trace"], artifact["final"]
    kinds = [row["kind"] for row in trace if row["kind"] in ("sell-scroll", "buy", "buy_refused")]
    sold = [e for e in artifact["route_events"] if e["event"] == "scroll_sold_for_arrows"]
    if scrolls == 2:
        # Refused, one scroll (194), refused, the second (260), then the pack.
        # The 1-arrow quiver is no pack, so a second pack is tried and refused
        # before input: 60 silver left.
        assert kinds == [
            "buy_refused", "sell-scroll", "buy_refused", "sell-scroll", "buy", "buy_refused"
        ]
        assert [e["silver"] for e in sold] == [194, 260]
        assert artifact["error"] is None and final["cycles"] == 1
        assert final["silver"] == 60
        assert final["arrows"]["equipped"]["amount"] + sum(
            b["amount"] for b in final["arrows"]["bag"]
        ) == 201
    else:
        # Not enough to sell: the refusal stands, as before.
        assert kinds == ["buy_refused"] + ["sell-scroll", "buy_refused"] * scrolls
        assert artifact["error"] == "Insufficient funds or inventory room to restock"
        assert final["silver"] == 128 + RESALE * scrolls
