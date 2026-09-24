from conquest.merchants.delivery_status import describe


def test_only_resolved_terminal_status_releases_route():
    receipt = {
        "request_id": "x",
        "outcome": "transferred",
        "next_action": "finalize_receiver_receipt",
    }
    assert describe(receipt, running=True)["state"] == "verifying"
    assert describe(receipt, running=False)["requires_attention"]
    receipt["next_action"] = "release_route"
    assert describe(receipt, running=False)["state"] == "returning"


def test_proven_untouched_rejection_is_deferred_not_failure():
    receipt = {
        "request_id": "x",
        "outcome": "retryable_before_input",
        "next_action": "release_route",
    }
    result = describe(receipt, running=False)
    assert result["state"] == "safely deferred" and not result["requires_attention"]
    assert describe(None, running=False)["requires_attention"]
