from conquest.route_recording import record_route


def run(points, spacing=3):
    now = [0.0]
    samples = iter(points)
    return record_route(
        lambda: next(samples),
        len(points),
        interval=1,
        spacing=spacing,
        clock=lambda: now[0],
        sleep=lambda n: now.__setitem__(0, now[0] + n),
    )


def test_stationary_samples_are_kept_as_evidence_without_duplicate_waypoints():
    result = run(
        [
            (1002, (430, 380)),
            (1002, (430, 380)),
            (1002, (432, 380)),
            (1002, (434, 380)),
            (1002, (435, 380)),
        ]
    )
    assert result["route"] == [[430, 380], [434, 380], [435, 380]]
    assert len(result["observations"]) == 5
    assert not result["qualified"] and not result["input_sent"]


def test_map_changes_and_teleports_cannot_create_false_connected_routes():
    result = run([(1002, (430, 380)), (2000, (440, 390))])
    assert result["reason"] == "map_changed" and result["route"] == [[430, 380]]
    result = run([(1002, (430, 380)), (1002, (700, 700))])
    assert result["reason"] == "position_discontinuity" and result["route"] == [
        [430, 380]
    ]
