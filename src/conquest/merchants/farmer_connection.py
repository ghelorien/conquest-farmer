"""Attach one explicitly identified existing client without launching another."""
import time
from conquest.character_context import farmer_name


def attach(ui,pid,started,*,queued_at):
    from conquest.mouse_priority import require_idle
    from conquest.reconnect import login_screen
    from conquest.memory_life import read_life
    app=ui.app
    if type(pid) is not int or type(started) is not int or min(pid,started)<=0:
        raise ValueError('An exact client PID and creation time are required')
    if time.monotonic()-queued_at>3:
        raise ValueError('Farmer attach request expired')
    if (ui.closed or app.closing or ui.coordinator.stopped or ui.coordinator.owner
            or not ui.safe_to_yield() or app.control.snapshot()['enabled']
            or ui.runtime.connecting or ui.runtime.refilling
            or any(ui.runtime.journal.pending(c) for c in ui.runtime.observers)):
        raise ValueError('Finish active work before attaching the farmer')
    if app.observer is not None or app.host.saved:
        raise ValueError('A farmer client is already attached')
    candidates=[w for w in app.catalog.windows()
                if w.identity['pid']==pid and w.identity['creation_time_100ns']==started]
    if len(candidates)!=1:raise ValueError('Selected client is absent, replaced or ambiguous')
    candidate=candidates[0]
    if any(o.adapter.identity==candidate.identity for o in ui.runtime.observers.values()):
        raise ValueError('Selected client belongs to a merchant')
    require_idle()
    # Verify the pinned process before changing its window ownership. A connected
    # client must identify as Parasite; a blank login cannot supply player stats.
    probe=app.observer_factory(pid,candidate.hwnd)
    try:
        probe.adapter.assert_identity()
        if probe.adapter.identity!=candidate.identity:raise ValueError('Selected client changed')
        if not login_screen(candidate.hwnd):
            read_life(probe.adapter,probe.health_layout,farmer_name())
    finally:probe.close()
    require_idle()
    app.embed(candidate)
    if getattr(app,'embed_layout_pending',False):
        return {'attached':False,'pending':True,'pid':pid,
                'farming_enabled':app.control.snapshot()['enabled']}
    if app.observer is None or app.observer.adapter.identity!=candidate.identity:
        raise ValueError('Farmer attachment did not complete')
    return {'attached':True,'pid':pid,'farming_enabled':app.control.snapshot()['enabled']}
