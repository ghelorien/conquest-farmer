from conquest.region_rotation import HuntingRegion,RegionRotation


def rotation():
    return RegionRotation(tuple(HuntingRegion(name=name,boundary=(0,y,20,y+19),patrol=((10,y+10),))
        for name,y in [('north',0),('center',20),('south',40)]))


def test_empty_regions_cycle_north_center_south_north():
    r=rotation()
    for start,y,name in [(0,10,'center'),(10,30,'south'),(20,50,'north')]:
        for t in range(start,start+8):assert r.observe(t,(10,y),False) is None
        assert r.observe(start+8,(10,y),False)['to_region']==name


def test_surviving_targets_prevent_rotation_even_when_most_samples_empty():
    r=rotation()
    for t in range(8):r.observe(t,(10,10),False)
    assert r.observe(8,(10,10),True) is None
    assert r.observe(9,(10,10),False)['empty_fraction']==.9


def test_busy_region_and_missing_reads_do_not_cause_rotation():
    r=rotation()
    for t in range(60):assert r.observe(t,(10,10),t%3!=0) is None
    for t in range(60,90):assert r.observe(t,(10,10),False,available=False) is None
    for t in range(90,98):assert r.observe(t,(10,10),False) is None
    assert r.observe(98,(10,10),False)['to_region']=='center'


def test_travel_does_not_skip_the_next_region():
    r=rotation()
    for t in range(9):r.observe(t,(10,10),False)
    for t in range(10,100):assert r.observe(t,(10,10),False) is None
    for t in range(100,108):assert r.observe(t,(10,30),False) is None
    assert r.observe(108,(10,30),False)['to_region']=='south'


def test_observation_gap_does_not_count_as_empty_time():
    r=rotation()
    for t in range(8):r.observe(t,(10,10),False)
    assert r.observe(100,(10,10),False) is None
    for t in range(101,108):assert r.observe(t,(10,10),False) is None
    assert r.observe(108,(10,10),False)['to_region']=='center'
