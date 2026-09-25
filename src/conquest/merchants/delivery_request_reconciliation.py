"""Exact ownership of an already-submitted request; never resends or accepts."""

from conquest.merchants.delivery import validate_snapshot
from conquest.merchants.manual_sessions import canonical_ownership


def ownership(intent, farmer, merchant, *, now):
    """Exact saved owners with just the intended incoming request added."""
    expected_name = intent["farmer"]["character"]
    expected_uid = intent["farmer"]["character_uid"]
    request = merchant.get("request")
    if (
        farmer.get("request") is not None
        or not isinstance(request, dict)
        or request.get("participant") != expected_name
        or type(request.get("participant_uid")) is not int
        or request["participant_uid"] != expected_uid
        or request.get("message") != expected_name + " wishes to trade with you."
        or request.get("server", merchant.get("server")) != intent["farmer"]["server"]
    ):
        raise ValueError("The exact submitted farmer request is not present")
    result = {}
    for role, snapshot in (("farmer", farmer), ("merchant", merchant)):
        before = intent[role]
        if before.get("map_id") != 1036:
            raise ValueError("Submitted request was not prepared in Market")
        validate_snapshot(snapshot, before["character"], now)
        observed = canonical_ownership(snapshot, require_closed=False)
        original = canonical_ownership(before)
        # All ownership, process and character fields stay fixed. The one
        # permitted difference is the exact receiver request verified above.
        comparable = {**observed, "request": None}
        if (
            comparable != original
            or snapshot.get("position") != before.get("position")
            or any(
                {item["uid"]: item.get("slot") for item in snapshot[field]}
                != {item["uid"]: item.get("slot") for item in before[field]}
                for field in ("inventory", "booth")
            )
        ):
            raise ValueError(
                "Submitted request participants, position or ownership changed"
            )
        result[role] = {
            **observed,
            "position": snapshot["position"],
            "map_id": snapshot["map_id"],
        }
    return result
