import json
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from conquest.character_profiles import ProfileRegistry
from conquest.merchants.manual_operator import (action_state, decision_seconds,
    exact_binding_text, status_text)
from conquest.merchants.manual_sessions import ManualSessionStore
from conquest.merchants.ui import UnifiedUI


def binding(request='request-one'):
    return {'version':1,'session_id':'session-one','request_id':request,
            'target_profile_id':'merchant-one',
            'visitor':{'target_profile_id':'merchant-one','visitor_name':'Guest',
                       'visitor_server':'America','visitor_uid':91},
            'game_process_identity':{'pid':10,'creation_time_100ns':20,'path':'game.exe'},
            'character_identity':{'character':'Seller','character_uid':11,'server':'America'},
            'request_fingerprint':'fingerprint','evidence_digest':'evidence',
            'ownership_digest':'ownership'}


def pending(request='request-one',deadline=130):
    value=binding(request)
    return {'id':'session-one','phase':'approval_pending','request_state':'pending',
            'visitor':value['visitor'],'approval_binding':value,'deadline':deadline,
            'fence_scope':'target','ever_approved':False}


class Var:
    def __init__(self):self.value=''
    def set(self,value):self.value=value


class Button:
    def __init__(self):self.state=None
    def configure(self,**values):self.state=values.get('state',self.state)


def test_countdown_reaches_zero_without_extending_the_runtime_deadline():
    row=pending(deadline=130)
    assert decision_seconds(row,now=100)==30
    assert decision_seconds(row,now=129.2)==1
    assert decision_seconds(row,now=130)==0
    assert 'expires now' in status_text('Dutch',row,now=131).lower()
    assert action_state(row,now=129)=={'approve':True,'reject':True,'override':False}
    assert action_state(row,now=130)=={'approve':False,'reject':False,'override':False}


def test_farmer_unsupported_map_blocker_and_fence_are_explicit():
    row={'id':'unbound:farmer','phase':'needs_attention','request_state':None,
         'visitor':None,'approval_binding':None,'deadline':None,'fence_scope':'target',
         'ever_approved':False,'reason':'Farmer manual memory unavailable: unsupported map'}
    farmer={'session':row,'input_fenced':True,'observation':{
        'available':False,'reason':'Farmer manual memory unavailable: unsupported map',
        'qualified_full_snapshot_maps':[1002,1011,1036]}}
    text=status_text('Farmer',row,farmer_status=farmer,intent={'enabled':False})
    assert 'Input fence: target' in text
    assert '1002, 1011, 1036' in text and 'unsupported map' in text
    assert 'Saved farming intent: Off' in text
    assert action_state(row)['override'] and not action_state(row)['approve']


def test_refresh_renders_detached_farmer_and_merchant_rows_and_buttons():
    farmer=pending('farmer-request',deadline=160)
    farmer['id']='farmer-session';farmer['approval_binding']['session_id']='farmer-session'
    farmer['approval_binding']['target_profile_id']='Farmer'
    farmer['visitor']['target_profile_id']='Farmer'
    merchant=pending('merchant-request',deadline=160)
    farmer_status={'session':farmer,'observation':{'available':True},'input_fenced':True}
    runtime=NS(manual_farmer_status=lambda:farmer_status,
        manual_status=lambda target:merchant if target=='Dutch' else None)
    ui=UnifiedUI.__new__(UnifiedUI);ui.runtime=runtime
    ui.manual_displayed={};ui.manual_texts={target:Var() for target in ('Farmer','Spiritual','Dutch')}
    ui.manual_buttons={target:{action:Button() for action in ('approve','reject','override')}
                       for target in ('Farmer','Spiritual','Dutch')}
    statuses={'Spiritual':{'enabled':False,'refill':{'enabled':True}},
              'Dutch':{'enabled':True,'refill':{'enabled':False}}}

    ui.refresh_manual_operator(statuses,{'enabled':False,'paused':True},now=130)

    assert 'Saved farming intent: Off (paused)' in ui.manual_texts['Farmer'].value
    assert 'Input fence: target' in ui.manual_texts['Farmer'].value
    assert 'trading/repricing On, refill Off' in ui.manual_texts['Dutch'].value
    assert ui.manual_buttons['Farmer']['approve'].state=='normal'
    assert ui.manual_buttons['Dutch']['reject'].state=='normal'
    assert ui.manual_buttons['Spiritual']['approve'].state=='disabled'
    farmer['approval_binding']['request_id']='runtime-replaced'
    merchant['approval_binding']['request_id']='runtime-replaced'
    assert ui.manual_displayed['Farmer']['approval_binding']['request_id']=='farmer-request'
    assert ui.manual_displayed['Dutch']['approval_binding']['request_id']=='merchant-request'


