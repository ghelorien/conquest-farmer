import copy
import json
import pytest
from conquest.merchants.journal import Journal
from conquest.merchants.sales import observe, summary, format_summary
from conquest.merchants.sales_report import send_report
from conquest.discord_notify import DeliveryError


def state(at=100, character="Dutch"):
    def item(uid, price):
        return dict(
            uid=uid,
            name="Hat",
            type_id=113504,
            plus=2,
            gem1=0,
            gem2=0,
            quantity=1,
            price=price,
            bound=False,
        )

    return dict(
        character=character,
        server="America",
        identity={"pid": 1, "creation_time_100ns": 20},
        timestamp=at,
        inventory=[],
        booth=[item(1, 100), item(2, 200)],
        silver=1000,
        request=None,
        trade=None,
    )


def test_verified_sale_restart_and_duplicate_observation(tmp_path):
    path = tmp_path / "journal.sqlite3"
    j = Journal(path)
    before = state()
    observe(j, before)
    after = state(101)
    after["booth"].pop()
    after["silver"] += 194
    observe(j, after)
    observe(Journal(path), after)
    s = summary(j, now=102)["characters"]
    assert s["Dutch"]["total"] == {"items": 1, "silver": 194}
    assert s["Spiritual"]["started_at"] is None
    assert len([e for e in j.events() if e["event"] == "sale_verified"]) == 1


@pytest.mark.parametrize("net_silver,verified", [(97, True), (0, False)])
def test_spiritual_new_listing_is_not_a_sale_and_disappearance_needs_cash(
    tmp_path, net_silver, verified
):
    j = Journal(tmp_path / "journal.sqlite3")
    before = state(character="Spiritual")
    new_item = {**before["booth"][0], "uid": 3}
    before["inventory"] = [new_item]
    observe(j, before)
    listed = copy.deepcopy(before)
    listed.update(timestamp=101, inventory=[], booth=before["booth"] + [new_item])
    observe(j, listed)
    assert not [e for e in j.events() if e["event"].startswith("sale_")]
    sold = copy.deepcopy(listed)
    sold.update(timestamp=102, booth=before["booth"], silver=1000 + net_silver)
    observe(j, sold)
    if not verified:
        later = copy.deepcopy(sold)
        later["timestamp"] = 108
        observe(j, later)
    result = summary(j, now=109)["characters"]["Spiritual"]
    assert result["total"] == (
        {"items": 1, "silver": 97} if verified else {"items": 0, "silver": 0}
    )
    receipts = [e for e in j.events() if e["event"] == "sale_verified"]
    assert len(receipts) == int(verified)


def test_unrelated_incoming_request_does_not_hide_exact_sale_receipt(tmp_path):
    j = Journal(tmp_path / "journal.sqlite3")
    before = state()
    before["request"] = {"participant": "Proxyy-Starr", "participant_uid": 77}
    observe(j, before)
    after = state(101)
    after["request"] = copy.deepcopy(before["request"])
    after["booth"].pop()
    after["silver"] += 194
    observe(j, after)
    receipt = summary(j, now=102)["characters"]["Dutch"]
    assert receipt["total"] == {"items": 1, "silver": 194}
    evidence = json.loads(
        next(e["payload"] for e in j.events() if e["event"] == "sale_verified")
    )
    assert evidence["foreign_request_observed"] is True


def test_trusted_delivery_request_still_blocks_sale_attribution(tmp_path):
    j = Journal(tmp_path / "journal.sqlite3")
    before = state()
    before["request"] = {"participant": "Parasite", "participant_uid": 999}
    observe(j, before)
    after = state(101)
    after["request"] = copy.deepcopy(before["request"])
    after["booth"].pop()
    after["silver"] += 194
    observe(j, after)
    assert summary(j, now=102)["characters"]["Dutch"]["total"] == {
        "items": 0,
        "silver": 0,
    }


def test_unidentified_request_does_not_relax_sale_causality(tmp_path):
    j = Journal(tmp_path / "journal.sqlite3")
    before = state()
    before["request"] = {"participant": ""}
    observe(j, before)
    after = state(101)
    after["request"] = {"participant": ""}
    after["booth"].pop()
    after["silver"] += 194
    observe(j, after)
    assert summary(j, now=102)["characters"]["Dutch"]["total"] == {
        "items": 0,
        "silver": 0,
    }


