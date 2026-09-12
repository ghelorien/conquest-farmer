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


def ensure_running(route_id,*,root=None):
    root=Path(root or Path(__file__).resolve().parents[2])
    if read_json(root/state_path('.runtime/storage-halt.json')).get('active'):return False
    now=time.time()
    status=read_json(root/state_path('reports/overnight/status.json'))
    if (status.get('phase') in ACTIVE and 0<=now-status.get('updated_at',0)<15
            and process_alive(status.get('pid')) is not False):
        return False
    launch=root/state_path('.runtime/route-controller-launch.json')
    if 0<=now-read_json(launch).get('time',0)<5:return False
    from conquest.routes import RouteLibrary
    RouteLibrary(root/'profiles/routes').load(route_id)
    # This function is called only while current farming intent is On.
    (root/state_path('.runtime/overnight.stop')).unlink(missing_ok=True)
    launch.parent.mkdir(parents=True,exist_ok=True)
    python=Path(sys.executable).with_name('pythonw.exe')
    if not python.exists():python=Path(sys.executable)
    with (root/state_path('.runtime/route-controller-error.log')).open('ab') as error:
        process=subprocess.Popen([str(python),str(root/'scripts/run_overnight.py'),'--route',route_id],
            cwd=root,stdin=subprocess.DEVNULL,stdout=error,stderr=error,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    write_json(launch,{'time':now,'pid':process.pid,'route':route_id})
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
