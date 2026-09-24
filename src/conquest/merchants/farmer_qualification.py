"""Farmer/build-scoped delivery qualification and evidence-bound migration."""

from pathlib import Path
import re

from conquest.character_context import current, registry, state_path
from conquest.discord_notify import read_json, write_json


LEGACY = ".runtime/merchants/farmer-delivery-qualified.json"


def _context(character, build):
    context = current()
    if context is None:
        if registry() is not None:
            raise ValueError(
                "Select a farmer profile before using delivery qualification"
            )
        return None
    if (
        context.profile.role != "Farmer"
        or context.profile.name != character
        or context.profile.server != "America"
        or not context.profile.local_enabled
    ):
        raise ValueError(
            "Delivery qualification does not belong to the selected farmer"
        )
    if not isinstance(build, str) or re.fullmatch(r"[0-9a-fA-F]{64}", build) is None:
        raise ValueError("Delivery qualification needs an exact client build identity")
    return context


def _path(context, build):
    # Keep Windows temporary-file paths short. The full build hash inside the
    # document is always checked; the filename prefix is never identity proof.
    return (
        context.state_dir / ".runtime" / "delivery-q" / (build[:16].lower() + ".json")
    )


def _matches(data, context, build, *, legacy=False):
    profile = context.profile
    return (
        data.get("character") == profile.name
        and data.get("server") == profile.server
        and data.get("client_sha256") == build
        and data.get("capabilities", {}).get("farmer_delivery") is True
        and bool(data.get("evidence"))
        and data.get("profile_id") in ((None, profile.id) if legacy else (profile.id,))
        and profile.character_uid is not None
        and data.get("character_uid")
        in ((None, profile.character_uid) if legacy else (profile.character_uid,))
    )


def _migration_receipt_matches(data, context):
    """An old display name alone cannot bind qualification to a new profile."""
    state = read_json(data.get("evidence", ""))
    intent = state.get("intent", {})
    before = intent.get("farmer", {})
    after = state.get("farmer_after", {})
    merchant = state.get("merchant_after", {})
    profile = context.profile
    if (
        state.get("phase") != "delivery_verified"
        or not state.get("verified_at")
        or not intent.get("items")
        or any(
            snapshot.get("character") != profile.name
            or snapshot.get("server") != profile.server
            or snapshot.get("character_uid") != profile.character_uid
            for snapshot in (before, after)
        )
        or before.get("identity") != after.get("identity")
        or intent.get("merchant", {}).get("identity") != merchant.get("identity")
    ):
        return False
    from conquest.merchants.delivery import reconcile

    return reconcile(
        intent,
        after,
        merchant,
        now=max(after.get("timestamp", 0), merchant.get("timestamp", 0)),
    )


def qualification_path(observer, *, migrate=True):
    """Resolve at use time so changing active profiles cannot reuse a constant."""
    build = observer.adapter.expected_sha256
    context = _context(observer.character, build)
    if context is None:
        return Path(state_path(LEGACY))
    destination = _path(context, build)
    if destination.exists():
        if not _matches(read_json(destination), context, build):
            raise ValueError(
                "Stored delivery qualification has a different profile or build identity"
            )
        return destination
    old_path = Path(state_path(LEGACY))
    old = read_json(old_path) if migrate else {}
    if (
        old
        and _matches(old, context, build, legacy=True)
        and _migration_receipt_matches(old, context)
    ):
        migrated = {
            **old,
            "profile_id": context.profile.id,
            "character_uid": context.profile.character_uid,
            "qualification_scope_version": 1,
            "migrated_from": str(old_path),
        }
        # Keep the original evidence and file intact for other profile checks.
        write_json(destination, migrated)
    return destination


def promotion_destination(requested_path, data, farmer):
    """Bind new proof to the selected profile; retain standalone explicit paths."""
    context = _context(farmer["character"], data["client_sha256"])
    if context is None:
        return Path(requested_path), data
    if (
        context.profile.character_uid is None
        or farmer.get("character_uid") != context.profile.character_uid
        or farmer.get("server") != context.profile.server
    ):
        raise ValueError(
            "Bind the exact farmer UID before promoting delivery qualification"
        )
    return _path(context, data["client_sha256"]), {
        **data,
        "profile_id": context.profile.id,
        "character_uid": context.profile.character_uid,
        "qualification_scope_version": 1,
    }
