"""Offline, atomic migration. Original data is retained for rollback."""
from pathlib import Path
from contextlib import closing
import json
import os
import shutil
import sqlite3
import uuid
import yaml
from conquest.character_profiles import ProfileRegistry, write_json


def _copy(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    if source.suffix in ('.sqlite','.sqlite3'):
        with closing(sqlite3.connect(source)) as before,closing(sqlite3.connect(target)) as after:
            before.backup(after)
    else:shutil.copy2(source,target)


def require_offline(source):
    # Do not import engines here: their default paths must resolve only after
    # the launcher has selected the character namespace.
    from conquest.win32 import WindowsBackend
    backend=WindowsBackend()
    for relative in ('.runtime/merchants/bridge.json','reports/overnight/status.json','reports/desktop-farming/app-state.json'):
        path=source/relative
        if not path.exists():continue
        value=json.loads(path.read_text(encoding='utf-8'))
        pid=value.get('pid')
        if pid:
            try:backend.identity(pid)
            except OSError as error:
                if getattr(error,'winerror',None)==87:continue
            raise ValueError('Close the existing Conquest app and workers before importing local state')


def migrate_legacy(source,root,*,check_offline=require_offline,copy_file=_copy):
    source=Path(source).resolve();root=Path(root).resolve()
    marker=root/'migration.json'
    if marker.exists():return json.loads(marker.read_text())
    if root.exists():raise ValueError('Destination already exists; migration will not replace character settings')
    check_offline(source)
    root.parent.mkdir(parents=True,exist_ok=True)
    stage=root.with_name(root.name+'.migration-'+uuid.uuid4().hex)
    registry=ProfileRegistry(stage)
    config_path=source/'profiles/desktop-foreground.local.yaml'
    config=yaml.safe_load(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
    farmer=registry.add(config.get('character','Parasite'))
    merchants=[registry.add(name,role='Merchant') for name in ('Spiritual','Dutch')]
    by_name={p.name:p for p in merchants}
    for folder in ('.runtime','reports'):
        if not (source/folder).exists():continue
        for path in (source/folder).rglob('*'):
            if not path.is_file() or path.suffix not in ('.json','.jsonl','.sqlite','.sqlite3','.dpapi','.yaml','.log'):continue
            relative=path.relative_to(source).as_posix()
            # Session tokens, lock files and input approvals never survive import.
            if any(word in path.name.lower() for word in ('worker','bridge','qualification','input-profile','reload-resume')):continue
            shared=relative.startswith(('.runtime/merchants/','reports/merchants/','.runtime/shop-','.runtime/shops-'))
            dest=stage/('machine-state' if shared else 'characters/'+farmer.id)/relative
            copy_file(path,dest)
    for p in (farmer,*merchants):
        original=source/('.runtime/account.dpapi' if p.role=='Farmer' else '.runtime/merchants/'+p.name.lower()+'/account.dpapi')
        if original.exists():copy_file(original,stage/'accounts'/p.account_id/'account.dpapi')
    if config_path.exists():copy_file(config_path,stage/'characters'/farmer.id/'farmer.local.yaml')
    packaged=Path(__file__).resolve().parents[2]/'profiles/routes'
    for route in (source/'profiles/routes').glob('*.yaml'):
        builtin=packaged/route.name
        if not builtin.exists() or builtin.read_bytes()!=route.read_bytes():
            copy_file(route,stage/'characters'/farmer.id/'routes'/route.name)
    journal=stage/'machine-state/reports/merchants/journal.sqlite3'
    if journal.exists():
        _copy(journal,stage/'migration-backup/merchant-journal.sqlite3')
        with closing(sqlite3.connect(journal)) as db,db:
            for table in ('state','transactions','events','scan_requests','sales_baseline','sales','sales_reconciliations','delivery_reservations'):
                if not db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone():continue
                for name,p in by_name.items():db.execute(f'UPDATE {table} SET character=? WHERE character=?',(p.id,name))
            for p in merchants:
                db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(p.id,'profile_initialized','true'))
            # Preserve only the legacy explicitly authorized source, and only
            # when its UID is supported consistently by durable trade intent.
            for p in merchants:
                sources=set()
                for row in db.execute("SELECT before_json FROM transactions WHERE character=? AND kind='delivery'",(p.id,)):
                    trade=json.loads(row[0]).get('trade',{})
                    if trade.get('participant')==farmer.name and type(trade.get('participant_uid')) is int and trade['participant_uid']>0:
                        sources.add(trade['participant_uid'])
                if len(sources)==1:
                    registry.update(p.id,{'trusted_sources':[{'name':farmer.name,'server':farmer.server,'character_uid':sources.pop()}]},stopped=True,pending=False)
    result={'schema_version':1,'source':str(source),'farmer_profile_id':farmer.id,
        'profiles':{p.name:p.id for p in (farmer,*merchants)},'state':'complete',
        'rollback':'Original source data is unchanged. Keep old and new app instances mutually exclusive.'}
    write_json(stage/'migration.json',result)
    # Destination remains absent until the entire import and DB rewrite succeed.
    os.rename(stage,root)
    return result
