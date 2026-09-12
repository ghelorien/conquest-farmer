"""Post one idempotent four-hour sales summary to Discord #shops."""
import argparse
import json
from conquest.merchants.sales_report import send_report

if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview',action='store_true')
    args = parser.parse_args()
    print(json.dumps(send_report(preview=args.preview),indent=2))
