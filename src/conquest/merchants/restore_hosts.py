"""Restore hidden merchant hosts without stealing the farmer's input."""

from conquest.merchants.journal import CHARACTERS
from conquest.capture import CaptureUnavailable


def _farmer_health_cache(ui):
    """Fetch farmer health off the Tk poll thread, sharing a short-lived read."""
    import os
    import threading
    import time
    from pathlib import Path

    from conquest.character_context import state_path
    from conquest.worker import request

    lock = getattr(ui, "_readonly_farmer_health_lock", None)
    if lock is None:
        lock = threading.Lock()
        ui._readonly_farmer_health_lock = lock
        ui._readonly_farmer_health_cache = {
            "pending": False,
            "requested_at": 0.0,
            "health": None,
        }
    cache = ui._readonly_farmer_health_cache
    now = time.monotonic()
    with lock:
        if not cache["pending"] and now - cache["requested_at"] >= 0.5:
            cache["pending"] = True
            cache["requested_at"] = now
            info = Path(state_path(".runtime")) / f"embedded-worker-{os.getpid()}.json"

            def read_health():
                try:
                    health = request(info, "health")
                except Exception:
                    health = None
                with lock:
                    cache["health"] = health
                    cache["pending"] = False

            threading.Thread(
                target=read_health, name="readonly-farmer-health", daemon=True
            ).start()
        return cache["health"]


def _readonly_market_hosting_safe(ui):
    """A native safe parked baseline; neither Market nor route exit is required."""
    from conquest.merchants.background_probe import probe_busy
    from conquest.merchants.listing_handoff_1078 import parked

    app, runtime, coordinator = ui.app, ui.runtime, ui.coordinator
    hosting_owner = (
        coordinator.owner == "Farmer" and coordinator.purpose == "merchant_host"
    )
    if (
        ui.closed
        or app.closing
        or coordinator.stopped
        or coordinator.owner
        and not hosting_owner
        or coordinator.manual_active()
        or runtime.manual_handoff_status() is not None
        or any(coordinator.manual_session_blocked(c) for c in ("Farmer", *CHARACTERS))
        or probe_busy(ui)
        or runtime.connecting
        or runtime.refilling
        or ui.calibrating
        or getattr(runtime, "delivery_window", None)
        or getattr(runtime, "refill_window", None)
    ):
        return False
    try:
        fence = getattr(ui, "grant_fence", None)
        grant = getattr(ui, "grant", None)
        if grant:
            token = fence.check()
            if (
                token != fence.active
                or token.request_id != grant["request_id"]
                or token.revision != app.control.snapshot()["revision"]
            ):
                return False
        parked(ui)
        # A dormant Farmer banking journal does not own this display surface.
        # Keep its assets and recovery intent untouched: fresh parked memory,
        # exclusive ownership and the active-operation guards above suffice
        # for no-activation hosting. Merchant transactions still protect their
        # own exact layout until reconciled below.
        with runtime.journal.db() as db:
            if db.execute(
                "SELECT 1 FROM transactions WHERE phase NOT IN "
                "('verified','aborted','operator_overridden') LIMIT 1"
            ).fetchone():
                return False
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        CaptureUnavailable,
    ):
        return False
    return True


