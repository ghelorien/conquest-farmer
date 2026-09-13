import os
from pathlib import Path
import runpy
import sys
from types import ModuleType

import pytest

from conquest.legacy_startup import configure, ENVIRONMENT


@pytest.fixture
def legacy(tmp_path):
    for name in ('profiles', 'reports', '.runtime'):
        (tmp_path/name).mkdir()
    return tmp_path


def test_default_managed_mode_unchanged():
    env={'CONQUEST_DATA_ROOT':'saved', 'CONQUEST_PROFILE_ID':'farmer'}
    original=dict(env)
    result=configure(['--profile-id','farmer'],environ=env)
    assert result.legacy_root is None
    assert result.arguments==('--profile-id','farmer')
    assert env==original


def test_explicit_namespace_inherits_and_preserves_application_options(legacy):
    env={}
    result=configure(['--legacy-data-root',str(legacy),'--profile','profiles/farmer.yaml'],environ=env)
    assert result.legacy_root==legacy.resolve()
    assert result.arguments==('--profile','profiles/farmer.yaml')
    assert env=={ENVIRONMENT:str(legacy.resolve())}
    assert configure(['--check-imports'],environ=env).legacy_root==legacy.resolve()


@pytest.mark.parametrize('name',['CONQUEST_DATA_ROOT','CONQUEST_PROFILE_ID'])
def test_managed_environment_conflict_never_silently_cleared(legacy,name):
    env={name:'saved',ENVIRONMENT:str(legacy)}
    saved=dict(env)
    with pytest.raises(ValueError,match='conflicts'):
        configure(['--check-imports'],environ=env)
    assert env==saved


@pytest.mark.parametrize('option',['--data-root=x','--profile-id=x','--manage-profiles','--migrate-from=x'])
def test_managed_options_rejected(legacy,option):
    with pytest.raises(ValueError,match='conflicts'):
        configure([option],environ={ENVIRONMENT:str(legacy)})


def test_wrong_or_relative_root_rejected_without_creation(tmp_path):
    for value in ('relative',str(tmp_path/'missing'),str(tmp_path),'//server/share'):
        with pytest.raises((ValueError,OSError)):
            configure(['--legacy-data-root',value],environ={})
    assert list(tmp_path.iterdir())==[]


def test_inherited_destination_cannot_be_replaced(legacy,tmp_path_factory):
    other=tmp_path_factory.mktemp('other')
    for name in ('profiles','reports','.runtime'):(other/name).mkdir()
    with pytest.raises(ValueError,match='differs'):
        configure(['--legacy-data-root',str(other)],environ={ENVIRONMENT:str(legacy)})


@pytest.mark.parametrize('check',[False,True])
def test_actual_launcher_bypasses_bootstrap_and_uses_live_cwd(legacy,monkeypatch,check):
    for name in ('CONQUEST_DATA_ROOT','CONQUEST_PROFILE_ID',ENVIRONMENT):monkeypatch.delenv(name,raising=False)
    calls=[]
    app=ModuleType('conquest.desktop_app')
    app.main=lambda:calls.append((Path.cwd(),list(sys.argv),os.environ.get(ENVIRONMENT)))
    monkeypatch.setitem(sys.modules,'conquest.desktop_app',app)
    # Any accidental bootstrap import/use will fail loudly.
    monkeypatch.setitem(sys.modules,'conquest.profile_bootstrap',None)
    script=Path(__file__).resolve().parents[1]/'scripts/start_desktop_app.py'
    monkeypatch.setattr(sys,'argv',[str(script),'--legacy-data-root',str(legacy),*(['--check-imports'] if check else [])])
    monkeypatch.setattr(sys,'path',list(sys.path))
    monkeypatch.chdir(legacy)
    runpy.run_path(str(script),run_name='__main__')
    assert Path.cwd()==legacy
    assert os.environ[ENVIRONMENT]==str(legacy)
    assert calls==[] if check else calls==[(legacy,[str(script)],str(legacy))]
    assert 'CONQUEST_DATA_ROOT' not in os.environ


def test_elevation_carries_explicit_legacy_namespace(legacy,monkeypatch):
    for name in ('CONQUEST_DATA_ROOT','CONQUEST_PROFILE_ID'):monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv(ENVIRONMENT,str(legacy))
    from conquest.desktop_launch import start_arguments
    args=start_arguments(legacy,legacy/'profiles/farmer.yaml',False)
    assert args[-2:]==['--legacy-data-root',str(legacy)]
