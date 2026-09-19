import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from conquest.character_profiles import ProfileRegistry, context_for, write_json
from conquest.merchants.journal import Journal
from conquest.merchants.manual_sessions import ManualSessionStore
from conquest.profile_migration import migrate_legacy, require_offline
from conquest import profile_migration
from conquest.profile_readiness import ProfileReadiness


def merchant_journal(root):
    return Journal(Path(root)/'machine-state/reports/merchants/journal.sqlite3')


def test_profile_readiness_distinguishes_terminal_unresolved_and_unrelated(tmp_path,monkeypatch):
    registry=ProfileRegistry(tmp_path);first=registry.add('First',role='Merchant')
    other=registry.add('Other',role='Merchant')
    monkeypatch.setenv('CONQUEST_DATA_ROOT',str(tmp_path))
    monkeypatch.setenv('CONQUEST_PROFILE_ID',first.id)
    journal=merchant_journal(tmp_path)
    journal.begin('other-work',other.id,'listing',{'uid':1})
    registry.update(first.id,{'local_enabled':False})
    registry.update(first.id,{'local_enabled':True})
    journal.begin('first-terminal',first.id,'listing',{'uid':2})
    journal.transition('first-terminal','aborted')
    registry.update(first.id,{'local_enabled':True})
    journal.begin('first-open',first.id,'listing',{'uid':3})
    with pytest.raises(ValueError,match='this profile'):
        registry.update(first.id,{'trusted_sources':[
            {'name':'Farmer','server':'America','character_uid':7}]})
    # Cosmetic preferences remain independent of both profiles' journals.
    registry.update(first.id,{'label':'North shop','overrides':{}})
    assert registry.resolve(first.id).label=='North shop'


def test_profile_readiness_covers_target_scoped_manual_recovery(tmp_path):
    registry=ProfileRegistry(tmp_path);first=registry.add('First',role='Merchant')
    other=registry.add('Other',role='Merchant');journal=merchant_journal(tmp_path)
    with journal.db() as db:
        db.executescript('''CREATE TABLE manual_rebaseline(
            id TEXT PRIMARY KEY,target_profile_id TEXT,phase TEXT);
            CREATE TABLE manual_replans(session_id TEXT PRIMARY KEY,target_profile_id TEXT,
            merchant_pending INTEGER,farmer_pending INTEGER);''')
        db.execute('INSERT INTO manual_rebaseline VALUES(?,?,?)',
                   ('other-baseline',other.id,'needs_attention'))
        db.execute('INSERT INTO manual_replans VALUES(?,?,?,?)',
                   ('other-replan',other.id,1,1))
        for name,value in (
                ('manual_reader_hold',{'target_profile_id':other.id}),
                ('accepted_request',{'opened_at':1}),
                ('unrelated_request_decline',{'phase':'submitted'})):
            db.execute('INSERT INTO state VALUES(?,?,?)',(other.id,name,json.dumps(value)))
    # Every unresolved row above belongs to the other profile.
    registry.update(first.id,{'local_enabled':False})

    def blocked():
        with pytest.raises(ValueError,match='this profile'):
            registry.update(first.id,{'local_enabled':not registry.resolve(first.id).local_enabled})
    def succeeds():
        registry.update(first.id,{'local_enabled':not registry.resolve(first.id).local_enabled})
    def state(name,value):
        with journal.db() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',
                       (first.id,name,json.dumps(value)))

    with journal.db() as db:
        db.execute('INSERT INTO manual_rebaseline VALUES(?,?,?)',
                   ('first-baseline',first.id,'settlement_observed'))
    with pytest.raises(ValueError,match='this profile'):
        registry.update(first.id,{'trusted_sources':[
            {'name':'Farmer','server':'America','character_uid':7}]})
    blocked()
    with journal.db() as db:
        db.execute("UPDATE manual_rebaseline SET phase='completed' WHERE id='first-baseline'")
    succeeds()

    state('manual_reader_hold',{'target_profile_id':first.id,'phase':'needs_attention'})
    visitor={'target_profile_id':first.id,'visitor_name':'Guest',
             'visitor_server':'America','visitor_uid':91}
    store=ManualSessionStore(journal.path)
    with store.db() as db:
        db.execute('INSERT INTO visitor_permissions VALUES(?,?,?,?,1,?)',
                   (*visitor.values(),1.0))
    with pytest.raises(ValueError,match='this profile'):
        registry.revoke_visitors(first.id,[visitor],operator='tester')
    blocked();state('manual_reader_hold',None)
    assert registry.revoke_visitors(first.id,[visitor],operator='tester')==[]
    succeeds()

    with journal.db() as db:
        db.execute('INSERT INTO manual_replans VALUES(?,?,?,?)',
                   ('first-replan',first.id,1,0))
    blocked()
    with journal.db() as db:
        db.execute("UPDATE manual_replans SET merchant_pending=0 WHERE session_id='first-replan'")
    succeeds()

    state('accepted_request',{'opened_at':1})
    with pytest.raises(ValueError,match='this profile'):
        registry.update(first.id,{'role':'Farmer'})
    blocked();state('accepted_request',None);succeeds()
    registry.update(first.id,{'role':'Farmer'});registry.update(first.id,{'role':'Merchant'})
    state('unrelated_request_decline',{'phase':'submitted'})
    with pytest.raises(ValueError,match='this profile'):
        registry.update(first.id,{'trusted_sources':[
            {'name':'Farmer','server':'America','character_uid':8}]})
    blocked();state('unrelated_request_decline',{'phase':'verified'})
    registry.update(first.id,{'trusted_sources':[
        {'name':'Farmer','server':'America','character_uid':8}]})
    succeeds()