@pytest.mark.parametrize(
    "case",
    ["cash_mismatch", "trade", "inventory_change", "gap", "pid_reused", "repricing"],
)
def test_ambiguous_disappearance_is_not_a_sale(tmp_path, case):
    j = Journal(tmp_path / "journal.sqlite3")
    before = state()
    observe(j, before)
    after = state(101)
    after["booth"].pop()
    after["silver"] += 194
    if case == "cash_mismatch":
        after["silver"] += 1
    if case == "trade":
        after["trade"] = {"participant": "Parasite"}
    if case == "inventory_change":
        after["inventory"] = [{**before["booth"][0], "uid": 3}]
    if case == "gap":
        after["timestamp"] = 200
    if case == "pid_reused":
        after["identity"]["creation_time_100ns"] = 21
    if case == "repricing":
        with j.db() as db:
            db.execute(
                "INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?)",
                ("x", "Dutch", "listing", "verified", "{}", "{}", 100, 101),
            )
    observe(j, after)
    result = summary(j, now=201)["characters"]["Dutch"]
    assert result["total"] == {"items": 0, "silver": 0}
    # A time gap or replaced process is an observation gap, with no claimed
    # item departure. Other ambiguous same-process losses stay unconfirmed.
    assert result["unconfirmed"] == (0 if case in ("gap", "pid_reused") else 1)
    if case in ("gap", "pid_reused"):
        assert not [e for e in j.events() if e["event"].startswith("sale_")]
        assert any(e["event"] == "sales_observation_gap" for e in j.events())


def test_manual_unlist_and_existing_stock_are_not_sales(tmp_path):
    j = Journal(tmp_path / "journal.sqlite3")
    before = state()
    observe(j, before)
    after = state(101)
    after["inventory"] = [after["booth"].pop()]
    observe(j, after)
    assert not [e for e in j.events() if e["event"].startswith("sale_")]


def test_period_totals_multiple_sales_and_stale_coverage(tmp_path):
    j = Journal(tmp_path / "journal.sqlite3")
    before = state()
    observe(j, before)
    after = state(101)
    after.update(booth=[], silver=1291)
    observe(j, after)
    s = summary(j, now=200, since=150)
    assert s["characters"]["Dutch"]["period"] == {"items": 0, "silver": 0}
    assert s["characters"]["Dutch"]["total"] == {"items": 2, "silver": 291}
    text = format_summary(s)
    assert (
        "291 silver" in text
        and "may be incomplete" in text
        and "Earlier sales are unavailable" in text
    )


@pytest.mark.parametrize("order", ["stock_first", "cash_first"])
def test_delayed_receipt_survives_restart_without_double_counting(tmp_path, order):
    j = Journal(tmp_path / "journal.sqlite3")
    before = state()
    observe(j, before)
    middle = state(101)
    if order == "stock_first":
        middle["booth"].pop()
    else:
        middle["silver"] += 194
    observe(j, middle)
    assert summary(j, now=101)["characters"]["Dutch"]["total"]["silver"] == 0
    after = state(103)
    after["booth"].pop()
    after["silver"] += 194
    observe(Journal(j.path), after)
    observe(j, after)
    assert summary(j, now=104)["characters"]["Dutch"]["total"] == {
        "items": 1,
        "silver": 194,
    }
    evidence = json.loads(
        next(e["payload"] for e in j.events() if e["event"] == "sale_verified")
    )
    assert (
        evidence["before_silver"] == 1000
        and evidence["after_silver"] == 1194
        and evidence["deduction"] == 6
    )


@pytest.mark.parametrize("price,net", [(89999, 87299), (6331050, 6141119), (100, 97)])
def test_net_receipt_records_observed_rounding_and_whole_stack_price(
    tmp_path, price, net
):
    j = Journal(tmp_path / "journal.sqlite3")
    before = state()
    before["booth"][1].update(price=price, quantity=10)
    observe(j, before)
    after = copy.deepcopy(before)
    after.update(timestamp=101, silver=1000 + net)
    after["booth"].pop()
    observe(j, after)
    assert summary(j, now=102)["characters"]["Dutch"]["total"] == {
        "items": 10,
        "silver": net,
    }


