"""Persisted, memory-accounted Poltergeist silver goal and essential spending."""

import time
from conquest import session_plan
from conquest.discord_notify import write_json


def savings_plan():
    plan = session_plan.active_plan()
    return plan if plan and plan.get("mode") == "save_silver" else None


def configure_route(route, balance=None):
    plan = savings_plan()
    if not plan:
        return route
    if balance is None:
        balance = plan.get("balance", plan["starting_silver"])
        from conquest.banking import policy, STATUS
        from conquest.discord_notify import read_json

        if policy().get("enabled"):
            balance += read_json(STATUS).get("stored_silver", 0)
    arrow = (
        route.supplies.arrow_type
        if plan.get("allow_iron_arrows") and route.supplies.arrow_type == 1050001
        else 1050000
    )
    return route.model_copy(
        update={
            "supplies": route.supplies.model_copy(
                update={
                    "arrow_type": arrow,
                    "arrows_restock_to": 10000 if arrow == 1050001 else 2000,
                    "healing_restock_to": 5,
                    "arrows_return_below": 3,
                    "healing_return_below": 1,
                }
            )
        }
    )


def affordable_supply(kind, price, counts, route):
    plan = savings_plan()
    if not plan:
        return True
    if kind == 1050001:
        return bool(
            plan.get("allow_iron_arrows") and 0 < price <= counts["silver"] - 3000
        )
    if kind not in (1050000, route.supplies.healing_type):
        return False
    count = counts["arrows"] if kind == 1050000 else counts["potions"]
    minimum = (
        route.supplies.arrows_return_below
        if kind == 1050000
        else route.supplies.healing_return_below
    )
    # Once essentials are covered, preserve a small cash reserve instead of
    # spending the last silver topping up to a comfortable stock level.
    reserve = 200 if count >= minimum else 0
    return 0 < price <= counts["silver"] - reserve


def review_ammunition(loop):
    """User-authorized arrow upgrade only, after the healing refill."""
    plan = savings_plan()
    if not plan or not plan.get("allow_iron_arrows"):
        return
    from conquest.arrow_upgrades import review_arrows

    products = loop.town("shop", vendor_type=5)["products"]
    state = loop.town("gear")
    bag = loop.town("supplies")
    review_arrows(
        loop, [p for p in products if p["type_id"] == 1050001], state, bag["silver"]
    )


def progress(loop, balance):
    plan = savings_plan()
    if not plan:
        return False
    target = plan.get("silver_target")
    reached = target is not None and balance >= target
    if time.monotonic() >= getattr(loop, "next_savings_report", 0) or reached:
        loop.next_savings_report = time.monotonic() + 10
        plan.update(balance=balance, observed_at=time.time())
        try:
            write_json(session_plan.PLAN, plan)
            loop.record(
                "savings_progress",
                silver=balance,
                silver_target=plan["silver_target"],
                silver_gained=balance - plan["starting_silver"],
            )
            loop.savings_report_error = None
        except OSError as error:
            # This is display telemetry, not a trade or a policy change. Retry
            # at the next report tick while combat and healing keep running.
            loop.savings_report_error = str(error)
    return reached


def finish_in_town(loop):
    plan = savings_plan()
    if not plan or plan.get("silver_target") is None:
        return False
    loop.phase = "restocking"
    loop.record(
        "savings_return",
        activity="Silver target reached; returning to Twin City to finish safely",
    )
    loop.travel(loop.route.restock_anchor)
    first = loop.town("supplies")["silver"]
    time.sleep(0.2)
    balance = loop.town("supplies")["silver"]
    if min(first, balance) < plan["silver_target"]:
        return False
    life = loop.living()["embedded_controls"]["life"]
    from conquest.city_travel import city_for

    left, top, right, bottom = city_for(loop.route.map_id)["town_boundary"]
    x, y = life["position"]
    if (
        life["map_id"] != loop.route.map_id
        or life["dead_candidate"]
        or not (left <= x <= right and top <= y <= bottom)
    ):
        raise ValueError("Savings completion requires a living character in town")
    plan.update(balance=balance, completed=True, completed_at=time.time())
    write_json(session_plan.PLAN, plan)
    loop.phase = "completed"
    loop.record(
        "savings_complete",
        silver=balance,
        silver_target=plan["silver_target"],
        activity=f"Savings goal complete: {balance:,} silver; parked in Twin City",
    )
    return True
