"""Start fingerprint-pinned, read-only workers for the two named merchants."""
import contextlib
import json
from pathlib import Path
import threading
import traceback

from conquest.client_wrapper import ClientCatalog
from conquest.memory import MemorySession
from conquest.memory_life import CLIENT_SHA256, read_life
from conquest.memory_health import HealthLayout
from conquest.win32 import WindowsBackend
from conquest.worker import Operations, serve
from types import SimpleNamespace
import yaml


def main():
    root = Path(__file__).resolve().parents[1]
    catalog = ClientCatalog(WindowsBackend(), image_path=r'C:\Program Files\Classic Conquer 2.0\bin\64\ImConquer.exe')
    health = HealthLayout.model_validate(yaml.safe_load((root/'profiles/classic-1074-health-candidate.yaml').read_text()))
    threads = []
    for name in ('Spiritual', 'Dutch'):
        matches = [w for w in catalog.windows() if w.title == f'[{name} - ClassicConquer]']
        if len(matches) != 1:
            raise ValueError(f'{name}: expected one client, found {len(matches)}')
        candidate = matches[0]
        with MemorySession(candidate.identity['pid'], CLIENT_SHA256) as session:
            operations = Operations(session, candidate.hwnd, read_only=True)
            adapter = SimpleNamespace(expected_sha256=CLIENT_SHA256, modules=session.modules,
                identity=session.identity, read=session.read, read_block=session.read,
                assert_identity=session.assert_identity, request=lambda op, body: operations.dispatch(op, body))
            life = read_life(adapter, health, name)
            print(json.dumps({'character': name, 'identity': session.identity, 'map': life.map_id}), flush=True)
        path = root/'.runtime'/f'merchant-diagnostic-{name.lower()}.json'
        thread = threading.Thread(target=serve, args=(candidate.identity['pid'], candidate.hwnd, CLIENT_SHA256, path),
            kwargs={'lifetime': 14400, 'read_only': True}, daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    (root/'reports').mkdir(exist_ok=True)
    with (root/'reports/merchant-diagnostics.log').open('w', encoding='utf-8') as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                main()
            except Exception:
                traceback.print_exc()
