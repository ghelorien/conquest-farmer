from types import SimpleNamespace as NS
import struct
import pytest
from conquest.memory_warehouse import money_received
from conquest.warehouse_money import money_points


@pytest.mark.parametrize(
    "direction,wallet,stored", [("deposit", 900, 600), ("withdraw", 1100, 400)]
)
def test_exact_two_sided_money_receipt(direction, wallet, stored):
    before = NS(silver=1000, items=(1,), equipped_ammo=2)
    bank = NS(silver=500)
    after = NS(silver=wallet, items=(1,), equipped_ammo=2)
    fresh = NS(silver=stored)
    assert money_received(direction, 100, before, bank, after, fresh)
    fresh.silver += 1
    assert not money_received(direction, 100, before, bank, after, fresh)
    fresh.silver -= 1
    after.items = ()
    assert not money_received(direction, 100, before, bank, after, fresh)


def test_money_widget_positions_come_from_live_geometry_and_font_metrics():
    memory = {
        0x6966F0: struct.pack("<Q", 1000),
        1000 + 0x3C20: struct.pack("<Q", 2000),
        1000 + 0x3C28: struct.pack("<f", 12),
        2000: struct.pack("<IIQ", 128, 128, 3000),
        2000 + 0x14: struct.pack("<f", 12),
        1000 + 0x3834: struct.pack("<2f", 4, 3),
        1000 + 0x3844: struct.pack("<2f", 8, 4),
        3000: struct.pack("<128f", *[7.0] * 128),
    }
    window = NS(position=(51.0, 99.0), size=(312.0, 460.0))
    bank = NS(
        window=window,
        grid=NS(position=(71.0, 195.0), size=(272.0, 326.0), scroll=(0.0, 0.0)),
    )
    reader = NS(
        session=NS(read_block=lambda a, n: memory[a]),
        gui=NS(base=0, read=lambda n: window),
    )
    assert money_points(reader, bank) == {
        "amount": (119, 182),
        "deposit": (204, 182),
        "withdraw": (272, 182),
    }
    bank.grid.position = (71.0, 196.0)
    with pytest.raises(ValueError, match="geometry"):
        money_points(reader, bank)


@pytest.mark.parametrize("wrong_amount", [False, True])
def test_money_submission_requires_verified_numeric_buffer_and_is_not_repeated(
    monkeypatch, wrong_amount
):
    from conquest import warehouse_money as m

    state = {"wallet": 1000, "bank": 500, "amount": ""}
    clicks = []

    def bag():
        return NS(silver=state["wallet"], items=(1,), equipped_ammo=2)

    def bank():
        return NS(silver=state["bank"], amount=state["amount"])

    monkeypatch.setattr(m, "WarehouseMoneyReader", lambda *a: NS(read=bank))
    monkeypatch.setattr(
        m,
        "money_points",
        lambda *a: {"amount": (10, 20), "deposit": (30, 20), "withdraw": (50, 20)},
    )
    monkeypatch.setattr(
        m,
        "type_amount",
        lambda target, value: state.update(amount="10" if wrong_amount else str(value)),
    )

    def click(point):
        clicks.append(point)
        if point == (30, 20):
            state.update(wallet=900, bank=600)

    def verified(read, accept, failure, **kwargs):
        result = read()
        if not accept(result):
            raise ValueError(failure)
        return result

    trade = NS(
        vendor=lambda kind: 123,
        inventory=NS(read=bag),
        observer=NS(adapter=None, operations=NS(target=None)),
        click=click,
        verified_read=verified,
    )
    if wrong_amount:
        with pytest.raises(ValueError, match="amount entry"):
            m.transfer(trade, "deposit", 100)
        assert clicks == [(10, 20)] and state["wallet"] == 1000
    else:
        receipt = m.transfer(trade, "deposit", 100)
        assert receipt["silver"] == 900 and receipt["stored_silver"] == 600
        assert clicks == [(10, 20), (30, 20)]


@pytest.mark.parametrize(
    "raw,expected",
    [("14,021", "14021"), ("100", "100"), ("1,000,000", "1000000"), ("", "")],
)
def test_client_formatted_amount_matches_exact_transfer(raw, expected):
    from conquest.memory_warehouse import normalized_money_amount

    assert normalized_money_amount(raw) == expected


@pytest.mark.parametrize("raw", ["14,02", "1,,000", "14.021", "-100", "1e3"])
def test_malformed_amount_cannot_authorize_submission(raw):
    from conquest.memory_warehouse import normalized_money_amount

    with pytest.raises(ValueError):
        normalized_money_amount(raw)
