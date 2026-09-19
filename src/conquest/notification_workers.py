"""Handover only the native app's two fixed, paused notification children."""
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from conquest.character_context import state_path
from conquest.discord_notify import read_json,write_json

WORKERS={'farmer':('run_discord_notifications.py','reports/discord-status.json'),
         'shops':('run_shop_notifications.py','reports/merchants/shops-alert-status.json')}
_lock=threading.Lock()
HANDOVER='.runtime/notification-worker-handover.json'


def python_paths(python):
    base=Path(getattr(sys,'_base_executable',sys.executable))
    return {str(Path(python).resolve()).casefold(),
            str(base.with_name(Path(python).name).resolve()).casefold()}


def validate_worker(worker,report,identity,*,root,owner_pid,python,now,owned=False):
    if worker not in WORKERS:raise ValueError('Unknown notification worker')
    pid=report.get('pid');updated=report.get('updated_at')
    if (type(pid) is not int or pid<=0 or pid==owner_pid or identity.get('pid')!=pid
            or type(updated) not in (int,float) or not -2<=now-updated<=15
            or report.get('state')!='Paused'):
        raise ValueError('Notification worker needs a fresh paused receipt')
    started=identity.get('creation_time_100ns',0)
    if started<=0 or (started/10000000-11644473600)>updated:
        raise ValueError('Notification worker PID was reused after its receipt')
    expected=[str(Path(python).resolve()),str((Path(root)/'scripts'/WORKERS[worker][0]).resolve())]
    actual=identity.get('argv',[])
    if (not owned and identity.get('parent_pid')!=owner_pid or len(actual)!=2
            or str(Path(actual[0]).resolve()).casefold() not in python_paths(python)
            or str(Path(actual[1]).resolve()).casefold()!=expected[1].casefold()
            or str(Path(identity.get('path','')).resolve()).casefold() not in python_paths(python)):
        raise ValueError('Process is not this app\'s exact notification child')
    return {'pid':pid,'creation_time_100ns':started,'path':identity['path']}


def same_script(left,right,python):
    return (len(left)==len(right)==2
        and all(str(Path(args[0]).resolve()).casefold() in python_paths(python) for args in (left,right))
        and str(Path(left[1]).resolve()).casefold()==str(Path(right[1]).resolve()).casefold())


def ownership_chain(processes,identity,owner,*,python):
    """Allow only the known venv Python wrapper between worker and app."""
    chain=[];parent=identity.get('parent_pid')
    for _ in range(3):
        if parent==owner['pid']:
            with processes.pinned(parent,terminate=False) as (actual,_stop):
                if any(actual.get(key)!=owner.get(key) for key in ('pid','creation_time_100ns','path')):
                    raise ValueError('Notification owner PID changed')
            return chain
        if type(parent) is not int or parent<=0:break
        with processes.pinned(parent,terminate=False) as (wrapper,_stop):
            if (str(Path(wrapper['path']).resolve()).casefold() not in python_paths(python)
                    or not same_script(wrapper.get('argv',[]),identity.get('argv',[]),python)):
                raise ValueError('Notification worker has an unrelated process ancestor')
            chain.append(wrapper);parent=wrapper.get('parent_pid')
    raise ValueError('Notification worker is not descended from the recorded native app')


