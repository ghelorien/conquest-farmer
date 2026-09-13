import copy
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from conquest.capture import CaptureUnavailable
from conquest.merchants.journal import Journal
from conquest.merchants.unrelated_request import decline_unrelated_request


def item(uid,price=None):
    return {'uid':uid,'type_id':130003,'name':'Coat','plus':1,'gem1':0,'gem2':0,
            'quantity':1,'bound':False,'slot':0,'price':price,'category':None}


def state(request=True):
    return {'character':'Dutch','identity':{'pid':7,'creation':8},'silver':100,
            'map_id':1036,'hp':100,
            'inventory':[item(11)],'booth':[item(22,900)],'trade':None,
            'request':({'participant':'Stranger','participant_uid':44,
                        'message':'Stranger wishes to trade with you.'} if request else None),
            'windows':[]}


class Layout:
    def __init__(self):
        self.revision=NS(gui_size=(1000,800),client_size=(1500,1200))
    def stable(self):return self.revision
    def assert_current(self,revision):
        assert revision is self.revision
        return revision


def rig(tmp_path,before,after):
    layout=Layout();reads=[]
    gui=NS(viewport_size=lambda:(1000,800),assert_hovered=lambda window,label:None)
    target=NS(hwnd=17,snapshot=lambda:{'client_size':[1500,1200]})
    driver=NS(observer=object(),memory=NS(gui=gui),target=target,
        require_qualified=lambda capability:None,layout_revision=lambda:layout)
    driver.read=lambda:(reads.append(1) or copy.deepcopy(before))
    driver.wait_for=lambda predicate,check,seconds:copy.deepcopy(after)
    journal=Journal(tmp_path/'merchant.sqlite3')
    coordinator=NS(lease=lambda *args,**kwargs:nullcontext())
    controller=NS(character='Dutch',journal=journal,driver=driver,coordinator=coordinator,
                  active=lambda:True,check=lambda:None)
    return controller,reads


def patches(monkeypatch,click):
    monkeypatch.setattr('conquest.merchants.unrelated_request.requester_identity',
                        lambda observer,name:{'uid':44,'name':name,'position':[1,2]})
    monkeypatch.setattr('conquest.merchants.unrelated_request._cancel_control',
                        lambda driver,snapshot,name:({'name':'Trade###Confirm'},(500,400)))
    monkeypatch.setattr('conquest.desktop_runtime.physical_coordinates',lambda:nullcontext())
    monkeypatch.setattr('conquest.focus_recovery.activate_client',lambda hwnd,identity:True)
    monkeypatch.setattr('conquest.merchants.driver.wait_hover_validation',
                        lambda before,check:before())
    monkeypatch.setattr('conquest.foreground.foreground_click',click)


def test_decline_scales_logical_cancel_and_verifies_exact_state(tmp_path,monkeypatch):
    before,after=state(),state(False);sent=[]
    controller,_=rig(tmp_path,before,after)
    def click(target,x,y,size,**kwargs):
        sent.append((x,y,size));kwargs['before_press']()
    patches(monkeypatch,click)
    assert decline_unrelated_request(controller,before,operations_enabled=True)
    assert sent==[(750,600,(1500,1200))]
    saved=Journal(controller.journal.path).get('Dutch','unrelated_request_decline')
    assert saved['phase']=='verified' and saved['inventory'][0][0]==11
    assert saved['booth'][0][-1]==900


def test_lost_ack_modal_absent_restart_reconciles_without_second_click(tmp_path,monkeypatch):
    before,after=state(),state(False);attempts=[]
    controller,_=rig(tmp_path,before,after)
    def lost(target,x,y,size,**kwargs):
        attempts.append((x,y));kwargs['before_press']()
        raise OSError('decline acknowledgement lost')
    patches(monkeypatch,lost)
    with pytest.raises(OSError,match='acknowledgement lost'):
        decline_unrelated_request(controller,before,operations_enabled=True)
    assert Journal(controller.journal.path).get('Dutch','unrelated_request_decline')['phase']=='submitted'
    restarted,_=rig(tmp_path,after,after)
    restarted.journal=Journal(controller.journal.path)
    assert decline_unrelated_request(restarted,after,operations_enabled=True)
    assert attempts==[(750,600)]
    assert restarted.journal.get('Dutch','unrelated_request_decline')['phase']=='verified'


def test_pending_decline_rejects_changed_identity_or_booth(tmp_path,monkeypatch):
    before,after=state(),state(False)
    controller,_=rig(tmp_path,before,after)
    def lost(target,x,y,size,**kwargs):
        kwargs['before_press']();raise OSError('lost')
    patches(monkeypatch,lost)
    with pytest.raises(OSError):
        decline_unrelated_request(controller,before,operations_enabled=True)
    changed=copy.deepcopy(after);changed['booth'][0]['price']+=1
    restarted,_=rig(tmp_path,changed,changed);restarted.journal=Journal(controller.journal.path)
    with pytest.raises(CaptureUnavailable,match='stock, booth, or silver'):
        decline_unrelated_request(restarted,changed,operations_enabled=True)


@pytest.mark.parametrize('change',({'map_id':1002},{'hp':0}))
def test_decline_requires_living_market_snapshot(tmp_path,monkeypatch,change):
    before=state();before.update(change);controller,_=rig(tmp_path,before,before)
    patches(monkeypatch,lambda *a,**k:pytest.fail('No decline input is allowed'))
    assert decline_unrelated_request(controller,before,operations_enabled=True) is False


def test_pending_asset_transaction_blocks_decline_before_input(tmp_path,monkeypatch):
    before=state();controller,_=rig(tmp_path,before,before)
    controller.journal.begin('unfinished','Dutch','sale',{'item':item(11)})
    patches(monkeypatch,lambda *a,**k:pytest.fail('No decline input is allowed'))
    with pytest.raises(CaptureUnavailable,match='transaction reconciliation'):
        decline_unrelated_request(controller,before,operations_enabled=True)


def test_reservation_created_during_hover_blocks_decline_press(tmp_path,monkeypatch):
    from conquest.merchants import unrelated_request as module
    before=state();controller,_=rig(tmp_path,before,state(False));checks=[0]
    patches(monkeypatch,lambda target,x,y,size,**kwargs:kwargs['before_press']())
    original=module._require_no_owned_work
    def changed(current):
        checks[0]+=1
        if checks[0]==3:
            raise CaptureUnavailable('Incoming request waits for transaction reconciliation')
        return original(current)
    monkeypatch.setattr(module,'_require_no_owned_work',changed)
    with pytest.raises(CaptureUnavailable,match='transaction reconciliation'):
        decline_unrelated_request(controller,before,operations_enabled=True)
    assert controller.journal.get('Dutch','unrelated_request_decline') is None
