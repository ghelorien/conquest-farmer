"""Visible native app entry point; no console and no repeating elevation."""
from pathlib import Path
import os
import sys
import traceback

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
os.environ['PYTHONPATH']=str(root/'src')+os.pathsep+os.environ.get('PYTHONPATH','')
os.chdir(root)
startup = None
try:
    from conquest.legacy_startup import configure
    startup = configure(sys.argv[1:])
    sys.argv[1:] = startup.arguments
    if startup.legacy_root is not None:
        os.chdir(startup.legacy_root)
    if '--check-imports' in sys.argv:
        from conquest.desktop_app import main
    elif startup.legacy_root is not None:
        from conquest.desktop_app import main
        main()
    else:
        from conquest.profile_bootstrap import initialize, app_owner, select_profile
        options,destination=initialize(root,sys.argv[1:])
        with app_owner(destination):
            profile=select_profile(root,options,destination)
            if profile is not None:
                if '--profile' in sys.argv:
                    sys.argv[sys.argv.index('--profile')+1]=str(profile)
                else:sys.argv+=['--profile',str(profile)]
                from conquest.desktop_app import main
                main()
except Exception:
    if startup is not None and startup.legacy_root is not None:
        output=startup.legacy_root/'reports'
    elif os.environ.get('CONQUEST_LEGACY_DATA_ROOT') or any(
            arg.split('=',1)[0]=='--legacy-data-root' for arg in sys.argv):
        # An invalid namespace must never create managed storage or write into
        # the unvalidated requested destination.
        raise
    else:
        from conquest.character_profiles import data_root
        output=data_root()/'diagnostics'
    output.mkdir(parents=True,exist_ok=True)
    (output/'desktop-startup-error.txt').write_text(traceback.format_exc())
    raise
