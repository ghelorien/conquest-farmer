from scripts.report_unified_validation import completed_cycles


def cycle():
    return [
        dict(time=1, event="kill_verified", count=2),
        dict(time=2, event="return_required", reason="ammo_unavailable"),
        dict(time=3, event="valuable_stored", uid=123, verified_in_warehouse=True),
        dict(
            time=4,
            event="restock_complete",
            supplies=dict(arrows=5000, potions=5, free_slots=30),
        ),
        dict(time=5, event="farming_area_reached"),
        dict(time=6, event="kill_verified", count=1),
    ]


def test_natural_cycle_requires_ordered_storage_arrival_and_resumed_kills():
    rows = cycle()
    assert completed_cycles(rows)[0]["hunting_resumed_at"] == 6
    for index in range(len(rows)):
        assert not completed_cycles(rows[:index] + rows[index + 1 :])


def test_forced_trip_unverified_storage_and_unrelated_events_never_qualify():
    rows = cycle()
    rows[1]["validation_cycle"] = True
    assert not completed_cycles(rows)
    rows = cycle()
    rows[2]["verified_in_warehouse"] = False
    assert not completed_cycles(rows)
    rows = cycle()
    rows[2]["time"] = 0.5
    assert not completed_cycles(rows)
    rows = cycle()
    rows[4]["time"] = 3.5
    assert not completed_cycles(rows)


def test_extension_counts_later_downtime_and_preserves_original_baseline(tmp_path):
    import json, sqlite3
    from scripts.report_unified_validation import report
    from conquest.discord_notify import write_json

    config = tmp_path / "reports/performance/unified-validation.json"
    write_json(
        config, {"started_at": 100, "duration_seconds": 120, "baseline_event_id": 0}
    )
    database = tmp_path / "reports/desktop-farming/trial.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as db:
        db.execute("create table events(time real,event text,payload text)")
        db.executemany(
            "insert into events values(?,?,?)",
            [
                (110, "kill_verified", json.dumps({"count": 120})),
                (240, "kill_verified", json.dumps({"count": 30})),
            ],
        )
    baseline = report(tmp_path, now=300)
    output = tmp_path / "reports/performance/unified-validation-report.json"
    preserved = output.read_bytes()
    configuration = config.read_bytes()
    extended = report(tmp_path, now=300, extend=True)
    assert baseline["verified_kills"] == 120 and baseline["elapsed_seconds"] == 120
    assert extended["verified_kills"] == 150 and extended["elapsed_seconds"] == 200
    assert extended["overall_kills_per_minute"] == 45
    assert output.read_bytes() == preserved and config.read_bytes() == configuration
