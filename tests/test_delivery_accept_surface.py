import struct
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from conquest.merchants.delivery_accept_probe import control
from conquest.merchants.ui import UnifiedUI, callback_failure


def test_accept_control_rejects_open_booth_confirmation(monkeypatch):
    """A visible selling list/Open Booth dialog can never become an accept click."""
    base,model,window=0x140000000,0x100000,0x200000
    handlers={base+0x95fd6:bytes.fromhex('e835dcfaff'),
              base+0x95fdf:bytes.fromhex('b201488bcbe857070000')}
    raw=bytearray(0x250)
    struct.pack_into('<2f',raw,0xe8,1036,373)
    struct.pack_into('<f',raw,0x114,18)
    def read(address,size):
        if address==model+12:return b'\x01'
        if address==window:return bytes(raw[:size])
        return handlers[address][:size]
    labels=['Trade###Confirm','Parasite wishes to trade with you.','Accept','Cancel']
    monkeypatch.setattr('conquest.merchants.delivery_accept_probe.string',
        lambda _session,address:labels[(address-model-0x48)//0x20])
    driver=NS(observer=NS(adapter=NS(read_block=read)),
              memory=NS(gui=NS(base=base,model=lambda *_args:model)))
    snapshot={'windows':[{'name':'Open Booth###Confirm','address':window,
                          'geometry':[844,282,200,100]}]}
    with pytest.raises(ValueError,match='Trade confirmation window'):
        control(driver,snapshot)


def request_verified(tmp_path,monkeypatch,identity,character='Spiritual',*,target_profile_id=None,server='America'):
    from conquest.merchants import delivery_probe
    path=tmp_path/'probe.json'
    monkeypatch.setattr(delivery_probe,'JOURNAL',path)
    delivery_probe.write_probe(path,{'phase':'request_verified','character':character,
        'target_profile_id':target_profile_id or character,
        'intent':{'merchant':{'character':character,'server':server,'identity':identity}}})


def bind_accept_binding(ui):
    ui.delivery_accept_binding=lambda character,expected=None:UnifiedUI.delivery_accept_binding(ui,character,expected)


def bound_show_ui(identity, *, layout=None, pane_size=(1200,800)):
    api=NS(assert_owner=Mock(),show_async=Mock(),gui=NS(GetForegroundWindow=lambda:0),backend=NS())
    host=NS(saved=NS(hwnd=77,identity=identity),api=api,mode='owned',resize=Mock(),detach=Mock())
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),operations=NS(target=NS(hwnd=77)))
    selected=['overview']
    def select(value=None):
        if value is not None:selected[0]=value
        return selected[0]
    ui=NS(closed=False,app=NS(closing=False),runtime=NS(observers={'Spiritual':observer}),
          hosts={'Spiritual':host},notebook=NS(select=select),frames={'Spiritual':'spiritual-frame'},
          detail_tabs={'Spiritual':NS(select=Mock())},root=NS(update_idletasks=Mock()),
          input_bookmarks={},client_panes={'Spiritual':NS(winfo_width=lambda:pane_size[0],
              winfo_height=lambda:pane_size[1])},apply_client_compact_layout=layout)
    bind_accept_binding(ui)
    return ui,host,api,observer


def managed_registry_stub():
    return NS(resolve=lambda profile_id,**_kwargs:NS(id='Spiritual',name='Spiritual',
        server='America',local_enabled=True))


def managed_show_ui(tmp_path,monkeypatch,identity):
    from conquest.character_profiles import ProfileRegistry
    from conquest.character_context import ProfileMap,ProfileName
    registry=ProfileRegistry(tmp_path/'profiles')
    profile=registry.add('Spiritual',role='Merchant')
    monkeypatch.setenv('CONQUEST_DATA_ROOT',str(registry.root))
    request_verified(tmp_path,monkeypatch,identity,target_profile_id=profile.id)
    ui,host,api,observer=bound_show_ui(identity)
    key=ProfileName(profile.name,profile.id)
    for name,value in [('observers',observer),('hosts',host),('frames','spiritual-frame'),
                       ('detail_tabs',ui.detail_tabs['Spiritual']),('client_panes',ui.client_panes['Spiritual'])]:
        mapping=ProfileMap();mapping[key]=value
        if name=='observers':ui.runtime.observers=mapping
        else:setattr(ui,name,mapping)
    ui.notebook=NS(select=Mock(return_value='overview'))
    return registry,profile,key,ui,host,api


