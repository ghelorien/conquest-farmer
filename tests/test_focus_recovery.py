from types import SimpleNamespace
import pytest
from conquest.focus_recovery import AutoRefocuser,activate_client


def test_refocus_retries_with_backoff_and_off_cancels():
    now=[0.];calls=[]
    r=AutoRefocuser(clock=lambda:now[0])
    def activate(): calls.append(now[0]);return False
    assert r.step(True,False,activate) is None
    now[0]=.5;assert r.step(True,False,activate) is False
    now[0]=.9;r.step(True,False,activate)
    now[0]=1.5;r.step(True,False,activate)
    assert calls==[.5,1.5]
    now[0]=2.5;r.step(False,False,activate)
    now[0]=3;r.step(True,False,activate)
    assert calls==[.5,1.5]
    now[0]=3.5;assert r.step(True,False,lambda:True) is True
    now[0]=4;r.step(True,True,activate)
    assert calls==[.5,1.5]


@pytest.mark.parametrize('fail',[False,True])
def test_foreground_input_queues_always_detach(monkeypatch,fail):
    import win32api,win32process
    calls=[];foreground=[20]
    monkeypatch.setattr(win32api,'GetCurrentThreadId',lambda:1)
    monkeypatch.setattr(win32process,'GetWindowThreadProcessId',lambda hwnd:(2,3))
    monkeypatch.setattr(win32process,'AttachThreadInput',lambda a,b,on:calls.append(on))
    def activate(hwnd):
        import pywintypes
        if not calls: raise pywintypes.error(0,'SetForegroundWindow','Retry with queue')
        if fail: raise OSError('Focus denied')
        foreground[0]=hwnd
    api=SimpleNamespace(assert_owner=lambda hwnd,identity:None,gui=SimpleNamespace(
        GetAncestor=lambda hwnd,flag:10,IsIconic=lambda hwnd:False,
        GetForegroundWindow=lambda:foreground[0],SetForegroundWindow=activate))
    if fail:
        with pytest.raises(OSError):activate_client(10,{},api=api)
    else:
        assert activate_client(10,{},api=api)
    assert calls==[True,False]


def test_changed_client_is_rejected_before_focus_input():
    def reject(*args):raise ValueError('Client identity changed')
    with pytest.raises(ValueError,match='identity'):
        activate_client(10,{},api=SimpleNamespace(assert_owner=reject,gui=None))


def test_windows_focus_denial_does_not_stop_refocus_loop():
    import pywintypes
    now=[0.];r=AutoRefocuser(clock=lambda:now[0])
    def denied():raise pywintypes.error(0,'SetForegroundWindow','Denied')
    r.step(True,False,denied)
    now[0]=1
    assert r.step(True,False,denied) is False
    now[0]=2
    assert r.step(True,False,lambda:True) is True