def test_farmer_readiness_is_profile_scoped_and_terminal_history_is_inert(tmp_path):
    registry=ProfileRegistry(tmp_path);first=registry.add('First');other=registry.add('Other')
    own=context_for(first.id,tmp_path).state_dir/'reports/banking/merchant-journey.json'
    foreign=context_for(other.id,tmp_path).state_dir/'reports/banking/merchant-journey.json'
    write_json(foreign,{'phase':'return_pending'})
    assert ProfileReadiness(tmp_path).transaction_idle(first.id)
    registry.update(first.id,{'local_enabled':False})
    write_json(own,{'phase':'completed'})
    registry.update(first.id,{'local_enabled':True})
    write_json(own,{'phase':'market'})
    with pytest.raises(ValueError):registry.update(first.id,{'local_enabled':False})
    registry.update(first.id,{'label':'still editable'})


def test_role_requires_explicit_exact_visitor_revocation(tmp_path):
    registry=ProfileRegistry(tmp_path);merchant=registry.add('Seller',role='Merchant')
    path=tmp_path/'machine-state/reports/merchants/journal.sqlite3'
    store=ManualSessionStore(path)
    visitor={'target_profile_id':merchant.id,'visitor_name':'Guest',
             'visitor_server':'America','visitor_uid':91}
    with store.db() as db:
        db.execute('INSERT INTO visitor_permissions VALUES(?,?,?,?,1,?)',
                   (*visitor.values(),1.0))
    assert registry.list_visitors(merchant.id)[0]['visitor_name']=='Guest'
    with pytest.raises(ValueError,match='visitor'):
        registry.update(merchant.id,{'role':'Farmer'})
    # An affected unresolved journal also blocks the permission edit.
    with store.db() as db:
        db.execute("INSERT INTO manual_sessions(id,target_profile_id,visitor_json,process_json,"
                   "character_json,phase,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                   ('open',merchant.id,json.dumps(visitor),json.dumps({'pid':1}),
                    json.dumps({'character':'Seller'}),'needs_attention',1,1))
    with pytest.raises(ValueError,match='this profile'):
        registry.revoke_visitors(merchant.id,[visitor],operator='tester')
    with store.db() as db:
        db.execute("UPDATE manual_sessions SET phase='operator_overridden',terminal_json='{}' "
                   "WHERE id='open'")
    assert registry.revoke_visitors(merchant.id,[visitor],operator='tester')==[]
    registry.update(merchant.id,{'role':'Farmer'})
    assert registry.resolve(merchant.id).role=='Farmer'


def legacy_journal(source):
    return Journal(source/'reports/merchants/journal.sqlite3')


