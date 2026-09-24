# Farmer and merchant rollout — September 12, 2026

This rollout is **in progress**. Warehouse storage remains the active destination.
Do not enable `profiles/merchant-deliveries.json` merely because unit tests pass.

## Publication checkpoint

The older process IDs, missing-credential observations and running-monitor
statements below are historical rollout notes, not instructions to start or
resume those processes on another PC. The original observation and heartbeat
have finished. Both merchants subsequently logged in successfully and were
taken to Market manually. See [merchant identity validation](merchant-identity-validation.md)
for the current identity-reader fixes and remaining live gates. Credentials,
local qualifications and runtime evidence are intentionally absent from GitHub.
Unattended merchant deliveries remain disabled in this source snapshot.

Each farmer tab has a **Transfer loot to merchants** checkbox beneath the farming
controls. It is saved per character across app restarts. Off prevents new merchant
delivery work and keeps warehouse fallback; it does not change merchant selling
or refill permissions. Previously submitted transfers still require reconciliation.
On permits qualified deliveries but does not bypass the rollout or input checks.

A supervised Market transfer to Spiritual subsequently completed with 12 eligible
valuables. The exact offer and both final inventories reconciled with no currency
transfer. This exposed and fixed the native confirmed-zero gold field (`0 ✔`)
reader and asynchronous placement observation. Staged probes retain their journal
before input and never repeat an uncertain drag or confirmation. Qualification
for Dutch, interrupted transfers and the unattended delivery cycle is still pending.

## Running validation

The original two-hour observation starts at Unix time 1789228367.7013636.
Its deadline is 1789235567.7013636. Keep this start, all initial Market failures,
all reloads, and normal travel/storage downtime in the denominator.

`scripts/monitor_unified_validation.py` is already running independently of the
AI and never sends control input. Its output is
`reports/performance/unified-validation-status.json`. Do not launch a duplicate.
Rebuild the detailed report with `scripts/report_unified_validation.py`; it reads
original verified kill events, jump/Scatter timing, stalls and route receipts.
The observation deadline ends measurement, never farming.

The task heartbeat `farmer-merchant-rollout-validation` checks every 15 minutes.
When the observation completes, rebuild the detailed report, report once, and
pause that heartbeat. Keep the pre-existing area efficiency heartbeat paused
unless separately requested. Do not force an unnecessary shopping trip to
manufacture a full-cycle result.

## Implemented and tested

- Farmer tab layering and selection preserve the embedding and route controls.
  The sidebar scrolls at smaller heights and keeps a fixed 500-pixel outer width
  while embedded. Full-size layout was visually inspected with user permission;
  smaller layouts, tab transitions and stable width have Tk regression coverage.
- The existing safe reload was exercised; the client returned to Bandits alive.
- Market startup now uses the verified return trip before attempting town setup.
  A journal records submission before choosing travel options. An empty NPC
  dialogue is retryable observation rather than a corrupt-deque failure.
- All ten Dragonball-related definitions were read twice from the live pinned
  client's definition table. Exact IDs and provenance are in
  `profiles/valuable-items.json`; the bounded reader was `item_definitions.py`
  (1074-pinned; removed after `r38-baseline`).
  Shared classification covers pickup, protection, urgent storage, labels and
  notifications. Seven star variants and EpicDragonBall are storage-only and
  excluded from merchant listing, repricing, restoration and incoming refill.
- Native merchant refill intervals, countdown deadlines and documentation now
  use 900 seconds. Legacy schedules migrate once, restarts retain their deadline,
  and a completed check schedules from its actual completion without catch-up.
- A gated native farmer handoff reserves at most one hunting work window per
  15 minutes. It searches locally for a memory-verified safe spot, defers when
  unsafe, stops starting work after 15 seconds, and releases merchant input
  before restoring prior farmer intent. Manual Stop/F11 and input ownership win.
  Required town visits can request an immediate capacity/refill check.
- Delivery selection and a transaction core are implemented and unit-tested in
  `merchants/delivery.py`: capacity first, verified distance for ties, split
  batches, supplies/gear retention, no rare-star refill, exact two-sided offers,
  zero currency, durable pre-confirmation journal and two-inventory receipts.
  Changed or uncertain results do not authorize automatic retry.
- Tests use a separate input lock and cannot contend with the running farmer.
- Merchant delivery reservations now hold inventory/listings until the complete
  batch is verified. The authenticated bridge reads both clients itself and
  accepts exact UIDs, never caller-supplied snapshots. Per-request receipts and
  the active hold persist atomically; old retries cannot reopen a completed
  trade or replace a newer reservation. Farmer confirmation is preceded by its
  submitted journal and the receiver's complete-offer acknowledgement. Read-only
  recovery reconciles both inventories and retries only the release receipt.
- Optional inventory cleanup requires fresh, clear nearby monster memory and
  backs off repeated failures up to five minutes. It does not delay valuable
  ground pickups; pending panel closure still takes priority. This change was
  safely loaded in app PID 1244880, which returned to Bandits alive.
