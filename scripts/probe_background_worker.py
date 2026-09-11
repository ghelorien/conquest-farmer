"""One calibrated background click through an already-running input worker."""
import argparse
import json
from pathlib import Path

import yaml

from conquest.addressing import sample_player
from conquest.memory_health import HealthLayout
from conquest.worker import request


def probe(info, profile, character, point, size):
    health = request(info, 'health')
    if health.get('read_only') or health.get('input_revision', 0) < 7:
        raise ValueError('This worker cannot run the background click diagnostic; no input sent')
    layout = HealthLayout.model_validate(profile)
    before = sample_player(info, layout.player)
    if before['process_identity'] != health['target']:
        raise ValueError('Worker process changed before the probe')
    result = request(info, 'background-click', {
        'health_profile': layout.model_dump(), 'character': character,
        'point': point, 'expected_size': size})
    # Never retry after uncertain delivery. Record a failed after-sample with the
    # already-dispatched result so it cannot be mistaken for "no input sent".
    try:
        after = sample_player(info, layout.player)
        if after['process_identity'] != health['target']:
            raise ValueError('Worker process changed after the probe')
        result['player_after'] = after
    except (ValueError, OSError, KeyError, TypeError) as error:
        result['after_observation_error'] = str(error)
    result['player_before'] = before
    result['autonomous_actions_enabled'] = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-info', type=Path, required=True)
    parser.add_argument('--health-profile', type=Path, required=True)
    parser.add_argument('--character', required=True)
    parser.add_argument('--point', type=int, nargs=2, required=True)
    parser.add_argument('--expected-size', type=int, nargs=2, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = probe(args.worker_info, yaml.safe_load(args.health_profile.read_text()),
                       args.character, args.point, args.expected_size)
        status = 3
    except (ValueError, OSError, KeyError, TypeError) as error:
        result = {'qualified': False, 'autonomous_actions_enabled': False,
                  'error': str(error),
                  'note': 'Inspect the game before retrying; delivery may be uncertain.'}
        status = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
