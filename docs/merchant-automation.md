# Unified Conquest merchant automation

Farmer integration baseline: private repository commit
`117cc08d9ed8d418ae5b483f49196b143322a2a0` (September 12 update).
Merchant development initially used `1b4d916`; the combined source now includes
the newer farmer's embedding, viewport, ammunition and recovery changes.
The existing desktop entry point now adds Overview, Farmer, Spiritual and Dutch
tabs. Farmer configuration, travel routes, loot policy and delivery timing stay
with the existing farmer. Nothing here starts a route or changes Farming On.

## Current rollout state

### September 20: operator delivery controls

In the Farmer controls, **Automatic merchant delivery** is the real On/Off
switch for normal loot deliveries. It is saved locally for that farmer and
survives app restart. It does not start farming, enable merchant trading, or
change the separate automatic-refill setting. Packaged rollout remains Off
unless an operator enables this switch (or a separately armed live acceptance
run authorizes its exact item). Enabling it is operator permission, not a claim
that repeatability or another PC's trade controls have been validated.

On permits the native route to deliver eligible unbound +1/Super equipment and
MeteorScrolls to a qualified owned merchant. Loose Meteors stay bank-only;
Dragonballs and +2-or-higher equipment keep urgent-banking priority. Recipient
identity, capacity, manual holds, Stop and transaction reconciliation still apply.
Off prevents further delivery submissions; it never erases a submitted trade or
its reconciliation history. Unknown prices leave received items queued.

**Clear stale handoff…**, under Recovery holds, previews an expired reservation
that never admitted a trade. Stop farming first. Confirmation is bound to the
exact preview, with fresh ownership and closed-window checks. Clearing keeps an
immutable audit and leaves farming Off; it cannot clear an admitted, submitted,
uncertain or manually held transaction. If evidence changes, recheck rather than
forcing it. The action clears a reservation, not the item or delivery history.

Latest live evidence is **two consecutive complete loops on release AA**, followed
by an unsubmitted stale-handoff failure on the third. The toggle/clear update is
not evidence of three completed loops. Spiritual's historical sales-baseline
problem and live proof of the five-second unknown-request cancellation remain
separate limitations. No unit or regression suite was run for this control update.

The pre-merge Windows suite passed with **1,650 tests**, including existing farmer
regressions, shared input guards, merchant isolation, native UI integration and
sales accounting. Final integration validation is recorded in
[farmer integration parity](farmer-tab-merge-parity.md). Farmer profiles, routes,
combat and loot policy are preserved from the updated farmer baseline. Automated
tests do not qualify the remaining live gameplay scenarios below.

Both merchants have completed foreground listing/repricing batches on the
installed client. The app contains native 12-hour pricing, fifteen-minute refill,
four-hour Discord sales reporting, current booth value and net sales totals.
Saved operation pauses, refill pauses and Global Stop remain authoritative;
installing this update does not start farming or enable merchant operations.
The pinned client is
`c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396`.
Incoming Parasite trades and reconnect/market-return behavior still require
their own live qualification; implemented readers are not proof of a completed
trade or recovery. A different installation needs its own identity, geometry,
credentials and memory-layout checks.

The experimental background backend failed interrupted-drag qualification and
rejects live input modes. Production uses foreground input only. See
[the capability matrix](background-input-proof.md) for the failed and untested
controls and the unresolved item incident. This release does not enable
simultaneous focus-independent farmers. See [sales accounting](merchant-sales-accounting.md)
for the net-proceeds fix and audited historical reconciliation.

The diagnostic reader is not proof that clicking or reconnecting is qualified.
The controls below deliberately have no default screen coordinates. Unknown
geometry, changed client builds, missing qualifications and ambiguous receipts
stop the affected operation. No screenshots, OCR, injection, packet replay or
process-memory writes are used for merchant decisions.

Launch the app with the existing shortcut or:

```powershell
.venv\Scripts\pythonw.exe scripts/start_desktop_app.py
```

Reading elevated game clients requires running this app as administrator. Only
one merchant app may own the localhost bridge. A second instance cannot start
another merchant controller. The tabs display independent observations even
while merchant input is paused. The Overview includes the farmer's activity;
each merchant has Inventory, Booth, Comparisons, Trades, Price history, Deferred
and Logs views. Pause/Resume, Scan now, Retry reconnect, Configure login and
Global stop are available. Closing or manually stopping the app cancels input.

## Local configuration and records