def test_migration_remaps_every_character_table_and_manual_targets(tmp_path):
    source=tmp_path/'legacy';journal=legacy_journal(source)
    journal.admit_delivery('admission','Dutch',[7],{'operation_id':'admission'},items=None)
    manual=ManualSessionStore(journal.path)
    with manual.db() as db:
        db.execute('INSERT INTO visitor_permissions VALUES(?,?,?,?,1,?)',
                   ('Dutch','Guest','America',8,1.0))
        db.execute("INSERT INTO manual_sessions(id,target_profile_id,visitor_json,process_json,"
                   "character_json,phase,created_at,updated_at,terminal_json) "
                   "VALUES(?,?,?,?,?,?,?,?,?)",
                   ('done','Dutch','null','{}','{}','completed',1,2,'{}'))
        db.execute("INSERT INTO manual_audit(session_id,event,at,payload_json,previous_digest,digest) "
                   "VALUES(?,?,?,?,?,?)",('done','kept',1,'{}','','audit-digest'))
        db.execute('CREATE TABLE custom_character_receipts(character TEXT, receipt TEXT)')
        db.execute('INSERT INTO custom_character_receipts VALUES(?,?)',('Dutch','kept'))
        # This is a durable, unresolved delivery artifact: migration must map
        # its owner just like the built-in admission table rather than leaving
        # an old display name capable of bypassing profile-scoped readiness.
        db.execute('CREATE TABLE pending_receipts(character TEXT, receipt TEXT)')
        db.execute('INSERT INTO pending_receipts VALUES(?,?)',('Dutch','pending-proof'))
    destination=tmp_path/'managed'
    result=migrate_legacy(source,destination,check_offline=lambda _:None)
    dutch=result['profiles']['Dutch'];path=destination/'machine-state/reports/merchants/journal.sqlite3'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT character FROM delivery_admissions').fetchone()[0]==dutch
        assert db.execute('SELECT character,receipt FROM custom_character_receipts').fetchone()==(dutch,'kept')
        assert db.execute('SELECT character,receipt FROM pending_receipts').fetchone()==(dutch,'pending-proof')
        assert db.execute('SELECT target_profile_id FROM visitor_permissions').fetchone()[0]==dutch
        assert db.execute('SELECT target_profile_id FROM manual_sessions').fetchone()[0]==dutch
        assert db.execute('SELECT digest FROM manual_audit').fetchone()[0]=='audit-digest'
        triggers={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert 'manual_session_identity_immutable' in triggers


def test_migration_refuses_nonterminal_manual_binding_instead_of_rewriting_evidence(tmp_path):
    source=tmp_path/'legacy';journal=legacy_journal(source);manual=ManualSessionStore(journal.path)
    with manual.db() as db:
        db.execute("INSERT INTO manual_sessions(id,target_profile_id,visitor_json,process_json,"
                   "character_json,phase,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                   ('open','Dutch',json.dumps({'target_profile_id':'Dutch'}),'{}','{}',
                    'needs_attention',1,2))
        db.execute("INSERT INTO manual_audit(session_id,event,at,payload_json,previous_digest,digest) "
                   "VALUES(?,?,?,?,?,?)",('open','kept',1,'{}','','immutable-digest'))
    destination=tmp_path/'managed'
    with pytest.raises(ValueError,match='nonterminal manual'):
        migrate_legacy(source,destination,check_offline=lambda _:None)
    assert not destination.exists()
    with manual.db() as db:
        assert db.execute('SELECT target_profile_id FROM manual_sessions').fetchone()[0]=='Dutch'
        assert db.execute('SELECT digest FROM manual_audit').fetchone()[0]=='immutable-digest'


def test_migration_refuses_nonnull_manual_reader_hold(tmp_path):
    source=tmp_path/'legacy';journal=legacy_journal(source)
    hold={'target_profile_id':'Dutch','phase':'needs_attention','reason':'reader failed'}
    journal.set('Dutch','manual_reader_hold',hold)
    destination=tmp_path/'managed'
    with pytest.raises(ValueError,match='manual reader hold'):
        migrate_legacy(source,destination,check_offline=lambda _:None)
    assert not destination.exists()
    assert journal.get('Dutch','manual_reader_hold')==hold


