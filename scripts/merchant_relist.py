"""Collect once, or run the deterministic 12-hour worker after live qualification."""
import argparse
from pathlib import Path
import time

from conquest.discord_notify import write_json
from conquest.character_context import state_path
from conquest.merchants.collector import collect_market
from conquest.merchants.relist import run_cycle
from conquest.merchants.rollout import verify_rollout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch',action='store_true',help='Run until stopped; requires verified live rollout. Default only collects prices.')
    args = parser.parse_args()
    # One standalone collector/scheduler. The existing Codex task stays paused.
    lock_path = Path(state_path('.runtime/merchant-relist.lock'))
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    import msvcrt
    with lock_path.open('a+b') as lock:
        lock.seek(0,2)
        if lock.tell()==0:
            lock.write(b'0');lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            parser.exit(1,'A scripted market worker is already running.\n')
        try:
            if not args.watch:
                data = collect_market()
                print(f'Collected {data["total"]} America listings. No game operation requested.')
                return
            verify_rollout()
            while True:
                try:
                    result = run_cycle()
                    write_json(state_path('reports/merchants/script-worker.json'),{'checked_at':time.time(),**result})
                    delay = 60
                except (OSError,ValueError):
                    # Back off; do not retry the website every second or expose session details.
                    write_json(state_path('reports/merchants/script-worker.json'),{'checked_at':time.time(),
                        'error':'Scan deferred: check website access, app connection and live qualification.',
                        'next_attempt':time.time()+900})
                    delay = 900
                time.sleep(delay)
        except KeyboardInterrupt:
            print('Scripted market worker stopped.')
        except (OSError,ValueError) as error:
            parser.exit(1,str(error)+'\n')


if __name__=='__main__':
    main()