def requested(ui):
    """Separate host intent cannot replace a queued refill or enable operations."""
    import time
    import uuid
    from conquest.memory_build_layout import CLIENT_SHA256_1078

    if (
        ui.closed
        or ui.app.closing
        or ui.coordinator.stopped
        or ui.coordinator.manual_active()
        or ui.runtime.manual_handoff_status() is not None
        or any(
            ui.coordinator.manual_session_blocked(c) for c in ("Farmer", *CHARACTERS)
        )
        or ui.runtime.connecting
        or ui.runtime.refilling
        or ui.calibrating
        or getattr(ui.runtime, "delivery_window", None)
        or getattr(ui.runtime, "refill_window", None)
    ):
        return None
    # A stranded transaction needs its exact reconciliation/cleanup grant.
    # Keep the display intent for afterward; a host-only window cannot help it.
    with ui.runtime.journal.db() as db:
        if db.execute(
            "SELECT 1 FROM transactions WHERE phase NOT IN "
            "('verified','aborted','operator_overridden') LIMIT 1"
        ).fetchone():
            return None
    wanted = {}
    for character in CHARACTERS:
        if character in ui.released_clients:
            continue
        observer = ui.runtime.observers.get(character)
        host = ui.hosts.get(character)
        if (
            observer is None
            or getattr(observer.adapter, "expected_sha256", None) != CLIENT_SHA256_1078
            or not getattr(observer, "merchant_observation_only", False)
        ):
            continue
        if host and host.saved:
            if not ui.client_panes[character].winfo_ismapped() or ui.layout_status.get(
                character, {}
            ).get("native_visible"):
                continue
        snapshot = ui.runtime.latest.get(character) or {}
        if (
            snapshot.get("identity") != observer.adapter.identity
            or not 0 <= time.time() - snapshot.get("timestamp", 0) <= 5
        ):
            continue
        wanted[str(character)] = dict(observer.adapter.identity)
    if not wanted:
        ui.native_host_request = None
        return None
    old = getattr(ui, "native_host_request", None)
    if not old or old.get("identities") != wanted:
        old = {"request_id": "merchant-host:" + uuid.uuid4().hex, "identities": wanted}
        ui.native_host_request = old
    return old


def released(ui, character, value):
    """Only explicit release/show changes this durable preference."""
    ui.runtime.journal.set(character, "host_released", bool(value))
    if value:
        ui.released_clients.add(character)
    else:
        ui.released_clients.discard(character)


def present_existing(ui, character, *, select=False, expected=None):
    """Show an already owned native client without granting gameplay authority.

    A login shell and an interrupted town journal must not make the user's
    attached window inaccessible. Only Win32 identity/ownership and the exact
    existing host are used here; no memory snapshot, focus or input is needed.
    """
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    from conquest.merchants.background_probe import probe_busy

    coordinator = ui.coordinator
    if not coordinator.lock.acquire(blocking=False):
        raise ValueError("An active input action owns the client surface")
    try:
        host = ui.hosts.get(character)
        observer = ui.runtime.observers.get(character)
        pane = ui.client_panes[character]

        def binding():
            if (
                ui.closed
                or ui.app.closing
                or coordinator.owner is not None
                or probe_busy(ui)
                or character in ui.released_clients
                or not host
                or not host.saved
                or host.mode != "owned"
                or observer is None
                or getattr(observer.adapter, "expected_sha256", None)
                != CLIENT_SHA256_1078
                or ui.hosts.get(character) is not host
                or ui.runtime.observers.get(character) is not observer
                or host.saved.identity != observer.adapter.identity
                or host.saved.hwnd != observer.hwnd
                or host.parent != pane.winfo_id()
            ):
                raise ValueError(
                    "Exact existing merchant host is unavailable for display"
                )
            if expected is not None and (
                host is not expected[0]
                or observer is not expected[1]
                or (host.saved.hwnd, host.saved.identity) != expected[2:]
            ):
                raise ValueError("Requested merchant view binding changed")
            observer.adapter.assert_identity()
            host.api.assert_owner(host.saved.hwnd, host.saved.identity)
            gui = host.api.gui
            if gui.GetAncestor(host.saved.hwnd, 2) != host.saved.hwnd or gui.GetWindow(
                host.saved.hwnd, 4
            ) != gui.GetAncestor(pane.winfo_id(), 2):
                raise ValueError("Existing merchant native owner changed")

        binding()
        if select:
            ui.notebook.select(ui.frames[character])
            ui.detail_tabs[character].select(0)
            layout = getattr(ui, "apply_client_compact_layout", None)
            if layout:
                layout()
            ui.root.update_idletasks()
        binding()
        if (
            not pane.winfo_ismapped()
            or min(pane.winfo_width(), pane.winfo_height()) <= 1
        ):
            raise ValueError(
                "Select the merchant Client tab before showing its existing view"
            )
        for other, other_host in ui.hosts.items():
            if other != character and other_host.saved:
                other_host.api.assert_owner(
                    other_host.saved.hwnd, other_host.saved.identity
                )
                other_host.api.show_async(other_host.saved.hwnd, 0)
                ui.layout_status.setdefault(other, {}).update(
                    native_visible=False, selected=False
                )
        binding()
        size = (pane.winfo_width(), pane.winfo_height())
        host.resize(*size)
        binding()
        visible = bool(host.api.gui.IsWindowVisible(host.saved.hwnd))
        ui.layout_status.setdefault(character, {}).update(
            native_visible=visible,
            selected=True,
            pane_size=list(size),
            existing_host_view=True,
        )
        return {
            "character": str(character),
            "selected": True,
            "native_visible": visible,
            "hwnd": host.saved.hwnd,
            "identity": dict(host.saved.identity),
            "display_only": True,
        }
    finally:
        coordinator.lock.release()