def test_accept_surface_embeds_only_the_exact_observer(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    api=NS(assert_owner=Mock())
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),
                operations=NS(target=NS(hwnd=77)))
    # An already-owned matching host is re-shown, not detached or replaced.
    ui=NS(runtime=NS(observers={'Spiritual':observer}),
             hosts={'Spiritual':NS(saved=NS(hwnd=77,identity=identity),api=api,mode='owned')},
             embed_merchant=Mock(),show_merchant=Mock(),
             embed_delivery_accept_merchant=Mock(),show_delivery_accept_merchant=Mock())
    bind_accept_binding(ui)
    UnifiedUI.prepare_delivery_accept_surface(ui,'Spiritual')
    assert observer.adapter.assert_identity.call_count>=2
    assert all(call.args==(77,identity) for call in api.assert_owner.call_args_list)
    ui.embed_merchant.assert_not_called()
    ui.show_delivery_accept_merchant.assert_called_once_with('Spiritual',(77,identity,'Spiritual'))
    ui.show_merchant.assert_not_called()


def test_accept_surface_rejects_changed_saved_host_without_showing_or_reembedding(tmp_path,monkeypatch):
    saved=NS(hwnd=78,identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'})
    api=NS(assert_owner=Mock())
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),
                operations=NS(target=NS(hwnd=77)))
    ui=NS(runtime=NS(observers={'Spiritual':observer}),
          hosts={'Spiritual':NS(saved=saved,api=api,mode='owned')},
          embed_merchant=Mock(),show_merchant=Mock())
    bind_accept_binding(ui)
    with pytest.raises(ValueError,match='host changed'):
        UnifiedUI.prepare_delivery_accept_surface(ui,'Spiritual')
    assert api.assert_owner.call_args_list==[
        ((77,identity),{}),((78,saved.identity),{})]
    ui.embed_merchant.assert_not_called()
    ui.show_merchant.assert_not_called()


def test_accept_surface_rejects_rebound_target_before_showing(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    api=NS(assert_owner=Mock(side_effect=ValueError('target process changed')))
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),operations=NS(target=NS(hwnd=77)))
    ui=NS(runtime=NS(observers={'Spiritual':observer}),
          hosts={'Spiritual':NS(saved=NS(hwnd=77,identity=identity),api=api,mode='owned')},
          embed_merchant=Mock(),show_merchant=Mock())
    bind_accept_binding(ui)
    with pytest.raises(ValueError,match='target process changed'):
        UnifiedUI.prepare_delivery_accept_surface(ui,'Spiritual')
    observer.adapter.assert_identity.assert_called_once_with()
    api.assert_owner.assert_called_once_with(77,identity)
    ui.embed_merchant.assert_not_called()
    ui.show_merchant.assert_not_called()


@pytest.mark.parametrize('changed',[('phase','accept_submitted'),('character','Dutch'),
                                    ('identity',{'pid':8,'creation_time_100ns':10})])
def test_accept_surface_requires_active_journal_binding_before_window_action(tmp_path,monkeypatch,changed):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    from conquest.merchants import delivery_probe
    state=delivery_probe.read_probe()
    field,value=changed
    if field=='identity':state['intent']['merchant']['identity']={**value,'path':'ImConquer.exe'}
    else:state[field]=value
    delivery_probe.write_probe(delivery_probe.JOURNAL,state)
    api=NS(assert_owner=Mock())
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),operations=NS(target=NS(hwnd=77)))
    ui=NS(runtime=NS(observers={'Spiritual':observer}),
          hosts={'Spiritual':NS(saved=NS(hwnd=77,identity=identity),api=api,mode='owned')},
          embed_merchant=Mock(),show_merchant=Mock())
    bind_accept_binding(ui)
    with pytest.raises(ValueError):UnifiedUI.prepare_delivery_accept_surface(ui,'Spiritual')
    observer.adapter.assert_identity.assert_not_called()
    api.assert_owner.assert_not_called()
    ui.embed_merchant.assert_not_called()
    ui.show_merchant.assert_not_called()