def prepare_handover(previous_root,*,workers=('farmer',),processes=None,python=None,clock=time.time):
    """Mint an operator-reviewed receipt while the previous native app lives.

    The root is a source release selected by the operator, never a bridge arg.
    Only fixed reports/scripts are inspected; this function stops no process.
    """
    root=Path(previous_root).resolve(strict=True);processes=processes or NativeProcesses()
    python=Path(python or Path(sys.executable).with_name('pythonw.exe'))
    if not Path(state_path('.runtime/discord.paused')).exists():raise ValueError('Notifications must be paused')
    app=read_json(state_path('reports/desktop-farming/app-state.json'));now=clock()
    if type(app.get('pid')) is not int or not -2<=now-app.get('updated_at',0)<=15:
        raise ValueError('Native app ownership receipt is stale')
    entries={}
    with processes.pinned(app['pid'],terminate=False) as (owner,_stop):
        argv=owner.get('argv',[])
        entries_allowed={str(root/'scripts/start_desktop_app.py').casefold()}
        legacy=os.environ.get('CONQUEST_LEGACY_DATA_ROOT')
        if legacy:
            entries_allowed.add(str((Path(legacy)/'scripts/start_desktop_app.py').resolve()).casefold())
        if (len(argv)<2 or str(Path(argv[1]).resolve()).casefold() not in entries_allowed
                or str(Path(owner['path']).resolve()).casefold() not in python_paths(python)
                or owner['creation_time_100ns']/10000000-11644473600>app['updated_at']):
            raise ValueError('Recorded owner is not the expected native desktop app')
        for worker in workers:
            if worker not in WORKERS:raise ValueError('Unknown notification worker')
            report=read_json(state_path(WORKERS[worker][1]))
            if type(report.get('pid')) is not int:raise ValueError('Notification worker receipt is missing')
            with processes.pinned(report['pid'],terminate=False) as (identity,_stop):
                expected_argv=[str(python),str(root/'scripts'/WORKERS[worker][0])]
                chain=ownership_chain(processes,identity,owner,python=python)
                validate_worker(worker,report,identity,
                    root=root,owner_pid=owner['pid'],python=python,now=now,owned=True)
                entries[worker]={'identity':identity,'ancestors':chain,'expected_argv':expected_argv,
                    'report_updated_at':report['updated_at'],'phase':'prepared'}
        receipt={'version':1,'phase':'prepared','prepared_at':now,'prior_root':str(root),
                 'prior_owner':owner,'workers':entries,'verification':'full_process_identity_and_owned_script'}
        write_json(state_path(HANDOVER),receipt)
    return receipt


