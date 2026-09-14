"""Configure the process namespace before importing behavior modules."""
from contextlib import contextmanager, closing
from pathlib import Path
import argparse
import json
import os
import time
from conquest.character_profiles import ProfileRegistry, data_root, write_json, context_for


@contextmanager
def app_owner(root,timeout=15):
    import msvcrt
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with (root/'app.lock').open('a+b') as lock:
        lock.seek(0,2)
        if not lock.tell():lock.write(b'0');lock.flush()
        deadline=time.monotonic()+timeout
        while True:
            lock.seek(0)
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1);break
            except OSError:
                if time.monotonic()>=deadline:raise ValueError('Conquest is already running on this PC') from None
                time.sleep(.1)
        yield


def initialize(repo,argv):
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--profile-id');parser.add_argument('--data-root')
    parser.add_argument('--manage-profiles',action='store_true')
    parser.add_argument('--migrate-from')
    args,_=parser.parse_known_args(argv)
    if args.data_root:os.environ['CONQUEST_DATA_ROOT']=str(Path(args.data_root).resolve())
    destination=data_root()
    if not destination.exists() and not args.migrate_from and (Path(repo)/'reports/merchants/journal.sqlite3').exists():
        # Explain and perform migration before the destination or any engine
        # default paths are created. Cancel leaves the legacy setup intact.
        import tkinter as tk
        from tkinter import messagebox
        from conquest.window_host import use_unaware_dpi
        use_unaware_dpi();dialog=tk.Tk();dialog.withdraw()
        try:
            migrate=messagebox.askyesnocancel('Import existing Conquest setup',
                'Import the existing farmer and merchant records into local character profiles?\n'
                'Close the previous Conquest app first. Its files remain unchanged for rollback.\n'
                'Choose No to create fresh paused profiles.',parent=dialog)
        finally:dialog.destroy()
        if migrate is None:raise SystemExit(0)
        if migrate:args.migrate_from=str(Path(repo).resolve())
    if args.migrate_from:
        from conquest.profile_migration import migrate_legacy
        migrate_legacy(args.migrate_from,destination)
    os.environ['CONQUEST_DATA_ROOT']=str(destination)
    return args,destination


def select_profile(repo,args,destination):
    registry=ProfileRegistry(destination)
    machine_path=destination/'machine.json'
    machine=json.loads(machine_path.read_text()) if machine_path.exists() else {}
    selected=args.profile_id or machine.get('active_profile_id')
    if args.manage_profiles or not registry.profiles():
        from conquest.profile_editor import manage_profiles
        selected=manage_profiles(registry,selected)
        if selected is None:return None
    if not selected:
        profiles=registry.profiles()
        selected=next((p.id for p in profiles if p.role=='Farmer' and p.local_enabled),profiles[0].id)
    profile=registry.resolve(selected)
    if not profile.local_enabled:raise ValueError('Select a profile enabled on this PC')
    os.environ['CONQUEST_PROFILE_ID']=profile.id
    # The editor may have updated local installation locations.
    machine=json.loads(machine_path.read_text()) if machine_path.exists() else {}
    machine['active_profile_id']=profile.id;write_json(machine_path,machine)
    ctx=context_for(profile.id,destination)
    config=ctx.state_dir/'farmer.local.yaml'
    if not config.exists():
        import yaml
        base=yaml.safe_load((Path(repo)/'profiles/desktop-foreground.example.yaml').read_text())
        base['character']=profile.name
        # This is an engine configuration, not copied session observations.
        config.parent.mkdir(parents=True,exist_ok=True)
        config.write_text(yaml.safe_dump(base,sort_keys=False),encoding='utf-8')
    return config


def offline_edit_ready(root):
    """Unfinished receipts must not be orphaned by changing roles or trust."""
    import sqlite3
    terminal={'complete','completed','verified','aborted','cancelled','idle','skipped','operator_overridden'}
    for path in (Path(root)/'characters').glob('*/reports/banking/*.json'):
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('phase') and value['phase'] not in terminal:return False
    path=Path(root)/'machine-state/reports/merchants/journal.sqlite3'
    if not path.exists():return True
    with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True)) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'transactions' in tables and db.execute("SELECT 1 FROM transactions WHERE phase NOT IN ('verified','aborted','operator_overridden') LIMIT 1").fetchone():
            return False
        if 'delivery_reservations' in tables:
            for row in db.execute('SELECT state FROM delivery_reservations'):
                if json.loads(row[0]).get('phase') not in ('verified','cancelled','expired','operator_overridden'):return False
    return True
