from types import SimpleNamespace
import pytest
from conquest.client_attachment import AttachmentStatus, fit_geometry, require_viewport, ViewportTooSmall, find_installation


@pytest.mark.parametrize('resolution',[(1366,768),(1920,1080),(3840,2160)])
@pytest.mark.parametrize('scale',[1,1.25,1.5,1.75,2])
def test_restored_app_is_reachable_after_monitor_removed(resolution,scale):
    width,height=[int(v/scale) for v in resolution]
    w,h,x,y=fit_geometry(1500,1100,5000,-2500,[(0,0,width,height-40)])
    assert x>=0 and y>=0 and x+w<=width and y+h<=height-40
    if w<1036 or h<793:
        with pytest.raises(ViewportTooSmall):require_viewport(w,h)
    else:assert require_viewport(w,h)==(w,h)


def test_mixed_monitor_negative_origin_preserved():
    assert fit_geometry(1000,700,-1200,50,[(-1920,0,0,1040),(0,0,3840,2120)])==(1000,700,-1200,50)


def test_install_discovery_does_not_depend_on_program_files(tmp_path):
    root=tmp_path/'Games/My Conquest';client=root/'bin/64/ImConquer.exe'
    client.parent.mkdir(parents=True);client.write_bytes(b'MZ');(root/'ini').mkdir();(root/'map').mkdir()
    assert find_installation(client)==root
    with pytest.raises(ValueError):find_installation(root/'Other.exe')


def test_diagnostics_never_copy_exception_secrets():
    status=AttachmentStatus();status.enter('behavior',pid=123);status.attached=True
    try:raise ValueError('password and webhook secret')
    except ValueError as error:status.fail(error)
    text=status.copy_text()
    assert 'password' not in text and 'webhook secret' not in text
    assert status.attached and not status.ready and status.error['frames']


def test_behavior_failure_keeps_host_attached(monkeypatch):
    from conquest.desktop_app import DesktopApp
    app=DesktopApp.__new__(DesktopApp)
    class Var:
        def set(self,value):self.value=value
    app.state_text=Var();app.attachment_text=Var();app.attachment=AttachmentStatus()
    app.character_context=None;app.observer=None;app.thread=None;app.runtime=None
    app.client=(7,8,{'creation_time_100ns':9});app.refresh_client=lambda:None
    app.root=SimpleNamespace(update_idletasks=lambda:None)
    app.pane=SimpleNamespace(winfo_id=lambda:10,winfo_width=lambda:1200,winfo_height=lambda:900)
    app.embedded_layout=lambda:None;app.compact=lambda:None;app.record=lambda **_:None
    closed=[];app.stop_observer=lambda:closed.append(True)
    host=SimpleNamespace(saved=None,focus=lambda:None)
    def attach(*args):host.saved=True
    host.attach=attach;host.detach=lambda:closed.append('detach');app.host=host
    app.observer_factory=lambda *args:SimpleNamespace()
    def fail():app.attachment.enter('behavior');raise ValueError('terrain missing')
    app.initialize_attached_behavior=fail
    app.embed()
    assert host.saved and app.observer and not closed
    assert app.attachment.attached and not app.attachment.ready
    assert 'automation setup failed' in app.attachment_text.value


def test_hosting_failure_restores_native_window():
    from conquest.desktop_app import DesktopApp
    # Test the underlying reversible host independently of behavior modules.
    from conquest.window_host import EmbeddedWindow
    events=[]
    api=SimpleNamespace(snapshot=lambda *a:SimpleNamespace(hwnd=1,identity={'pid':2}),
        require_matching_dpi=lambda *a:None,owns_window=lambda *a:True,
        restore=lambda *a:events.append('restore'))
    def fail(*args):raise OSError('attachment failed')
    api.embed_owned=fail
    host=EmbeddedWindow(api,mode='owned')
    with pytest.raises(OSError):host.attach(1,{'pid':2},3,1200,900)
    assert events==['restore'] and host.saved is None


def layout_app(size):
    from conquest.desktop_app import DesktopApp
    app=DesktopApp.__new__(DesktopApp)
    app.observer=object();app.closing=False;app.embed_layout_pending=True
    app.attachment=AttachmentStatus();app.attachment.enter('attachment')
    callbacks=[];finished=[];errors=[]
    app.root=SimpleNamespace(after=lambda delay,callback:callbacks.append(callback))
    app.pane=SimpleNamespace(winfo_width=lambda:size[0],winfo_height=lambda:size[1],winfo_ismapped=lambda:True)
    app.finish_embed=lambda:finished.append(True)
    app.embed_failed=errors.append
    return app,callbacks,finished,errors


def test_embed_waits_for_native_maximize_and_stable_pane(monkeypatch):
    monkeypatch.setattr('conquest.desktop_app.time.monotonic',lambda:10)
    size=[999,650];app,callbacks,finished,errors=layout_app(size)
    app.wait_for_embed_layout(app.observer,12)
    assert not finished and not errors and len(callbacks)==1
    size[:]=[1700,1100]
    callbacks.pop(0)()
    assert not finished and len(callbacks)==1
    callbacks.pop(0)()
    assert finished==[True] and not errors and not app.embed_layout_pending
    assert app.attachment.evidence['pane_size']==size


def test_small_monitor_stops_at_deadline_with_measured_error(monkeypatch):
    monkeypatch.setattr('conquest.desktop_app.time.monotonic',lambda:13)
    app,callbacks,finished,errors=layout_app([800,600])
    app.wait_for_embed_layout(app.observer,12)
    assert not callbacks and not finished
    assert isinstance(errors[0],ViewportTooSmall) and '800×600' in str(errors[0])


def test_released_observer_cannot_attach_from_queued_resize():
    app,callbacks,finished,errors=layout_app([1700,1100])
    old=app.observer;app.observer=None
    app.wait_for_embed_layout(old,12)
    assert not callbacks and not finished and not errors and not app.embed_layout_pending
