"""A hunt that burns potions fast returns to town while some are left.

Live 2026-09-28 (Toxic, Bandits): 16 potions in three minutes at 01:00-01:03;
it turned for town on its last one and died on the way (01:04).
"""

from conquest import overnight
from conquest.overnight import OvernightLoop


def burn(samples):
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.potion_samples = []
    return [loop.heavy_burn(potions, now=t) for t, potions in samples]


def test_six_potions_inside_two_minutes_send_the_farmer_home():
    assert overnight.HEAVY_BURN_POTIONS == 6 and overnight.HEAVY_BURN_WINDOW == 120
    results = burn([(0, 28), (20, 26), (40, 25), (60, 24), (80, 23), (100, 22)])
    assert results == [False, False, False, False, False, True]


def test_pickups_never_offset_use_and_old_use_ages_out():
    # Two used, one picked up, four more used: six used.
    assert burn([(0, 20), (10, 18), (20, 21), (30, 17)])[-1] is True
    # The same six spread over three minutes (a busy but normal fight).
    steady = [(t, 30 - t // 30) for t in range(0, 181, 30)]
    assert not any(burn(steady))
