from pathlib import Path
from types import SimpleNamespace as NS
import sys

import pytest


@pytest.mark.parametrize("calibration", [False, True])
@pytest.mark.parametrize("clients", [[], [(10, 20, 30)], [(10, 20, 30), (40, 50, 60)]])
def test_memory_only_start_rejects_before_discovery_or_elevation(
    monkeypatch, calibration, clients
):
    from conquest import desktop_app

    messages, records = [], []

    def forbidden(*args, **kwargs):
        pytest.fail("Memory-only --start must not discover, select or elevate")

    app = NS(
        profile=Path(__file__).resolve().parents[1]
        / "profiles/desktop-foreground.example.yaml",
        state_text=NS(set=messages.append),
        record=lambda **fields: records.append(fields),
        refresh_client=forbidden,
        catalog=NS(windows=forbidden),
        client=clients[0] if clients else None,
    )
    monkeypatch.setattr(desktop_app, "elevated_start", forbidden)
    desktop_app.DesktopApp.start(app, calibration)
    assert (
        "--embed-client --client-pid PID --client-started CREATION_TIME --client-hwnd HWND"
        in messages[-1]
    )
    assert records[-1]["state"] == "Stopped"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--embed-client"],
        ["--embed-client", "--client-pid", "7", "--client-hwnd", "9"],
        ["--start", "--client-pid", "7", "--client-started", "8", "--client-hwnd", "9"],
    ],
)
def test_cli_rejects_incomplete_or_misapplied_pin_before_gui(monkeypatch, arguments):
    from conquest import desktop_app

    monkeypatch.setattr(sys, "argv", ["conquest", *arguments])
    monkeypatch.setattr(
        desktop_app.tk, "Tk", lambda: pytest.fail("Invalid pin must fail before GUI")
    )
    with pytest.raises(SystemExit) as error:
        desktop_app.main()
    assert error.value.code == 2


def test_existing_embedding_path_accepts_only_one_exact_identity():
    from conquest.client_wrapper import ClientWindow, pinned_client

    selected = ClientWindow({"pid": 7, "creation_time_100ns": 8}, 9, "Test")
    other = ClientWindow({"pid": 7, "creation_time_100ns": 80}, 9, "Test")
    assert (
        pinned_client(NS(windows=lambda: [other, selected]), selected.key) is selected
    )
    for candidates in ([], [other], [selected, selected]):
        with pytest.raises(ValueError, match="closed or changed"):
            pinned_client(NS(windows=lambda: candidates), selected.key)
