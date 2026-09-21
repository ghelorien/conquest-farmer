"""Run a saved route without an AI connection. F12 stops the complete loop."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).absolute().parent))
from _bootstrap import activate
activate(__file__)
from conquest.overnight import OvernightLoop

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--route',default='turtledove')
    parser.add_argument('--hours',type=float,default=None,help='Optional explicit time limit; otherwise run until stopped')
    parser.add_argument('--first-hunt-seconds',type=int)
    args = parser.parse_args()
    from conquest.route_controller import controller_guard
    with controller_guard() as acquired:
        if acquired:
            OvernightLoop(args.route,args.hours,first_hunt_seconds=args.first_hunt_seconds).run()
