from types import SimpleNamespace
import numpy as np
import pytest

from conquest.desktop_runtime import LocalSession
from conquest.vision import health_ratio
from conquest.window_host import EmbeddedWindow, WindowState, HostApi
from conquest.desktop_launch import start_arguments
from conquest.desktop_app import DesktopApp


@pytest.mark.parametrize('enabled',[True,False])
def test_startup_error_preserves_real_cause_and_manual_stop(tmp_path,enabled):
    from conquest.control import FarmingControl
    app=DesktopApp.__new__(DesktopApp)
    app.control=FarmingControl(tmp_path/'control.json')
    app.control.update({'enabled':enabled})
    app.runtime=SimpleNamespace(external_failure=None)
    records=[];app.record=lambda **fields:records.append(fields)
    def fail():raise ValueError('Learned Scatter range is invalid')
    app._start_embedded_farm=fail
    assert app.start_embedded_farm() is False
    state=app.control.snapshot()
    assert state['enabled'] is enabled
    if enabled:
        assert state['execution_state']=='runner_stopped'
        assert 'Learned Scatter range is invalid' in state['note']
        assert app.runtime.external_failure[0]==state['revision']
        assert records[-1]['state']=='Farming could not start'
    else:
        assert app.runtime.external_failure is None and not records


def test_elevated_start_preserves_profile_and_requested_action(tmp_path):
    root = tmp_path/'Repo with spaces'
    profile = root/'profiles/session.local.yaml'
    args = start_arguments(root,profile,False)
    assert args == [str(root/'scripts/start_desktop_app.py'),'--profile',str(profile),'--start']
    assert start_arguments(root,profile,True)[-1]=='--calibrate'
    assert start_arguments(root,profile,False,launch_client=True)[-1]=='--launch-client'


def test_embed_restart_preserves_exact_process_creation_and_hwnd(tmp_path):
    args = start_arguments(tmp_path,tmp_path/'p.yaml',False,embed_client=(42,987654321,12345))
    assert args[3:] == ['--embed-client','--client-pid','42','--client-started','987654321','--client-hwnd','12345']
    with pytest.raises(ValueError):
        start_arguments(tmp_path,tmp_path/'p.yaml',False,embed_client=(42,0,12345))
    with pytest.raises(ValueError):
        start_arguments(tmp_path,tmp_path/'p.yaml',False,launch_client=True,embed_client=(42,1,12345))


def test_scaled_input_keeps_physical_origin_and_guard():
    calls = []
    session = LocalSession.__new__(LocalSession)
    session.logical_size, session.physical_size = (1536,793), (1920,991)
    session.operations = SimpleNamespace(dispatch=lambda op, body: calls.append((op, body)))
    body = {'point':[826,751], 'expected_size':[1536,793],
            'expected_origin':[0,29], 'guard':{'name':'Parasite'}, 'require_foreground':True}
    session.request('foreground-click',body)
    sent = calls[0][1]
    assert sent['point'] == [1032,939]
    assert sent['expected_origin'] == [0,29]
    assert sent['expected_size'] == [1920,991]
    assert sent['require_foreground'] and sent['guard'] == body['guard']
    assert body['point'] == [826,751]
    with pytest.raises(ValueError, match='calibration'):
        session.request('foreground-click', {**body,'expected_size':[1584,861]})
    assert len(calls) == 1


