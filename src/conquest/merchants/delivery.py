"""Exact two-account delivery plans and durable, fail-closed reconciliation."""

from conquest.merchants.capacity import available_slots
from conquest.character_context import farmer_name
import math
import time
import uuid
import json
import hashlib
from conquest.merchants.journal import CHARACTERS
from conquest.merchants.controller import identities
from conquest.valuables import SPECIAL_LOOT_TYPES, DRAGONBALL_TYPES, storage_only


def eligible(item, reserved=()):
    kind, plus, slot = item.get("type_id"), item.get("plus"), item.get("slot")
    if kind == 1088001:
        return False  # Bank loose Meteors; transfer only scrolls.
    if (
        item.get("uid") in reserved
        or item.get("bound") is not False
        or type(slot) is not int
        or not 0 <= slot < 40
        or storage_only(item)
    ):
        return False
    return kind in SPECIAL_LOOT_TYPES or (
        type(kind) is int
        and 100000 <= kind < 600000
        and (kind % 10 == 9 or type(plus) is int and 1 <= plus <= 12)
    )


def exact_items(items):
    """UID keyed ownership including every value-bearing item attribute."""
    result = identities(items)
    for item in items:
        if type(item.get("bound")) is not bool:
            raise ValueError("Item binding is unknown")
        result[item["uid"]] = (*result[item["uid"]], item["bound"])
    return result


def exact_listings(items):
    result = exact_items(items)
    return {
        uid: (*details, next(item.get("price") for item in items if item["uid"] == uid))
        for uid, details in result.items()
    }


class ReconciliationBlocked(ValueError):
    """Exact bilateral ownership cannot yet prove a terminal disposition."""

    def __init__(self, message, *, code="evidence_unavailable"):
        super().__init__(message)
        self.code = code


def _empty_pair_trade(snapshot, other):
    trade = snapshot.get("trade")
    if not trade:
        return True
    return (
        trade.get("participant") == other["character"]
        and trade.get("participant_uid") == other["character_uid"]
        and not trade.get("own_items")
        and not trade.get("items")
        and trade.get("own_silver") == 0
        and trade.get("other_silver") == 0
        and trade.get("accepted") is False
        and trade.get("other_accepted") is False
    )


def _trace_ready(trace):
    return any(
        step.get("stage") == "action_trace" and step.get("status") == "initialized"
        for step in (trace or ())
    )


SETTLEMENT_WINDOW = 5


def ownership_digest(intent, farmer, merchant, sale_receipts=()):
    """Stable digest for repeated read-only ownership observations."""
    return _proof_digest(intent, farmer, merchant, (), sale_receipts)


def _negative_outcome_proven(trace, digest, now):
    actions = [step for step in (trace or ()) if step.get("status") == "before_action"]
    if not actions:
        # The instrumented driver records immediately before every possible
        # input. An initialized trace with no boundary proves this worker never
        # attempted gameplay input.
        return True
    terminal = [
        step
        for step in (trace or ())
        if step.get("stage") == "trade_session"
        and step.get("status") == "terminal"
        and step.get("payload", {}).get("outcome")
        in ("rejected", "cancelled_unaccepted")
        and step.get("payload", {}).get("ownership_digest") == digest
    ]
    if terminal:
        return True
    if any(step.get("stage") == "farmer_confirm" for step in actions):
        return False
    observations = []
    for step in trace or ():
        payload = step.get("payload") or {}
        if (
            step.get("stage") == "reconciliation_observation"
            and step.get("status") == "observed"
            and payload.get("ownership_digest") == digest
            and type(payload.get("observed_at")) in (int, float)
            and payload["observed_at"] <= now
        ):
            observations.append(payload["observed_at"])
    return bool(
        observations and max(observations) - min(observations) >= SETTLEMENT_WINDOW
    )