@pytest.mark.parametrize('phase',['embed','show'])
def test_accept_surface_rechecks_after_specialized_embed_or_show_observer_swap(tmp_path,monkeypatch,phase):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    api=NS(assert_owner=Mock())
    host=NS(saved=None if phase=='embed' else NS(hwnd=77,identity=identity),api=api,
            mode='owned',attach=Mock(),detach=Mock())
    observer=NS(adapter=NS(identity=identity,assert_identity=Mock()),operations=NS(target=NS(hwnd=77)))
    replacement=NS(adapter=NS(identity={'pid':8,'creation_time_100ns':10,'path':'ImConquer.exe'},
                               assert_identity=Mock()),operations=NS(target=NS(hwnd=88)))
    runtime=NS(observers={'Spiritual':observer})
    def swap(*_args):runtime.observers['Spiritual']=replacement
    ui=NS(runtime=runtime,hosts={'Spiritual':host},embed_merchant=Mock(),show_merchant=Mock(),
          embed_delivery_accept_merchant=Mock(side_effect=swap),
          show_delivery_accept_merchant=Mock(side_effect=swap))
    bind_accept_binding(ui)
    with pytest.raises(ValueError,match='exact merchant process'):
        UnifiedUI.prepare_delivery_accept_surface(ui,'Spiritual')
    if phase=='embed':
        ui.embed_delivery_accept_merchant.assert_called_once_with('Spiritual',(77,identity,'Spiritual'))
        ui.show_delivery_accept_merchant.assert_not_called()
    else:
        ui.show_delivery_accept_merchant.assert_called_once_with('Spiritual',(77,identity,'Spiritual'))
    host.attach.assert_not_called()
    host.detach.assert_not_called()
    ui.embed_merchant.assert_not_called()
    ui.show_merchant.assert_not_called()


