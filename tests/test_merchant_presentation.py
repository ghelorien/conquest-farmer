import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from conquest.merchants.presentation import MerchantPresentation
from conquest.merchants.journal import Journal
from conquest.merchants.ui import UnifiedUI


def test_slow_observer_does_not_block_ui_snapshot_or_replace_last_complete_view(tmp_path):
    entered=threading.Event();release=threading.Event()
    def slow():
        entered.set();release.wait(2);return {}
    runtime=SimpleNamespace(status=slow,journal=Journal(tmp_path/'journal.sqlite3'),
                            sales_worker=SimpleNamespace(status=lambda:{'status':'waiting'}))
    presenter=MerchantPresentation(runtime)
    old={'sales':{'at':time.time()},'collected_at':time.monotonic()}
    presenter.latest=old
    presenter.start()
    try:
        assert entered.wait(1)
        started=time.monotonic()
        assert presenter.latest is old and not presenter.ready.is_set()
        assert time.monotonic()-started<.05
        release.set();assert presenter.ready.wait(1)
        assert presenter.latest is not old and presenter.latest['characters']=={}
    finally:
        release.set();presenter.close()


@pytest.mark.parametrize('released,owner,attached,expected',[
    (False,None,False,True),(True,None,False,False),(False,'Farmer',False,False),
    (False,None,True,False)])
def test_selected_client_shows_without_reading_shop_state_or_verifying(released,owner,attached,expected):
    ui=SimpleNamespace(closed=False,auto_embedding=False,coordinator=SimpleNamespace(owner=owner),calibrating=set(),
        notebook=SimpleNamespace(select=lambda:'Dutch'),frames={'Dutch':'Dutch','Spiritual':'Spiritual'},
        released_clients={'Dutch'} if released else set(),
        client_panes={'Dutch':SimpleNamespace(winfo_ismapped=lambda:True)},auto_embed_retry={},
        hosts={'Dutch':SimpleNamespace(saved=object())} if attached else {},
        runtime=SimpleNamespace(observers={'Dutch':object()}),embed_client=Mock())
    UnifiedUI.auto_show_selected(ui)
    assert ui.embed_client.call_count==int(expected)


def test_driver_preserves_saved_evidence_across_ordinary_live_resize(tmp_path):
    import json
    from conquest.merchants.driver import MerchantDriver
    path=tmp_path/'qualification.json'
    path.write_text(json.dumps(dict(client_sha256='test',character='Dutch',server='America',
        capabilities={'booth_input':True},evidence='verified',client_size=[1036,793],gui_size=[1036,793])))
    driver=SimpleNamespace(qualification=path,observer=SimpleNamespace(
        adapter=SimpleNamespace(expected_sha256='test'),character='Dutch'),
        target=SimpleNamespace(snapshot=lambda:{'client_size':[1400,900]}),
        memory=SimpleNamespace(gui=SimpleNamespace(viewport_size=lambda:[1400,900])))
    evidence=MerchantDriver.require_qualified(driver,'booth_input')
    assert evidence['client_size']==[1036,793]
    assert driver.target.snapshot()['client_size']==[1400,900]
    assert driver.memory.gui.viewport_size()==[1400,900]


@pytest.mark.parametrize('mode',['delayed','covered','behind_wrapper','wrong_size','stop'])
def test_input_waits_for_exclusive_native_surface(mode):
    from conquest.merchants.ui import wait_for_merchant_surface
    from conquest.capture import CaptureUnavailable
    now=[0.0]
    gui=SimpleNamespace(GetClientRect=lambda p:(0,0,1200,800),ClientToScreen=lambda p,xy:(2500,200),
        GetAncestor=lambda h,flag:10,
        IsWindowVisible=lambda h:h==10 or now[0]>=.05,IsIconic=lambda h:False,
        GetWindowRect=lambda h:(2500,200,3700 if mode!='wrong_size' else 3600,1000))
    host=SimpleNamespace(saved=SimpleNamespace(hwnd=20,identity={}),parent=10,
                         api=SimpleNamespace(gui=gui,assert_owner=lambda *a:None,is_above=lambda *a:mode!='behind_wrapper'))
    sibling=SimpleNamespace(saved=SimpleNamespace(hwnd=30),api=SimpleNamespace(gui=SimpleNamespace(
        IsWindowVisible=lambda h:mode=='covered' or now[0]<.1)))
    def check():
        if mode=='stop' and now[0]>=.025:raise CaptureUnavailable('Manual Stop')
    def sleep(seconds):now[0]+=seconds
    if mode=='delayed':
        wait_for_merchant_surface(host,[sibling],check,clock=lambda:now[0],sleep=sleep)
        assert now[0]>=.1
    else:
        with pytest.raises((ValueError,CaptureUnavailable)):
            wait_for_merchant_surface(host,[sibling],check,clock=lambda:now[0],sleep=sleep)


def test_handoff_hides_previous_native_client_before_resizing_selected():
    calls=[]
    hosts={c:SimpleNamespace(saved=SimpleNamespace(hwnd=h,identity={'pid':h}),api=SimpleNamespace(
        gui=SimpleNamespace(GetForegroundWindow=lambda:0),assert_owner=lambda *a:None,
        show_async=lambda hwnd,command:calls.append(('hide',hwnd,command))))
        for c,h in [('Spiritual',20),('Dutch',30)]}
    ui=SimpleNamespace(closed=False,app=SimpleNamespace(closing=False),safe_to_yield=lambda:True,
        hosts=hosts,runtime=SimpleNamespace(observers={c:SimpleNamespace(adapter=SimpleNamespace(identity=h.saved.identity)) for c,h in hosts.items()}),
        notebook=SimpleNamespace(select=lambda *a:calls.append(('select',*a))),frames={'Spiritual':'S','Dutch':'D'},
        detail_tabs={c:SimpleNamespace(select=lambda *a:None) for c in hosts},input_bookmarks={},
        root=SimpleNamespace(update_idletasks=lambda:None),resize_merchant=lambda c,**kw:calls.append(('resize',c)))
    UnifiedUI.show_merchant(ui,'Spiritual')
    assert calls[-2:]==[('hide',30,0),('resize','Spiritual')]
    UnifiedUI.show_merchant(ui,'Dutch')
    assert calls[-2:]==[('hide',20,0),('resize','Dutch')]