def _proof_digest(intent, farmer, merchant, trace, sale_receipts=()):
    # Observations receive full journal receipts; reconciliation receives the
    # validated canonical receipts. Hash the same value-bearing fields in both
    # paths so a verified concurrent sale cannot prevent stable settlement.
    sale_receipts = [
        {
            "id": receipt.get("id"),
            "observed_at": receipt.get("observed_at"),
            "silver": receipt.get("silver"),
            "items": [
                {
                    name: item.get(name)
                    for name in (
                        "uid",
                        "type_id",
                        "plus",
                        "gem1",
                        "gem2",
                        "quantity",
                        "bound",
                        "price",
                    )
                }
                for item in receipt.get("items", [])
            ],
        }
        for receipt in sale_receipts or ()
    ]
    durable = []
    observations = {}
    for step in trace or ():
        record = {k: step.get(k) for k in ("stage", "status", "payload")}
        if step.get("stage") == "reconciliation_observation":
            payload = step.get("payload") or {}
            digest = payload.get("ownership_digest")
            if isinstance(digest, str):
                observations.setdefault(digest, []).append(record)
        elif (
            step.get("stage") == "action_trace"
            or step.get("status") in ("before_action", "observed", "terminal")
            and step.get("stage")
            not in ("failure", "cleanup", "cleanup_trade", "receiver_receipt")
        ):
            durable.append(record)
    # Bind the minimum stable observation window. Later idempotent recovery
    # polls must not change an already committed disposition digest.
    action_attempted = any(row["status"] == "before_action" for row in durable)
    for records in observations.values() if action_attempted else ():
        records.sort(key=lambda row: (row.get("payload") or {}).get("observed_at", 0))
        if records:
            durable.append(records[0])
            start = records[0]["payload"]["observed_at"]
            second = next(
                (
                    row
                    for row in records[1:]
                    if row["payload"]["observed_at"] - start >= SETTLEMENT_WINDOW
                ),
                None,
            )
            if second is not None:
                durable.append(second)
    proof = {
        "operation": {
            name: intent.get(name)
            for name in (
                "operation_id",
                "town_visit_id",
                "visit_id",
                "farmer_profile_id",
            )
        },
        "operation_items": exact_items(intent["items"]),
        "farmer": {
            "identity": farmer["identity"],
            "character_uid": farmer["character_uid"],
            "silver": farmer["silver"],
            "inventory": exact_items(farmer["inventory"]),
            "booth": exact_listings(farmer.get("booth", [])),
            "trade": farmer.get("trade"),
            "request": farmer.get("request"),
        },
        "merchant": {
            "identity": merchant["identity"],
            "character_uid": merchant["character_uid"],
            "silver": merchant["silver"],
            "inventory": exact_items(merchant["inventory"]),
            "booth": exact_listings(merchant.get("booth", [])),
            "trade": merchant.get("trade"),
            "request": merchant.get("request"),
        },
        "sale_receipts": sale_receipts,
        "trace": durable,
    }
    return hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()


def _sale_adjustment(intent, merchant, receipts):
    original = exact_listings(intent["merchant"].get("booth", []))
    sold = {}
    canonical = []
    from conquest.merchants.sales import net_bounds

    for receipt in receipts or ():
        if (
            type(receipt.get("id")) is not int
            or receipt["id"] <= 0
            or receipt.get("phase") != "verified"
            or type(receipt.get("observed_at")) not in (int, float)
            or not max(intent["farmer"]["timestamp"], intent["merchant"]["timestamp"])
            < receipt["observed_at"]
            <= merchant["timestamp"]
            or type(receipt.get("silver")) is not int
            or receipt["silver"] < 0
        ):
            raise ReconciliationBlocked(
                "Sale receipt is not qualified for this operation"
            )
        items = receipt.get("items")
        try:
            listed = exact_listings(items)
        except (ValueError, KeyError, TypeError) as error:
            raise ReconciliationBlocked(
                "Sale receipt lacks exact item attributes"
            ) from error
        if (
            not listed
            or set(listed) & set(sold)
            or any(original.get(uid) != details for uid, details in listed.items())
        ):
            raise ReconciliationBlocked(
                "Sale receipt stock does not match the original booth"
            )
        low, high = net_bounds(items)
        if not low <= receipt["silver"] <= high:
            raise ReconciliationBlocked(
                "Sale receipt currency does not match exact listing value"
            )
        sold.update(listed)
        canonical.append(
            {
                "id": receipt["id"],
                "observed_at": receipt["observed_at"],
                "items": [
                    {
                        name: item.get(name)
                        for name in (
                            "uid",
                            "type_id",
                            "plus",
                            "gem1",
                            "gem2",
                            "quantity",
                            "bound",
                            "price",
                        )
                    }
                    for item in items
                ],
                "silver": receipt["silver"],
            }
        )
    current_inventory = exact_items(merchant["inventory"])
    if set(sold) & set(current_inventory):
        raise ReconciliationBlocked("Sold booth stock is present in merchant inventory")
    expected_booth = {
        uid: details for uid, details in original.items() if uid not in sold
    }
    return expected_booth, sum(row["silver"] for row in canonical), canonical


