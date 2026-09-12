"""Visible native app entry point; no console and no repeating elevation."""
from pathlib import Path
import os
import sys
import traceback

root = Path(__file__).resolve().parents[1]
os.chdir(root)
try:
    if '--check-imports' in sys.argv:
        from conquest.desktop_app import main
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
    from conquest.character_profiles import data_root
    output=data_root()/'diagnostics';output.mkdir(parents=True,exist_ok=True)
    (output/'desktop-startup-error.txt').write_text(traceback.format_exc())
    raise
