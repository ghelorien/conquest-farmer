"""Move carried warehouse overflow to Market through saved memory services."""

from conquest.character_context import installation_path, state_path
from pathlib import Path
import time
from conquest.discord_notify import read_json, write_json
from conquest.meteor_banking import POLICY, trip
from conquest.town_trade import stash_candidate

JOURNAL = Path(state_path("reports/banking/overflow.json"))


def pending():
    from conquest.recovery_override import read_recovered

    return read_recovered(JOURNAL).get("phase") in ("departing", "market", "returning")


def recheck(loop):
    return {
        "observed_at": time.time(),
        "life": loop.living()["embedded_controls"]["life"],
        "supplies": loop.town("supplies"),
    }


def operator_override(
    loop, *, operator_confirmed=False, confirmation_reference=None, operator=None
):
    from conquest.recovery_override import operator_override as close

    try:
        fresh = recheck(loop)
    except (ValueError, OSError, KeyError, TypeError) as error:
        fresh = {
            "recheck_unavailable": type(error).__name__,
            "reason": "Fresh farmer memory unavailable; resume requires a fresh replan",
        }
    return close(
        JOURNAL,
        pending_phases=("departing", "market", "returning"),
        operator_confirmed=operator_confirmed,
        confirmation_reference=confirmation_reference,
        operator=operator,
        fresh_evidence=fresh,
        incident="storage-overflow",
    )


def extras(loop):
    return [i for i in loop.town("supplies")["items"] if stash_candidate(i)]


def eligible(stored, carried):
    meteors = sum(
        i["amount"] for i in stored["items"] + carried if i["type_id"] == 1088001
    )
    return len(stored["items"]) >= stored["capacity"] and meteors < 10 and bool(carried)


def handle(loop, stored):
    carried = extras(loop)
    if not eligible(stored, carried):
        return False
    policy = read_json(POLICY)
    if not policy.get("overflow_enabled"):
        return False
    origin = loop.living()["embedded_controls"]["life"]["map_id"]
    route = policy.get("origins", {}).get(str(origin))
    if not route or not all(
        route[leg].get("verified") for leg in ("outbound", "return")
    ):
        raise ValueError(
            "Warehouse overflow needs a verified Market round trip from this town"
        )
    if pending():
        raise ValueError(
            "Resume the existing storage overflow trip before starting another"
        )
    from conquest.banking import transfer, transport_reserve

    reserve = route["outbound"]["fare"] + route["return"]["fare"] + transport_reserve()
    wallet = loop.town("supplies")["silver"]
    if wallet < reserve:
        bank = loop.town("warehouse-money")
        amount = reserve - wallet
        if bank["stored_silver"] < amount:
            raise ValueError("Insufficient transport funds for Market overflow")
        transfer(loop, "withdraw", amount)
    state = {
        "phase": "departing",
        "origin": origin,
        "route": route,
        "started_at": time.time(),
        "uids": [i["uid"] for i in carried],
        "receipts": [],
    }
    write_json(JOURNAL, state)
    loop.record(
        "storage_overflow_started",
        activity="Town warehouse full; taking extra valuables to Market",
    )
    resume(loop)
    return True


def resume(loop):
    state = read_json(JOURNAL)
    if not pending():
        return False
    from conquest.banking import open_warehouse, close_warehouse
    from conquest.navigation import read_terrain
    from conquest.storage_halt import request_stop

    loop.phase = "restocking"
    life = loop.living()["embedded_controls"]["life"]
    world = life["map_id"]
    origin = state["origin"]
    loop.terrain = read_terrain(
        installation_path(r"C:\Program Files\Classic Conquer 2.0"), world
    )
    if world == origin and state["phase"] == "departing":
        if state.get("departure_attempted"):
            raise ValueError(
                "Previous overflow departure was not verified; no repeat fare issued"
            )
        close_warehouse(loop)
        outbound = {
            **state["route"]["outbound"],
            "activity": "Heading to Phoenix Conductress for Market overflow storage",
        }

        def mark_departure_submission():
            state["departure_attempted"] = True
            state["departure_submitted_at"] = time.time()
            write_json(JOURNAL, state)

        trip(loop, outbound, before_submit=mark_departure_submission)
        world = 1036
    if world == 1036:
        state["phase"] = "market"
        write_json(JOURNAL, state)
        from conquest.merchants.delivery_route import (
            market_storage,
            warehouse_exhausted,
        )

        market_storage(loop)
        from conquest.meteor_banking import approach_market_warehouse

        approach_market_warehouse(
            loop, "Heading to Market Warehouseman to store overflow valuables"
        )
        open_warehouse(loop)
        while True:
            stored = loop.town("warehouse-items")
            carried = extras(loop)
            if warehouse_exhausted(loop, stored, carried):
                state["phase"] = "full"
                write_json(JOURNAL, state)
                request_stop(loop, stored, carried)
            if not carried:
                break
            item = carried[0]
            receipt = loop.town("warehouse-deposit", uid=item["uid"])
            if receipt.get("verified_in_warehouse") is not True:
                raise ValueError("Market deposit receipt missing; no repeat issued")
            state["receipts"].append(receipt)
            write_json(JOURNAL, state)
            loop.record(
                "valuable_stored",
                **receipt,
                plus=item.get("plus"),
                activity="Stored overflow valuable in Market warehouse",
            )
        close_warehouse(loop)
        state["phase"] = "returning"
        write_json(JOURNAL, state)
        trip(loop, state["route"]["return"])
        world = origin
    if world != origin or state["phase"] not in ("returning", "departing"):
        raise ValueError("Overflow trip is on an unexpected map; valuables preserved")
    from conquest.merchants.service_visit import MarketVisit

    MarketVisit().departed(world)
    open_warehouse(loop)
    state.update(phase="completed", completed_at=time.time())
    write_json(JOURNAL, state)
    loop.record(
        "storage_overflow_complete",
        activity="Overflow valuables stored in Market; returning to the farm route",
    )
    return True