Configure each merchant's login using **Configure login** on its own tab. Files
are Windows DPAPI encrypted under `.runtime/merchants/spiritual/account.dpapi`
and `.runtime/merchants/dutch/account.dpapi`. The farmer's `.runtime/account.dpapi`
is unchanged. Passwords never go through the merchant bridge or event journal.
The farmer's `.runtime/discord-webhook.dpapi` and notification policy remain.
Shop sales and alerts use the separate encrypted
`.runtime/merchants/shops-webhook.dpapi` configured locally for Discord #shops.
No webhook or password belongs in a profile, log, task prompt or Git commit.

Durable state lives in `reports/merchants/journal.sqlite3`: character controls,
pending scans, inventory queues, transaction intents, receipts and character-tagged
events. SQLite transactions use WAL and full synchronous writes. An interrupted
intent prevents another transaction until its result can be proved. Do not erase
an uncertain intent to force a retry. The UI's pause state and retry budget survive
restarts; Retry reconnect resets the budget without resuming a paused character.

The notifier appends merchant delivery receipts, scan summaries, failures that
persist for 60 seconds and memory-verified recovery messages to its existing
queue. Cursor and queue are persisted together before delivery. Discord transport
retains the existing retry behavior (a transport crash after Discord accepted a
message but before local acknowledgement can still cause a repeated message).
Farmer notification behavior is unchanged.

## Foreground handoffs and farmer bridge integration

An OS file lock protects the entire input action, including focus, pointer
movement, press and release. It also covers existing farmer foreground helpers
in separate route-controller processes. Merchant leases require the farmer to
be stopped or to explicitly acknowledge a safe handoff. They never stop combat
or select a safe location themselves. Physical mouse input, manual character
Pause and Global stop revoke further automated input; releases of held automated
keys/buttons remain permitted.

The authenticated merchant bridge binds only `127.0.0.1`. Its connection metadata
is `.runtime/merchants/bridge.json`. Use the client helper without printing its
token:

```python
from conquest.merchants.bridge import request
status = request({'action': 'status'})
request({'action': 'scan', 'request_id': '12h:YOUR_STABLE_BUCKET'})
events = request({'action': 'receipts', 'after': 0})
```

`status.characters` exposes connection, verified capacity, readiness, pending
transactions, scan timestamps and qualification. `handoff_requested` identifies
pending merchant foreground work. The farmer may acknowledge that request only
after its own controller has stopped issuing input at a safe location:

```python
# The farmer owns this decision and must quiesce its own input first.
request({'action': 'handoff-grant', 'request_id': request_id,
         'revision': current_farmer_control_revision, 'safe': True,
         'expires_at': time.time() + 30})
# Revoke the grant; wait until released is true before resuming farmer input.
request({'action': 'handoff-release', 'request_id': request_id})
```

Grants expire after at most 30 seconds and are invalidated by a farmer control
revision change. No handoff grant changes the farmer's intent. The API also
accepts `handoff-request` with a request ID for farmer-initiated coordination.
The farmer can use readiness and capacity to choose its delivery timing and use
the durable delivery event cursor for receipts; its route logic is not modified.

## Trades, pricing and market collection

For the requested one-time rollout, use **List both once, then pause** on Overview
or **List once** on a merchant tab. The equivalent authenticated command is
`scripts/merchant_scan.py --list-once --browser-pages <fresh-json>`.
This lists eligible inventory and reprices comparable existing listings once,
then atomically pauses the character with completion. It does not accept incoming
trades during the batch and does not enable the 12-hour task. Duplicate request
IDs never resume completed or manually paused work. All live input qualification
and fresh-price requirements still apply.

Only incoming requests naming exact `Parasite` may open a trade. The accepted
request is associated with the merchant process identity and participant UID;
an independently opened or outgoing trade is not accepted. Require zero
merchant-side items and silver, enough physical inventory slots, known tradable
items and the same offer on consecutive observations immediately before input.
Persist intent before confirmation, then verify item identities/attributes and
silver in inventory with the trade closed before recording success. Uncertainty
pauses input for that character. Receipt reconciliation never clicks Retry.

