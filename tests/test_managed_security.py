from pathlib import Path
import os

import pytest

from conquest import managed_security as security
from conquest import profile_migration, profile_bootstrap
from conquest.character_profiles import ProfileRegistry


def test_new_root_private_acl_is_idempotent_and_children_are_same_user_writable(tmp_path):
    root=tmp_path/'managed'
    assert security.ensure_managed_directory(root)
    child=root/'child';child.mkdir();(child/'state.json').write_text('{}')
    security.verify_existing(child, directory=True)
    security.verify_existing(child/'state.json', directory=False)
    assert not security.ensure_managed_directory(root)
    if os.name=='nt':
        api,constants,native=security._windows()
        descriptor=native.GetNamedSecurityInfo(str(root),native.SE_FILE_OBJECT,native.DACL_SECURITY_INFORMATION)
        acl=descriptor.GetSecurityDescriptorDacl()
        rows=[acl.GetAce(i) for i in range(acl.GetAceCount())]
        user=native.ConvertSidToStringSid(security._user_sid(api,constants,native))
        assert [(native.ConvertSidToStringSid(row[2]),row[1]) for row in rows]==[
            (user,security.MODIFY),('S-1-5-18',security.FULL),('S-1-5-32-544',security.FULL)]
        assert all(row[0][1]==security.INHERIT for row in rows)
        assert not rows[0][1] & (0x40000|0x80000)


def test_fresh_app_and_profile_roots_provision_before_payload(tmp_path):
    root=tmp_path/'managed'
    with profile_bootstrap.app_owner(root):
        profile=ProfileRegistry(root).add('Test')
    assert ProfileRegistry(root).resolve(profile.id).name=='Test'
    for path in (root/'app.lock',root/'profiles.lock',root.parent/'.managed.managed.lock'):
        security.verify_existing(path,directory=False)


def test_failed_acl_provision_leaves_no_stage_or_new_root(tmp_path, monkeypatch):
    def fail(*args,**kwargs):raise ValueError('ACL unavailable')
    monkeypatch.setattr(security,'provision_new',fail)
    root=tmp_path/'managed'
    with pytest.raises(ValueError,match='ACL unavailable'):security.ensure_managed_directory(root)
    assert not root.exists()
    with pytest.raises(ValueError,match='ACL unavailable'):profile_migration._create_stage(root)
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError,match='ACL unavailable'):security.open_managed_lock(tmp_path/'lock')
    assert not list(tmp_path.iterdir())


def test_stage_acl_failure_preserves_diagnostics_and_legacy_source(tmp_path, monkeypatch):
    source=tmp_path/'legacy';source.mkdir();(source/'keep').write_text('legacy')
    destination=tmp_path/'managed';(destination/'diagnostics').mkdir(parents=True)
    diagnostic=destination/'diagnostics'/'startup.txt';diagnostic.write_text('diagnostic')
    original=security.provision_new
    def provision(path, *, directory):
        if directory:raise ValueError('cannot protect stage')
        original(path,directory=directory)
    monkeypatch.setattr(security,'provision_new',provision)
    with pytest.raises(ValueError,match='cannot protect stage'):
        profile_migration.migrate_legacy(source,destination,check_offline=lambda _:None)
    assert diagnostic.read_text()=='diagnostic' and (source/'keep').read_text()=='legacy'
    assert not list(tmp_path.glob('managed.migration-*'))
    assert not list(tmp_path.glob('managed.diagnostics-before-*'))


@pytest.mark.skipif(os.name!='nt',reason='Windows TokenUser boundary')
def test_missing_or_service_token_fails_closed_and_cleans_new_root(tmp_path, monkeypatch):
    def unavailable(*args):raise ValueError('interactive user SID unavailable')
    monkeypatch.setattr(security,'_user_sid',unavailable)
    root=tmp_path/'managed'
    with pytest.raises(ValueError,match='same-account'):security.ensure_managed_directory(root)
    assert not root.exists()


def test_existing_root_is_never_silently_repaired(tmp_path, monkeypatch):
    root=tmp_path/'managed';root.mkdir();(root/'keep').write_text('unchanged')
    calls=[];monkeypatch.setattr(security,'provision_new',lambda *a,**k:calls.append(a))
    monkeypatch.setattr(security,'verify_existing',lambda *a,**k:(_ for _ in ()).throw(ValueError('foreign or inaccessible')))
    with pytest.raises(ValueError,match='foreign'):security.ensure_managed_directory(root)
    assert not calls and (root/'keep').read_text()=='unchanged'


def test_reparse_root_is_rejected_without_changes(tmp_path, monkeypatch):
    root=tmp_path/'managed';root.mkdir()
    original=security.os.lstat
    def fake(path):
        from types import SimpleNamespace
        if Path(path)==root:return SimpleNamespace(st_mode=0,st_file_attributes=0x400)
        return original(path)
    monkeypatch.setattr(security.os,'lstat',fake)
    with pytest.raises(ValueError,match='reparse'):security.ensure_managed_directory(root)
    assert not list(root.iterdir())
