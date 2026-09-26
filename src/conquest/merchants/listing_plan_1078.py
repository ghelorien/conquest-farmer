"""Shared read-only price queue for supervised and recurring 1078 listings."""

from conquest.merchants.refill_preview_1078 import (
    _owned_profiles,
    _same_stock,
    _saved_prices,
    _queue,
)
from conquest.merchants.observe_1078 import observe
from conquest.merchants.restoration_preview_1078 import _preview
from conquest.memory_build_layout import CLIENT_SHA256_1078


# An item whose listing keeps aborting before its OK must not hold the rest of
# the queue: both the refill's choice and the one-shot "highest-valued item"
# rule read this plan. Three verified-unchanged aborts skip it for a day.
STUCK_ABORTS = 3
STUCK_SECONDS = 24 * 3600
# Aborts before the 150 ms price-dialog clicks went live (r64, 09-26 13:20)
# were the lost-click defect, not the item; they never mark an item stuck.
STUCK_SINCE = 1790443240.0


def _stuck_items(journal, character):
    import json
    import time
    from conquest.merchants.booth_listing_once_1078 import KIND
    from conquest.merchants.journal import character_name

    counts = {}
    with journal.db() as db:
        for row in db.execute(
            "SELECT before_json FROM transactions WHERE kind=? AND character=? "
            "AND phase='aborted' AND created > ?",
            (
                KIND,
                character_name(character),
                max(time.time() - STUCK_SECONDS, STUCK_SINCE),
            ),
        ):
            uid = (json.loads(row["before_json"] or "{}").get("request") or {}).get(
                "item_uid"
            )
            counts[uid] = counts.get(uid, 0) + 1
    return {uid for uid, count in counts.items() if count >= STUCK_ABORTS}


def _deferred(item, stuck):
    """Why an item cannot be listed now, or None."""
    # Live 09-26: a DragonBall's price dialog never took the price key in eight
    # attempts; each aborted with unchanged stock while it blocked the queue.
    if item["uid"] in stuck:
        return "repeated_pre_confirmation_aborts"
    return None


class OwnedPeerUnavailable(ValueError):
    """A named owned peer could not supply the fresh price-floor proof."""

    def __init__(self, character, error):
        self.character = character
        super().__init__(f"{character} owned booth observation unavailable: {error}")


def _peer(runtime, profile):
    try:
        return observe(runtime, profile.id)
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise OwnedPeerUnavailable(profile.name, error) from error


def plan(runtime, character, snapshot):
    from conquest.merchants.booth_listing_once_1078 import _profile, _item_fingerprint

    profile = _profile(character)
    if (
        snapshot["character_uid"] != profile.character_uid
        or snapshot["character"] != profile.name
    ):
        raise ValueError("Owned pricing requires the exact configured merchant")
    snapshot = {
        **snapshot,
        "profile_id": profile.id,
        "profile_uid_verified": True,
        "client_sha256": CLIENT_SHA256_1078,
        "closed_modal": snapshot.get(
            "closed_modal",
            snapshot.get("trade") is None and snapshot.get("request") is None,
        ),
    }
    profiles = _owned_profiles()
    if profile.id not in {other.id for other in profiles}:
        raise ValueError("Selected merchant is not a configured local owned profile")
    peers = [_peer(runtime, other) for other in profiles if other.id != profile.id]
    if len({source["character_uid"] for source in (snapshot, *peers)}) != len(profiles):
        raise ValueError("Owned merchant identity attribution is ambiguous")
    path = runtime.market_path.with_name("price-history.sqlite3")
    catalog, quotes = _saved_prices(path)
    names = ("shop_return", "recovery_safety", "connect_hold", "refill")
    state = {key: runtime.journal.get(character, key) for key in names}
    from conquest.merchants.restoration_preview_1078 import (
        listing_receipts,
        sale_receipts,
    )

    state["verified_listing_receipts_1078"] = listing_receipts(
        runtime.journal.path, profile.id
    )
    incident = state["shop_return"] or {}
    since = incident.get("started_at") or 0
    state["verified_sale_receipts"] = sale_receipts(
        runtime.journal.path, profile.id, since
    )
    restoration = (
        _preview(snapshot, state)
        if incident.get("phase") not in (None, "complete", "operator_overridden")
        else None
    )
    if (
        state["connect_hold"]
        or (state["recovery_safety"] or {}).get("active")
        or restoration
        and "prior_listing_price_changed" in restoration["blockers"]
    ):
        raise ValueError(
            "Shop restoration has an unresolved ownership or recovery hold"
        )
    # The preview's exact queue: merchant-held Dragonballs carry the same
    # not-bound, last-refresh and lowest-comparable guard in both callers.
    rows = _queue(snapshot, catalog, quotes, restoration, owned_snapshots=peers)
    for peer in peers:
        peer_profile = next(
            other for other in profiles if other.id == peer["profile_id"]
        )
        if not _same_stock(peer, _peer(runtime, peer_profile)):
            raise ValueError("Owned peer booth changed while computing listing prices")
    if (
        _owned_profiles() != profiles
        or _saved_prices(path) != (catalog, quotes)
        or any(runtime.journal.get(character, key) != state[key] for key in names)
        or listing_receipts(runtime.journal.path, profile.id)
        != state["verified_listing_receipts_1078"]
        or sale_receipts(runtime.journal.path, profile.id, since)
        != state["verified_sale_receipts"]
    ):
        raise ValueError("Owned profiles, saved prices, or restoration intent changed")
    items = {item["uid"]: item for item in snapshot["inventory"]}
    stuck = _stuck_items(runtime.journal, character)
    return [
        {
            **row,
            "price": None
            if _deferred(items[row["uid"]], stuck)
            else row["total_listing_price"],
            "deferred_reason": _deferred(items[row["uid"]], stuck),
            "attributes": _item_fingerprint(items[row["uid"]]),
            "reference": row["source"],
        }
        for row in rows
    ]
