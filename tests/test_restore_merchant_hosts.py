from types import SimpleNamespace as NS
import threading
from conquest.merchants.restore_hosts import restore


def test_hidden_hosts_restore_without_selection_input_or_permission_changes():
    calls = []

    class Host:
        saved = None

        def attach(self, *a):
            calls.append(a)
            self.saved = True

    pane = NS(
        winfo_ismapped=lambda: False,
        winfo_id=lambda: 9,
        winfo_width=lambda: 1,
        winfo_height=lambda: 1,
    )
    observer = NS(
        adapter=NS(identity={"pid": 1}, assert_identity=lambda: None),
        operations=NS(target=NS(hwnd=2)),
    )
    ui = NS(
        closed=False,
        coordinator=NS(owner=None, lock=threading.RLock()),
        calibrating=set(),
        released_clients=set(),
        client_panes={"Dutch": pane, "Spiritual": pane},
        hosts={},
        runtime=NS(observers={"Dutch": observer, "Spiritual": observer}),
        layout_status={},
        calibration_results={},
    )
    restore(ui, host_factory=Host)
    assert len(calls) == 2
    restore(ui, host_factory=Host)
    assert len(calls) == 2
    ui.hosts = {}
    ui.released_clients = {"Dutch"}
    restore(ui, host_factory=Host)
    assert len(calls) == 3
    ui.hosts = {}
    ui.coordinator.owner = "Spiritual"
    restore(ui, host_factory=Host)
    assert len(calls) == 3
