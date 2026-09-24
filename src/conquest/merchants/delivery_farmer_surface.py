"""Present the exact farmer host for a receipt-bound delivery worker.

Tk only changes presentation. Activation and all gameplay input remain on the
original worker thread under its coordinator lease.
"""

from copy import deepcopy
from dataclasses import dataclass
import ctypes
import json
import threading
import time

from conquest.capture import CaptureUnavailable
from conquest.character_context import is_farmer_owner
from conquest.merchants.delivery_probe import read_probe
from conquest.recovery_override import evidence_digest

PHASES = {
    "delivery_request_probe": "prepared",
    "delivery_offer_probe": "trade_open_verified",
    "delivery_confirm_probe": "offer_verified",
}


def _source_row(operation):
    with operation.journal.db() as db:
        row = db.execute(
            "SELECT id,character,character AS target_profile_id,kind,phase,before_json FROM transactions WHERE id=?",
            (operation.key,),
        ).fetchone()
    if not row:
        raise ValueError("Farmer delivery source receipt is missing")
    return dict(row)


def _matching_intents(source, receiver):
    from conquest.merchants.manual_sessions import canonical_ownership
    from conquest.merchants.delivery import exact_items

    # Reservation snapshots are freshly read, so time/HP/GUI are not equal.
    # Exact ownership, identity, location, selected items and origin must be.
    if exact_items(source["items"]) != exact_items(receiver["items"]):
        return False
    for role in ("farmer", "merchant"):
        if canonical_ownership(source[role]) != canonical_ownership(
            receiver[role]
        ) or any(
            source[role].get(k) != receiver[role].get(k) for k in ("map_id", "position")
        ):
            return False
    return all(
        source.get(k) == receiver.get(k)
        for k in ("operation_id", "town_visit_id", "visit_id", "farmer_profile_id")
    )


def verify_stage_pair(intent, farmer, merchant, *, stage):
    """Reobserve the authorized stage before any worker-side activation."""
    from conquest.merchants.delivery import prepare, validate_offers

    if stage == "open":
        prepare(farmer, merchant, intent["items"])
        if not _matching_intents(
            intent, {**intent, "farmer": farmer, "merchant": merchant}
        ):
            raise ValueError("Delivery ownership changed before request activation")
    else:
        from conquest.merchants.farmer_trade import partial_offer

        if stage == "place":
            partial_offer(intent, farmer, merchant)
        elif stage == "confirm":
            validate_offers(intent, farmer, merchant)
        else:
            raise ValueError("Unknown farmer delivery input stage")
        if any(
            s["trade"]["accepted"] or s["trade"]["other_accepted"]
            for s in (farmer, merchant)
        ):
            raise ValueError("Trade was already accepted before farmer input")


@dataclass(frozen=True)
class _Authority:
    ui: object
    purpose: str
    phase: str
    receipt_json: str
    intent_json: str
    worker: object
    revision: int
    deadline: float
    farmer_id: str
    merchant_id: str
    operation: object = None
    driver: object = None
    reservation_json: str | None = None
    token: object = None
    worker_binding: object = None

    def receipt(self):
        ui = self.ui
        intent = json.loads(self.intent_json)
        if self.operation is None:
            state = read_probe()
            if (
                ui.delivery_probe_thread is not self.worker
                or not state
                or state.get("phase") != self.phase
                or evidence_digest(state)
                != evidence_digest(json.loads(self.receipt_json))
            ):
                raise CaptureUnavailable("Farmer presentation receipt changed")
        else:
            operation = self.operation
            key = json.loads(self.receipt_json)["id"]
            fence = ui.coordinator.fence
            if (
                self.driver.operation is not operation
                or operation.key != key
                or ui.delivery_workers.get(key) is not self.worker
                or _source_row(operation) != json.loads(self.receipt_json)
                or fence is None
                or self.token is None
            ):
                raise CaptureUnavailable(
                    "Farmer delivery operation changed before presentation"
                )
            fence.check(self.token)
            with fence.lock:
                if (
                    fence.workers.get(id(self.worker_binding))
                    is not self.worker_binding
                    or self.worker_binding["token"] != self.token
                    or not self.worker_binding["action_capable"]
                ):
                    raise CaptureUnavailable("Farmer delivery worker binding changed")
            grant = getattr(ui, "grant", None) or {}
            if (
                self.token.request_id != key
                or self.token.scope != "market_visit"
                or self.token.farmer_profile_id != self.farmer_id
                or self.token.revision != self.revision
                or self.token.expires_at <= time.time()
                or any(
                    grant.get(k) != v
                    for k, v in (
                        ("request_id", key),
                        ("revision", self.revision),
                        ("expires_at", self.token.expires_at),
                        ("scope", "market_visit"),
                        ("farmer_profile_id", self.farmer_id),
                    )
                )
                or any(
                    grant.get(k) != intent.get(k) for k in ("visit_id", "town_visit_id")
                )
                or not ui.runtime.enabled(intent["merchant"]["character"])
            ):
                raise CaptureUnavailable(
                    "Farmer delivery grant or receiver enablement changed"
                )
            from conquest.merchants.delivery_reservation import require

            reservation = require(
                ui.runtime.journal, intent["merchant"]["character"], key
            )
            if evidence_digest(reservation) != evidence_digest(
                json.loads(self.reservation_json)
            ):
                raise CaptureUnavailable("Farmer delivery receiver reservation changed")
        return intent