- The final restock inventory-space check now follows storage, so a full bag of
  protected loot can reach the warehouse. A completely full bag also gets
  verified warehouse storage before purchases, then returns to the same shop.
  These changes are loaded in app PID 1257496.
- The detailed validation report now requires an ordered natural restock,
  verified warehouse receipt, hunting-area arrival and subsequent verified kill
  before claiming a complete cycle. Unrelated event totals are insufficient.

The native farmer input adapter and asynchronous bridge commands are now
implemented; see `farmer-native-delivery.md` for the qualification contract.
They are loaded but remain unqualified and disabled. The source-side pending
journal blocks reload, and duplicate requests recover without resending items.

The late-run slowdown met the reassessment threshold: two non-overlapping low
five-minute windows and a fifteen-minute rate below 40/min. Diagnosis found that
qualified HP was discarded before region occupancy decisions. The correction
retains it for chase/escape/rotation, and is loaded in PID 1257496. Measurements
are in `reports/performance/rotation-hp-validation.json`; the original two-hour
start and all downtime remain unchanged. Do not attribute all variation to this
fix or treat the short recovery window as a qualified route winner.

Latest full suite: 1887 passed, including the input-purpose and delivery-window recovery regressions. The repeated bridge authentication failure was
fixed by draining bounded rejected request bodies before closing the connection;
a delayed-body regression test proves clean rejection without dispatch. A later
recoverable-wait error handling change passed 15 targeted bridge/integration
tests and is source-only until the next safe update. Merchant live qualification
is still required; these tests do not establish real transfer success.

## Remaining gates and implementation work

**Merchant transfers remain unqualified and disabled.** Market storage stages
now call the native delivery protocol in source before warehouse fallback, with
terrain-based selection, fresh capacity rechecks, split deliveries and durable
restart reconciliation. Consolidated scrolls can satisfy storage through a
verified merchant receipt. Required restock visits can now start a journaled
Market delivery round trip after shopping and consolidation, using detailed
memory preflight and preserved transport funds. Verified trades can use only
the original grant's remaining time for refill, without repricing or recovery
input. Qualify the adapter and both merchant transfers before enabling it.
Preserve silver banking and all storage-only items during that work.

Neither merchant credential file existed at the latest check, and neither
merchant had a verified connected client. A request is pending for the user to
save Spiritual and Dutch credentials through **More / help → Set up automatic
login → Save locally**. Never print the passwords or encrypted blobs. The
farmer's existing encrypted login remains in place.

Live farmer/merchant reconnect, Conductress path, merchant booth restoration,
lower-value transfers to both merchants, interrupted transfers and the enabled
15-minute handoff comparison remain unqualified. The original two-hour window
did not include a natural restock cycle. The separately recorded extension now
includes two verified natural restock/storage/return cycles.
Live tab/input/resize testing beyond the inspected full-size layout remains to
be completed safely. Unit tests are not substitutes for those observations.

Enable unattended delivery only after these gates pass. If every safe storage
destination is full, preserve the existing safe-stop/disconnect notification.

## Completed baseline observation

The original 7,200-second window recorded 7,247 verified kills (60.39/minute),
with all downtime retained. Minimum observed HP was 55.3%; no death/revive event
was recorded. Across 1,490 paired jump/Scatter observations, median arrival-to-
attack-input delay was 0.207 seconds and p95 was 0.796 seconds. Unpaired jumps
alone do not establish a missed attack opportunity. The validation heartbeat
is paused; farming remains independent and active. The full natural restock
cycle was verified in the extension; performance with enabled merchant work
remains unverified.

The extended observation preserves the original start and adds all subsequent
downtime, without modifying the original report or deadline. At 9,795.6 seconds
it recorded 10,771 verified kills (65.97/minute), including two natural storage
cycles. The latest cycle restocked to 10,000 arrows and five potions, stored two
Meteors, deposited 5,282 silver, retained 200 for transport and resumed verified
kills after about 90 seconds. See `reports/performance/unified-validation-extended-report.json`.

Safe reload was deferred in app PID 1257496 because its cached storage module
lacked a helper imported by updated route code. Source now uses stable symbols
and has a regression test for that mixed-version case. The running app remains
on its previous modules; the next replacement must still use a verified safe
handoff. Do not treat the newly wired merchant features as loaded or enabled.
The read-only Farmer-tab layout inspection confirmed visible routes, pickup
history, monster groups and statistics without overlap. No gameplay choices
were made from that inspection; live input/tab/resize qualification remains.

The next source update also checks merchant operation permissions before focus
preparation, restricts dedicated windows to their trade/refill purpose, and
binds refill permission to its executing thread. Regression tests cover stale
listing plans, denied-focus callbacks, unrelated-thread access, repeated window
requests and scheduler-write failures. These safeguards are not loaded in the
current administrator app and do not qualify live merchant delivery.
