"""Script-only scan scheduling. The running app still owns all game input."""
from conquest.character_context import state_path
import json
from pathlib import Path
import time

from conquest.merchants.bridge import request
from conquest.merchants.collector import collect_market
from conquest.merchants.journal import CHARACTERS
from conquest.merchants.market import MarketSnapshot
from conquest.merchants.rollout import verify_rollout


def run_cycle(*, now=None, bridge=request, collect=collect_market, verify=verify_rollout,
              market_path=state_path('reports/merchants/market.json')):
    """Request only due, enabled merchants; never resume manual pause or one-time work."""
    now = time.time() if now is None else now
    verify()
    statuses = bridge({'action':'status'})['characters']
    due = []
    for character in CHARACTERS:
        state = statuses[character]
        scan = state.get('scan',{})
        if state.get('enabled') is not True or scan.get('one_time') and scan.get('pending'):
            continue
        if scan.get('pending') or (scan.get('next_scan') or 0)<=now:
            due.append(character)
    if not due:
        return {'requested':[],'collected':False}
    collected = False
    try:
        MarketSnapshot(json.loads(Path(market_path).read_text(encoding='utf-8')),now=now,max_age=120)
    except (OSError,ValueError):
        data = collect(destination=market_path)
        MarketSnapshot(data)  # Recheck before requesting any game operation.
        collected = True
    # A pause during collection must not be undone. Duplicate IDs remain durable.
    current = bridge({'action':'status'})['characters']
    requested = []
    for character in due:
        state = current[character]
        if state.get('enabled') is not True or state.get('scan',{}).get('one_time') and state['scan'].get('pending'):
            continue
        bridge({'action':'scan','character':character,'request_id':f'12h:{int(now//43200)}'})
        requested.append(character)
    return {'requested':requested,'collected':collected}