def present_user_view(ui, character, *, expected=None, host_factory=None):
    """Explicit display request may host an already identified observer only.

    No login discovery, activation or gameplay memory read occurs. Automatic
    tab polling uses present_existing and never enters this attachment path.
    """
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    from conquest.merchants.background_probe import probe_busy

    coordinator = ui.coordinator
    if not coordinator.lock.acquire(blocking=False):
        raise ValueError("An active input action owns the client surface")
    try:
        host = ui.hosts.get(character)
        observer = ui.runtime.observers.get(character)
        if (
            ui.closed
            or ui.app.closing
            or coordinator.owner is not None
            or probe_busy(ui)
            or character in ui.released_clients
            or observer is None
            or not getattr(observer, "merchant_observation_only", False)
            or observer.adapter.expected_sha256 != CLIENT_SHA256_1078
        ):
            raise ValueError(
                "Manual display requires an already identified native merchant"
            )
        if expected is not None and (
            host is not expected[0]
            or observer is not expected[1]
            or (observer.hwnd, observer.adapter.identity) != expected[2:]
        ):
            raise ValueError("Requested merchant view binding changed")
        if host and host.saved:
            return present_existing(ui, character, select=True, expected=expected)
        identity = dict(observer.adapter.identity)
        hwnd = observer.hwnd
        observer.adapter.assert_identity()
        if host_factory is None:
            from conquest.window_host import EmbeddedWindow

            host_factory = lambda: EmbeddedWindow(mode="owned")
        candidate = host_factory()
        if candidate.mode != "owned" or candidate.saved is not None:
            raise ValueError("Manual display requires a new owned-window host")
        gui = candidate.api.gui
        candidate.api.assert_owner(observer.hwnd, observer.adapter.identity)
        if (
            gui.GetAncestor(observer.hwnd, 2) != observer.hwnd
            or gui.GetWindow(observer.hwnd, 4)
            or gui.IsIconic(observer.hwnd)
        ):
            raise ValueError(
                "Manual display needs the exact unowned, restored native window"
            )
        ui.notebook.select(ui.frames[character])
        ui.detail_tabs[character].select(0)
        layout = getattr(ui, "apply_client_compact_layout", None)
        if layout:
            layout()
        ui.root.update_idletasks()
        pane = ui.client_panes[character]
        if (
            not pane.winfo_ismapped()
            or min(pane.winfo_width(), pane.winfo_height()) <= 1
            or ui.closed
            or ui.app.closing
            or coordinator.owner is not None
            or probe_busy(ui)
            or character in ui.released_clients
            or ui.runtime.observers.get(character) is not observer
            or ui.hosts.get(character) is not host
            or observer.hwnd != hwnd
            or observer.adapter.identity != identity
        ):
            raise ValueError("Manual merchant pane or identity changed before display")
        observer.adapter.assert_identity()
        candidate.api.assert_owner(observer.hwnd, observer.adapter.identity)
        if gui.GetWindow(observer.hwnd, 4) or gui.IsIconic(observer.hwnd):
            raise ValueError("Native window ownership changed before manual display")
        candidate.attach(
            hwnd, identity, pane.winfo_id(), pane.winfo_width(), pane.winfo_height()
        )
        ui.hosts[character] = candidate
        return present_existing(ui, character)
    finally:
        coordinator.lock.release()


