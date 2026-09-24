"""Farmer-side client for the authenticated merchant reservation protocol."""

from conquest.merchants.bridge import request


class DeliveryPeer:
    def __init__(self, send=request):
        self.send = send

    def command(self, action, key, intent, phase):
        body = {
            "action": action,
            "character": intent["merchant"]["character"],
            "request_id": key,
        }
        uids = [item["uid"] for item in intent["items"]]
        if action == "delivery-reserve":
            body["uids"] = uids
        result = self.send(body)
        if (
            result.get("request_id") != key
            or result.get("phase") != phase
            or sorted(result.get("uids", [])) != sorted(uids)
        ):
            raise ValueError("Merchant delivery acknowledgement is missing or changed")
        return result

    def reserve(self, key, intent):
        return self.command("delivery-reserve", key, intent, "reserved")

    def ready(self, key, intent):
        return self.command("delivery-ready", key, intent, "offer_ready")

    def finish(self, key, intent):
        return self.command("delivery-finish", key, intent, "verified")

    def disposition(self, key, intent, outcome):
        if outcome not in ("no_transfer", "partial_transfer"):
            raise ValueError("Unknown delivery disposition")
        body = {
            "action": "delivery-disposition",
            "character": intent["merchant"]["character"],
            "request_id": key,
            "outcome": outcome,
        }
        result = self.send(body)
        if (
            result.get("request_id") != key
            or result.get("outcome") != outcome
            or not isinstance(result.get("proof_digest"), str)
        ):
            raise ValueError(
                "Merchant delivery disposition acknowledgement is missing or changed"
            )
        return result
