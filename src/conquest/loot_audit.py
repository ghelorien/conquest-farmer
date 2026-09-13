"""Durable pickup decisions, written by the UI consumer outside combat input."""
from datetime import datetime,timezone
import json
from pathlib import Path
import time


def append(output,event,fields,*,now=None):
    if not (event.startswith(('memory_loot_','memory_pickup_'))
            or event in ('paused','resumed')):
        return False
    now=time.time() if now is None else now
    day=datetime.fromtimestamp(now,timezone.utc).strftime('%Y-%m-%d')
    directory=Path(output)/'loot-decisions'
    row={'recorded_at':now,'event':event,'fields':fields}
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/(day+'.jsonl')).open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(row,separators=(',',':'))+'\n')
    return True