def present_native(ui, character, expected):
    """Tk presentation for an already admitted native lease, never attachment."""
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    from conquest.client_attachment import require_viewport

    host, observer, hwnd, identity, purpose = expected

    def binding():
        if (
            ui.closed
            or ui.app.closing
            or ui.coordinator.stopped
            or ui.coordinator.manual_active()
            or ui.coordinator.owner != character
            or ui.coordinator.purpose != purpose
            or purpose
            not in (
                "booth_listing_1078_once",
                "owned_booth_panel_1078",
                "trade",
                "delivery_accept_probe",
                "delivery_confirm_probe",
                "empty_delivery_cancel",
                "delivery_empty_recovery",
                "merchant_return_1078",
            )
            or not ui.safe_to_yield()
            or character in ui.released_clients
            or ui.hosts.get(character) is not host
            or ui.runtime.observers.get(character) is not observer
            or not host.saved
            or host.mode != "owned"
            or host.saved.hwnd != hwnd
            or host.saved.identity != identity
            or observer.adapter.identity != identity
            or observer.adapter.expected_sha256 != CLIENT_SHA256_1078
            or observer.hwnd != hwnd
        ):
            raise ValueError("Exact native merchant host lease changed")
        observer.adapter.assert_identity()
        host.api.assert_owner(hwnd, identity)

    binding()
    foreground = host.api.gui.GetForegroundWindow()
    bookmark = {"tab": ui.notebook.select(), "hwnd": foreground, "identity": None}
    if foreground:
        try:
            import ctypes
            from ctypes import wintypes

            pid = wintypes.DWORD()
            host.api.backend.window_pid(foreground, ctypes.byref(pid))
            bookmark["identity"] = host.api.backend.identity(pid.value)
        except (OSError, ValueError):
            pass
    ui.input_bookmarks[character] = bookmark
    ui.notebook.select(ui.frames[character])
    ui.detail_tabs[character].select(0)
    layout = getattr(ui, "apply_client_compact_layout", None)
    if layout:
        layout()
    ui.root.update_idletasks()
    binding()
    pane = ui.client_panes[character]
    size = require_viewport(pane.winfo_width(), pane.winfo_height())
    if not pane.winfo_ismapped():
        raise ValueError("Exact native merchant pane is not mapped")
    for other in ui.hosts.values():
        if other is not host and other.saved:
            other.api.assert_owner(other.saved.hwnd, other.saved.identity)
            other.api.show_async(other.saved.hwnd, 0)
    binding()
    host.resize(*size)
    binding()
    ui.layout_status.setdefault(character, {}).update(
        selected=True,
        native_visible=bool(host.api.gui.IsWindowVisible(hwnd)),
        pane_size=list(size),
    )


