"""Start fingerprint-pinned, read-only workers for the two named merchants."""
import contextlib
import json
from pathlib import Path
if __name__ == '__main__':
    from _bootstrap import activate
    activate(__file__)
from conquest.character_context import state_path
from conquest.merchants.journal import CHARACTERS
import threading
import traceback

from conquest.client_wrapper import ClientCatalog, ClientWindow
from conquest.memory import MemorySession
from conquest.memory_life import CLIENT_SHA256, read_life
from conquest.memory_health import HealthLayout
from conquest.win32 import WindowsBackend
from conquest.worker import Operations, serve
from types import SimpleNamespace
import yaml


def merchant_candidates(catalog):
    """Read-only diagnostics must also see hidden embedded game surfaces."""
    windows=[]
    for identity in catalog.identities():
        for window in catalog.backend.windows(identity['pid']):
            size=window.get('client_size') or [0,0]
            if size[0]>=200 and size[1]>=150:
                windows.append(ClientWindow(identity,window['hwnd'],window['title']))
    result=[]
    for name in CHARACTERS:
        matches=[w for w in windows if w.title==f'[{name} - ClassicConquer]']
        if len(matches)!=1:
            raise ValueError(f'{name}: expected one client, found {len(matches)}')
        result.append((name,matches[0]))
    return result


def main():
    root = Path(__file__).resolve().parents[1]
    catalog = ClientCatalog(WindowsBackend())
    health = HealthLayout.model_validate(yaml.safe_load((root/'profiles/classic-1074-health-candidate.yaml').read_text()))
    candidates=merchant_candidates(catalog)
    # Validate both accounts before starting either server. A missing second
    # account must not terminate an already-started daemon and orphan its file.
    for name,candidate in candidates:
        with MemorySession(candidate.identity['pid'], CLIENT_SHA256) as session:
            operations = Operations(session, candidate.hwnd, read_only=True)
            adapter = SimpleNamespace(expected_sha256=CLIENT_SHA256, modules=session.modules,
                identity=session.identity, read=session.read, read_block=session.read,
                assert_identity=session.assert_identity, request=lambda op, body: operations.dispatch(op, body))
            life = read_life(adapter, health, name)
            print(json.dumps({'character': name, 'identity': session.identity, 'map': life.map_id}), flush=True)
        path=root/Path(state_path(f'.runtime/merchant-diagnostic-{name.lower()}.json'))
        if path.exists():raise ValueError('Diagnostic connection file already exists; check its worker first')
    threads = []
    for name,candidate in candidates:
        path = root/Path(state_path(f'.runtime/merchant-diagnostic-{name.lower()}.json'))
        thread = threading.Thread(target=serve, args=(candidate.identity['pid'], candidate.hwnd, CLIENT_SHA256, path),
            kwargs={'lifetime': 14400, 'read_only': True}, daemon=False)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    Path(state_path('reports/merchants')).mkdir(parents=True,exist_ok=True)
    with (Path(state_path('reports/merchants/diagnostics.log'))).open('w', encoding='utf-8') as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                main()
            except Exception:
                traceback.print_exc()
