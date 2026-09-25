# Receipt-qualified foreground listing integration

The app observer loop now calls `refill_1078.step` for observed 1078 merchants.
It uses the existing persistent fifteen-minute refill schedule and the same
exact-item input engine as the authenticated one-shot listing command. Trading
and refill retain separate preferences. New merchant profiles default refill on;
existing explicit pause values are preserved.

This is a bounded integration, **not completed autonomous merchant operation**.
The engine requires the merchant's already-open owned booth. It consumes the
existing route controller's bounded safe parking grant, or requires a stopped
farmer in Market when no grant exists. It requests that handoff without changing
farmer controls; the existing route controller retains parking/resume ownership.
Each input boundary rechecks the grant, farmer identity, fresh HP/monster memory,
manual input and execution release. Under the exclusive lease it activates the
exact native merchant HWND through the existing verified-titlebar focus API,
then confirms actual foreground ownership. It cannot open the booth, log in,
travel or recover a booth. Trade remains separately live-qualified. No live qualification was
established by implementing this source or by synthetic tests.

## Live qualification and durable receipts

An authenticated `merchant-booth-list-once-1078` request retains the existing
exact request schema: character, unique `booth-list1078-` request ID, item UID,
full item fingerprint, total price, expected process identity, character UID and
owned booth UID. The engine independently rechecks the historical/restoration
price and highest-value priority, both merchants' fresh owned prices, native
model/GUI ownership, loaded renderer signature, field/button hover, farmer
safety, manual input, Stop and immutable control revision.

On exact verified inventory-to-booth transfer, the journal now retains both
fresh post-input ownership samples. In the same SQLite transaction it records
the verified receipt and `foreground_open_booth_listing_1078` evidence. Promotion
requires the new engine revision plus ordered native dialog/drag/price/confirm
trace. Static code pins, old qualification JSON, a closed popup, a saved boolean
or an old receipt without this complete evidence cannot qualify input.

The capability is restricted to that configured merchant, exact process
identity and owned booth. A process replacement or different booth invalidates
it. Each use validates the durable source receipt and digest. The status and
reconcile commands remain `merchant-booth-list-once-status-1078` and
`merchant-booth-list-once-reconcile-1078`, with character and request ID. There is
no separate endpoint that sets a qualified flag.

`merchant-booth-list-once-cancel-1078` takes exactly character and the existing
request ID. It can only cancel a still-open exact native price dialog when no
OK marker exists, the original process/UID/booth and complete ownership remain
unchanged, and the current amount is a prefix of the requested price. It uses
the pinned Cancel semantics and live native hover under a fresh safe lease.
An atomic `cancel_press` marker precedes mouse-down; after that marker the command
and ordinary reconcile endpoint are read-only. Two unchanged ownership samples,
closed modal and cleared native selected UID/buffer settle an aborted receipt.
It never resumes typing, submits OK, qualifies listing or repeats uncertain Cancel.

## Recurring behavior and remaining blockers

Once that restricted capability exists, due checks request the established route
handoff when needed and fill the already-open booth under verified native focus.
The route's saved handoff policy and explicit refill/Stop controls still apply.
Every item is re-evaluated
and known total prices sort descending; unknown/protected items remain queued.
The shared planner uses query-only price history, preserves exact prior booth
restoration prices and reobserves configured owned peers instead of dropping a
stale peer from the owned price floor. No fresh website scan or second discount
is required.

Each scheduled item gets a durable exact request before the worker starts. An
uncertain/prepared/submitted request is observed for settlement and never sent
again after restart. Verified receipts advance the refill cursor exactly once.
Interrupted checks retain their due time; only an observed completed/full/empty
check advances it. Legacy pending refill cursors require their existing separate
reconciliation before this scheduler can take ownership.

Scheduled input requires twenty seconds remaining in its grant, including a
second admission check after price/layout preflight. Full native stock/model/grid
checks remain at drag press and release. Intermediate drag movement checks the
current Stop/grant/identity and stable layout without traversing all inventory
items repeatedly. A short exhausted grant leaves the next item queued before
any new dialog is opened.

The normal merchant status includes the restricted qualification and a
`foreground_refill_1078` result. Examples of explicit blockers are
`listing_live_receipt_missing`, `legacy_refill_cursor_needs_reconciliation`,
`waiting_farmer_handoff`, `native_owner_surface_unavailable`,
`owned_booth_open_required`, and `listing_receipt_needs_reconciliation`.
Delivery `ready` requires its own live trade/request qualifications, fresh living
Market ownership, capacity and clear transaction/manual holds. Recovery and
booth reopening remain explicitly unavailable; listing evidence cannot unlock them.
# Listing-only input window

`merchant-booth-list-handoff-1078` is read-only admission. For cleanup, send
`character` and the exact unresolved `booth-list1078-...` request ID. For a
first listing, send the complete intended `merchant-booth-list-once-1078` body
with only its action changed to this admission action. It reobserves the exact
highest-priced eligible item. The returned `requested` key is a unique input
handoff key; it is separate from the immutable listing request ID.

Grant that key with `handoff-grant`, `scope: listing_1078`, `character`, current
parked-farmer `revision`, `safe: true`, and `expires_at` at most 45 seconds ahead.
Admission expires after 120 seconds and grant admission rechecks the original
item or unresolved cancellation receipt and fresh safe farmer memory. Use the
original listing request ID for listing/cancellation/status, and the returned
handoff key for `handoff-release`. Release must report `released: true` before
farmer input resumes. The scope permits only the named merchant's listing lease.

The route controller requests the same 45-second scope only for the exact
`merchant-refill:<character>:<timestamp>` whose merchant has a current genuine
listing receipt and refill enabled. Generic hunting remains 15 seconds and
Market visits remain bounded by their original 60-second deadline. Listing
input is bounded to 35 seconds with at least three seconds reserved before grant
expiry; scheduled items still require twenty seconds remaining before starting.
Queued inventory waits for the existing next fifteen-minute route check when
the window is exhausted; no extra recurring checker is created.

Town batch refill (`merchants/town_batch.py`, policy `town_batch_refill` in
`profiles/merchant-deliveries.json`): the refill engine publishes, read-only,
`eligible_backlog` (reliably priced items that fit free booth slots; unknown
prices never count). When a due hunting window finds any enabled, qualified
merchant at or above `backlog_threshold` (5) and no Stop, pause, manual,
transaction, recovery, delivery, town-visit or banking obligation, the farmer
uses the existing `require_city` parking (saved Phoenix terrain/anchor
191,249, checked travel, three quiet stable seconds) instead of field parking.
It then grants the same exact 45-second listing scope repeatedly, one at a
time, rechecking fresh farmer memory (identity, alive, city spot, no threat,
no damage, no manual input) before each grant and only reading memory while
a merchant holds input. It releases a grant as soon as its verified listing
leaves no time for another. The batch ends on an empty queue, `drain_below`,
`max_seconds` (600, from departure), two grants without a verified listing,
an unreconciled listing, any blocker, or unsafe farmer state; farming then
resumes through the normal hunt-return path and the next merchant window
waits `cooldown_seconds` (900). Failed city parking falls back to the
existing field window in the same interval. Events:
`merchant_town_batch_started`, `merchant_parking_finished` (`mode: city`),
`merchant_work_started` per grant, `merchant_town_batch_finished` (counts,
listings per merchant, backlogs, parking and elapsed seconds) and
`merchant_town_batch_refused` (rate-limited reasons).