Each outside seller contributes its lowest comparable unit price. One comparable
seller is enough to price an item. With four or more outside sellers, each outlier
is tested against the median of at least three **other** sellers. Reject prices
strictly below half that median; equality is retained. Use 99% of the lowest
remaining price, round down to whole silver, and allow both increases and
decreases. Equipment compares by market subtype, quality group, plus and both socket
contents across item names/levels. Other products compare exact names, verified
quality, equivalent quantities and silver currency. Missing attributes, unknown
quantities still defer the item. Every inventory item is evaluated on each scan.
Spiritual and Dutch are treated as one owner: when their lowest comparable unit
price is at or below the lowest valid outside offer, match that owned price
without a further 1% discount. This includes ties and holds their shared lowest
price steady on repeat scans. If an outside seller is cheaper, both use the same
99% outside-price target. Fresh verified booth observations (at most five seconds
old) replace that character's website offers; otherwise the fresh market data
is used. Equal quantities match exactly; unequal quantities round the matching
total upward to whole silver so the unit price never undercuts the owned floor.
Owned sellers never count toward outside-seller outlier medians, and an owned
listing alone is sufficient to match its price. Highest-total-value listing
priority remains unchanged.

If there is no live exact comparable, use the last observed exact comparable unit
price without another discount. If neither exists for +2 equipment, use three
times the equivalent +1 live unit price, or its historical value when no live +1
exists. On + items, Fixed, Normal, Refined, Unique and Elite form one quality group at the
user's request; Super stays separate. Unplussed items still match exact
quality. Type and both sockets remain identical: unsocketed, one-socket and
two-socket items never cross groups, and gem contents also match. There is no
extrapolation to other plus levels. These computed fallback totals
are rounded up to whole silver. Example: a +1 value of 5M gives +2 a value of 15M.
The prices the user mentioned for archer hats and backswords were illustrations,
not fixed reference values or overrides.

The collector and runtime preserve validated observations in
`reports/merchants/price-history.sqlite3`, shared by both merchants. History stores
the original observation timestamp and reference price, never a repeatedly
discounted target. Missing groups survive later scans; older/repeated snapshots
cannot overwrite newer history. Historical type/name mappings remain available
when a category disappears from the live market. Plans still require a fresh
complete market scan before input, and identify historical or derived pricing
in their reason and original source timestamp. Unknown prices remain deferred.

The subsequent all-stock batch was verified from memory and durable receipts:
Spiritual posted nine additional items and repriced eleven, filling its 32 slots;
Dutch posted thirteen additional items, reaching 21 listings. Two priced items
remain queued for Spiritual booth space. Seven other inventory items lack a live,
historical or equivalent +1 price. Both one-time requests completed and paused;
recurring scans remain off. See the local ignored audit
`reports/merchants/expanded-pricing-completion.json`. The full suite passed 1,360
tests with the sparse-market/history/conversion policy installed.
The installed client's booth dialog accepts at most 999,999,999 silver. Targets
above that limit are deferred before removing a listing or sending input.

### Client display responsiveness

Selecting a merchant's Client tab automatically embeds its already identified
process when a safe handoff is available. Display does not wait for a valid booth
snapshot or an input-calibration probe. Release client suppresses automatic
embedding until Embed client is requested again. Idle show/hide/resize operations
use non-activating asynchronous Win32 calls. Input still requires fresh process
identity, viewport qualification and the shared input lease.

Merchant status, sales totals and journal rows are collected on a background
thread; Tk renders the most recent complete model and refreshes only the visible
table. Stale status is labelled without making the game inaccessible. Resizing
preserves calibration evidence on disk while marking the UI check pending;
the driver independently rejects changed native or GUI dimensions before input.

### Fifteen-minute inventory refill

Each merchant has a durable fifteen-minute capacity timer in the native
runtime, enabled by default and independent of the operations toggle and website
scan schedule. Pausing trades/repricing does not stop this timer. Separate Pause
refill / Resume refill controls affect only inventory filling; Global Stop stops
both features and persists refill pause across restarts. The refill controller
cannot accept trades or reprice existing listings, and it does not enable login
or travel. When space and eligible stock
are available, it rebuilds an inventory-only plan from saved comparable quotes
and fresh owned-booth prices, then posts the highest total value first. Historical
quotes retain their original source timestamps and are not discounted again.
Unknown categories/attributes/prices remain deferred. It never reprices existing
stock as part of this check. Full booths and empty inventories require no input.
Pending handoffs and interrupted work are retried with fresh memory observations;
explicit refill pause/Global Stop, recovery and trade reconciliation retain priority. Merchant
status shows the refill countdown and the last check is stored in the journal.

### Return to shop after reconnect