def reconciliation_outcome(
    intent, farmer, merchant, *, trace=None, sale_receipts=(), now=None
):
    """Classify exact bilateral ownership without authorizing any new input.

    A legacy transaction without an initialized action trace may still prove a
    completed delivery, but it cannot be automatically declared a no-transfer
    or partial disposition.
    """
    now = time.time() if now is None else now
    try:
        source = validate_snapshot(farmer, farmer_name(), now)
        destination = validate_snapshot(merchant, intent["merchant"]["character"], now)
        expected_booth, sale_silver, sales = _sale_adjustment(
            intent, merchant, sale_receipts
        )
        cleanup = []
        for role, current, other in (
            ("farmer", farmer, merchant),
            ("merchant", merchant, farmer),
        ):
            before = intent[role]
            expected_silver = before["silver"] + (
                sale_silver if role == "merchant" else 0
            )
            booth = (
                expected_booth
                if role == "merchant"
                else exact_listings(before.get("booth", []))
            )
            if (
                current["identity"] != before["identity"]
                or current["character_uid"] != before["character_uid"]
                or current["silver"] != expected_silver
                or exact_listings(current.get("booth", [])) != booth
            ):
                raise ReconciliationBlocked(
                    "Participant identity, currency or booth stock changed"
                )
            if not _empty_pair_trade(current, other):
                raise ReconciliationBlocked(
                    "A non-empty or unrelated trade remains active"
                )
            if current.get("trade"):
                cleanup.append(current["character"])
        if farmer.get("request"):
            raise ReconciliationBlocked("A farmer request remains active")
        request = merchant.get("request")
        if request and (
            request.get("participant") == farmer["character"]
            or request.get("participant_uid") == farmer["character_uid"]
        ):
            raise ReconciliationBlocked("The reserved farmer request remains active")
        wanted = exact_items(intent["items"])
        before_source = exact_items(intent["farmer"]["inventory"])
        before_destination = exact_items(intent["merchant"]["inventory"])
        from conquest.merchants.operator_addition import additions

        external, operator_receipts = additions(intent, farmer, merchant, trace, now)
        moved = {
            uid: details
            for uid, details in wanted.items()
            if uid not in source and destination.get(uid) == details
        }
        remaining = {
            uid: details
            for uid, details in wanted.items()
            if source.get(uid) == details and uid not in destination
        }
        if set(moved) | set(remaining) != set(wanted) or set(moved) & set(remaining):
            raise ReconciliationBlocked("Reserved items have ambiguous ownership")
        if source != {
            uid: details for uid, details in before_source.items() if uid not in moved
        }:
            raise ReconciliationBlocked("Farmer surrounding inventory changed")
        if destination != {**before_destination, **moved, **external}:
            raise ReconciliationBlocked("Merchant surrounding inventory changed")
        if not remaining:
            outcome = "delivered"
        elif not moved:
            outcome = "no_transfer"
        else:
            outcome = "partial_transfer"
        if outcome != "delivered":
            if not _trace_ready(trace):
                raise ReconciliationBlocked(
                    "Legacy transaction lacks an explicit action trace"
                )
            if cleanup:
                raise ReconciliationBlocked("The attempted trade session is still open")
            digest = ownership_digest(intent, farmer, merchant, sales)
            if not _negative_outcome_proven(trace, digest, now):
                raise ReconciliationBlocked(
                    "The attempted trade lacks stable terminal settlement evidence",
                    code="settlement_pending",
                )
        return {
            "outcome": outcome,
            "delivered": [i for i in intent["items"] if i["uid"] in moved],
            "remaining": [i for i in intent["items"] if i["uid"] in remaining],
            "sale_receipts": sales,
            "operator_additions": operator_receipts,
            "cleanup_pending": cleanup,
            "farmer": farmer,
            "merchant": merchant,
            "proof_digest": _proof_digest(intent, farmer, merchant, trace, sales),
            "reconciled_at": now,
        }
    except ReconciliationBlocked:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise ReconciliationBlocked(
            str(error) or "Bilateral ownership is unavailable"
        ) from error


