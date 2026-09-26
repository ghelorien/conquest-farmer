# Live operator helpers (this PC only)

Scripts used on 09-25 and 09-26 to deploy, park, inspect and test the live
bot. They are kept here so they are not lost; they are not part of the bot
and have no tests.

They hard-code this machine's paths, the farmer profile id and the client
process ids of that day, and they write their results next to themselves.
Copy them to `cf-release-src\.runtime` (gitignored) to use them. Scripts
that send input or change controls must run elevated, and only with the
user's go-ahead (see CLAUDE.md).

- Deploy and rollback: `deploy_stop.py`, `restart_release.py`, `start_r51.py`,
  `farming_on.py`, `deploy_cleanup.py`, `resume_cleanup.py`
- Park the farmer: `park_market.py`, `park_watch.py`, `park_city_cleanup.py`
- Supervised Market tests (movement only): `market_circuit.py`,
  `market_delivery_test.py`
- Merchant listing by the operator: `operator_listing.py`,
  `operator_list_all.py`, `listing_cancel.py`
- Toggles: `refill_toggle.py`, `delivery_toggle.py`, `farm_first.py`
- Read-only checks: `refill_health.py`, `refill_results.py`,
  `refill_preview.py`, `merchant_state.py`, `dutch_state.py`, `open_tx.py`,
  `journal_events.py`, `db_history.py`, `bag_left.py`, `listing_status.py`,
  `market_moves.py`, `delivery_spots.py`, `delivery_probe_read.py`,
  `ingress_check.py`, `farmer_*.py`, `crowd_dryrun.py`, `find_cmd.py`,
  `market_watch.py`, `market_watch2.py`
- Screenshot of the farmer client (read-only): `capture_farmer.ps1`
