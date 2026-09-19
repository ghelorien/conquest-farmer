import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
from conquest.character_profiles import ProfileRegistry, context_for, write_json
from conquest.character_context import (state_path, farmer_name, resolve_merchant, credential_for,
    OwnedMerchants, trusted_delivery, ProfileMap, ProfileName, is_farmer_owner)


@pytest.fixture(autouse=True)
def clean_context(monkeypatch):
    monkeypatch.delenv('CONQUEST_DATA_ROOT',raising=False)
    monkeypatch.delenv('CONQUEST_PROFILE_ID',raising=False)


def activate(monkeypatch,registry,profile):
    monkeypatch.setenv('CONQUEST_DATA_ROOT',str(registry.root))
    monkeypatch.setenv('CONQUEST_PROFILE_ID',profile.id)


def test_character_and_account_state_are_independent(tmp_path,monkeypatch):
    r=ProfileRegistry(tmp_path);a=r.add('Parasite');b=r.add('FreshArcher')
    ca,cb=context_for(a.id,tmp_path),context_for(b.id,tmp_path)
    assert ca.credentials!=cb.credentials and ca.state_dir!=cb.state_dir
    assert b.character_uid is None and not b.trusted_sources and r.effective(b)=={}
    activate(monkeypatch,r,a);old=state_path('.runtime/native-controls.json')
    activate(monkeypatch,r,b)
    assert old!=state_path('.runtime/native-controls.json') and farmer_name()=='FreshArcher'
    assert Path(state_path('.runtime/account.dpapi'))==cb.credentials
    assert Path(state_path('.runtime/merchant-input.lock'))==tmp_path/'locks/input.lock'


def test_labels_do_not_key_records_or_credentials(tmp_path,monkeypatch):
    r=ProfileRegistry(tmp_path);p=r.add('Seller',role='Merchant');activate(monkeypatch,r,p)
    from conquest.merchants.journal import Journal
    journal=Journal(tmp_path/'journal.sqlite3');name=resolve_merchant(p.id)
    journal.set(name,'total',90210);journal.begin('intent',name,'listing',{'uid':123})
    path=credential_for(name)
    r.update(p.id,{'label':'New label'},stopped=True,pending=True)
    journal.transition('intent','aborted')
    r.update(p.id,{'label':'Seller on laptop'},stopped=True,pending=False)
    assert journal.get(p.id,'total')==90210 and credential_for(p.id)==path
    with sqlite3.connect(journal.path) as db:
        assert db.execute('SELECT character FROM state').fetchone()[0]==p.id
    with journal.db() as db:
        row=db.execute('SELECT character FROM state').fetchone()
        assert row['character']=='Seller' and row['character'].profile_id==p.id


def test_duplicate_names_across_servers_require_bridge_id(tmp_path,monkeypatch):
    r=ProfileRegistry(tmp_path);a=r.add('Seller',role='Merchant');r.add('Seller','Europe','Merchant')
    activate(monkeypatch,r,a)
    from conquest.portable_ui import normalize_command
    with pytest.raises(ValueError):normalize_command({'action':'pause','character':'Seller'})
    command=normalize_command({'action':'pause','profile_id':a.id})
    assert command['character'].profile_id==a.id
    with pytest.raises(ValueError):r.add('seller',role='Farmer')
    with pytest.raises(ValueError):normalize_command({'action':'pause','profile_id':'Seller'})


def test_owned_roster_and_explicit_trust(tmp_path,monkeypatch):
    r=ProfileRegistry(tmp_path);farmer=r.add('NewArcher');seller=r.add('MyShop',role='Merchant')
    r.add('OtherShop',role='Merchant',local_enabled=False);activate(monkeypatch,r,farmer)
    assert set(OwnedMerchants())=={'myshop','othershop'}
    assert not trusted_delivery(seller.id,farmer.name,42)
    r.update(seller.id,{'trusted_sources':[{'name':farmer.name,'server':'America','character_uid':42}]},stopped=True,pending=False)
    assert trusted_delivery(seller.id,farmer.name,42)
    assert not trusted_delivery(seller.id,farmer.name,43)
    assert not trusted_delivery(seller.id,'Stranger',42)


def test_binding_rejects_wrong_character_server_and_uid(tmp_path):
    r=ProfileRegistry(tmp_path);p=r.add('Archer')
    for name,server,uid in [('Other','America',11),('Archer','Europe',11),('Archer','America',0)]:
        with pytest.raises(ValueError):r.bind(p.id,name,server,uid)
    r.bind(p.id,'Archer','America',11)
    with pytest.raises(ValueError):r.bind(p.id,'Archer','America',12)
    with pytest.raises(ValueError):context_for(p.id,tmp_path).verify({'character':'Archer','server':'America','character_uid':12})