def validate_snapshot(snapshot, character, now):
    if (
        snapshot.get("character") != character
        or snapshot.get("server") != "America"
        or not snapshot.get("identity")
        or type(snapshot.get("character_uid")) is not int
        or snapshot["character_uid"] <= 0
        or not 0 <= now - snapshot.get("timestamp", 0) <= 5
        or snapshot.get("hp", 0) <= 0
        or snapshot.get("map_id") != 1036
        or type(snapshot.get("silver")) is not int
        or snapshot["silver"] < 0
    ):
        raise ValueError(
            "Delivery requires fresh, living, identified Market participants"
        )
    inventory = exact_items(snapshot["inventory"])
    if not 0 <= len(inventory) <= snapshot["capacity"] <= 40:
        raise ValueError("Invalid delivery inventory capacity")
    if set(inventory) & set(exact_items(snapshot.get("booth", []))):
        raise ValueError("Ambiguous booth and inventory ownership")
    return inventory


def plan_deliveries(farmer, merchants, *, reserved=(), now=None):
    now = time.time() if now is None else now
    validate_snapshot(farmer, farmer_name(), now)
    candidates = []
    seen = set()
    for state in merchants:
        name = state.get("character")
        if name in seen:
            raise ValueError("Duplicate merchant capacity observation")
        seen.add(name)
        if name not in CHARACTERS or not state.get("ready"):
            continue
        snapshot = state["snapshot"]
        try:
            inventory = validate_snapshot(snapshot, name, now)
        except (ValueError, KeyError, TypeError):
            continue
        distance = state.get("verified_travel_distance")
        if (
            not snapshot.get("booth_open")
            or snapshot.get("trade")
            or snapshot.get("request")
            or type(distance) not in (int, float)
            or not math.isfinite(distance)
            or distance < 0
        ):
            continue
        space = available_slots(snapshot)
        if space:
            candidates.append((-space, distance, name, snapshot))
    # Spend limited merchant space on urgent valuables first; residual items
    # remain with the warehouse caller after all ready merchants are exhausted.
    items = sorted(
        (i for i in farmer["inventory"] if eligible(i, reserved)),
        key=lambda i: (
            0
            if i["type_id"] in DRAGONBALL_TYPES
            else 1
            if type(i.get("plus")) is int and i["plus"] >= 2
            else 2
        ),
    )
    plans = []
    for negative_space, distance, name, snapshot in sorted(
        candidates, key=lambda row: row[:3]
    ):
        # Reserve time for request/acceptance and both confirmations inside
        # the fifteen-second handoff, even with delayed server acknowledgments.
        for offset in range(0, -negative_space, 5):
            batch = items[: min(5, -negative_space - offset)]
            if not batch:
                break
            items = items[len(batch) :]
            plans.append(
                {
                    "merchant": name,
                    "items": batch,
                    "merchant_identity": snapshot["identity"],
                    "merchant_uid": snapshot["character_uid"],
                    "verified_travel_distance": distance,
                }
            )
    return {
        "deliveries": plans,
        "warehouse": [
            i
            for i in farmer["inventory"]
            if (storage_only(i) or i.get("type_id") == 1088001)
            and i.get("slot") is not None
        ]
        + items,
    }


