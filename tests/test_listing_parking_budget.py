import pytest

from test_merchant_handoff import town_service


@pytest.mark.parametrize("safe", [True, False])
def test_earned_listing_gets_thirty_seconds_without_retrying_interval(
    town_service, monkeypatch, safe
):
    from conquest.merchants import bridge, handoff
    from conquest import safe_reload

    r = town_service
    r.health["embedded_controls"]["life"]["map_id"] = 1011
    original = bridge.request
    events = []

    def merchant(body):
        result = original(body)
        if body["action"] == "status":
            result["handoff_requested"] = "merchant-refill:Dutch:123"
            result["characters"]["Dutch"].update(
                refill={"enabled": True},
                qualification={"foreground_open_booth_listing_1078": True},
            )
        return result

    monkeypatch.setattr(bridge, "request", merchant)
    monkeypatch.setattr(handoff.time, "monotonic", lambda: r.now[0])
    r.loop.record = lambda event, **fields: events.append((event, fields))

    def park(loop, cancelled, notify, **kwargs):
        assert kwargs["seconds"] == 30 and kwargs["allow_town_retreat"] is False
        cancelled.is_set()
        r.now[0] += 25 if safe else 30
        kwargs["diagnostic"].update(reached_steps=3, unreached_steps=1)
        if not safe:
            raise ValueError("No quiet nearby spot verified; reload deferred")
        return {"target": r.health["target"]}

    monkeypatch.setattr(safe_reload, "park", park)
    assert handoff.service_window(r.loop) is safe
    assert r.windows.state()["next_check"] == 1900
    grants = [c for c in r.calls if c["action"] == "handoff-grant"]
    assert bool(grants) is safe
    if safe:
        assert grants[0]["scope"] == "listing_1078" and grants[0]["expires_at"] == 1070
    else:
        assert r.windows.state()["phase"] == "unsafe_deferred"
    row = next(
        fields["parking"]
        for event, fields in events
        if event == "merchant_parking_finished"
    )
    assert row["outcome"] == ("safe" if safe else "deferred")
    assert row["elapsed_seconds"] == (25 if safe else 30)
    assert row["unreached_steps"] == 1
    assert not handoff.service_window(r.loop)  # No immediate second attempt.


def test_earned_market_listing_parking_still_uses_original_deadline(
    town_service, monkeypatch
):
    from conquest.merchants import bridge
    from conquest import safe_reload

    r = town_service
    r.visit.begin(parent="required-town:1")
    r.now[0] = 1058
    original = bridge.request

    def merchant(body):
        result = original(body)
        if body["action"] == "status":
            result["handoff_requested"] = "merchant-refill:Dutch:123"
            result["characters"]["Dutch"].update(
                refill={"enabled": True},
                qualification={"foreground_open_booth_listing_1078": True},
            )
        return result

    monkeypatch.setattr(bridge, "request", merchant)

    def park(loop, cancelled, notify, **kwargs):
        assert kwargs["seconds"] == 2 and kwargs["allow_town_retreat"] is False
        cancelled.is_set()
        r.now[0] += 2
        return {"target": r.health["target"]}

    monkeypatch.setattr(safe_reload, "park", park)
    assert not r.run()
    assert not any(c["action"] == "handoff-grant" for c in r.calls)
    assert r.windows.state()["deadline"] == 1060


@pytest.mark.parametrize("movement_error", [False, True])
@pytest.mark.parametrize("available", [False, True])
def test_park_diagnostic_preserves_evidence_without_claiming_safety(
    monkeypatch, movement_error, available
):
    import threading
    from types import SimpleNamespace as NS
    from conquest import safe_reload as s
    from test_safe_reload import health

    now = [1000.0]
    monkeypatch.setattr(s.time, "time", lambda: now[0])
    monkeypatch.setattr(s.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        s.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay)
    )
    h = health()
    h["embedded_controls"]["monsters"] = [{"position": [101, 350], "alive": None}]

    def living():
        h["embedded_controls"].update(
            observed_at=now[0] if available else None, observations_available=available
        )
        return h

    monkeypatch.setattr(
        "conquest.scene_input.memory_player_anchor", lambda *a: (518, 396)
    )
    monkeypatch.setattr(
        s, "nearby_escape", lambda *a, **kw: (102, 350) if movement_error else None
    )
    loop = NS(
        living=living,
        care=NS(check=lambda h: None, session=None),
        terrain=NS(map_id=1002),
        stepper=NS(
            step_to=lambda *a, **kw: {
                "reached": False,
                "error": "Route movement stopped progressing",
            }
        ),
    )
    diagnostic = {}
    with pytest.raises(ValueError, match="No quiet nearby spot"):
        s.park(
            loop,
            threading.Event(),
            lambda note: None,
            seconds=1,
            allow_town_retreat=False,
            diagnostic=diagnostic,
        )
    assert (
        diagnostic["nearby_threats"] == 1
        and diagnostic["last_observation_quiet"] is False
    )
    assert diagnostic["quiet_seconds"] == 0 and diagnostic["reached_steps"] == 0
    assert diagnostic["position"] == [100, 350] and diagnostic["hp"] == 600
    assert diagnostic["observation_age_seconds"] == (0 if available else None)
    if movement_error:
        assert diagnostic["unreached_steps"] > 0
        assert diagnostic["last_step_error"] == "Route movement stopped progressing"
    else:
        assert diagnostic["no_candidate_observations"] > 0