@pytest.mark.parametrize('forbidden',['password','webhook','pid','hwnd','level','class','skills','equipment','geometry','qualification','transactions'])
def test_exports_reject_non_preferences(tmp_path,forbidden):
    r=ProfileRegistry(tmp_path)
    with pytest.raises(ValueError):r.add('Archer',overrides={forbidden:1})


def test_export_import_new_identity_no_secrets_or_trust(tmp_path):
    r=ProfileRegistry(tmp_path/'a');p=r.add('Old',overrides={'heal_below':.55},
        trusted_sources=[{'name':'Friend','server':'America','character_uid':11}])
    r.bind(p.id,p.name,p.server,22)
    payload=r.export(p.id)
    assert set(payload)=={'schema_version','kind','role','label','settings'}
    other=ProfileRegistry(tmp_path/'b');new=other.import_preferences(payload,'New')
    assert new.id!=p.id and new.account_id!=p.account_id
    assert new.character_uid is None and not new.trusted_sources
    assert new.overrides=={'heal_below':.55}
    assert not context_for(new.id,other.root).credentials.exists()
    with pytest.raises(ValueError):other.import_preferences({**payload,'secret':'no'},'Third')


def test_template_overrides_only_explicit_settings(tmp_path):
    r=ProfileRegistry(tmp_path);key=r.save_template('Archer defaults',{'heal_below':.4,'jump_scatter':False})
    a=r.add('A',template=key,overrides={'heal_below':.6});b=r.add('B',template=key)
    assert r.effective(a)=={'heal_below':.6,'jump_scatter':False}
    assert r.effective(b)=={'heal_below':.4,'jump_scatter':False}


def test_role_change_requires_affected_profile_transaction_idle(tmp_path):
    r=ProfileRegistry(tmp_path);p=r.add('A');other=r.add('B')
    path=context_for(p.id,tmp_path).state_dir/'reports/banking/merchant-journey.json'
    other_path=context_for(other.id,tmp_path).state_dir/'reports/banking/merchant-journey.json'
    write_json(other_path,{'phase':'return_pending'})
    r.update(p.id,{'role':'Merchant'})
    r.update(p.id,{'role':'Farmer'})
    write_json(path,{'phase':'return_pending'})
    with pytest.raises(ValueError,match='this profile'):
        r.update(p.id,{'role':'Merchant'})
    assert r.resolve(p.id).role=='Farmer'


def test_ui_and_input_roles_do_not_collide_with_character_name(tmp_path):
    r=ProfileRegistry(tmp_path);p=r.add('Farmer',role='Merchant');name=ProfileName(p.name,p.id)
    mapping=ProfileMap();mapping['Farmer']='role';mapping[name]='character'
    assert mapping['Farmer']=='role' and mapping[name]=='character'
    assert is_farmer_owner('Farmer') and not is_farmer_owner(name)


def test_profile_runtime_starts_all_operations_paused(tmp_path,monkeypatch):
    r=ProfileRegistry(tmp_path);p=r.add('Seller',role='Merchant');activate(monkeypatch,r,p)
    from conquest.merchants.journal import Journal
    from conquest.merchants.runtime import MerchantRuntime
    from conquest.merchants.coordination import InputCoordinator
    journal=Journal(tmp_path/'journal.sqlite3')
    runtime=MerchantRuntime(None,InputCoordinator(path=tmp_path/'input.lock'),journal=journal)
    assert not runtime.enabled(p.id) and not runtime.refill_enabled(p.id)
    assert journal.get(p.id,'profile_initialized') is True