def test_migration_copies_only_recognized_dpapi_secrets(tmp_path):
    source=tmp_path/'legacy'
    recognized=source/'.runtime/account.dpapi';recognized.parent.mkdir(parents=True)
    recognized.write_bytes(b'account')
    leak=source/'.runtime/private-backup.dpapi';leak.write_bytes(b'must-not-copy')
    nested=source/'reports/merchants/leaked.dpapi';nested.parent.mkdir(parents=True)
    nested.write_bytes(b'must-not-copy-either')
    destination=tmp_path/'managed'
    migrate_legacy(source,destination,check_offline=lambda _:None)
    dpapi=list(destination.rglob('*.dpapi'))
    assert len(dpapi)==1 and dpapi[0].read_bytes()==b'account'
    assert all(b'must-not-copy' not in path.read_bytes() for path in dpapi)


def test_offline_check_uses_full_identity_and_scans_all_durable_receipts(tmp_path):
    source=tmp_path/'legacy'
    write_json(source/'.runtime/unusual.json',
               {'pid':41,'creation_time_100ns':500,'path':r'C:\Python\python.exe'})
    write_json(source/'reports/controller-two.json',
               {'identity':{'pid':42,'creation_time_100ns':600,
                            'path':r'C:\Python\pythonw.exe'}})
    class Backend:
        def __init__(self,second_creation=601):self.second_creation=second_creation
        def identity(self,pid):
            if pid==41:return {'pid':41,'creation_time_100ns':501,'path':r'C:\Python\python.exe'}
            return {'pid':42,'creation_time_100ns':self.second_creation,
                    'path':r'C:\Python\pythonw.exe'}
    require_offline(source,backend=Backend())
    with pytest.raises(ValueError,match='Close'):
        require_offline(source,backend=Backend(600))
    # A bare reused PID can never become a blocker by itself.
    write_json(source/'.runtime/status.json',{'pid':99})
    require_offline(source,backend=Backend())


def test_migration_lock_and_legacy_app_fence_exclude_concurrency(tmp_path):
    from conquest.profile_bootstrap import managed_root_owner, _owner_file
    source=tmp_path/'legacy';write_json(source/'.runtime/state.json',{'value':1})
    destination=tmp_path/'managed'
    with managed_root_owner(destination,timeout=0):
        with pytest.raises(ValueError,match='running'):
            migrate_legacy(source,destination,check_offline=lambda _:None)
    with managed_root_owner(source,timeout=0):
        with pytest.raises(ValueError,match='running'):
            migrate_legacy(source,destination,check_offline=lambda _:None)
    destination.mkdir();(destination/'app.lock').write_bytes(b'0')
    with _owner_file(destination/'app.lock',0,'held'):
        with pytest.raises(ValueError,match='running'):
            migrate_legacy(source,destination,check_offline=lambda _:None)
    (destination/'app.lock').unlink();destination.rmdir()
    (source/'app.lock').write_bytes(b'0')
    with _owner_file(source/'app.lock',0,'held'):
        with pytest.raises(ValueError,match='running'):
            migrate_legacy(source,destination,check_offline=lambda _:None)


def test_diagnostics_destination_moves_aside_and_restores_on_failure(tmp_path):
    source=tmp_path/'legacy';write_json(source/'.runtime/state.json',{'value':1})
    destination=tmp_path/'managed';write_json(destination/'diagnostics/failure.json',{'kept':True})
    result=migrate_legacy(source,destination,check_offline=lambda _:None)
    rollback=Path(result['diagnostics_rollback'])
    assert (rollback/'diagnostics/failure.json').exists()
    assert json.loads((rollback/'diagnostics/failure.json').read_text())=={'kept':True}
    assert (destination/'profiles.json').exists()

    second=tmp_path/'second';write_json(second/'diagnostics/failure.json',{'kept':True})
    def fail(*_):raise OSError('copy interrupted')
    with pytest.raises(OSError):
        migrate_legacy(source,second,check_offline=lambda _:None,copy_file=fail)
    assert json.loads((second/'diagnostics/failure.json').read_text())=={'kept':True}


def test_existing_managed_destination_is_rejected(tmp_path):
    source=tmp_path/'legacy';source.mkdir()
    destination=tmp_path/'managed';write_json(destination/'profiles.json',{'managed':True})
    with pytest.raises(ValueError,match='managed installation'):
        migrate_legacy(source,destination,check_offline=lambda _:None)


