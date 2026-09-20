"""Restore the standalone route controller whenever farming is enabled."""
from conquest.character_context import state_path
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import time
from conquest.discord_notify import read_json,write_json,process_alive

ACTIVE={'starting','hunting','restocking','changing_route','recovering_route','visiting_town','reloading'}
STARTUP_GRACE_SECONDS=15


def _pending_launch(launch, now):
    """A live launcher has not necessarily published its first heartbeat yet."""
    if not 0<=now-launch.get('time',0)<STARTUP_GRACE_SECONDS:
        return False
    pid=launch.get('pid')
    identity=launch.get('identity')
    if identity:
        try:
            from conquest.win32 import WindowsBackend
            return WindowsBackend().identity(pid)==identity
        except (OSError,TypeError,ValueError):
            return False
    return process_alive(pid) is not False


def ensure_running(route_id,*,root=None,fresh_start=False):
    from conquest.application_layout import RuntimeLayout
    # The app calls this on each heartbeat. Validate root/pin and entry paths
    # now, but hash the full immutable payload only when a launch is needed.
    layout=RuntimeLayout.resolve(root,verify=False)
    root=layout.root
    stop=root/state_path('.runtime/overnight.stop')
    if stop.exists():
        return False
    script=layout.script('run_overnight.py')
    python=layout.python(windowed=True)
    from conquest.protected_withdrawal import pending
    locks=pending(root/state_path('reports/banking/protected-withdrawals.sqlite3'))
    if locks:
        from conquest.merchants.delivery_journey import matching_scroll_recovery
        if not matching_scroll_recovery(locks,path=root/state_path('reports/banking/merchant-journey.json')):
            return False
    if read_json(root/state_path('.runtime/storage-halt.json')).get('active'):return False
    now=time.time()
    status=read_json(root/state_path('reports/overnight/status.json'))
    if status.get('phase')=='needs_attention' and not fresh_start:
        return False
    if (status.get('phase') in ACTIVE and 0<=now-status.get('updated_at',0)<15
            and process_alive(status.get('pid')) is not False):
        return False
    launch=root/state_path('.runtime/route-controller-launch.json')
    if _pending_launch(read_json(launch),now):
        return False
    layout.verify_for_launch()
    from conquest.routes import RouteLibrary
    RouteLibrary(root/'profiles/routes').load(route_id)
    # A Stop arriving during validation must still prevent this launch.
    if stop.exists():
        return False
    launch.parent.mkdir(parents=True,exist_ok=True)
    with (root/state_path('.runtime/route-controller-error.log')).open('ab') as error:
        process=subprocess.Popen([str(python),'-B',str(script),'--route',route_id],
            cwd=root,env=layout.environment(),stdin=subprocess.DEVNULL,stdout=error,stderr=error,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        from conquest.win32 import WindowsBackend
        identity=WindowsBackend().identity(process.pid)
    except (OSError,TypeError,ValueError):
        identity=None
    write_json(launch,{'time':now,'pid':process.pid,'identity':identity,'route':route_id})
    return True


@contextmanager
def controller_guard(path=Path(state_path('.runtime/route-controller.lock'))):
    import msvcrt
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as handle:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            yield False
            return
        try:yield True
        finally:
            handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