@pytest.mark.parametrize(
    "ending", ["late_cash", "trade", "returned", "restart_gap", "negative_cash"]
)
def test_pending_receipt_never_counts_ambiguous_completion(tmp_path, ending):
    j = Journal(tmp_path / "journal.sqlite3")
    before = state()
    observe(j, before)
    middle = state(101)
    middle["booth"].pop()
    observe(j, middle)
    after = copy.deepcopy(middle)
    after.update(timestamp=103, silver=1194)
    if ending == "late_cash":
        after["timestamp"] = 107
    if ending == "trade":
        after["request"] = {"participant": "Parasite"}
    if ending == "returned":
        after["inventory"] = [before["booth"][1]]
    if ending == "restart_gap":
        after["timestamp"] = 120
    if ending == "negative_cash":
        after["silver"] = 900
    observe(Journal(j.path), after)
    assert summary(j, now=121)["characters"]["Dutch"]["total"]["silver"] == 0
    with j.db() as db:
        assert "_sales_anchor" not in json.loads(
            db.execute("SELECT snapshot FROM sales_baseline").fetchone()[0]
        )


def test_two_characters_delayed_sales_are_isolated(tmp_path):
    j = Journal(tmp_path / "journal.sqlite3")
    for char in ("Spiritual", "Dutch"):
        observe(j, state(character=char))
    a = state(101, "Spiritual")
    a["booth"].pop()
    observe(j, a)
    b = state(101, "Dutch")
    b["silver"] += 194
    observe(j, b)
    a.update(timestamp=102, silver=1194)
    observe(j, a)
    s = summary(j, now=103)["characters"]
    assert (
        s["Spiritual"]["total"]["silver"] == 194 and s["Dutch"]["total"]["silver"] == 0
    )


def test_legacy_history_reconciliation_is_audited_and_idempotent(tmp_path):
    from conquest.merchants.sales_recovery import reconcile

    j = Journal(tmp_path / "journal.sqlite3")
    before = state()
    observe(j, before)
    after = state(110)
    after["booth"] = []
    after["silver"] = 1291
    with j.db() as db:
        for at, i in zip((102, 104), before["booth"]):
            db.execute(
                "INSERT INTO sales(character,observed_at,phase,items,silver,note) VALUES(?,?,?,?,?,?)",
                (
                    "Dutch",
                    at,
                    "unconfirmed",
                    json.dumps([i]),
                    0,
                    "Legacy gross comparison",
                ),
            )
    options = dict(sources=["saved memory before", "saved memory after"])
    assert reconcile(j, "Dutch", before, after, **options)["silver"] == 291
    assert summary(j, now=111)["characters"]["Dutch"]["total"]["silver"] == 0
    reconcile(j, "Dutch", before, after, apply=True, **options)
    assert reconcile(Journal(j.path), "Dutch", before, after, apply=True, **options)[
        "already_reconciled"
    ]
    s = summary(j, now=111, since=103)["characters"]["Dutch"]
    assert s["total"] == {"silver": 291, "items": 2} and s["unconfirmed"] == 0
    assert (
        s["period"]["silver"] == 0
        and s["period_incomplete"]
        and s["recovered_silver"] == 291
    )
    bad = copy.deepcopy(after)
    bad["silver"] += 1
    with pytest.raises(ValueError):
        reconcile(j, "Dutch", before, bad, apply=True, **options)


@pytest.mark.parametrize("case", ["cash", "stock", "identity", "trade_transaction"])
def test_history_repair_rejects_inconsistent_evidence(tmp_path, case):
    from conquest.merchants.sales_recovery import reconcile

    j = Journal(tmp_path / "journal.sqlite3")
    before = state()
    observe(j, before)
    after = state(110)
    after["booth"].pop()
    after["silver"] = 1194
    with j.db() as db:
        db.execute(
            "INSERT INTO sales(character,observed_at,phase,items,silver,note) VALUES(?,?,?,?,?,?)",
            (
                "Dutch",
                102,
                "unconfirmed",
                json.dumps([before["booth"][1]]),
                0,
                "Legacy",
            ),
        )
        if case == "trade_transaction":
            db.execute(
                "INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?)",
                ("trade", "Dutch", "trade", "verified", "{}", "{}", 101, 104),
            )
    if case == "cash":
        after["silver"] += 1
    if case == "stock":
        after["inventory"] = [before["booth"][1]]
    if case == "identity":
        after["identity"]["pid"] = 2
    with pytest.raises(ValueError):
        reconcile(j, "Dutch", before, after, sources=["test"], apply=True)
    assert summary(j, now=111)["characters"]["Dutch"]["total"]["silver"] == 0