def test_offline_migration_preserves_receipts_and_pause_intent(tmp_path,monkeypatch):
    from conquest.merchants.journal import Journal
    from conquest.profile_migration import migrate_legacy
    source=tmp_path/'legacy';old=Journal(source/'reports/merchants/journal.sqlite3')
    old.set('Dutch','enabled',False);old.set('Dutch','refill_enabled',True)
    old.set('Dutch','silver_total',123456);old.begin('pending','Dutch','listing',{'uid':7})
    write_json(source/'.runtime/native-controls.json',{'enabled':False,'revision':11})
    write_json(source/'.runtime/merchants/dutch/qualification.json',{'unsafe':'old geometry'})
    target=tmp_path/'portable';result=migrate_legacy(source,target,check_offline=lambda _:None)
    assert migrate_legacy(source,target)==result
    assert old.get('Dutch','silver_total')==123456 and old.pending('Dutch')
    assert (target/'migration-backup/merchant-journal.sqlite3').exists()
    assert not list(target.rglob('qualification.json'))
    r=ProfileRegistry(target);activate(monkeypatch,r,r.resolve(result['farmer_profile_id']))
    new=Journal(target/'machine-state/reports/merchants/journal.sqlite3')
    assert new.get(result['profiles']['Dutch'],'refill_enabled') is True
    assert new.get(result['profiles']['Dutch'],'silver_total')==123456
    assert new.pending(result['profiles']['Dutch'])[0]['id']=='pending'
    from conquest.profile_bootstrap import offline_edit_ready
    assert not offline_edit_ready(target)


def test_interrupted_migration_never_activates_partial_state(tmp_path):
    from conquest.profile_migration import migrate_legacy
    source=tmp_path/'old';write_json(source/'.runtime/test.json',{'value':1})
    def fail(*args):raise OSError('simulated disk failure')
    destination=tmp_path/'new'
    with pytest.raises(OSError):migrate_legacy(source,destination,check_offline=lambda _:None,copy_file=fail)
    assert not destination.exists() and (source/'.runtime/test.json').exists()
    assert migrate_legacy(source,destination,check_offline=lambda _:None)['state']=='complete'


def test_app_lock_excludes_second_farmer_environment(tmp_path):
    from conquest.profile_bootstrap import app_owner
    with app_owner(tmp_path):
        with pytest.raises(ValueError):
            with app_owner(tmp_path,timeout=0):pass
    with app_owner(tmp_path,timeout=0):pass


def test_route_copies_are_saved_to_selected_character(tmp_path,monkeypatch):
    r=ProfileRegistry(tmp_path);a=r.add('A');b=r.add('B');activate(monkeypatch,r,a)
    from conquest.routes import RouteLibrary
    library=RouteLibrary();route=library.all()[0].model_dump()
    route.update(id='private-route',name='A private route')
    path=library.save(route)
    assert path.is_relative_to(context_for(a.id,tmp_path).state_dir)
    activate(monkeypatch,r,b)
    with pytest.raises(ValueError):RouteLibrary().load('private-route')


def test_surface_block_prevents_farmer_input_and_releases_lease(tmp_path):
    from conquest.merchants.coordination import InputCoordinator, install, input_scope
    from conquest.capture import CaptureUnavailable
    guard=InputCoordinator(lambda:True,path=tmp_path/'input.lock');guard.surface_blocks['Farmer']=True
    install(guard)
    try:
        with pytest.raises(CaptureUnavailable):
            with input_scope():pytest.fail('Invalid surface must not receive input')
        assert guard.owner is None and guard.thread is None
    finally:install(None)


def test_range_override_cannot_exceed_engine_capability(tmp_path,monkeypatch):
    import yaml
    from conquest.trial import TrialConfig
    from conquest.character_context import apply_overrides
    r=ProfileRegistry(tmp_path);p=r.add('Fresh',overrides={'attack_range_tiles':20})
    activate(monkeypatch,r,p)
    config=TrialConfig.model_validate(yaml.safe_load(Path('profiles/desktop-foreground.example.yaml').read_text()))
    config=config.model_copy(update={'attack_range_tiles':5})
    actual=apply_overrides(config)
    assert actual.attack_range_tiles==5 and actual.character=='Fresh'


def test_account_storage_is_encrypted_and_compatible(tmp_path):
    from conquest.profile_secrets import save_login
    from conquest.reconnect import load_credentials
    r=ProfileRegistry(tmp_path);p=r.add('A');other=r.add('B');ctx=context_for(p.id,tmp_path)
    save_login(ctx,'test-account','test-password')
    assert b'test-password' not in ctx.credentials.read_bytes()
    assert load_credentials(ctx.credentials)=={'username':'test-account','password':'test-password'}
    assert not context_for(other.id,tmp_path).credentials.exists()


def test_role_change_blocks_unfinished_farmer_delivery(tmp_path):
    from conquest.profile_bootstrap import offline_edit_ready
    r=ProfileRegistry(tmp_path);p=r.add('A')
    path=context_for(p.id,tmp_path).state_dir/'reports/banking/merchant-journey.json'
    write_json(path,{'phase':'return_pending'})
    assert not offline_edit_ready(tmp_path)
    write_json(path,{'phase':'completed'})
    assert offline_edit_ready(tmp_path)
