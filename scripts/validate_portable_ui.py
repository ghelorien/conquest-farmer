"""Hidden UI smoke check with no game discovery, input hooks or workers."""

from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / "src"))
os.chdir(repo)


def main():
    with tempfile.TemporaryDirectory(prefix="conquest-ui-check-") as temporary:
        os.environ["CONQUEST_DATA_ROOT"] = temporary
        from conquest.character_profiles import ProfileRegistry

        registry = ProfileRegistry(temporary)
        active = registry.add("FreshArcher")
        registry.add("Parasite")
        registry.add("Spiritual", role="Merchant")
        registry.add("Dutch", role="Merchant")
        registry.add("Farmer", role="Merchant")
        registry.add("Spiritual", "Europe", "Merchant")
        os.environ["CONQUEST_PROFILE_ID"] = active.id
        from conquest.profile_bootstrap import select_profile

        config = select_profile(
            repo,
            SimpleNamespace(profile_id=active.id, manage_profiles=False),
            Path(temporary),
        )
        from conquest.window_host import use_unaware_dpi

        use_unaware_dpi()
        import tkinter as tk
        from conquest.desktop_app import DesktopApp
        from conquest.merchants.ui import UnifiedUI
        from conquest.merchants.runtime import MerchantRuntime
        from conquest.merchants.presentation import MerchantPresentation

        root = tk.Tk()
        root.withdraw()
        with (
            patch(
                "conquest.mouse_priority.install",
                return_value=SimpleNamespace(active=lambda: False),
            ),
            patch.object(DesktopApp, "poll", lambda self: None),
            patch.object(DesktopApp, "poll_pointer_focus", lambda self: None),
            patch.object(MerchantRuntime, "start", lambda self: None),
            patch.object(MerchantPresentation, "start", lambda self: None),
        ):
            app = DesktopApp(root, config, catalog=SimpleNamespace(windows=lambda: []))
            ui = UnifiedUI(app)
            app.unified = ui
            root.update_idletasks()
            labels = [ui.notebook.tab(tab, "text") for tab in ui.notebook.tabs()]
            assert len(labels) == 7, labels
            assert "FreshArcher · Farmer" in labels and "Farmer · Merchant" in labels
            assert app.observer is None and not app.control.snapshot()["enabled"]
            assert all(not ui.runtime.refill_enabled(c) for c in ui.runtime.recoveries)
            assert (
                ui.dispatch({"action": "profiles"})["profiles"][0]["profile_id"]
                == active.id
            )
            ui.close()
            app.stop_observer()
            root.destroy()
            print(
                "PASS: dynamic tabs, reserved-name isolation, duplicate-server profile, paused startup, no client input"
            )


if __name__ == "__main__":
    main()