@pytest.mark.parametrize('size', [(1584,861),(1536,793)])
def test_health_bar_is_relative_to_bottom_and_center(size):
    width,height = size
    frame = np.zeros((height,width,3),dtype=np.uint8)
    strip = frame[height-89:height-86,width//2-457:width//2-4]
    strip[:] = (70,30,20)
    strip[:,:200] = (30,20,180)
    assert health_ratio(frame,size) == pytest.approx(200/453)
    strip[:,210:220] = (30,20,180)
    with pytest.raises(ValueError,match='contiguous'):
        health_ratio(frame,size)


class Host:
    def __init__(self):
        self.calls = []
        self.fail_embed = self.fail_restore = self.dpi_mismatch = False
    def snapshot(self, hwnd, identity):
        return WindowState(identity,hwnd,0x80000000,0,0,())
    def require_matching_dpi(self,*args):
        if self.dpi_mismatch:
            raise ValueError('DPI mismatch')
    def assert_owner(self,*args): pass
    def owns_window(self,*args): return True
    def embed(self,*args):
        self.calls.append('embed')
        if self.fail_embed: raise ValueError('embed failed')
    def resize(self,*args): self.calls.append('resize')
    def restore(self,*args):
        self.calls.append('restore')
        if self.fail_restore: raise ValueError('restore failed')


def test_embedding_rolls_back_partial_failure():
    api = Host()
    api.fail_embed = True
    host = EmbeddedWindow(api)
    with pytest.raises(ValueError,match='embed failed'):
        host.attach(1,{'pid':2},3,800,600)
    assert api.calls == ['embed','restore']
    assert host.saved is None


def test_failed_restore_retains_ownership_for_retry():
    api = Host()
    api.fail_embed = api.fail_restore = True
    host = EmbeddedWindow(api)
    with pytest.raises(RuntimeError,match='could not be restored'):
        host.attach(1,{'pid':2},3,800,600)
    assert host.saved.hwnd == 1
    api.fail_restore = False
    host.detach()
    assert host.saved is None


def test_dpi_mismatch_never_changes_window():
    api = Host()
    api.dpi_mismatch = True
    host = EmbeddedWindow(api)
    with pytest.raises(ValueError,match='DPI mismatch'):
        host.attach(1,{'pid':2},3,800,600)
    assert not api.calls and host.saved is None


def test_closed_or_reused_window_is_forgotten_without_restoration():
    api = Host()
    host = EmbeddedWindow(api)
    host.attach(1,{'pid':2},3,800,600)
    api.owns_window = lambda *args:False
    assert not host.is_alive()
    host.detach()
    assert host.saved is None and 'restore' not in api.calls


def test_access_error_keeps_saved_window_for_later_restoration():
    api = Host()
    host = EmbeddedWindow(api)
    host.attach(1,{'pid':2},3,800,600)
    def denied(*args): raise OSError('Access denied')
    api.owns_window = denied
    with pytest.raises(OSError,match='Access denied'):
        host.detach()
    assert host.saved.hwnd == 1


def focus_api(focused=40, foreground=10):
    api = HostApi.__new__(HostApi)
    calls = []
    info = SimpleNamespace(hwndFocus=focused)
    api.assert_owner = lambda *args: calls.append('identity')
    api.thread_info = lambda hwnd: (200,info)
    api.current_thread = lambda: 100
    api.attach_input = lambda first,second,attach: calls.append(('attach',attach)) or True
    def set_focus(hwnd):
        calls.append(('focus',hwnd))
        info.hwndFocus = hwnd
    api.gui = SimpleNamespace(GetAncestor=lambda *args:10,GetForegroundWindow=lambda:foreground,
        IsChild=lambda parent,child:parent==20 and child==21,SetFocus=set_focus)
    return api,calls,info,WindowState({'pid':1},20,0,0,0,())


def test_keyboard_focus_transfers_and_detaches_thread_input():
    api,calls,info,state = focus_api()
    assert api.focus(state)==20
    assert calls==['identity',('attach',True),('focus',20),('attach',False)]


def test_keyboard_focus_preserves_the_clients_edit_control():
    api,calls,info,state = focus_api(focused=21)
    assert api.focus(state)==21
    assert calls==['identity']


def test_keyboard_focus_never_activates_a_background_wrapper():
    api,calls,info,state = focus_api(foreground=99)
    with pytest.raises(ValueError,match='Activate the wrapper'):
        api.focus(state)
    assert calls==['identity']


def test_keyboard_focus_failure_releases_thread_attachment():
    api,calls,info,state = focus_api()
    def fail(hwnd): raise OSError('Focus rejected')
    api.gui.SetFocus = fail
    with pytest.raises(OSError,match='Focus rejected'):
        api.focus(state)
    assert calls==['identity',('attach',True),('attach',False)]


def test_keyboard_focus_rechecks_foreground_after_attaching():
    api,calls,info,state = focus_api()
    foreground = iter([10,99])
    api.gui.GetForegroundWindow = lambda:next(foreground)
    with pytest.raises(ValueError,match='lost focus'):
        api.focus(state)
    assert calls==['identity',('attach',True),('attach',False)]


@pytest.mark.parametrize('release_ok',[True,False])
def test_reload_releases_client_before_starting_pinned_replacement(monkeypatch,tmp_path,release_ok):
    calls = []
    def release():
        calls.append('release')
        return release_ok
    app = SimpleNamespace(thread=None,profile=tmp_path/'profile.yaml',release=release,
        host=SimpleNamespace(saved=WindowState({'pid':42,'creation_time_100ns':123},99,0,0,0,())),
        root=SimpleNamespace(destroy=lambda:calls.append('destroy')),
        state_text=SimpleNamespace(set=lambda text:calls.append(text)))
    monkeypatch.setattr('conquest.desktop_app.subprocess.Popen',lambda args,**kwargs:calls.append(args))
    monkeypatch.setattr('conquest.desktop_app.subprocess.run',lambda *args,**kwargs:SimpleNamespace(returncode=0))
    app.last={'worker_info_path':'test'};app.reload_proof={};app.reload_resume=True
    monkeypatch.setattr('conquest.safe_reload.validate_handoff',lambda *args:calls.append('safe'))
    monkeypatch.setattr('conquest.safe_reload.save_resume',lambda *args:None)
    DesktopApp._restart_now(app)
    if release_ok:
        assert calls[:2]==['safe','release'] and calls[3]=='destroy'
        assert calls[2][-7:]==['--embed-client','--client-pid','42','--client-started','123','--client-hwnd','99']
    else:
        assert calls==['safe','release']


def test_reload_invalid_new_source_preserves_running_app_and_client(monkeypatch):
    calls=[]
    app=SimpleNamespace(thread=None, release=lambda:calls.append('release'),
        root=SimpleNamespace(destroy=lambda:calls.append('destroy')),
        state_text=SimpleNamespace(set=lambda text:calls.append(text)))
    monkeypatch.setattr('conquest.desktop_app.subprocess.run',lambda *args,**kwargs:SimpleNamespace(returncode=1))
    monkeypatch.setattr('conquest.desktop_app.subprocess.Popen',lambda *args,**kwargs:calls.append('spawn'))
    assert DesktopApp._restart_now(app) is False
    assert len(calls)==1 and calls[0].startswith('Reload canceled: startup check failed')