def test_actual_specialized_show_rejects_undersized_attached_pane_before_resize(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    monkeypatch.setattr('conquest.character_context.registry',managed_registry_stub)
    ui,host,api,_observer=bound_show_ui(identity,pane_size=(100,100))
    with pytest.raises(ValueError,match='viewport'):
        UnifiedUI.show_delivery_accept_merchant(ui,'Spiritual',(77,identity,'Spiritual'))
    host.resize.assert_not_called()
    host.detach.assert_not_called()
    api.show_async.assert_not_called()


def test_undersized_accept_pane_does_not_hide_sibling_host(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    monkeypatch.setattr('conquest.character_context.registry',managed_registry_stub)
    ui,host,api,_observer=bound_show_ui(identity,pane_size=(100,100))
    sibling_api=NS(assert_owner=Mock(),show_async=Mock())
    sibling=NS(saved=NS(hwnd=88,identity={'pid':8,'creation_time_100ns':10,'path':'ImConquer.exe'}),
               api=sibling_api,mode='owned',detach=Mock())
    ui.hosts['Dutch']=sibling
    with pytest.raises(ValueError,match='viewport'):
        UnifiedUI.show_delivery_accept_merchant(ui,'Spiritual',(77,identity,'Spiritual'))
    host.resize.assert_not_called()
    host.detach.assert_not_called()
    api.show_async.assert_not_called()
    sibling_api.show_async.assert_not_called()
    sibling.detach.assert_not_called()


def test_actual_specialized_show_rechecks_observer_after_layout_before_resize(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    request_verified(tmp_path,monkeypatch,identity)
    monkeypatch.setattr('conquest.character_context.registry',lambda:None)
    ui,host,api,_observer=bound_show_ui(identity)
    replacement=NS(adapter=NS(identity={'pid':8,'creation_time_100ns':10,'path':'ImConquer.exe'},
                               assert_identity=Mock()),operations=NS(target=NS(hwnd=88)))
    ui.apply_client_compact_layout=lambda:ui.runtime.observers.update(Spiritual=replacement)
    with pytest.raises(ValueError,match='exact merchant process'):
        UnifiedUI.show_delivery_accept_merchant(ui,'Spiritual',(77,identity,'Spiritual'))
    host.resize.assert_not_called()
    host.detach.assert_not_called()
    api.show_async.assert_not_called()


@pytest.mark.parametrize('fault',['missing','disabled','renamed','different_profile','other_server'])
def test_managed_accept_profile_binding_fails_before_any_tab_or_window_mutation(tmp_path,monkeypatch,fault):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    registry,profile,key,ui,host,api=managed_show_ui(tmp_path,monkeypatch,identity)
    from conquest.merchants import delivery_probe
    state=delivery_probe.read_probe()
    if fault=='missing':state['target_profile_id']='missing-profile'
    elif fault=='disabled':registry.update(profile.id,{'local_enabled':False})
    elif fault=='renamed':
        state['character']=state['intent']['merchant']['character']='Renamed'
    elif fault=='different_profile':
        other=registry.add('Dutch',role='Merchant')
        state['target_profile_id']=other.id
    else:
        other=registry.add('Spiritual',server='Europe',role='Merchant')
        state['target_profile_id']=other.id
    delivery_probe.write_probe(delivery_probe.JOURNAL,state)
    with pytest.raises(ValueError,match='profile|request-verified'):
        UnifiedUI.show_delivery_accept_merchant(ui,'Spiritual',(77,identity,profile.id))
    ui.notebook.select.assert_not_called()
    ui.root.update_idletasks.assert_not_called()
    host.resize.assert_not_called()
    host.detach.assert_not_called()
    api.show_async.assert_not_called()


def test_managed_profile_name_binds_plain_journal_character_for_specialized_show(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    _registry,profile,key,ui,host,_api=managed_show_ui(tmp_path,monkeypatch,identity)
    UnifiedUI.show_delivery_accept_merchant(ui,'Spiritual',(77,identity,profile.id))
    assert ui.notebook.select.call_args_list[-1].args==('spiritual-frame',)
    host.resize.assert_called_once_with(1200,800)
    assert key.profile_id==profile.id


def test_managed_prepare_input_uses_profile_key_after_callback(tmp_path,monkeypatch):
    identity={'pid':7,'creation_time_100ns':9,'path':'ImConquer.exe'}
    _registry,profile,key,ui,host,_api=managed_show_ui(tmp_path,monkeypatch,identity)
    class ImmediateQueue:
        def put(self,entry):
            callback,done,result=entry
            callback()
            if done:done.set()
    waited=Mock()
    monkeypatch.setattr('conquest.merchants.ui.wait_for_merchant_surface',waited)
    ui.coordinator=NS(purpose='delivery_accept_probe',check=Mock())
    ui.safe_to_yield=lambda:True
    ui.ui_requests=ImmediateQueue()
    ui.grant_fence=None
    ui.prepare_delivery_accept_surface=lambda character:UnifiedUI.prepare_delivery_accept_surface(ui,character)
    ui.show_delivery_accept_merchant=lambda character,expected:UnifiedUI.show_delivery_accept_merchant(ui,character,expected)
    UnifiedUI.prepare_input(ui,'Spiritual')
    assert waited.call_args.args[0] is host
    assert key.profile_id==profile.id


def test_ui_callback_failure_preserves_callback_and_cause_details():
    try:
        try:
            raise OSError('native pane resize failed')
        except OSError as cause:
            raise RuntimeError('handoff callback failed') from cause
    except RuntimeError as error:
        detail=callback_failure(error)
    assert detail == ('Embedded client UI action failed: RuntimeError: handoff callback failed'
                      ' <- OSError: native pane resize failed')


def test_ui_callback_failure_is_single_line_and_bounded_for_adversarial_messages():
    try:
        raise RuntimeError('first\n\tsecond\x00'+'z'*1000)
    except RuntimeError as error:
        detail=callback_failure(error)
    assert '\n' not in detail and '\r' not in detail and '\x00' not in detail
    assert 'first second' in detail
    assert len(detail)<=640
