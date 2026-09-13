"""Restore hidden merchant hosts without stealing the farmer's input."""
from conquest.merchants.journal import CHARACTERS


def restore(ui, *, host_factory=None):
    if ui.closed or ui.coordinator.owner or ui.calibrating: return
    if not ui.coordinator.lock.acquire(blocking=False): return
    try:
        if ui.coordinator.owner: return
        if host_factory is None:
            from conquest.window_host import EmbeddedWindow
            host_factory=lambda:EmbeddedWindow(mode='owned')
        for character in CHARACTERS:
            if character in ui.released_clients: continue
            pane=ui.client_panes[character]
            # Showing/selecting a tab remains the existing, guarded UI path.
            if pane.winfo_ismapped(): continue
            host=ui.hosts.get(character)
            if host and host.saved: continue
            observer=ui.runtime.observers.get(character)
            if observer is None: continue
            try:
                observer.adapter.assert_identity()
                host=host or host_factory()
                ui.hosts[character]=host
                host.attach(observer.operations.target.hwnd,observer.adapter.identity,pane.winfo_id(),
                    max(1,pane.winfo_width()),max(1,pane.winfo_height()))
                ui.layout_status[character]={'attached':True,'native_visible':False,'selected':False}
                ui.calibration_results[character]={'verified':False,
                    'note':'Client embedded; input geometry is checked when its tab opens'}
            except (ValueError,OSError) as error:
                ui.calibration_results[character]={'verified':False,'note':str(error)}
    finally:
        ui.coordinator.lock.release()