`ShopReturn` persists a per-character recovery itinerary. A detected disconnect,
replacement client, or attachment in Twin City queues it before merchant trades
or scans. It requests the shared foreground handoff, follows checked terrain to
the Twin City Conductress approach (438,444), verifies the live Market option and
100-silver fare, then verifies Market arrival. It selects a memory-qualified vacant
stall near the saved booth location, walks there and claims it. Restoring previous
listings uses durable item identities and prices, highest value first. Missing or
changed stock blocks restoration after reaching Market. Existing uncertain trade
or listing transactions require reconciliation before any route input.

Transfer/setup intent survives restart; an uncertain payment or claim is never
blindly repeated. Manual pause/Stop remains authoritative. Recovery is reported
only after Market, the owned booth, inventory and prior listings are verified.
Route stalls are bounded and failures use the independent #shops notifier.

**Live rollout remains pending:** `market_return` and `booth_setup` require separate
input qualification for each merchant/client viewport. `shop_setup` must record a
live-validated vacancy offset/mask/value and direct-claim or exact dialog evidence.
Neither the ShopFlag name nor its model grants vacancy/claim qualification. The
read-only `return-route-status` bridge action reports map, position and scene flags
to support this validation. Unqualified controls produce an attention error rather
than route or shop input. The current occupied shops are not closed for this check.

### Four-hour Discord sales summaries

Both merchant observers track sales even while listing automation is paused.
A verified sale requires continuously observed identical process identity,
booth item disappearance without its return to inventory, unchanged remaining
stock, and the observed net silver increase after the America booth deduction.
A durable five-second window reconciles stock and balance updates arriving in
either order. Trade activity, overlapping
listing operations, inventory changes, observation gaps over ten seconds or
ambiguous money changes prevent sale confirmation. Such departures are recorded
as unconfirmed and excluded from revenue. Baselines and receipts are committed
atomically to the merchant journal. Initial stock is not counted as sales.
The legacy gross-price comparison was repaired using audited historical stock
and balance anchors; recovered batches are explicitly labeled in the UI/reports.

`scripts/merchant_sales_report.py` sends the last four hours and cumulative
verified item counts/silver for Spiritual, Dutch and both combined. It explicitly
states the tracking start and coverage gaps. `--preview` only renders the report.
The Overview button **Configure Discord #shops** saves a dedicated webhook using
Windows DPAPI in `.runtime/merchants/shops-webhook.dpapi`; the farmer webhook is
never used as a fallback. The Sales tab shows confirmed receipt events.
The persistent top bar shows the next four-hour report countdown, each merchant's
repricing countdown or paused state, and combined/per-merchant net silver
earned since tracking began. It uses durable sale receipts, not wallet balances
or listing values. Timers refresh every second and totals every five seconds;
missing observations and unconfirmed stock departures show incomplete coverage.

Conquest runs a dedicated script timer thread every four hours. Keep the PC and
Conquest running; Codex/AI is not required. The old AI heartbeat is PAUSED.
The timer migrates the last delivered report's timestamp, saves its next due time,
honors retry delays and coalesces missed intervals after restart. It operates
independently of gameplay pause/focus. Duplicate
requests within a report period cannot repost a confirmed message. Discord HTTP
rejections are retryable; an interrupted send or lost network receipt requires
reconciliation rather than a blind retry. Missing configuration sends nothing.
Both ordinary zero-sale updates and nonzero-sale updates are requested by the
user. Sales tracking does not resume farming, trading or repricing.

Merchant input automatically embeds the verified process during a safe handoff
and checks the calibration before listing. Missing or resized calibration runs
the memory-based price-entry-and-cancel probe before any listing intent. An
existing price dialog or unresolved transaction remains protected. The normal
window focus APIs are attempted first; if Windows denies focus, a guarded click
may activate the verified native caption of this app's own window, followed by
confirmation of game foreground ownership. Manual input and Stop still take
priority; no game control is located visually or clicked by guesswork.

### Failure and needs-attention notifications

`scripts/run_shop_notifications.py` runs independently of the desktop app, using
the dedicated encrypted #shops webhook. Conquest starts it automatically; a
Windows process lock prevents duplicate monitors. It polls authenticated local
status every five seconds. Uncertain transactions and auto-paused unexpected
failures alert immediately. Disconnects, stalled transactions, persistent focus
or layout errors, exhausted reconnects, frozen UI, report-worker failures and app
crashes alert after 60 seconds. Manual Pause/Stop, ordinary pointer activity and
safe-handoff waits stay silent. Clean app shutdown is distinguished from an
unexpected exit; a failed restart remains monitored.