def prepare(farmer, merchant, items, *, now=None):
    now = time.time() if now is None else now
    name = merchant.get("character")
    if name not in CHARACTERS:
        raise ValueError("Unknown delivery recipient")
    source = validate_snapshot(farmer, farmer_name(), now)
    destination = validate_snapshot(merchant, name, now)
    offered = exact_items(items)
    if (
        not 1 <= len(offered) <= 20
        or len(offered) > available_slots(merchant)
        or any(not eligible(i) for i in items)
        or any(source.get(uid) != details for uid, details in offered.items())
        or set(offered) & set(destination)
        or farmer.get("trade")
        or merchant.get("trade")
        or farmer.get("request")
        or merchant.get("request")
    ):
        raise ValueError(
            "Delivery identity, eligibility, capacity or modal state changed"
        )
    return {"farmer": farmer, "merchant": merchant, "items": items}


def validate_offers(intent, farmer, merchant, *, now=None):
    now = time.time() if now is None else now
    wanted = exact_items(intent["items"])
    for role, snapshot, other in (
        ("farmer", farmer, merchant),
        ("merchant", merchant, farmer),
    ):
        before = intent[role]
        validate_snapshot(snapshot, before["character"], now)
        trade = snapshot.get("trade")
        if (
            snapshot["identity"] != before["identity"]
            or snapshot["character_uid"] != before["character_uid"]
            or not trade
            or trade.get("participant") != other["character"]
            or trade.get("participant_uid") != other["character_uid"]
            or trade.get("own_silver") != 0
            or trade.get("other_silver") != 0
            or snapshot["silver"] != before["silver"]
        ):
            raise ValueError("Delivery participants or currency changed")
        if exact_items(snapshot.get("booth", [])) != exact_items(
            before.get("booth", [])
        ):
            raise ValueError("Booth stock changed during delivery")
        own, received = exact_items(trade["own_items"]), exact_items(trade["items"])
        if (own, received) != ((wanted, {}) if role == "farmer" else ({}, wanted)):
            raise ValueError("Trade contains missing, changed or unintended items")
    # Items offered by this client may move out of its inventory into its
    # trade deque. Reconcile their union rather than assume one representation.
    source = exact_items(farmer["inventory"])
    original = exact_items(intent["farmer"]["inventory"])
    if any(
        source.get(uid) != details
        for uid, details in original.items()
        if uid not in wanted
    ):
        raise ValueError("Farmer supplies changed during trade")
    if set(source) - set(original) or any(
        uid in source and source[uid] != details for uid, details in wanted.items()
    ):
        raise ValueError("Farmer inventory changed during trade")
    if exact_items(merchant["inventory"]) != exact_items(
        intent["merchant"]["inventory"]
    ):
        raise ValueError("Merchant inventory changed during trade")
    if len(wanted) > available_slots(merchant):
        raise ValueError("Merchant capacity changed during trade")


def reconcile(intent, farmer, merchant, *, now=None):
    try:
        return (
            reconciliation_outcome(intent, farmer, merchant, now=now)["outcome"]
            == "delivered"
        )
    except ReconciliationBlocked:
        return False


