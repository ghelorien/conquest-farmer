"""Exact one-shot retrieval of a banked MeteorScroll for merchant delivery.

Admission comes from fresh, rich warehouse memory, not an arbitrary item type
or a caller-supplied ownership claim. The protected-withdrawal engine supplies
the durable input fence, exact conservation receipt and read-only recovery.
Sharing its journal also shares the existing route/reload/delivery asset holds.
"""

import hashlib

from conquest.protected_withdrawal import (
    ProtectedWithdrawal,
    _core_map,
    _digest,
    _identifier,
    _plain_observation,
    _source,
)

SCROLL = 720027


class ScrollWithdrawal(ProtectedWithdrawal):
    @staticmethod
    def plan_id(operation_id):
        _identifier(operation_id, "scroll withdrawal operation ID")
        return "meteor-scroll:" + hashlib.sha256(operation_id.encode()).hexdigest()

    @staticmethod
    def _profile():
        from conquest.character_context import current

        context = current()
        if context is None or context.profile.role != "Farmer":
            raise ValueError(
                "Scroll withdrawal requires the authenticated farmer profile"
            )
        return context

    def _plan(self, plan_id, operation_id, uid):
        if type(uid) is not int or uid <= 0 or plan_id != self.plan_id(operation_id):
            raise ValueError(
                "Scroll withdrawal requires an exact positive UID and operation ID"
            )
        context = self._profile()
        old = self.journal.get(operation_id)
        if old:
            if old["plan_id"] != plan_id or old["uid"] != uid:
                raise ValueError("Scroll withdrawal operation ID was reused")
            anchor = old["intent"].get("deposit_receipt") or {}
            if (
                anchor.get("kind") != "fresh_stored_meteor_scroll"
                or anchor.get("farmer_profile_id") != context.profile.id
            ):
                raise ValueError("Scroll withdrawal belongs to another farmer profile")
            observation = old["intent"]["before"]
        else:
            observation = self._observe()
            anchor = {
                "kind": "fresh_stored_meteor_scroll",
                "farmer_profile_id": context.profile.id,
                "client_sha256": self.town.observer.adapter.expected_sha256,
                "ownership_digest": _digest(_plain_observation(observation)),
            }
        source = _source(observation["source"])
        context.verify(observation["source"])
        item = _core_map(observation["warehouse"]["items"]).get(uid)
        if (
            item is None
            or item["type_id"] != SCROLL
            or item["quantity"] != 1
            or item["bound"] is not False
            or item["plus"] != 0
            or item["gem1"] != 0
            or item["gem2"] != 0
            or uid in source["inventory"]
            or uid in source["booth"]
        ):
            raise ValueError(
                "One exact unbound stored MeteorScroll is required; loose Meteors are forbidden"
            )
        identity = source["identity"]
        if (
            not isinstance(identity, dict)
            or type(identity.get("pid")) is not int
            or identity["pid"] <= 0
            or type(identity.get("creation_time_100ns")) is not int
            or identity["creation_time_100ns"] <= 0
            or not isinstance(identity.get("path"), str)
            or not identity["path"]
            or identity.get("architecture") != "x64"
            or type(source["character_uid"]) is not int
            or source["character_uid"] <= 0
            or context.profile.character_uid != source["character_uid"]
        ):
            raise ValueError(
                "Scroll withdrawal requires the exact bound character and process creation identity"
            )
        selected = {
            "item": item,
            "withdrawal_operation_id": operation_id,
            "deposit_receipt": anchor,
        }
        # The common engine calls this field deposit_receipt for historical
        # equipment qualification. This typed anchor is fresh bank ownership,
        # deliberately not represented as a historical deposit receipt.
        plan = {
            "plan_id": plan_id,
            "farmer": {
                "character": source["character"],
                "character_uid": source["character_uid"],
                "server": source["server"],
                "process_identity": identity,
                "client_sha256": anchor["client_sha256"],
            },
            "market_reentry": {
                "snapshot": observation["source"],
                "equipped_ammo": observation["equipped_ammo"],
                "warehouse": observation["warehouse"],
            },
            "items": [selected],
        }
        # Snapshot timestamps can advance between admission and the final
        # pre-input read. Bind the immutable typed anchor, not those clocks.
        plan["plan_sha256"] = _digest(
            {"anchor": anchor, "item": item, "farmer": plan["farmer"]}
        )
        return plan, selected

    def _expected_preflight(self, plan, selected, current):
        super()._expected_preflight(plan, selected, current)
        source = current["source"]
        stamp = source.get("timestamp")
        if (
            type(stamp) not in (int, float)
            or not 0 <= self.clock() - stamp <= 5
            or type(source.get("capacity")) is not int
            or not 0 < source["capacity"] <= 40
            or len(source["inventory"]) >= source["capacity"]
        ):
            raise ValueError(
                "Scroll withdrawal needs fresh inventory evidence and a free slot"
            )
        if any(row["type_id"] == 1088001 for row in source["inventory"]):
            raise ValueError(
                "Bank leftover loose Meteors before retrieving a delivery scroll"
            )
        if any(
            any(
                word in row.get("name", "")
                for word in ("Confirm", "Trade", "Dialog", "Shop", "Add Item to Booth")
            )
            for row in source.get("windows", [])
        ):
            raise ValueError(
                "Close conflicting town/trade modals before retrieving a delivery scroll"
            )

    @staticmethod
    def _result(state):
        result = ProtectedWithdrawal._result(state)
        result["kind"] = "meteor_scroll_withdrawal"
        if state["phase"] == "withdrawn":
            result["next_action"] = "prepare_exact_bilateral_delivery"
        return result


def withdraw(town, operation_id, uid, **options):
    work = ScrollWithdrawal(town, **options)
    return work.run(work.plan_id(operation_id), operation_id, uid)


def reconcile(town, operation_id, uid, **options):
    """Settle without gameplay input, closing an absent admission before reuse.

    The bridge serializes requests. A durable no-input tombstone also prevents
    a late original request from ever reaching its click boundary.
    """
    work = ScrollWithdrawal(town, **options)
    state = work.journal.get(operation_id)
    if state is None:
        plan, selected = work._plan(work.plan_id(operation_id), operation_id, uid)
        observation = work._observe()
        work._expected_preflight(plan, selected, observation)
        now = work.clock()
        state = work.journal.begin(
            {
                "version": 1,
                "operation_id": operation_id,
                "plan_id": plan["plan_id"],
                "uid": uid,
                "phase": "prepared",
                "created_at": now,
                "input_deadline": now,
                "intent": {
                    "item": selected["item"],
                    "plan_sha256": plan["plan_sha256"],
                    "deposit_receipt": selected["deposit_receipt"],
                    "before": _plain_observation(observation),
                },
            }
        )
        if state["phase"] == "prepared":
            state = work.journal.transition(
                operation_id,
                "prepared",
                "no_transfer",
                receipt={
                    "operation_id": operation_id,
                    "plan_id": plan["plan_id"],
                    "item": selected["item"],
                    "input_attempted": False,
                    "admission_closed": True,
                    "verified_at": now,
                },
            )
    plan, selected = work._plan(work.plan_id(operation_id), operation_id, uid)
    result = work.reconcile(state, plan, selected)
    result["no_input_proven"] = (
        result["phase"] == "no_transfer"
        and (result.get("receipt") or {}).get("input_attempted") is False
        and all(
            row["phase"] in ("prepared", "no_transfer")
            for row in work.journal.history(operation_id)
        )
    )
    return result