def prepare_delivery(driver, intent, *, stage, deadline):
    """Only a durable transaction and its bound Market-visit worker may present."""
    phases = {"open": "prepared", "place": "prepared", "confirm": "submitted"}
    if stage not in phases:
        raise ValueError("Unknown farmer delivery presentation stage")
    ui = driver.ui
    operation = driver.operation
    if operation is None or not operation.key:
        raise ValueError("Farmer delivery operation is unavailable")
    row = _source_row(operation)
    key = operation.key
    farmer_id = intent.get("farmer_profile_id")
    if (
        row["kind"] != "farmer_delivery"
        or row["phase"] != phases[stage]
        or str(row["character"]) != str(intent["merchant"]["character"])
        or row["target_profile_id"]
        != ui.runtime.manual_target(intent["merchant"]["character"])
        or evidence_digest(json.loads(row["before_json"])) != evidence_digest(intent)
        or intent.get("operation_id") != key
        or not farmer_id
    ):
        raise ValueError("Farmer delivery stage or source intent changed")
    from conquest.merchants.delivery_reservation import require

    reservation = require(ui.runtime.journal, intent["merchant"]["character"], key)
    if reservation["phase"] != "reserved" or not _matching_intents(
        intent, reservation["intent"]
    ):
        raise ValueError("Farmer delivery reservation intent changed")
    fence = ui.coordinator.fence
    if fence is None or not fence._stack("workers"):
        raise CaptureUnavailable(
            "Farmer delivery requires its bound Market-visit worker"
        )
    token = fence.check()
    worker_binding = fence._stack("workers")[-1]
    authority = _Authority(
        ui,
        "farmer_delivery",
        phases[stage],
        json.dumps(row),
        json.dumps(intent),
        threading.current_thread(),
        driver.revision,
        deadline,
        farmer_id,
        ui.runtime.manual_target(intent["merchant"]["character"]),
        operation,
        driver,
        json.dumps(reservation),
        token,
        worker_binding,
    )
    return _present(authority, driver.check)


def prepare(ui, state, *, purpose, revision, deadline):
    """Supervised factory: exact purpose, phase, and durable probe worker."""
    if purpose not in PHASES or state.get("phase") != PHASES[purpose]:
        raise ValueError("Farmer presentation probe stage changed")
    authority = _Authority(
        ui,
        purpose,
        PHASES[purpose],
        json.dumps(state),
        json.dumps(state["intent"]),
        threading.current_thread(),
        revision,
        deadline,
        state["farmer_profile_id"],
        state["target_profile_id"],
    )

    def check():
        from conquest.merchants.farmer_preferences import permits_new_delivery

        permits_new_delivery(json.loads(authority.intent_json)["farmer"]["character"])
        ui.coordinator.check()

    return _present(authority, check)