Each incident alerts once, with durable state in
`.runtime/merchants/shops-alerts.json`. A recovery notice requires fresh healthy
checks for at least five seconds after a delivered failure alert. Transient
issues and unsent alerts that already resolved are discarded. Offline sends and
Discord rate limits are retried; an ambiguous network receipt can duplicate a
retry, as with the farmer notifier. Webhook URLs and credentials are never
included in alert/error text. Monitor health is saved in
`reports/merchants/shops-alert-status.json`. No AI checks or gameplay input are
used for these alerts.

### Scripted collection and scheduling (no AI)

`scripts/merchant_relist.py` implements deterministic collection and scheduling.
It uses ordinary headless Playwright Chromium and the public JSON endpoint used
by the website, `https://api.conqueronline.net/api/public/market/items`. America
is server 0. Requests respect the endpoint's maximum page size of 100. Every
page must have the same total and precise server update timestamp; item IDs must
be unique, every page complete, and a final repeat of page 1 must match. A live
collection of 1,777 America listings passed these checks on September 11, 2026.
Blocked access, changed data or incomplete pagination fails without publishing
new comparisons or requesting price changes. No cookies are copied and there
is no challenge bypass or AI fallback. The DOM fallback parses localized spaces
in counts and prices, including narrow no-break spaces.