class DeliveryTransaction:
    """Driver input is live-qualified separately; this journal survives crashes."""

    def __init__(
        self,
        journal,
        driver,
        peer=None,
        *,
        mark_read_only=None,
        sale_receipts=None,
        clock=None,
        monotonic=None,
        sleep=None,
    ):
        self.journal, self.driver = journal, driver
        if peer is None:
            from conquest.merchants.delivery_peer import DeliveryPeer

            peer = DeliveryPeer()
        self.peer = peer
        self.key = None
        self.mark_read_only = mark_read_only or (lambda: None)
        self.sale_receipts = sale_receipts or (lambda intent, farmer, merchant: ())
        self.clock, self.monotonic, self.sleep = (
            clock or time.time,
            monotonic or time.monotonic,
            sleep or time.sleep,
        )
        setter = getattr(driver, "set_operation", None)
        self.trace_enabled = callable(setter)
        if self.trace_enabled:
            setter(self)

    def before_action(self, stage):
        if self.key is None:
            raise ValueError("Delivery operation is not active")
        self.journal.step(
            self.key, stage, "before_action", terminal=stage == "cleanup_trade"
        )

    def action_observed(self, stage, evidence=None):
        if self.key is None:
            raise ValueError("Delivery operation is not active")
        self.journal.step(
            self.key, stage, "observed", evidence, terminal=stage == "cleanup_trade"
        )

    def finish_receiver(self, key, intent):
        receipt = self.peer.finish(key, intent)
        self.journal.step(
            key,
            "receiver_receipt",
            "observed",
            {
                "phase": receipt.get("phase")
                if isinstance(receipt, dict)
                else "verified"
            },
            terminal=True,
        )
        return receipt

    def trace(self, key):
        result = []
        for step in self.journal.trace(key):
            if step["stage"] == "transaction":
                continue
            result.append({**step, "payload": json.loads(step["payload"])})
        return result

    def run(self, merchant, items, *, request_id=None):
        self.driver.require_qualified("farmer_delivery")
        farmer, receiver = self.driver.read_pair(merchant)
        intent = prepare(farmer, receiver, items)
        key = request_id or "farmer-delivery:" + uuid.uuid4().hex
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise ValueError("Invalid delivery request ID")
        if not self.journal.begin(key, merchant, "farmer_delivery", intent):
            return self.recover(key)
        return self.execute(key, merchant, intent)

    def execute(self, key, merchant, intent):
        """Execute a newly prepared operation owned by this process once."""
        self.key = key
        items = intent["items"]
        if self.trace_enabled:
            self.journal.step(key, "action_trace", "initialized", {"version": 1})
        try:
            self.peer.reserve(key, intent)
            self.driver.open_trade(intent)
            for item in items:
                self.driver.place_item(intent, item)
            farmer, receiver = self.driver.read_pair(merchant)
            validate_offers(intent, farmer, receiver)
            # Persist submission intent before the first irreversible confirm.
            self.journal.transition(key, "submitted")
            self.driver.confirm(intent)
            # Release farmer input before the receiver confirms. A receiver
            # waiting for completion while holding input would prevent the
            # farmer's confirmation and deadlock both accounts.
            self.peer.ready(key, intent)
            farmer, receiver = self.driver.wait_pair(merchant)
            sales = self.sale_receipts(intent, farmer, receiver)
            result = reconciliation_outcome(
                intent, farmer, receiver, trace=self.trace(key), sale_receipts=sales
            )
            if result["outcome"] != "delivered":
                raise ValueError(
                    "Delivery result is uncertain; both inventories must reconcile"
                )
            self.journal.transition(key, "verified", {**result, "items": items})
            self.mark_read_only()
        except Exception as error:
            self.journal.step(
                key,
                "failure",
                "caught",
                {
                    "error_type": type(error).__name__,
                    "reason": str(error)
                    if isinstance(error, (ValueError, OSError))
                    else "Delivery operation failed",
                },
            )
            self.journal.transition(
                key,
                "uncertain",
                {
                    "outcome": "unknown",
                    "reason": str(error)
                    if isinstance(error, (ValueError, OSError))
                    else "Delivery operation failed",
                    "next_action": "reconcile_bilateral_ownership",
                },
            )
            self.mark_read_only()
            try:
                return self.recover(key, mark=False)
            except ReconciliationBlocked:
                raise error
        # A lost release acknowledgement cannot undo a verified transfer.
        # Retrying this read-only command after restart must never send items.
        self.finish_receiver(key, intent)
        return key

    def recover(self, key, *, mark=True):
        self.key = key
        if mark:
            self.mark_read_only()
        with self.journal.db() as db:
            row = db.execute("SELECT * FROM transactions WHERE id=?", (key,)).fetchone()
        if not row or row["kind"] != "farmer_delivery" or row["phase"] == "aborted":
            raise ValueError("Unknown recoverable farmer delivery")
        intent = json.loads(row["before_json"])
        saved = (json.loads(row["result_json"]) if row["result_json"] else {}) or {}
        if row["phase"] == "verified":
            if saved.get("cleanup_pending"):
                farmer, merchant = self.driver.read_pair(row["character"])
                sales = self.sale_receipts(intent, farmer, merchant)
                result = reconciliation_outcome(
                    intent, farmer, merchant, trace=self.trace(key), sale_receipts=sales
                )
                if result["outcome"] != "delivered" or result["cleanup_pending"]:
                    raise ReconciliationBlocked(
                        "Verified delivery still has native trade cleanup pending"
                    )
                self.journal.step(
                    key,
                    "cleanup",
                    "observed",
                    {"proof_digest": result["proof_digest"]},
                    terminal=True,
                )
            self.finish_receiver(key, intent)
            return key

        def observe():
            farmer, merchant = self.driver.read_pair(row["character"])
            trace = self.trace(key)
            sales = self.sale_receipts(intent, farmer, merchant)
            if _trace_ready(trace):
                self.journal.step(
                    key,
                    "reconciliation_observation",
                    "observed",
                    {
                        "ownership_digest": ownership_digest(
                            intent, farmer, merchant, sales
                        ),
                        "observed_at": self.clock(),
                    },
                )
            return farmer, merchant, sales

        farmer, merchant, sales = observe()
        try:
            result = reconciliation_outcome(
                intent,
                farmer,
                merchant,
                trace=self.trace(key),
                sale_receipts=sales,
                now=self.clock(),
            )
        except ReconciliationBlocked as error:
            trace = self.trace(key)
            confirm_attempted = any(
                s.get("stage") == "farmer_confirm"
                and s.get("status") == "before_action"
                for s in trace
            )
            if error.code == "settlement_pending" and not confirm_attempted:
                deadline = self.monotonic() + SETTLEMENT_WINDOW + 0.5
                while self.monotonic() < deadline:
                    self.sleep(min(0.1, max(0, deadline - self.monotonic())))
                    farmer, merchant, sales = observe()
                    try:
                        result = reconciliation_outcome(
                            intent,
                            farmer,
                            merchant,
                            trace=self.trace(key),
                            sale_receipts=sales,
                            now=self.clock(),
                        )
                        break
                    except ReconciliationBlocked as current:
                        error = current
                else:
                    raise ReconciliationBlocked(
                        "Delivery remains uncertain; no new input is authorized: "
                        + str(error)
                    ) from error
            else:
                raise ReconciliationBlocked(
                    "Delivery remains uncertain; no new input is authorized: "
                    + str(error)
                ) from error
        if result["outcome"] == "delivered":
            self.journal.transition(
                key,
                "verified",
                {
                    "items": intent["items"],
                    "farmer": farmer,
                    "merchant": merchant,
                    **result,
                },
            )
            self.finish_receiver(key, intent)
        else:
            acknowledgement = self.peer.disposition(key, intent, result["outcome"])
            if (
                acknowledgement.get("request_id") != key
                or acknowledgement.get("outcome") != result["outcome"]
                or acknowledgement.get("proof_digest") != result["proof_digest"]
            ):
                raise ReconciliationBlocked(
                    "Receiver disposition acknowledgement is missing or changed"
                )
            self.journal.transition(key, "aborted", result)
        return key