def test_reports_are_idempotent_and_separate_from_farmer_webhook(tmp_path):
    j = Journal(tmp_path / "journal.sqlite3")
    observe(j, state())
    calls = []

    def send(url, content):
        calls.append((url, content))
        return str(len(calls))

    opts = dict(journal=j, send=send, load=lambda: "shops-only")
    assert send_report(now=100, **opts)["status"] == "delivered"
    assert send_report(now=101, **opts)["status"] == "already_delivered"
    assert send_report(now=14500, **opts)["status"] == "delivered"
    assert len(calls) == 2 and all(c[0] == "shops-only" for c in calls)


def test_missing_secret_and_preview_never_send(tmp_path):
    j = Journal(tmp_path / "journal.sqlite3")

    def missing():
        raise OSError("SECRET MUST NOT APPEAR")

    def bad_send(*args):
        pytest.fail("must not send")

    assert (
        send_report(journal=j, now=100, load=missing, send=bad_send)["status"]
        == "needs_configuration"
    )
    assert (
        send_report(journal=j, now=100, load=missing, send=bad_send, preview=True)[
            "status"
        ]
        == "preview"
    )


def test_uncertain_delivery_never_blindly_reposts(tmp_path):
    j = Journal(tmp_path / "journal.sqlite3")
    calls = []

    def send(*args):
        calls.append(args)
        raise OSError("secret URL")

    result = send_report(journal=j, now=100, load=lambda: "secret", send=send)
    assert result["status"] == "needs_attention" and "secret" not in json.dumps(result)
    assert (
        send_report(journal=j, now=101, load=lambda: "secret", send=send)["status"]
        == "needs_attention"
    )
    assert len(calls) == 1


def test_http_rejection_can_be_retried_without_changing_report(tmp_path):
    j = Journal(tmp_path / "journal.sqlite3")

    def reject(*args):
        raise DeliveryError("Discord HTTP 429", 30)

    assert (
        send_report(journal=j, now=100, load=lambda: "shops", send=reject)["status"]
        == "retry_later"
    )
    assert (
        send_report(
            journal=j, now=101, load=lambda: "shops", send=lambda *args: "receipt"
        )["status"]
        == "delivered"
    )


def test_script_timer_migrates_receipt_and_preserves_cadence_across_restart(tmp_path):
    import threading
    from conquest.merchants.sales_report import SalesReportWorker

    j = Journal(tmp_path / "journal.sqlite3")
    now = [100]
    assert (
        send_report(
            journal=j, now=100, load=lambda: "shops", send=lambda *args: "first"
        )["status"]
        == "delivered"
    )
    calls = []

    def dispatch(**kwargs):
        calls.append(kwargs["now"])
        return {"status": "delivered"}

    worker = SalesReportWorker(
        j, threading.Event(), clock=lambda: now[0], dispatch=dispatch
    )
    assert worker.step()["next_due"] == 14500 and not calls
    now[0] = 14499
    worker.step()
    assert not calls
    now[0] = 14500
    assert worker.step()["next_due"] == 28900 and calls == [14500]
    worker = SalesReportWorker(
        Journal(j.path), threading.Event(), clock=lambda: now[0], dispatch=dispatch
    )
    worker.step()
    assert calls == [14500]
    now[0] = 60000
    assert worker.step()["next_due"] == 72100 and calls == [14500, 60000]


def test_script_timer_backs_off_and_stop_prevents_sending(tmp_path):
    import threading
    from conquest.merchants.sales_report import SalesReportWorker

    j = Journal(tmp_path / "journal.sqlite3")
    now = [0]
    stop = threading.Event()
    calls = []

    def reject(**kwargs):
        calls.append(kwargs["now"])
        return {"status": "retry_later", "retry_after": 60}

    worker = SalesReportWorker(j, stop, clock=lambda: now[0], dispatch=reject)
    worker.step()
    now[0] = 14400
    assert worker.step()["retry_at"] == 14460
    now[0] = 14430
    worker.step()
    assert calls == [14400]
    now[0] = 14460
    stop.set()
    worker.step()
    assert calls == [14400]
