from types import SimpleNamespace
import pytest
from conquest.client_wrapper import ClientWindow, LaunchWatch, ClientCatalog, pinned_client


def client(pid=5,creation=10,title='Login',hwnd=99):
    return ClientWindow({'pid':pid,'creation_time_100ns':creation,'path':'ImConquer.exe'},hwnd,title)


def watcher():
    current = [client()]
    catalog = SimpleNamespace(identities=lambda:[c.identity for c in current],windows=lambda:list(current))
    process = SimpleNamespace(returncode=None)
    process.poll = lambda:process.returncode
    now = [10]
    calls = []
    watch = LaunchWatch(catalog,['launcher.exe'],cwd='.',clock=lambda:now[0],
                        spawn=lambda *args,**kwargs:(calls.append((args,kwargs)) or process))
    watch.start()
    return watch,current,process,now,calls


def test_launch_waits_past_successful_bootstrap_exit_and_finds_login_window():
    watch,current,process,now,calls = watcher()
    process.returncode = 0
    assert watch.poll() is None and watch.pending
    current.append(client(6,11))
    assert watch.poll()==current[-1]
    assert watch.state=='ready' and len(calls)==1


def test_pid_reuse_is_a_new_process_and_ambiguous_windows_are_not_guessed():
    watch,current,*_ = watcher()
    current[:] = [client(5,20),client(6,30,hwnd=100)]
    assert watch.poll() is None and watch.state=='choose'
    watch,current,*_ = watcher()
    current[:] = [client(5,20)]
    assert watch.poll()==current[0]


def test_cancel_never_terminates_launcher_and_no_duplicate_start():
    watch,current,process,now,calls = watcher()
    with pytest.raises(ValueError,match='pending'):
        watch.start()
    watch.cancel()
    assert watch.poll() is None and process.returncode is None
    assert len(calls)==1


@pytest.mark.parametrize('exit_code,elapsed,state',[(7,0,'failed'),(0,181,'timed_out')])
def test_failed_and_timed_out_launches_are_terminal(exit_code,elapsed,state):
    watch,current,process,now,_ = watcher()
    process.returncode = exit_code
    now[0]+=elapsed
    assert watch.poll() is None and watch.state==state


def test_catalog_accepts_login_title_but_excludes_small_or_hidden_windows():
    identity = client().identity
    backend = SimpleNamespace(processes=lambda exe:[{'pid':5}],identity=lambda pid:identity,
        windows=lambda pid:[dict(hwnd=i,title='Login',visible=visible,client_size=size)
            for i,visible,size in [(1,True,[1024,768]),(2,False,[1024,768]),(3,True,[80,50])]])
    windows = ClientCatalog(backend).windows()
    assert [w.hwnd for w in windows]==[1]
    assert windows[0].title=='Login'


def test_restart_never_switches_to_another_client_or_reused_pid():
    selected = client()
    catalog = SimpleNamespace(windows=lambda:[selected,client(6,20,hwnd=100)])
    assert pinned_client(catalog,selected.key) is selected
    catalog.windows = lambda:[client(5,30),client(6,20,hwnd=100)]
    with pytest.raises(ValueError,match='closed or changed'):
        pinned_client(catalog,selected.key)