def restore_readonly(ui, character, *, host_factory=None):
    """No activation, input qualification, saved control or tab selection."""
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    from conquest.merchants.coordination import input_scope
    from conquest.client_attachment import require_viewport

    observer = ui.runtime.observers.get(character)
    host = ui.hosts.get(character)
    pane = ui.client_panes[character]
    status = ui.layout_status.get(character, {})
    if (
        host
        and host.saved
        and observer
        and host.saved.identity == observer.adapter.identity
        and pane.winfo_ismapped()
        and status.get("native_visible")
        and status.get("pane_size") == [pane.winfo_width(), pane.winfo_height()]
    ):
        return True  # Already displayed; no surface mutation or new permission.
    if (
        character in ui.released_clients
        or observer is None
        or observer.adapter.expected_sha256 != CLIENT_SHA256_1078
        or not _readonly_market_hosting_safe(ui)
    ):
        return False
    with input_scope(purpose="merchant_host"):
        if not _readonly_market_hosting_safe(ui):
            return False
        observer.adapter.assert_identity()
        host = ui.hosts.get(character)
        if host is None:
            if host_factory is None:
                from conquest.window_host import EmbeddedWindow

                host_factory = lambda: EmbeddedWindow(mode="owned")
            host = host_factory()
            ui.hosts[character] = host
        if host.mode != "owned":
            return False
        if host.api.gui.IsIconic(observer.hwnd):
            ui.calibration_results[character] = {
                "verified": False,
                "note": "Native host deferred: minimized client cannot use no-activation attachment",
            }
            return False
        if host.saved and host.saved.identity != observer.adapter.identity:
            raise ValueError("Merchant hosted process changed")
        mapped = bool(pane.winfo_ismapped())
        if mapped:
            size = require_viewport(pane.winfo_width(), pane.winfo_height())
        else:
            rect = host.api.gui.GetClientRect(observer.hwnd)
            size = require_viewport(rect[2] - rect[0], rect[3] - rect[1])
        if not host.saved:
            host.attach(
                observer.hwnd, observer.adapter.identity, pane.winfo_id(), *size
            )
        else:
            host.resize(*size)
        observer.adapter.assert_identity()
        host.api.assert_owner(observer.hwnd, observer.adapter.identity)
        gui = host.api.gui
        if gui.GetAncestor(observer.hwnd, 2) != observer.hwnd or gui.GetWindow(
            observer.hwnd, 4
        ) != gui.GetAncestor(pane.winfo_id(), 2):
            raise ValueError("Native merchant host ownership was not retained")
        ui.coordinator.surface_blocks[character] = True
        ui.layout_status[character] = {
            "attached": True,
            "native_visible": bool(gui.IsWindowVisible(observer.hwnd)),
            "selected": mapped,
            "auto_read_only_host": True,
            "pane_size": list(size),
        }
        status = getattr(ui.runtime, "attachments", {}).get(character)
        if status:
            status.attached = True
            status.enter("memory")
            status.observation_ready = True
            ui.runtime.journal.set(character, "attachment", status.snapshot())
        return True


def restore(ui, *, host_factory=None):
    if ui.closed or ui.coordinator.owner or ui.calibrating:
        return
    if not ui.coordinator.lock.acquire(blocking=False):
        return
    try:
        if ui.coordinator.owner:
            return
        if host_factory is None:
            from conquest.window_host import EmbeddedWindow

            host_factory = lambda: EmbeddedWindow(mode="owned")
        for character in CHARACTERS:
            if character in ui.released_clients:
                continue
            pane = ui.client_panes[character]
            host = ui.hosts.get(character)
            if host and host.saved:
                continue
            observer = ui.runtime.observers.get(character)
            if observer is None:
                continue
            if getattr(observer, "merchant_observation_only", False):
                try:
                    restore_readonly(ui, character, host_factory=host_factory)
                except (ValueError, OSError, CaptureUnavailable):
                    pass
                continue
            # Showing/selecting a tab remains the existing, guarded UI path.
            if pane.winfo_ismapped():
                continue
            try:
                observer.adapter.assert_identity()
                host = host or host_factory()
                ui.hosts[character] = host
                host.attach(
                    observer.operations.target.hwnd,
                    observer.adapter.identity,
                    pane.winfo_id(),
                    max(1, pane.winfo_width()),
                    max(1, pane.winfo_height()),
                )
                ui.layout_status[character] = {
                    "attached": True,
                    "native_visible": False,
                    "selected": False,
                }
                ui.calibration_results[character] = {
                    "verified": False,
                    "note": "Client embedded; input geometry is checked when its tab opens",
                }
            except (ValueError, OSError) as error:
                ui.calibration_results[character] = {
                    "verified": False,
                    "note": str(error),
                }
    finally:
        ui.coordinator.lock.release()
