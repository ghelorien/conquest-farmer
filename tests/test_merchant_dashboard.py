from conquest.merchants.dashboard import countdown, header_text


def test_on_sale_counts_listing_prices_once_and_excludes_inventory_and_stale_stock():
    from conquest.merchants.dashboard import on_sale_text

    states = {
        "Spiritual": {
            "connected": True,
            "snapshot": {
                "timestamp": 100,
                "booth_open": True,
                "booth": [
                    {"price": 1000000, "quantity": 10},
                    {"price": 250000, "quantity": 1},
                ],
                "inventory": [{"price": 9000000}],
            },
        },
        "Dutch": {
            "connected": True,
            "snapshot": {
                "timestamp": 100,
                "booth_open": True,
                "booth": [{"price": 500000, "quantity": 1}],
            },
        },
    }
    text = on_sale_text(states, now=101)
    assert "Currently on sale: 1,750,000 silver" in text
    assert "Spiritual: 1,250,000" in text and "Dutch: 500,000" in text
    states["Dutch"]["snapshot"]["timestamp"] = 80
    text = on_sale_text(states, now=101)
    assert "1,250,000 silver (partial)" in text and "Dutch: unavailable" in text
    states["Spiritual"]["snapshot"]["booth"] = []
    assert "0 silver (partial)" in on_sale_text(states, now=101)
    states["Spiritual"]["connected"] = False
    assert "Currently on sale: unavailable" in on_sale_text(states, now=101)


def test_countdown_rounds_up_and_never_goes_negative():
    assert countdown(3661.1, 0) == "01:01:02"
    assert countdown(10, 20) == "00:00:00"


def test_header_uses_lifetime_verified_receipts_and_respects_pause():
    sales = {
        "characters": {
            "Spiritual": {
                "started_at": 0,
                "last_observed_at": 100,
                "total": {"silver": 5000000},
                "period": {"silver": 1},
            },
            "Dutch": {
                "started_at": 0,
                "last_observed_at": 100,
                "total": {"silver": 900000},
                "unconfirmed": 2,
            },
        }
    }
    states = {
        "Spiritual": {"enabled": False, "scan": {"next_scan": 110}},
        "Dutch": {"enabled": True, "scan": {"next_scan": 200}},
    }
    result = header_text(
        sales, {"status": "waiting", "next_due": 3700}, states, now=100
    )
    assert "01:00:00" in result["timers"]
    assert "Spiritual: paused" in result["timers"]
    assert "Dutch: 00:01:40" in result["timers"]
    assert "5,900,000 (since tracking began)" in result["silver"]
    assert "Spiritual: 5,000,000" in result["silver"]
    assert "Dutch: 900,000" in result["silver"]
    assert "Coverage incomplete" in result["silver"]


def test_missing_tracking_and_report_failure_are_visible():
    result = header_text(
        {},
        {"status": "needs_configuration", "next_due": 1, "retry_at": 160},
        {},
        now=100,
    )
    assert "needs configuration · retry 00:01:00" in result["timers"]
    assert "Net silver earned: unavailable" in result["silver"]
    assert "Dutch: unavailable" in result["silver"]
    assert (
        "due now"
        in header_text({}, {"status": "waiting", "next_due": 1}, {}, now=100)["timers"]
    )


def test_status_distinguishes_refill_from_paused_operations_and_explains_blockers():
    from conquest.merchants.dashboard import merchant_text

    state = {
        "connected": True,
        "enabled": False,
        "refill": {"enabled": True, "next_check": 400},
        "snapshot": {"inventory": [], "booth": [], "capacity": 40},
        "scan": {"pending": True},
    }
    text = merchant_text(state, now=100)
    assert (
        "Shop update paused" in text
        and "Resume shop update" in text
        and "Auto-refill ON" in text
    )
    state["market_refresh"] = {"pending": True, "phase": "fetching"}
    assert "Downloading America" in merchant_text(state, now=100)
    state["pending"] = [
        {
            "before_json": '{"item":{"name":"Ring"}}',
            "result_json": '{"note":"A trade interrupted the listing control"}',
        }
    ]
    text = merchant_text(state, now=100)
    assert "interrupted Ring" in text and "trade interrupted" in text


def test_active_listing_intent_is_progress_not_an_attention_error():
    from conquest.merchants.dashboard import merchant_text

    state = {
        "connected": True,
        "enabled": True,
        "input_active": True,
        "scan": {"pending": True, "request_id": "a"},
        "batch_progress": {
            "request_id": "a",
            "changed": 2,
            "remaining": 3,
            "item": "Ring",
        },
        "pending": [{"phase": "prepared", "before_json": '{"item":{"name":"Ring"}}'}],
    }
    text = merchant_text(state, now=100)
    assert "2 done" in text and "3 remaining" in text and "Needs attention" not in text
    state["pending"][0]["phase"] = "uncertain"
    assert "Needs attention" in merchant_text(state, now=100)


def test_current_peer_blocker_replaces_stale_refill_completion_countdown():
    from conquest.merchants.dashboard import merchant_text

    state = {
        "connected": True,
        "enabled": True,
        "snapshot": {"inventory": [{"uid": 1}], "booth": [], "capacity": 40},
        "refill": {
            "enabled": True,
            "status": "no_stock",
            "pending": False,
            "next_check": 80,
        },
        "foreground_refill_1078": {
            "state": "waiting",
            "blocker": "owned_peer_observation_unavailable",
            "unavailable_peer": "Spiritual",
        },
    }
    text = merchant_text(state, now=100)
    assert "Spiritual owned booth memory is unavailable" in text
    assert "next check in" not in text