class NativeProcesses:
    @contextmanager
    def pinned(self,pid,*,terminate=True):
        from conquest.win32 import WindowsBackend,bind
        import pythoncom
        import win32com.client
        backend=WindowsBackend()
        may_terminate=terminate
        with backend.process_handle(pid,0x1000|(0x1|0x100000 if may_terminate else 0)) as handle:
            def identity():
                path=c.create_unicode_buffer(32768);size=w.DWORD(len(path))
                if not backend.image_name(handle,0,path,c.byref(size)):raise backend.error('Read notifier image')
                times=[w.FILETIME() for _ in range(4)]
                if not backend.times(handle,*[c.byref(value) for value in times]):raise backend.error('Read notifier lifetime')
                return {'pid':pid,'path':path.value,
                    'creation_time_100ns':(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime}
            initial=identity()
            pythoncom.CoInitialize()
            try:
                process=win32com.client.GetObject('winmgmts:').Get(f'Win32_Process.Handle="{int(pid)}"')
                command=str(process.CommandLine or '');parent=int(process.ParentProcessId)
            except Exception:
                raise ValueError('Notification process metadata is unavailable') from None
            finally:pythoncom.CoUninitialize()
            shell=c.WinDLL('shell32',use_last_error=True)
            parse=bind(shell,'CommandLineToArgvW',[w.LPCWSTR,c.POINTER(c.c_int)],c.POINTER(w.LPWSTR))
            free=bind(backend.kernel,'LocalFree',[w.HLOCAL],w.HLOCAL)
            arguments=[]
            if command:
                count=c.c_int();argv=parse(command,c.byref(count))
                if not argv:raise ValueError('Notification worker command line is unavailable')
                try:arguments=[argv[i] for i in range(count.value)]
                finally:free(c.cast(argv,w.HLOCAL))
            wait=bind(backend.kernel,'WaitForSingleObject',[w.HANDLE,w.DWORD],w.DWORD)
            terminate=bind(backend.kernel,'TerminateProcess',[w.HANDLE,w.UINT],w.BOOL)
            def stop(expected):
                if not may_terminate:raise ValueError('Read-only process evidence cannot stop a worker')
                if identity()!=expected:raise ValueError('Notification worker identity changed before stop')
                if wait(handle,0)==0:return
                if not terminate(handle,0):raise backend.error('Stop owned notification worker')
                if wait(handle,2000)!=0:raise ValueError('Owned notification worker has not exited')
            yield {**initial,'argv':arguments,'parent_pid':parent},stop

    def start(self,python,script,root):
        from conquest.application_layout import RuntimeLayout
        layout=RuntimeLayout.resolve(root)
        child=subprocess.Popen([str(python),str(script)],cwd=root,env=layout.environment(),
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return child.pid


def restart(worker,*,processes=None,root=None,owner_pid=None,python=None,clock=time.time):
    """No caller-provided process identity; all authority comes from fixed reports."""
    if worker not in WORKERS:raise ValueError('Unknown notification worker')
    from conquest.application_layout import RuntimeLayout
    layout=RuntimeLayout.resolve(root)
    root=layout.root
    script=layout.script(WORKERS[worker][0])
    owner_pid=os.getpid() if owner_pid is None else owner_pid
    python=layout.python(windowed=True) if layout.immutable or python is None else Path(python)
    if not python.is_file():raise ValueError('Notification worker Python is unavailable')
    if not Path(state_path('.runtime/discord.paused')).exists():
        raise ValueError('Pause Discord notifications before worker handover')
    processes=processes or NativeProcesses()
    if not _lock.acquire(blocking=False):raise ValueError('Notification worker handover already running')
    try:
        report=read_json(state_path(WORKERS[worker][1]));pid=report.get('pid')
        if type(pid) is not int or pid<=0:raise ValueError('Notification worker receipt is missing')
        handover=read_json(state_path(HANDOVER));saved=handover.get('workers',{}).get(worker)
        with processes.pinned(pid) as (identity,stop):
            if saved:
                if (handover.get('version')!=1 or saved.get('phase')!='prepared'
                        or not 0<=clock()-handover.get('prepared_at',0)<=900
                        or any(identity.get(key)!=saved.get('identity',{}).get(key)
                               for key in ('pid','creation_time_100ns','path','parent_pid'))
                        or saved.get('identity',{}).get('argv') and identity['argv']!=saved['identity']['argv']):
                    raise ValueError('Prepared notification handover expired, changed or was already consumed')
                for expected in saved.get('ancestors',[]):
                    with processes.pinned(expected['pid'],terminate=False) as (ancestor,_stop):
                        if (any(ancestor.get(key)!=expected.get(key) for key in ('pid','creation_time_100ns','path','parent_pid'))
                                or not same_script(ancestor.get('argv',[]),identity.get('argv',[]),python)):
                            raise ValueError('Notification wrapper identity changed before handover')
                proof=validate_worker(worker,report,identity,root=handover['prior_root'],
                    owner_pid=handover['prior_owner']['pid'],python=python,now=clock(),owned=True)
            else:
                with processes.pinned(owner_pid,terminate=False) as (owner,_stop):
                    ownership_chain(processes,identity,owner,python=python)
                proof=validate_worker(worker,report,identity,root=root,owner_pid=owner_pid,python=python,now=clock(),owned=True)
            fresh=read_json(state_path(WORKERS[worker][1]))
            if fresh.get('pid')!=pid or fresh.get('state')!='Paused':
                raise ValueError('Notification pause changed before handover')
            if not Path(state_path('.runtime/discord.paused')).exists():
                raise ValueError('Notification pause was removed before handover')
            if saved:
                saved.update(phase='consumed',consumed_by=owner_pid,consumed_at=clock())
                write_json(state_path(HANDOVER),handover)
            stop(proof)
        # The queue is deliberately neither rewritten nor filtered here. The
        # replacement reads its preserved state under the existing worker lock.
        replacement=processes.start(python,script,root)
        receipt={'worker':worker,'previous_pid':pid,'replacement_pid':replacement,
            'owner_pid':owner_pid,'at':clock(),'phase':'restart_requested','paused':True,
            'queue_preserved':True}
        if saved:
            saved['result']=receipt;write_json(state_path(HANDOVER),handover)
        else:write_json(state_path('.runtime/notification-worker-restart.json'),receipt)
        return receipt
    finally:_lock.release()
