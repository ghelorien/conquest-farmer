# Live-cycle takeover: 23 September 2026

The previous coding task is paused. Development is isolated on
`codex/reliable-1078-cycle`; the active installed controller is release
`2026.09.23-1078-cycle-r19`. Validation tests were deferred until the actual
transfer, refill and return to hunting completed; 512 selected checks now pass.

## What has actually worked

- Native farming recorded 890 verified kills in the first complete fifteen
  minutes (59.33/min). This does not establish the sustained 60/min goal.
- The farmer reached the Phoenix warehouse using the native travel path.
- Five carried Meteors were deposited with individual verified warehouse
  receipts, and silver was banked while retaining the configured reserve.
- Interrupted pre-confirmation merchant dialogs were canceled through the
  exact-request journaled path; unchanged ownership was verified.
- First listings succeeded for both merchants: Spiritual SpeedBow UID295079692
  at3,861,000 silver and Dutch OxhideBoots UID295190266 at6,930,000 silver.
- Both refills completed through the native engine: Spiritual has 32 listings
  and eight queued inventory items; Dutch has 19 listings and no remaining
  inventory. Refill must be reenabled after the current delivery cleanup;
  an earlier controller-close bug persisted Global Stop into both preferences.
- The farmer traveled to Market and withdrew the existing MeteorScroll
  UID296260049 with verified inventory and warehouse evidence.
- The first empty trade was canceled once through the memory-qualified native
  Close control. Both windows closed with unchanged ownership, and the manual
  fence cleared after five seconds of fresh stable observations. The incomplete
  historical observations remain preserved and their outcome remains unknown.
- A new actual transfer of MeteorScroll UID296260049 completed bilaterally.
  Receipt `8cd2b7f3311bbe8e457a96869088801c28fd11fcdd9c99817377209fca2ce44b`
  promoted normal Farmer delivery and Dutch trade capabilities.
- Dutch automatically listed the delivered scroll and now has 20 listings.
  Both independent fifteen-minute refill schedules are enabled.
- Normal route startup returned from Market to Phoenix, then to Bandit hunting.
  At the first post-return checkpoint, memory recorded 51 new verified kills.
  This is a live completion checkpoint, not a sustained throughput claim.
- The first complete five-minute interval after normal restart
  (`1790194375.5091681` through `1790194675.5091681`) contains 352 native
  `kill_verified` increments: 70.4/min including return travel. The read-only
  source is the Farmer's `reports/desktop-farming/trial.sqlite3` event journal.
  It does not establish a complete fifteen-minute rate or repeated town cycles.
- The next non-overlapping five-minute interval ends at `1790194975.5091681`
  and contains 393 verified increments (78.6/min). The combined ten-minute
  return/hunting sample is 745 kills, or 74.5/min. Earlier deployment and staged
  qualification downtime is outside these explicitly bounded intervals.

## Why the basic loop has not completed

The current 1078 client still reached several 1074-specific readers and input
qualification paths. Source changes now select build-aware readers for trade,
rich warehouse observation, protected withdrawal and trade preparation. These
changes do not substitute for actual transaction receipts.

The merchant listing path also treated transient renderer/worker observation
gaps as terminal failures. It now yields during bounded read retries, reports
the specific farmer safety failure, and retains immediate rejection for real
danger, changed identity, manual input or Stop. Confirmation still requires the
exact item, reliable price, owned booth, native control and foreground proof.

Focus recovery previously exited on Windows AttachThreadInput access denial,
before trying its caption fallback. The existing fallback only handled an
app-owned window. The new native fallback supports an exact, visible standalone
Conquest HWND and rechecks native HTCAPTION, WindowFromPoint, identity and input
ownership before clicking its title bar.

The earlier native window observation identified `LockScreenBackstopFrame`
covering the merchant caption. The user subsequently unlocked Windows. Native
focus now succeeds after the exact game window is temporarily raised above the
browser and its original topmost setting is restored after the title-bar click.

The next live drag opened the correct item dialog but exposed a structural
reader bug: 118 coherent samples showed the same 26 unique windows in two
alternating vector buffers with different capacities. Comparing the raw vector
header across a frame rejected unchanged window membership. The reader must
compare coherent pointer sets while retaining actual membership, geometry and
context checks; that fix is now running successfully.

Second-sample hover changes now use the same bounded exact-control reobservation
as an initial hover mismatch. Context changes still fail immediately. A safely
canceled listing used to leave refill blocked forever: the engine now clears
only an exact terminal cancellation with no confirmation and unchanged ownership,
then creates a new request from a fresh price plan. Dutch's final item completed
through this path; its canceled request was never replayed.

Measured successful listings take about seventeen seconds each. Guarded drag,
per-digit price entry and confirmation account for most of this, with a further
ten to fourteen seconds between receipts and the next request in these grants.

The background Farmer manual observer was still using an incomplete 1078
candidate schema, so it quarantined the bot's own trade. It now uses the same
canonical trade reader as the bilateral delivery path. A receipt-bound empty
trade cleanup closes this known failure without pretending the missing old
acceptance flag was observed.

Merchant confirmation then exposed another concrete integration bug: its
authorization guard directly accessed optional runtime window attributes which
were never initialized. Safe optional lookups now match the working request
acceptance path. The journal retained the successful Farmer confirmation, and
only Dutch's unsubmitted confirmation ran after the correction.

## Remaining work, in order

1. Continue observing ordinary autonomous banking/delivery trips. The first
   qualification transfer was explicitly staged; its actual receipt now enables
   the existing normal Dutch delivery path. Spiritual has listing/refill
   qualification but its separate trade capability has not been live-qualified.
2. Continue the existing native throughput/route comparison policy. Neither
   the earlier 59.33/min fifteen-minute sample nor a short new burst proves
   the sustained 60/min requirement.

At this checkpoint the farmer is On and hunting Bandits. Dutch operations are
On and Spiritual operations are Off; both refill preferences are On. Trade
windows are closed. The normal explicit restart consumed the helper-owned
`Deployment handoff r2` Stop marker. Real manual Stop and changed control
revisions must remain authoritative.

## Deferred checks, after the live return

- Banking, urgent banking, town visit and town-tail suites: 148 passed.
- Cycle validation, embedded bridge, focus recovery and owned window focus:
  76 passed across the four files after updating a stale safe-yield mock.
- The broader town/warehouse/delivery sample produced 284 passes and 49
  failures. Many fixtures omit existing required observer/build/bank fields or
  saved delivery permission; representative HEAD code has those same contracts.
  Three stable-NPC cases pass alone but fail in the combined run because of
  mock alias/order drift. The entire legacy suite is not green.
- No production guard was relaxed to satisfy an obsolete test fixture.
- Merchant listing, request targeting, confirmation and empty-trade cleanup:
  277 passed, including nine new optional-field and once-only input regressions.
- Manual Farmer observation: 11 passed after aligning the ordinary +2 sword
  assertion with the user's urgent-banking category rule. The selected groups
  total 512 passing checks; this is not a claim that the complete legacy suite
  passes. Production code was unchanged during these post-run fixture repairs.
