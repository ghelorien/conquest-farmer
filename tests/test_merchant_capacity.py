from types import SimpleNamespace
import pytest
from conquest.merchants.capacity import available_slots


@pytest.mark.parametrize(
    "inventory,booth,expected", [(8, 32, 0), (7, 32, 1), (4, 27, 9), (40, 0, 0)]
)
def test_combined_capacity(inventory, booth, expected):
    s = {
        "capacity": 40,
        "inventory": [{"uid": i + 1} for i in range(inventory)],
        "booth": [{"uid": 100 + i} for i in range(booth)],
    }
    assert available_slots(s) == expected


def test_duplicate_ownership_is_not_free_space():
    with pytest.raises(ValueError, match="Ambiguous"):
        available_slots(
            {"capacity": 40, "inventory": [{"uid": 1}], "booth": [{"uid": 1}]}
        )
