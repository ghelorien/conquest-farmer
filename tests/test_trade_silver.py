import pytest
from conquest.merchants.memory import trade_silver


def test_native_confirmed_zero_is_distinct_from_unverified_text():
    assert trade_silver('0 \u2714',accepted=True,locked=True)==0
    assert trade_silver('123')==123
    for text,accepted,locked in [('0 \u2714',False,True),('0 \u2714',True,False),
                                ('1 \u2714',True,True),('',True,True),('unknown',True,True)]:
        with pytest.raises(ValueError,match='Unverified'):
            trade_silver(text,accepted=accepted,locked=locked)
