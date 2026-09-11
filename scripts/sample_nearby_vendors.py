"""Read current vendor IDs without capturing images or sending input."""
import json
from pathlib import Path
from conquest.worker import request


if __name__ == '__main__':
    state = json.loads(Path('reports/desktop-farming/app-state.json').read_text())
    report = request(state['worker_info_path'], 'sample-npcs')
    Path('reports/nearby-vendors.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