def _present(authority, check):
    ui = authority.ui
    worker = authority.worker
    purpose = authority.purpose
    intent = json.loads(authority.intent_json)
    revision = authority.revision
    observer = ui.app.observer
    target = observer.operations.target
    host = ui.app.host
    identity = deepcopy(intent["farmer"]["identity"])
    hwnd = target.hwnd
    profile_id = authority.farmer_id
    saved = host.saved
    done, result = threading.Event(), {}
    expires = min(authority.deadline, time.monotonic() + 3)

    def binding():
        c = ui.coordinator
        control = ui.app.control.snapshot()
        if (
            result.get("expired")
            or time.monotonic() >= expires
            or c.purpose != purpose
            or not is_farmer_owner(c.owner)
            or c.thread != worker.ident
            or not worker.is_alive()
            or ui.closed
            or ui.app.closing
            or c.stopped
            or c.manual_active()
            or control["enabled"]
            or control.get("paused")
            or control["revision"] != revision
            or not ui.safe_to_yield()
            or any(
                ctypes.windll.user32.GetAsyncKeyState(k) & 0x8000 for k in (0x7A, 0x7B)
            )
        ):
            raise CaptureUnavailable("Farmer presentation authority stopped or changed")
        authority.receipt()
        if (
            ui.runtime.manual_target("Farmer") != profile_id
            or ui.runtime.manual_target(intent["merchant"]["character"])
            != authority.merchant_id
            or any(
                c.manual_session_blocked(owner)
                for owner in (profile_id, authority.merchant_id)
            )
        ):
            raise CaptureUnavailable(
                "Farmer presentation receipt or participant hold changed"
            )
        from conquest.character_context import current as selected_context, registry

        profiles = registry()
        selected = selected_context()
        if profiles:
            profile = profiles.resolve(profile_id, role="Farmer", server="America")
            if (
                selected is None
                or selected.profile.id != profile_id
                or profile.id != profile_id
                or profile.role != "Farmer"
                or selected.profile.role != "Farmer"
                or not profile.local_enabled
                or profile.name != intent["farmer"]["character"]
                or profile.server != intent["farmer"]["server"]
                or profile.character_uid is not None
                and profile.character_uid != intent["farmer"].get("character_uid")
            ):
                raise ValueError("Selected farmer profile changed before presentation")
        elif profile_id != "Farmer":
            raise ValueError("Farmer profile binding is unavailable")
        if (
            ui.app.observer is not observer
            or observer.operations.target is not target
            or type(hwnd) is not int
            or hwnd <= 0
            or target.hwnd != hwnd
            or observer.character != intent["farmer"]["character"]
            or intent["farmer"]["server"] != "America"
            or observer.adapter.identity != identity
            or ui.app.client != (identity["pid"], hwnd, identity)
            or ui.app.host is not host
            or host.mode != "owned"
            or host.saved is not saved
            or not saved
            or saved.hwnd != hwnd
            or saved.identity != identity
        ):
            raise ValueError("Farmer observer, target, or owned host changed")
        if authority.driver is not None and (
            authority.driver.driver.observer is not observer
            or authority.driver.driver.target is not target
        ):
            raise ValueError("Farmer delivery driver changed before presentation")
        observer.adapter.assert_identity()
        host.api.assert_owner(hwnd, identity)
        return host

    def callback():
        binding()
        from conquest.client_attachment import require_viewport

        pane = ui.app.pane
        require_viewport(pane.winfo_width(), pane.winfo_height())
        siblings = [
            other for other in ui.hosts.values() if other is not host and other.saved
        ]
        # Validate every sibling before any hide/show, including a later bad one.
        for other in siblings:
            other.api.assert_owner(other.saved.hwnd, other.saved.identity)
        binding()
        ui.notebook.select(ui.frames["Farmer"])
        binding()
        layout = getattr(ui, "apply_client_compact_layout", None)
        if layout:
            binding()
            layout()
            binding()
        binding()
        ui.root.update_idletasks()
        binding()
        if not pane.winfo_ismapped():
            raise ValueError("Farmer pane did not become visible")
        require_viewport(pane.winfo_width(), pane.winfo_height())
        for other in siblings:
            binding()
            other.api.assert_owner(other.saved.hwnd, other.saved.identity)
            other.api.show_async(other.saved.hwnd, 0)
            binding()
        binding()
        host.resize(pane.winfo_width(), pane.winfo_height())
        binding()
        result["prepared"] = True

    check()
    binding()
    fence = getattr(ui.coordinator, "fence", None)
    if fence:
        callback = fence.guard_callback(fence.capture(), callback)
    ui.ui_requests.put((callback, done, result))
    if not done.wait(max(0, expires - time.monotonic())):
        result["expired"] = True
        raise CaptureUnavailable(
            "Farmer presentation timed out; no delivery input sent"
        )
    if result.get("error"):
        raise ValueError(result["error"])
    if result.get("prepared") is not True:
        raise CaptureUnavailable(
            "Farmer presentation was not verified; no delivery input sent"
        )

    def verify():
        check()
        binding()

    verify()
    from conquest.merchants.ui import wait_for_merchant_surface

    wait_for_merchant_surface(host, list(ui.hosts.values()), verify)
    verify()
    return verify
