"""Persistent, user-requested shutdown when both warehouses are full."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import time
from conquest.discord_notify import read_json,write_json

HALT=Path('.runtime/storage-halt.json')
REASON='Town and Market warehouses are full; farming stopped and automatic reconnect disabled'


def active():return bool(read_json(HALT).get('active'))


def reason():return read_json(HALT).get('reason',REASON)


def clear_by_user():
    HALT.unlink(missing_ok=True)
    from conquest.storage_overflow import JOURNAL
    state=read_json(JOURNAL)
    if state.get('phase')=='full':
        state['phase']='market';write_json(JOURNAL,state)
    from conquest import meteor_banking
    state=read_json(meteor_banking.JOURNAL)
    if state.get('phase')=='market_full':
        state['phase']='storing_scroll';write_json(meteor_banking.JOURNAL,state)


def request_stop(loop,stored,pending,*,reason=REASON):
    from conquest.worker import request
    from conquest.overnight import OvernightStopped
    health=loop.health()
    write_json(HALT,{'active':True,'time':time.time(),'reason':reason,
        'target':health['target'],'market_capacity':stored['capacity'],
        'market_count':len(stored['items']),'pending_uids':[i['uid'] for i in pending],
        'disconnected':False})
    loop.phase='stopped'
    loop.record('storage_full_stop',detail=reason,activity=reason)
    request(loop.info,'controls',{'enabled':False})
    raise OvernightStopped(reason)


def disconnect_exact_client(identity):
    """Close only the pinned game process; verify identity on the same handle."""
    from conquest.win32 import WindowsBackend,bind
    if Path(identity['path']).name.casefold()!='imconquer.exe':
        raise ValueError('Storage shutdown only supports the verified game client')
    backend=WindowsBackend()
    try:
        handle=backend.open_process(0x1000|0x1|0x100000,False,identity['pid'])
        if not handle:
            if c.get_last_error()==87:return True  # Process already exited.
            raise backend.error('OpenProcess for storage shutdown')
        try:
            path=c.create_unicode_buffer(32768);size=w.DWORD(len(path))
            if not backend.image_name(handle,0,path,c.byref(size)):raise backend.error('Query game path')
            creation,exit_time,kernel,user=(w.FILETIME() for _ in range(4))
            if not backend.times(handle,*[c.byref(v) for v in (creation,exit_time,kernel,user)]):
                raise backend.error('Read game creation time')
            started=(creation.dwHighDateTime<<32)|creation.dwLowDateTime
            if started!=identity['creation_time_100ns'] or path.value.casefold()!=identity['path'].casefold():
                raise ValueError('Game process identity changed; no other process closed')
            terminate=bind(backend.kernel,'TerminateProcess',[w.HANDLE,w.UINT],w.BOOL)
            wait=bind(backend.kernel,'WaitForSingleObject',[w.HANDLE,w.DWORD],w.DWORD)
            if wait(handle,0)==0:return True
            if not terminate(handle,0):raise backend.error('Close game for full storage')
            if wait(handle,3000)!=0:raise ValueError('Game disconnection has not completed')
            return True
        finally:backend.close_handle(handle)
    except KeyError as error:raise ValueError('Incomplete game identity for disconnect') from error


def enforce(app,disconnect=disconnect_exact_client):
    halt=read_json(HALT)
    if not halt.get('active'):return False
    app.control.update({'enabled':False})
    app.reconnect_pending=False
    (app.output/'stop.request').write_text('Storage full')
    Path('.runtime/overnight.stop').write_text('Storage full')
    if getattr(app,'reload_preparing',False):app.reload_cancel.set()
    if not halt.get('disconnected') and time.time()>=getattr(app,'next_storage_disconnect',0):
        app.next_storage_disconnect=time.time()+5
        try:
            halt['disconnected']=disconnect(halt['target']) is True
            halt.pop('disconnect_error',None)
        except (ValueError,OSError) as error:halt['disconnect_error']=str(error)
        write_json(HALT,halt)
        app.record(storage_halt=halt,state='Storage full — stopped',kills_per_hour=0)
    text=('Storage full — disconnected; clear warehouse space and press Farming On to resume'
          if halt.get('disconnected') else 'Storage full — farming stopped; disconnect pending')
    if getattr(app,'last',{}).get('storage_halt')!=halt:
        app.record(storage_halt=halt,state='Storage full — stopped',current_activity=text,kills_per_hour=0)
    app.state_text.set(text);app.activity_text.set(text);app.memory_text.set(text)
    return True
