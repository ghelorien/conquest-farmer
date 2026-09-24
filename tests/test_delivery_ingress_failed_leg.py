from types import SimpleNamespace

from conquest.merchants.approach import ingress_position
from conquest.navigation import line_tiles, clear_segment


def terrain():
    return SimpleNamespace(
        travel_path=lambda a, b, **kw: line_tiles(a, b),
        walkable=lambda p: True,
        portals=set(),
    )


def probe():
    return {
        "reason": "recipient_absent",
        "point": None,
        "farmer_position": [230, 228],
        "merchant_position": [230, 173],
        "occupied_tiles": [],
        "viewport": [1416, 876],
    }


def test_failed_ingress_click_uses_another_checked_landing_not_a_fake_obstacle():
    world = terrain()
    sample = probe()
    source = tuple(sample["farmer_position"])
    first = ingress_position(world, sample)
    second = ingress_position(world, sample, failed_legs={(source, first)})
    assert first != second
    assert 8 <= max(abs(a - b) for a, b in zip(source, second)) <= 12
    assert clear_segment(world, source, second)
    assert second != tuple(sample["merchant_position"])


def test_exhausted_distinct_landings_defer_instead_of_replaying():
    world = terrain()
    sample = probe()
    source = tuple(sample["farmer_position"])
    failed = set()
    for _ in range(5):
        point = ingress_position(world, sample, failed_legs=failed)
        assert point is not None and (source, point) not in failed
        failed.add((source, point))
    assert ingress_position(world, sample, failed_legs=failed) is None


def test_expired_budget_never_selects_an_alternative():
    assert ingress_position(terrain(), probe(), deadline=0) is None


def test_alternative_preserves_solid_tile_guard():
    world = terrain()
    sample = probe()
    world.walkable = lambda p: p == tuple(sample["farmer_position"])
    assert ingress_position(world, sample) is None


def test_approach_reobserves_after_failed_leg_then_moves_to_different_ingress(
    monkeypatch,
):
    from conquest.merchants import delivery_route
    from conquest.travel_progress import TravelStalled

    sample = probe()
    source = tuple(sample["farmer_position"])
    moves = []
    events = []
    loop = SimpleNamespace(
        terrain=terrain(),
        check_stop=lambda: None,
        market_service_deadline=None,
        record=lambda event, **kw: events.append(event),
    )
    monkeypatch.setattr(delivery_route, "check_stop", lambda loop: None)

    def travel(point, **kw):
        moves.append(tuple(point))
        if len(moves) == 1:
            raise TravelStalled("native landing intercepted without movement")
        sample["farmer_position"] = list(point)

    loop.travel = travel
    observations = []

    def send(body):
        observations.append(tuple(sample["farmer_position"]))
        if len(moves) >= 2:
            # Complete this bounded test on the next read, without input.
            return {**sample, "merchant_position": [999, 999]}
        return dict(sample)

    assert not delivery_route.approach_merchant(
        loop, {"merchant": "Dutch", "position": sample["merchant_position"]}, send
    )
    assert len(moves) == 2 and moves[0] != moves[1]
    assert observations[:2] == [source, source]
    assert observations[2] == moves[1]
    assert events.count("merchant_approach_deferred") == 1
    assert loop.market_service_deadline is None
