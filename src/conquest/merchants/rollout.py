"""Recurring work is gated by actual durable live transaction receipts."""

from conquest.character_context import state_path
import json
from pathlib import Path
from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.journal import CHARACTERS, Journal


def verify_rollout(
    path=state_path("reports/merchants/rollout.json"),
    journal=None,
    qualification_dir=state_path(".runtime/merchants"),
):
    journal = journal or Journal()
    try:
        rollout = json.loads(Path(path).read_text())
        if rollout.get("client_sha256") != CLIENT_SHA256:
            raise ValueError("Rollout belongs to another client build")
        for character in CHARACTERS:
            profile = json.loads(
                (
                    Path(qualification_dir) / character.lower() / "qualification.json"
                ).read_text()
            )
            if (
                profile.get("client_sha256") != CLIENT_SHA256
                or profile.get("character") != character
                or profile.get("server") != "America"
                or not profile.get("evidence")
                or not all(
                    profile.get("capabilities", {}).get(c) is True
                    for c in ("trade", "trade_request", "booth_input")
                )
            ):
                raise ValueError("Live qualification is incomplete")
            receipts = rollout["characters"][character]
            records = {}
            with journal.db() as db:
                for key in ("delivery", "listing", "repricing"):
                    row = db.execute(
                        "SELECT * FROM transactions WHERE id=?", (receipts[key],)
                    ).fetchone()
                    if (
                        not row
                        or row["character"] != character
                        or row["phase"] != "verified"
                        or row["kind"]
                        != ("delivery" if key == "delivery" else "listing")
                    ):
                        raise ValueError(
                            "Live transaction receipt is missing or unverified"
                        )
                    records[key] = json.loads(row["before_json"])
            delivery, listing, reprice = (
                records[k] for k in ("delivery", "listing", "repricing")
            )
            if (
                listing["uid"] not in {i["uid"] for i in delivery["trade"]["items"]}
                or reprice["uid"] != listing["uid"]
                or reprice["item"]["price"] is None
                or reprice["price"] == reprice["item"]["price"]
                or any(
                    s.get("server") != "America" or s.get("character") != character
                    for s in (delivery, listing["snapshot"], reprice["snapshot"])
                )
            ):
                raise ValueError(
                    "Rollout needs a received item followed by verified listing and repricing"
                )
        return rollout
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError(
            "Live rollout is incomplete; recurring work remains paused"
        ) from None
