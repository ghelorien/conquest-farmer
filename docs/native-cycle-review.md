# Native farming-cycle review

The observed stops came from inconsistent integration contracts between
farming, town recovery and merchant input. Completing one component did not
prove that the next component could accept its result.

| Observed failure | Consequence | Correction |
| --- | --- | --- |
| Market grant validation instantiated the legacy merchant reader for the1078 client. | A completed Meteor exchange stopped before delivery admission. | Use the build-aware source reader while retaining fresh identity/map/HP checks. |
| Restoration compared current stock with an old pre-disconnect snapshot without consuming verified sales evidence. | Spiritual treated its sold BambooBow as unexplained missing ownership and stopped refill. | Reconcile exact atomic sale receipts; inventory-to-booth-to-sale requires the native listing receipt too. |
| Recovery treated a pending refill request as input ownership. | Farmer and merchant waited on each other before either had an input grant. | Distinguish a qualified waiting request from an active owner or grant. |
| Stopped-Market refill accepted only some terminal route labels. | An exited worker recorded as needs_attention/failed still blocked refill. | Accept that exact terminal combination only after OS-confirmed process exit. |
| A generic15-second town grant fed a listing engine requiring20 seconds remaining. | A nominal refill window could never start a listing. | Use the earned exact-merchant45-second listing scope outside Market; preserve the original Market visit budget. |
| Safe parking searched cardinal paths without passing failed-tile avoidance into the search. | A later waypoint could cross a previously failed tile. | Use checked travel diagonals and recheck the entire final segment after actual-camera shortening (r24). |
| A restart saw stocked supplies after earlier work had partially succeeded. | Blindly restarting restock would repeat purchases; blindly returning would skip the unfinished cash tail. | Bind the original process and trip, resume verified storage/return, then finish only the unsubmitted tail with durable one-shot transaction evidence. |

The interrupted visit `a34ed8c7e6ce45449fccbd2e58734b7c` completed in r21:
six warehouse deposits, a verified2900-silver deposit, and a fresh five-kill
receipt after return. The first complete15-minute window from that return
recorded964 verified kills, including the subsequent deployment downtime.
These prove that recovery finished; they do not yet prove the next ordinary
automatic merchant delivery/refill cycle.

Two scheduled r22 hunting refill attempts subsequently deferred because the
short nearby-parking window did not produce a verified safe location. Neither
attempt granted input or listed an item. The pending adjustment gives earned
1078 refill requests a bounded30-second nearby-parking opportunity; it does
not relax clearance, authorize a forced town trip, or reset the native timer.

The r23 thirty-second attempt also deferred. Its diagnostics recorded three
successful moves, two stalled moves and three threats still nearby. r24 fixes
the independently reproduced path-avoidance defect; the old live trace does
not establish that this defect caused that particular timeout.

The user's attack/movement efficiency concern is supported by the event audit.
Slow five-minute windows made91 movement attempts but only59–64 attacks. Long
attack gaps included positive target observations, so empty-target counts do
not establish a patrol problem. Source review found expensive ground-item and
ownership work between the target scan and the0.35-second attack freshness
guard. The current correction moves valuable-loot work before final combat
observation, retaining loot priority and fresh-memory input guards. Keep the
patrol unchanged while measuring this execution correction; do not present
configured cooldowns or short bursts as sustained throughput evidence.

The next engineering priorities after the live goal is proven are:

1. Keep one explicit contract for request, grant, active owner and release.
   A waiting request must never become an implicit ownership hold.
2. Make each operation's minimum required time part of admission, so the
   scheduler cannot create a window in which the operation cannot start.
3. Record original process identity and completed transaction boundaries at
   the start of every trip. Recovery should consume those records instead of
   reconstructing missing provenance from historical failures.
4. Keep current status fields separate from historical error details. Several
   successful movement/hunting updates retained stale error text, complicating
   diagnosis even while the native loop was progressing.
5. Keep focused integration checks for1078 readers, sale reconciliation,
   partial-trip restart, timer admission and manual input fences. Broader
   refactoring should wait until a naturally triggered delivery/refill/return
   has produced native receipts.

Current deployment and completion evidence are tracked in
`docs/native-cycle-goal.md`. Do not count a warehouse fallback, attempted
refill, forced trip or manually advanced trade as full-loop completion.
