import pytest
from conquest.merchants.delivery_trade_controls import cell


def test_drop_cell_tracks_live_grid_and_rejects_clipped_rows():
    table={'columns':[{'content_x':100+i*44} for i in range(5)],
           'outer':[100,150,320,326],'clip':[100,150,320,326],'row_height':44}
    assert cell(table,7)==(208,214)
    with pytest.raises(ValueError,match='clipped'):cell(table,20)
    with pytest.raises(ValueError,match='slot'):cell(table,-1)
