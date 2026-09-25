"""Request a durable scan from the running unified desktop app."""

import argparse
import json
import time
import uuid
from conquest.merchants.bridge import request
from conquest.character_context import state_path
from conquest.merchants.market import import_snapshot, browser_pages


def merchant_argument(value, registry=None):
    """Resolve a merchant from this PC's local profiles, never a built-in list."""
    from conquest.character_profiles import ProfileRegistry

    try:
        profile = (registry or ProfileRegistry()).resolve(
            value, role="Merchant", server="America"
        )
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    if not profile.local_enabled:
        raise argparse.ArgumentTypeError("This merchant is not enabled on this PC")
    return profile.name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshot", help="Complete, fresh browser-collected America market JSON"
    )
    parser.add_argument(
        "--browser-pages", help="Raw visible browser table pages to validate and import"
    )
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--status", action="store_true")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Require verified live rollout receipts",
    )
    parser.add_argument(
        "--list-once",
        action="store_true",
        help="List/reprice eligible stock once and automatically pause both merchants",
    )
    parser.add_argument(
        "--verify-booth",
        type=merchant_argument,
        metavar="MERCHANT",
        help="Embed one merchant and verify price entry/cancellation without submitting a listing",
    )
    args = parser.parse_args()
    if args.verify_booth:
        if (
            args.list_once
            or args.scheduled
            or args.status
            or args.snapshot
            or args.browser_pages
            or args.request_id
        ):
            parser.error("--verify-booth must be used alone")
        try:
            print(
                json.dumps(
                    request({"action": "verify-booth", "character": args.verify_booth}),
                    indent=2,
                )
            )
        except (OSError, ValueError):
            parser.exit(
                1, "Open the updated merchant app before verifying booth controls.\n"
            )
        return
    if args.list_once and (args.scheduled or args.status):
        parser.error("--list-once is separate from --scheduled and --status")
    if args.scheduled:
        from conquest.merchants.rollout import verify_rollout

        try:
            verify_rollout()
        except ValueError as error:
            parser.exit(1, str(error) + "\n")
    if args.browser_pages:
        from pathlib import Path
        from conquest.discord_notify import write_json

        definitions = json.loads(
            Path(r"C:\Program Files\Classic Conquer 2.0\ini\itemtype.json").read_text(
                encoding="utf-8"
            )
        )
        data = browser_pages(
            json.loads(Path(args.browser_pages).read_text(encoding="utf-8")),
            definitions,
        )
        write_json(state_path("reports/merchants/market.json"), data)
    if args.snapshot:
        import_snapshot(args.snapshot)
    body = (
        {"action": "status"}
        if args.status
        else {
            "action": "scan",
            "request_id": args.request_id or f"12h:{int(time.time() // 43200)}",
        }
    )
    if args.list_once:
        body = {
            "action": "list-once",
            "request_id": args.request_id or f"once:{uuid.uuid4().hex}",
        }
    try:
        print(json.dumps(request(body), indent=2))
    except (ValueError, OSError):
        parser.exit(
            1,
            "Merchant app unavailable or request rejected. Open the unified app and check its status.\n",
        )


if __name__ == "__main__":
    main()
