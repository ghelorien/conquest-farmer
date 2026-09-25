"""Recurring work is gated by actual durable live transaction receipts."""

from conquest.character_context import state_path
import json
from pathlib import Path
from conquest.merchants.journal import Journal


def verify_rollout(
    path=state_path("reports/merchants/rollout.json"),
    journal=None,
    qualification_dir=state_path(".runtime/merchants"),
):
    """No client build has qualified recurring-rollout receipts.

    The only receipts this gate ever accepted were stamped with the retired
    1074 client, so recurring work stays paused on every build: an absent or
    unreadable rollout is incomplete and any readable one belongs to another
    build. The journal is still opened first, as it always was.
    """
    journal = journal or Journal()
    try:
        rollout = json.loads(Path(path).read_text())
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError(
            "Live rollout is incomplete; recurring work remains paused"
        ) from None
    # A non-object rollout still fails here exactly as it always has.
    rollout.get("client_sha256")
    raise ValueError("Rollout belongs to another client build")
