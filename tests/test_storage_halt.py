from types import SimpleNamespace as NS
import pytest
from conquest import storage_halt as h
from conquest.discord_notify import write_json,read_json


def test_stop_is_persisted_before_disabling_controls(monkeypatch):
    calls=[]
    def request(*args):
        assert h.active()
        calls.append(args)
    monkeypatch.setattr('conquest.worker.request',request)
    from conquest.overnight import OvernightStopped
    loop=NS(info='worker',health=lambda:{'target':{'pid':42}},record=lambda *a,**kw:None)
    with pytest.raises(OvernightStopped):h.request_stop(loop,{'capacity':2,'items':[{},{}]},[{'uid':8}])
    assert calls==[('worker','controls',{'enabled':False})]
    assert read_json(h.HALT)['pending_uids']==[8]


def test_halt_survives_polling_and_requires_manual_resume(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);(tmp_path/'.runtime').mkdir()
    write_json(h.HALT,{'active':True,'target':{'pid':42},'disconnected':False})
    calls=[];widget=NS(set=lambda value:None)
    app=NS(control=NS(update=lambda value:calls.append(('control',value))),output=tmp_path,
        record=lambda **kw:None,state_text=widget,activity_text=widget,memory_text=widget)
    def disconnect(identity):calls.append(('disconnect',identity));return True
    assert h.enforce(app,disconnect)
    assert h.enforce(app,disconnect)
    assert sum(kind=='disconnect' for kind,value in calls)==1
    assert h.active() and read_json(h.HALT)['disconnected'] and not app.reconnect_pending
    from conquest.desktop_app import DesktopApp
    with pytest.raises(ValueError,match='manually'):DesktopApp.update_control(app,{'enabled':True})
    h.clear_by_user()
    assert not h.active()


@pytest.mark.parametrize('changed',[False,True])
def test_disconnect_uses_pinned_creation_time_on_termination_handle(monkeypatch,changed):
    from conquest import win32
    calls=[];waits=iter([258,0])
    identity={'pid':42,'path':r'C:\Game\ImConquer.exe','creation_time_100ns':123}
    def name(handle,flags,path,size):path.value=identity['path'];return True
    def times(handle,creation,*rest):creation._obj.dwLowDateTime=124 if changed else 123;return True
    api=NS(open_process=lambda *a:1,image_name=name,times=times,close_handle=lambda handle:calls.append('closed'),kernel=None)
    functions={'TerminateProcess':lambda *a:calls.append('terminated') or True,
               'WaitForSingleObject':lambda *a:next(waits)}
    monkeypatch.setattr(win32,'WindowsBackend',lambda:api)
    monkeypatch.setattr(win32,'bind',lambda library,name,*a:functions[name])
    if changed:
        with pytest.raises(ValueError,match='identity changed'):h.disconnect_exact_client(identity)
        assert calls==['closed']
    else:
        assert h.disconnect_exact_client(identity)
        assert calls==['terminated','closed']