Install the optional dependencies before the first run:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[market]"
.venv\Scripts\python.exe -m playwright install chromium
```

Collect and validate a snapshot without requesting any game input:

```powershell
.venv\Scripts\python.exe scripts/merchant_relist.py
```

After live qualification and the requested one-time listing run, the explicit
`--watch` option runs the script worker continuously. It checks the app each
minute and requests only due scans for enabled merchants. Completed scans store
their next due time 12 hours later in the durable app journal. Restarting the
worker preserves that schedule; stable request IDs coalesce duplicate requests.
It never resumes a paused merchant or takes over a pending one-time batch.
Failures back off for 15 minutes and appear in
`reports/merchants/script-worker.json`. The worker uses a process lock to prevent
two script instances. It requires real rollout receipts before starting and on
each scheduling cycle; it has not been enabled during implementation.

The running app calculates prices, obtains the farmer's safe handoff, operates
the game, verifies receipts and sends its existing Discord summaries. The script
worker only supplies market comparisons and semantic scan requests. Both the
worker and app must be running. Keep the previous Codex task paused when using
the script worker; an AI task is not required for these scheduled scans.

### Manual browser collection fallback

The market is browser protected; the app does not bypass its protections.
`scripts/collect_merchant_market.js` collects visible DOM table cells using the
Codex browser plugin. Start on America, with all other filters cleared, page 1.
Pass the browser tab and a fresh accessible-state observation callback. Save its
returned object as local JSON, then run:

```powershell
.venv\Scripts\python.exe scripts/merchant_scan.py --browser-pages reports/merchants/browser-pages.json
```

Collection requires every page, matching counts and an unchanged last-booth-change
timestamp. Discard partial or changed collections; do not relabel them complete.
When changing a text filter, commit it with Tab and verify the field before
applying filters. Wait for the Refresh button to become enabled after each page
request; reading the selected page number alone can race the table update.
The website omits stack quantities, so the parser uses one only for equipment
or official item definitions limited to one item; uncertain stacks do not compete.
Scans expire after 15 minutes. Deferred work reloads fresh comparisons before
input. Full booths leave stock in the durable inventory queue. **Download prices only**
(under **More / help**, formerly **Refresh prices**) downloads current America
prices without game input. **Update shop now** (formerly **Scan & list once**) first
requests a fresh download, then lists inventory and reprices the shop, and pauses
operations on completion. Both merchants share the app's background collector.
Paused batches show **Resume shop update**; repeated clicks do not create duplicate work.
The status panel distinguishes downloading, waiting for input, progress, completion
and interrupted transactions, with the blocking reason and a suggested next step.
Fifteen-minute refill remains independently enabled. Uncertain transactions require
reconciliation before a batch can resume; the UI never clears them to force a retry.

The main merchant controls separate **Auto-manage** (incoming trades, new-stock
listing, scheduled repricing and reconnect recovery) from **Auto-refill** (fill
empty booth slots every fifteen minutes using saved prices). Each has one button
that reflects its current state. A one-time update shows **Pause shop update**
while active. **Stop all (including farmer)** stops both plus farming.
**Waiting items** replaces the Deferred tab and shows each item's reason; the
status summarizes items needing a safe price versus space or safe input.
Embedding, verification, reconnect and login tools are under **More / help**,
along with an explanation of every control. These are presentation changes;
pricing, refill scheduling, transaction guards and saved pause intent are unchanged.

The earlier task automation **America merchant repricing** remains PAUSED.
The script worker above can replace its scheduling and collection role after
live validation. The app handles continuous trade detection and account recovery
independently. The machine, game clients and desktop app must remain running for
continuous monitoring.

## Completing live qualification

The September 11, 2026 one-time listing run is complete. Live memory and durable
transaction receipts confirm 1 new listing plus 8 reprices on Spiritual, and
8 new listings on Dutch. Booth totals are 26 and 8 respectively. Both merchants
are paused, all one-time requests are complete, and no transactions remain
unfinished. The final validated comparisons leave 14 Spiritual and 17 Dutch
inventory items deferred for insufficient comparison data. The detailed audit
is in `reports/merchants/one-time-completion.json` (local and git-ignored).
Two interrupted reprices were completed with user assistance and then verified
from memory. Runtime fixes now wait for price text, initial dialog sizing and
the rendered hover ID; every hover retry repeats the complete input guard.
Saved input calibration survives re-embedding only when the live pixel and GUI
dimensions still match. Foreground preflight runs before creating listing intent.
This one-time run does not qualify incoming trades, login/crash recovery, or
the full recurring rollout; the existing task remains paused and the scripted
watch worker is not running.

All merchant listing plans are ordered by fresh, verified total listing price,
highest first (including quantity). Ties use item UID for stable ordering.
The same order applies to new deliveries, one-time runs, recurring repricing,
and the durable excess-inventory queue. Items without reliable comparisons are
deferred after priced items. Available booth slots therefore go to the highest
value eligible stock first; existing listings are not evicted automatically.

Each merchant tab has a **Client** pane hosted with the farmer's reversible
owned-window mechanism. It fills the available width and height when the app is
resized or maximized. Status text has a reserved area and controls use two rows,
so status changes do not resize the game and buttons remain accessible. A user
resize pauses the merchant and invalidates booth calibration before resizing
the game. **Embed client** displays the game without running a calibration probe.
Calibration records the actual Windows
pixel dimensions separately from the game's memory-reported viewport; input
converts between the two for DPI scaling. A different viewport, pixel size,
window size, table spacing, process identity or hovered control stops input.
Moving the app without changing those dimensions does not invalidate it. Run
**Embed & verify** again after choosing the desired window size.
Tab and visibility events update the hosted windows immediately rather than
waiting for the status poll. Window size/maximized state is saved on close.
An actual click inside a visible merchant uses an explicit native focus handoff,
including when the wrapper still owns keyboard focus. It preserves an already
focused game edit control, ignores clicks outside the merchant or while another
app is active, and does not resume automation. The bridge reports UI timer gaps
and click-to-focus timings for responsiveness diagnostics. The user confirmed
immediate manual interaction after this fix on September 11, 2026.
Calibration waits up to 15 seconds for the initiating click and other mouse
activity to settle; Pause, Global stop and closing still cancel it immediately.

Use **Embed & verify** on each paused merchant, or request it through the app:

```powershell
.venv\Scripts\python.exe scripts/merchant_scan.py --verify-booth Spiritual
.venv\Scripts\python.exe scripts/merchant_scan.py --verify-booth Dutch
```

Run these sequentially and inspect bridge `calibration` status. The native app
opens or uses an existing price dialog, verifies a test entry of `123456`, then
cancels and verifies unchanged inventory, booth prices and silver. It never
submits a listing during this probe. Only successful cancellation writes the
local booth qualification. A failure leaves the character paused and requires
inspection before retrying. This probe does not qualify trading or login, and
does not replace the real transaction receipts required for recurring rollout.

Both clients remain observable from memory while idle. An input lease selects
the relevant embedded pane only after a safe farmer handoff. On release, the
app restores the previous tab/window if the user has not taken over; idle polls
do not activate merchant windows. **Release client** restores its original
standalone window after input has stopped. Closing the app releases both hosts.

Each local `.runtime/merchants/<character>/qualification.json` must contain:

- `client_sha256`, exact `character`, `server: "America"`, and an evidence path.
- `client_size` measured with normal window geometry APIs, and `gui_size` read
  from the client viewport in memory.
- Individually verified `capabilities`: `trade_request`, `trade`, `booth_input`,
  `login`. Leave capabilities false until tested on this client.
- `controls` for `accept_request`, `accept_trade`, `inventory_item`, `booth_drop`,
  `remove_listing`, `price_field`, `confirm_listing`. Each specifies an active
  memory window name (or a child-window prefix ending `_`), exact window `size`
  and an `offset`; grid controls also specify `columns` and `stride`.
  Native booth calibration also records a table label and cell offset so each
  input rechecks the live table's columns, row height, scroll and clipping.

Validate controls against the live memory layout, including scrolling. Do not
copy remembered coordinates. Existing login submission additionally requires
its qualified 1295×991 client geometry and recognized login-error state. Unknown
login geometry requires attention. Crashes relaunch the official ImBootstrapper
with a unique new-process association and at most three durable attempts. A
successful login does not itself prove recovery: character, America server,
inventory, own booth and any unfinished transaction must be checked before
resuming. A closed booth requires attention until its reopening controls are
qualified; the code does not guess a booth location after relaunch.

After a real Parasite delivery and listing/repricing of that received item on
both merchants, record the transaction IDs in `reports/merchants/rollout.json`:

```json
{
  "client_sha256": "c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396",
  "characters": {
    "Spiritual": {"delivery": "ACTUAL_ID", "listing": "ACTUAL_ID", "repricing": "ACTUAL_ID"},
    "Dutch": {"delivery": "ACTUAL_ID", "listing": "ACTUAL_ID", "repricing": "ACTUAL_ID"}
  }
}
```

`verify_rollout()` checks these against immutable verified journal records and
qualified profiles. A missing file or invented ID fails. Scheduled invocations
must pass `scripts/merchant_scan.py --scheduled` or the script worker's equivalent
`verify_rollout()` gate. Keep recurring work off until these checks pass. Use
only one scheduler; the script worker is not a way around a failed rollout check.

## Validation

Run `.venv\Scripts\python.exe -m pytest -q`. Tests cover pricing boundaries,
owned-seller exclusion, quantity equivalence, incoming-only offers, changing
items/process identities, full inventories, confirmation interruption and
reconciliation, durable requests, exclusive input, manual stop, credential
isolation, notification replay, bridge authentication and native tab structure,
alongside the existing farmer regressions. Test fixtures do not trade with the
live clients. Automated passes do not replace the live rollout requirement.
# Owned stalls and panel recovery (2026-09-12)

The merchant snapshot distinguishes `own_booth_uid` from `booth_open`.
A closed panel must not cause a merchant that still owns a stall to claim
another flag. Recovery first reopens the exact owned booth, reconciles stock,
opens Inventory if necessary, and restores verified listings. Each submitted
panel action waits for memory confirmation; an uncertain result is never
repeated blindly, including after an app restart.

These recovery controls require separate live capabilities: `booth_panel`
and `inventory_panel`. Booth verification now includes a bounded Items-button
close/open trial with unchanged stock and merchant identity. Existing ownership
or panels opened manually do not qualify automatic opening. Listing calibration
preserves independently qualified occupancy and panel-control specifications.

Live app 1342368 loaded this update. Both merchants passed automatic Inventory
close/open and booth price-entry/cancel checks, with their stock unchanged and
no listing submitted. Inventory reopening and booth listing controls are now
qualified; automatic owned-Booth-panel reopening and full disconnect recovery
are still unqualified. Unattended merchant delivery remains disabled.

The live price trial exposed a reader race: editable price bytes were included
in its booth-ownership comparison. Those bytes now use the separate exact-price
guard; the model/header, open state, owner and selected item still must remain
stable. Both merchants subsequently passed the price/cancel trial.

Safe reload now selects the Farmer tab before recovery input. Its 120-second
preparation bound also covers nested focus/revival waits. This does not impose
a farming-session timer. A live merchant-tab reload exposed the hidden farmer's
unsuccessful revive attempts; detaching its surface allowed a memory-verified
revive to full HP before the safe reload completed. The new tab-selection
behavior completed the subsequent reload without another manual panel change.

The authenticated `pause-merchant` action uses the same pause operation as the
native UI and preserves pending work; it does not pause the separate refill
permission or implicitly resume farming.
