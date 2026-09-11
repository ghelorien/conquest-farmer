from types import SimpleNamespace

from conquest.patrol_search import AdaptivePatrol,PatrolSearchConfig


def test_idle_expansion_occurs_after_seven_and_half_seconds_and_attack_resets_timer():
    search=AdaptivePatrol((100,100,130,130),PatrolSearchConfig(),0,(1000,1000))
    assert search.expand(7.4) is None
    assert search.expand(7.5)==(88,88,142,142)
    search.attacked(10)
    assert search.expand(17.4) is None
    assert search.expand(17.5)==(76,76,154,154)


def test_expansion_stays_inside_map_and_stops_at_saved_limit():
    search=AdaptivePatrol((10,10,90,90),PatrolSearchConfig(maximum_expansions=1),0,(100,100))
    assert search.expand(8)==(0,0,99,99)
    assert search.expand(16) is None
    assert all(0<=x<100 and 0<=y<100 for x,y in search.patrol_points(SimpleNamespace(walkable=lambda p:True)))


def test_saved_routes_retain_idle_search_policy():
    from conquest.routes import RouteLibrary
    for route in RouteLibrary().all():
        assert 5<=route.patrol_search.idle_seconds<=10
        assert route.patrol_search.expansion_tiles==(8 if route.id=='apparition' else 12)


def test_expanded_patrol_covers_interior_within_sixteen_tile_attack_range():
    search=AdaptivePatrol((572,498,716,642),PatrolSearchConfig(),0,(1000,1000))
    points=search.patrol_points(SimpleNamespace(walkable=lambda p:True))
    assert len(points)>4
    for x in range(572,717):
        for y in range(498,643):
            assert min(max(abs(x-px),abs(y-py)) for px,py in points)<=16
    # Consecutive rows reverse direction instead of circling the perimeter.
    assert points[0][1]==points[1][1] and points[0][0]<points[1][0]
    second=[p for p in points if p[1]==points[0][1]+24]
    assert second[0][0]>second[-1][0]


def test_sweep_skips_blocked_tiles_and_stays_inside_bounds():
    search=AdaptivePatrol((100,100,150,150),PatrolSearchConfig(),0,(1000,1000))
    terrain=SimpleNamespace(walkable=lambda p:p[0]>120)
    points=search.patrol_points(terrain)
    assert points and len(points)==len(set(points))
    assert all(100<=x<=150 and 100<=y<=150 and terrain.walkable((x,y)) for x,y in points)
