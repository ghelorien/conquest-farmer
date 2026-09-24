"""Exercise managed launcher/background writes without a real release or client."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]


def tree_snapshot(root):
    return {
        str(path.relative_to(root)): (
            "directory"
            if path.is_dir()
            else hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
    }


@pytest.mark.skipif(
    sys.platform != "win32", reason="Native workers use Windows file locks"
)
def test_managed_launcher_and_background_components_leave_release_unchanged(tmp_path):
    release = tmp_path / "release"
    data = tmp_path / "managed-data"
    for name in (
        "scripts/start_desktop_app.py",
        "scripts/_bootstrap.py",
        "pyproject.toml",
        "profiles/desktop-foreground.example.yaml",
    ):
        destination = release / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, destination)
    for name in ("src/conquest", "profiles/routes"):
        (release / name).mkdir(parents=True, exist_ok=True)
    from conquest.release import build_manifest

    assert "scripts/_bootstrap.py" in build_manifest(release)["files"]
    # Existing release artifacts must neither be overwritten nor used as state.
    for name in ("reports/merchants/market-guard.json", ".runtime/farmer-view.json"):
        destination = release / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text('{"release_sentinel": true}', encoding="utf-8")
    before = tree_snapshot(release)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CONQUEST_")
    }
    env.update(
        PYTHONPATH=str(REPO / "src"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), str(release), str(data)],
        cwd=release,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "managed probe complete" in result.stdout
    assert tree_snapshot(release) == before
    assert (data / "machine-state/reports/merchants/market-guard.json").is_file()
    assert (data / "machine-state/reports/merchants/shops-alert-status.json").is_file()
    assert (data / "machine-state/.runtime/merchant-relist.lock").is_file()


def _managed_probe(release, data):
    """Only the GUI entry is replaced; profile bootstrap and worker I/O are real."""
    import runpy
    from types import ModuleType, SimpleNamespace as NS
    from unittest.mock import patch
    from conquest.character_profiles import ProfileRegistry, context_for

    registry = ProfileRegistry(data)
    farmer = registry.add("TestFarmer")
    merchant = registry.add("Spiritual", role="Merchant")
    assert "conquest.discord_notify" not in sys.modules
    assert "conquest.merchants.journal" not in sys.modules

    def main():
        assert Path.cwd() == release
        assert os.environ["CONQUEST_DATA_ROOT"] == str(data)
        assert os.environ["CONQUEST_PROFILE_ID"] == farmer.id
        from conquest.character_context import state_path
        from conquest.discord_notify import read_json, write_json
        from conquest.merchants.coordination import InputCoordinator
        from conquest.merchants.runtime import MerchantRuntime
        from conquest.merchants.market_guard import MarketGuard
        from conquest.merchants.bridge import MerchantBridge, request
        from conquest.merchants.price_history import PriceHistory
        from conquest.merchants.ui import UnifiedUI
        from conquest.merchants import alerts, notification_health
        from conquest import discord_notify

        class OneTick:
            stopped = False

            def is_set(self):
                return self.stopped

            def wait(self, _):
                self.stopped = True

        class EndTick(BaseException):
            pass

        def finish_tick(*_):
            raise EndTick()

        profile = context_for(farmer.id, data).state_dir
        shared = data / "machine-state"
        coordinator = InputCoordinator()
        assert coordinator.path == data / "locks/input.lock"
        runtime = MerchantRuntime(NS(windows=lambda: []), coordinator)
        assert runtime.journal.path == shared / "reports/merchants/journal.sqlite3"
        assert PriceHistory().path == shared / "reports/merchants/price-history.sqlite3"
        runtime.stop_event = OneTick()
        MarketGuard(runtime).run()
        assert (
            read_json(state_path("reports/merchants/market-guard.json"))["running"]
            is True
        )
        # The existing defaults bind after profile selection in this process.
        assert discord_notify.STATE == profile / ".runtime/discord-notifications.json"
        assert alerts.STATE == shared / ".runtime/merchants/shops-alerts.json"
        bridge = MerchantBridge(lambda body: {"received": body["action"]})
        try:
            assert bridge.path == shared / ".runtime/merchants/bridge.json"
            assert request({"action": "status"}) == {"received": "status"}
        finally:
            bridge.close()
        runtime.sales_worker.stop = OneTick()
        runtime.sales_worker.run()
        with (
            patch.object(alerts, "request", return_value={"characters": {}}),
            patch("time.sleep", side_effect=finish_tick),
        ):
            for worker in (alerts.run, discord_notify.run):
                try:
                    worker()
                except EndTick:
                    pass
        assert read_json(alerts.STATUS)["state"] == "watching"
        assert read_json(discord_notify.STATUS)["state"] == "Webhook not configured"

        import queue

        resized = []
        app = NS(
            control=NS(snapshot=lambda: {"enabled": False, "revision": 1}),
            host=NS(api=NS(), resize=lambda *size: resized.append(size)),
            pane=NS(winfo_width=lambda: 800, winfo_height=lambda: 600),
        )
        ui = NS(
            app=app,
            ui_requests=queue.Queue(),
            runtime=runtime,
            safe_to_yield=lambda: True,
            coordinator=coordinator,
        )
        UnifiedUI.dispatch(ui, {"action": "farmer-view-height", "scale": 1.1})
        ui.ui_requests.get_nowait()[0]()
        assert app.host.api.height_scale == 1.1 and resized == [(800, 600)]
        assert read_json(profile / ".runtime/farmer-view.json") == {"height_scale": 1.1}
        diagnostic = Path(state_path(".runtime/account-diagnostic-spiritual.json"))
        write_json(diagnostic, {"port": 1})
        with (
            patch(
                "conquest.worker.request", return_value={"read_only": True}
            ) as health,
            patch(
                "subprocess.Popen",
                side_effect=AssertionError("Must reuse existing diagnostic"),
            ),
        ):
            result = UnifiedUI.dispatch(
                ui, {"action": "start-account-diagnostic", "profile_id": merchant.id}
            )
        assert result == {"existing": True, "read_only": True}
        assert health.call_args.args == (diagnostic, "health")
        assert (
            notification_health.describe()
            == "Discord farmer: not configured | Discord shops: not configured"
        )
        # Existence/status checks must also find configured managed paths.
        # These bytes are a test sentinel; no webhook is decrypted or contacted.
        discord_notify.SECRET.write_bytes(b"offline test sentinel")
        with patch.object(notification_health, "process_alive", return_value=True):
            assert (
                notification_health.service(
                    ".runtime/discord-webhook.dpapi",
                    "reports/discord-status.json",
                    now=read_json(discord_notify.STATUS)["updated_at"],
                )
                == "monitor running"
            )

        # Both cancellation helpers exercise their durable failure receipts;
        # identity/input validation is stubbed so this test cannot touch a client.
        from conquest.merchants import (
            cancel_reserved_request as reserved,
            empty_delivery_cancel as empty,
        )
        from conquest.merchants.journal import Journal

        class InlineThread:
            def __init__(self, *, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

            def is_alive(self):
                return False

        def no_input(*args, **kwargs):
            raise ValueError("offline test: no input")

        delivery = Journal(reserved.JOURNAL)
        delivery.begin("test-request", merchant.id, "farmer_delivery", {})
        delivery.transition("test-request", "uncertain")
        write_json(
            state_path("reports/banking/merchant-route.json"),
            {"active": {"request_id": "test-request"}},
        )
        with (
            patch("threading.Thread", InlineThread),
            patch.object(reserved, "pair", return_value=({}, {})),
            patch.object(reserved, "unchanged"),
            patch.object(reserved, "run", side_effect=no_input) as cancel,
        ):
            reserved.start(ui)
        receipt = shared / "reports/merchants/reserved-request-cancellation.json"
        assert read_json(receipt)["error"] == "offline test: no input"
        assert cancel.call_args.kwargs["output_path"] == receipt
        with (
            patch("threading.Thread", InlineThread),
            patch.object(empty, "pair", return_value=({}, {"trade": True})),
            patch.object(empty, "unchanged"),
            patch.object(empty, "run", side_effect=no_input),
        ):
            empty.start(ui, merchant.id)
        assert (
            read_json(shared / "reports/merchants/empty-delivery-cancel.json")["error"]
            == "offline test: no input"
        )

        # The supported standalone relist worker uses the same managed lock and report.
        relist = runpy.run_path(str(REPO / "scripts/merchant_relist.py"))["main"]
        with (
            patch.dict(
                relist.__globals__,
                verify_rollout=lambda: None,
                run_cycle=lambda: {"test": True},
            ),
            patch.object(sys, "argv", ["merchant_relist.py", "--watch"]),
            patch("time.sleep", side_effect=KeyboardInterrupt),
        ):
            relist()
        assert (
            read_json(shared / "reports/merchants/script-worker.json")["test"] is True
        )
        pages = data / "browser-pages.json"
        pages.write_text("{}", encoding="utf-8")
        scan = runpy.run_path(str(REPO / "scripts/merchant_scan.py"))["main"]
        original_read = Path.read_text

        def offline_read(path, *args, **kwargs):
            if str(path) == r"C:\Program Files\Classic Conquer 2.0\ini\itemtype.json":
                return "{}"
            return original_read(path, *args, **kwargs)

        with (
            patch.dict(
                scan.__globals__,
                browser_pages=lambda *_: {"test": True},
                request=lambda _: {},
            ),
            patch.object(
                sys, "argv", ["merchant_scan.py", "--browser-pages", str(pages)]
            ),
            patch.object(Path, "read_text", offline_read),
        ):
            scan()
        assert read_json(shared / "reports/merchants/market.json")["test"] is True

    app = ModuleType("conquest.desktop_app")
    app.main = main
    sys.modules["conquest.desktop_app"] = app
    launcher = release / "scripts/start_desktop_app.py"
    sys.argv = [str(launcher), "--data-root", str(data), "--profile-id", farmer.id]
    runpy.run_path(str(launcher), run_name="__main__")
    print("managed probe complete")


if __name__ == "__main__":
    _managed_probe(Path(sys.argv[1]), Path(sys.argv[2]))