def test_incomplete_migration_marker_is_not_an_idempotence_bypass(tmp_path):
    source=tmp_path/'legacy';source.mkdir();destination=tmp_path/'managed'
    write_json(destination/'migration.json',{'state':'copying'})
    with pytest.raises(ValueError,match='incomplete managed installation'):
        migrate_legacy(source,destination,check_offline=lambda _:None)


def test_destination_reparse_is_rejected_before_idempotence_marker(tmp_path,monkeypatch):
    source=tmp_path/'legacy';source.mkdir()
    destination=tmp_path/'managed';write_json(destination/'migration.json',{'state':'complete'})
    actual=profile_migration._reparse
    monkeypatch.setattr(profile_migration,'_reparse',
                        lambda path:Path(path)==destination or actual(path))
    with pytest.raises(ValueError,match='Destination is a reparse'):
        migrate_legacy(source,destination,check_offline=lambda _:None)


def test_source_mutation_is_rejected_without_activation(tmp_path):
    source=tmp_path/'legacy';item=source/'.runtime/state.json';write_json(item,{'value':1})
    destination=tmp_path/'managed'
    def mutate(original,target):
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(original,target)
        original.write_text('{"value":2}',encoding='utf-8')
    with pytest.raises(ValueError,match='changed'):
        migrate_legacy(source,destination,check_offline=lambda _:None,copy_file=mutate)
    assert not destination.exists()


def test_credential_source_mutation_is_rejected_before_managed_activation(tmp_path):
    """Fixed encrypted inputs participate in the final source manifest too."""
    source=tmp_path/'legacy';credential=source/'.runtime/merchants/shops-webhook.dpapi'
    credential.parent.mkdir(parents=True);credential.write_bytes(b'first-secret')
    destination=tmp_path/'managed'
    def mutate(original,target):
        profile_migration._copy(original,target)
        if Path(original)==credential:
            credential.write_bytes(b'replaced-secret')
    with pytest.raises(ValueError,match='changed'):
        migrate_legacy(source,destination,check_offline=lambda _:None,copy_file=mutate)
    assert not destination.exists()


def test_source_reparse_is_rejected_before_inspection(tmp_path,monkeypatch):
    source=tmp_path/'legacy';source.mkdir();destination=tmp_path/'managed'
    actual=profile_migration._reparse
    monkeypatch.setattr(profile_migration,'_reparse',
                        lambda path:Path(path)==source or actual(path))
    with pytest.raises(ValueError,match='reparse'):
        migrate_legacy(source,destination,check_offline=lambda _:None)
    assert not destination.exists()


def test_sqlite_wal_mutation_during_backup_is_rejected(tmp_path):
    source=tmp_path/'legacy';database=source/'reports/merchants/journal.sqlite3'
    database.parent.mkdir(parents=True)
    live=sqlite3.connect(database)
    live.execute('PRAGMA journal_mode=WAL');live.execute('CREATE TABLE sample(value TEXT)')
    live.execute("INSERT INTO sample VALUES('before')");live.commit()
    destination=tmp_path/'managed'
    def mutate_wal(original,target):
        profile_migration._copy(original,target)
        if Path(original)==database:
            live.execute("INSERT INTO sample VALUES('after')");live.commit()
    try:
        with pytest.raises(ValueError,match='changed'):
            migrate_legacy(source,destination,check_offline=lambda _:None,copy_file=mutate_wal)
    finally:
        live.close()
    assert not destination.exists()


def test_manifest_rejects_file_changed_after_its_individual_copy(tmp_path):
    source=tmp_path/'legacy';first=source/'.runtime/first.json';second=source/'.runtime/second.json'
    write_json(first,{'value':1});write_json(second,{'value':2})
    copied=[]
    def mutate_late(original,target):
        profile_migration._copy(original,target);copied.append(Path(original))
        if len(copied)==2:
            copied[0].write_text('{"value":"late"}',encoding='utf-8')
    destination=tmp_path/'managed'
    with pytest.raises(ValueError,match='before activation'):
        migrate_legacy(source,destination,check_offline=lambda _:None,copy_file=mutate_late)
    assert not destination.exists()
