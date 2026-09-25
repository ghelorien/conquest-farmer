"""Hidden UI smoke check with no game discovery, input hooks or workers.

Sample character names are arguments, never built-in merchant names: the
check proves the UI works for whatever characters a PC's profiles contain.
"""

import argparse
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / "src"))


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--farmer", default="FreshArcher", help="Active farmer")
    parser.add_argument(
        "--other-farmer", default="SecondArcher", help="A second, inactive farmer"
    )
    parser.add_argument(
        "--merchant",
        action="append",
        help="America merchant profile (repeatable; default: two sample merchants)",
    )
    options = parser.parse_args(argv)
    if not options.merchant:
        options.merchant = ["SampleMerchantOne", "SampleMerchantTwo"]
    return options


def main(argv=None):
    options = arguments(argv)
    os.chdir(repo)
    with tempfile.TemporaryDirectory(prefix="conquest-ui-check-") as temporary:
        os.environ["CONQUEST_DATA_ROOT"] = temporary
        from conquest.character_profiles import ProfileRegistry

        registry = ProfileRegistry(temporary)
        active = registry.add(options.farmer)
        registry.add(options.other_farmer)
        for name in options.merchant:
            registry.add(name, role="Merchant")
        # A merchant literally named like the reserved role label.
        registry.add("Farmer", role="Merchant")
        # The same merchant name on another server needs its own profile.
        registry.add(options.merchant[0], "Europe", "Merchant")
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
            # Overview + farmer + each America merchant (including "Farmer")
            # + the inactive farmer + the other-server merchant.
            assert len(labels) == 5 + len(options.merchant), labels
            assert (
                f"{options.farmer} · Farmer" in labels and "Farmer · Merchant" in labels
            )
            assert all(f"{name} · Merchant" in labels for name in options.merchant)
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
