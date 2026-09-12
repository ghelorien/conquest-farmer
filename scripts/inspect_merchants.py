"""Save read-only merchant observations; never grants an input qualification."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import yaml
from conquest.memory_health import HealthLayout,HealthWorkerSession
from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.memory import MerchantMemory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--character',choices=('Spiritual','Dutch'),required=True)
    parser.add_argument('--worker',type=Path,required=True,help='Existing read-only diagnostic connection')
    args = parser.parse_args()
    session = HealthWorkerSession(args.worker,CLIENT_SHA256)
    health = HealthLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-health-candidate.yaml').read_text()))
    observer = SimpleNamespace(adapter=session,health_layout=health,character=args.character)
    reader = MerchantMemory(observer)
    last = None
    for _ in range(3):
        try:
            snapshot = reader.read(max_seconds=15)
            directory = Path('reports/merchants');directory.mkdir(parents=True,exist_ok=True)
            (directory/f'{args.character.lower()}-read-validation.json').write_text(json.dumps(snapshot,indent=2))
            print(json.dumps({'character':args.character,'server':snapshot['server'],
                'inventory':len(snapshot['inventory']),'booth':len(snapshot['booth']),
                'trade':bool(snapshot['trade']),'incoming_request':snapshot['request'],
                'input_qualified':False}))
            return
        except (ValueError,OSError) as error:
            last = str(error)
    parser.exit(1,f'Read-only snapshot unavailable: {last}\n')


if __name__=='__main__':
    main()