def test_native_approval_submits_the_exact_displayed_binding_during_request_race(monkeypatch):
    shown=pending('shown-request')
    current=pending('replacement-request')
    runtime=NS(manual_status=lambda target:current,approve_manual=Mock(return_value={'phase':'manual_active'}))
    ui=UnifiedUI.__new__(UnifiedUI);ui.root=object();ui.runtime=runtime
    ui.manual_displayed={'Dutch':shown}
    monkeypatch.setattr('conquest.merchants.ui.messagebox.askyesno',lambda *a,**k:True)
    monkeypatch.setattr('conquest.merchants.ui.messagebox.showerror',lambda *a,**k:pytest.fail('unexpected error'))

    result=ui.approve_manual_displayed('Dutch')

    assert result=={'phase':'manual_active'}
    submitted=runtime.approve_manual.call_args.args[0]
    assert submitted==shown['approval_binding']
    assert submitted['request_id']=='shown-request'
    assert submitted!=runtime.manual_status('Dutch')['approval_binding']
    assert json.loads(exact_binding_text(shown))==shown['approval_binding']


def test_approval_never_changes_saved_pause_or_enablement(monkeypatch):
    shown=pending();state={'enabled':False,'refill_enabled':True,'farmer_enabled':False}
    def approve(value,operator='local'):
        assert value==shown['approval_binding']
        return {'phase':'manual_active'}
    runtime=NS(approve_manual=Mock(side_effect=approve),enable=Mock(),set_refill_enabled=Mock())
    ui=UnifiedUI.__new__(UnifiedUI);ui.root=object();ui.runtime=runtime;ui.manual_displayed={'Dutch':shown}
    monkeypatch.setattr('conquest.merchants.ui.messagebox.askyesno',lambda *a,**k:True)

    ui.approve_manual_displayed('Dutch')

    assert state=={'enabled':False,'refill_enabled':True,'farmer_enabled':False}
    runtime.enable.assert_not_called();runtime.set_refill_enabled.assert_not_called()


def test_bridge_manual_actions_require_and_forward_exact_binding():
    shown=pending();runtime=NS(
        manual_status=Mock(return_value=shown),
        manual_farmer_status=Mock(return_value={'session':None,'observation':{},'input_fenced':False}),
        approve_manual=Mock(return_value={'phase':'manual_active'}),
        reject_manual=Mock(return_value={'request_state':'decline_pending'}))
    ui=UnifiedUI.__new__(UnifiedUI);ui.runtime=runtime

    assert ui.dispatch({'action':'manual-status','character':'Farmer'})['farmer']['input_fenced'] is False
    ui.dispatch({'action':'manual-approve','binding':shown['approval_binding'],'operator':'Floor'})
    runtime.approve_manual.assert_called_once_with(shown['approval_binding'],operator='Floor')
    with pytest.raises(ValueError,match='Exact displayed'):
        ui.dispatch({'action':'manual-reject','binding':'session-one'})


def test_exact_profile_visitor_revocation_is_separate_from_delivery_trust(tmp_path):
    registry=ProfileRegistry(tmp_path);profile=registry.add('Seller',role='Merchant',
        trusted_sources=[{'name':'Courier','server':'America','character_uid':7}])
    path=tmp_path/'machine-state/reports/merchants/journal.sqlite3';store=ManualSessionStore(path)
    exact={'target_profile_id':profile.id,'visitor_name':'Guest',
           'visitor_server':'America','visitor_uid':91}
    other={**exact,'visitor_uid':92}
    with store.db() as db:
        db.execute('INSERT INTO visitor_permissions VALUES(?,?,?,?,1,?)',(*exact.values(),time.time()))
        db.execute('INSERT INTO visitor_permissions VALUES(?,?,?,?,1,?)',(*other.values(),time.time()))
    displayed=[{name:row[name] for name in exact} for row in registry.list_visitors(profile.id)]

    remaining=registry.revoke_visitors(profile.id,[displayed[0]],operator='profile UI')

    assert len(remaining)==1 and remaining[0]['visitor_uid']==92
    assert registry.resolve(profile.id).trusted_sources==profile.trusted_sources
    with pytest.raises(ValueError,match='exact allowed'):
        registry.revoke_visitors(profile.id,[displayed[0]],operator='profile UI')
    with pytest.raises(ValueError,match='visitor'):
        registry.update(profile.id,{'role':'Farmer'})


def test_meteor_recovery_actions_do_not_share_manual_override_route(monkeypatch):
    """Manual UI routing must not become a generic shortcut for Meteor recovery."""
    runtime=NS(override_manual=Mock(return_value={'phase':'operator_overridden'}))
    ui=UnifiedUI.__new__(UnifiedUI);ui.runtime=runtime
    ui._merchant_recovery_incidents=lambda character:[]
    meteor=Mock(side_effect=AssertionError('Meteor route must remain dedicated'))
    monkeypatch.setattr('conquest.meteor_banking.operator_override',meteor)

    ui.dispatch({'action':'manual-override','session_id':'manual-session',
                 'confirmation_reference':'manual-check','operator':'Floor','reason':'Reviewed'})

    runtime.override_manual.assert_called_once_with('manual-session',
        confirmation_reference='manual-check',operator='Floor',reason='Reviewed')
    meteor.assert_not_called()
