# Reusable overnight Turtledove route

Run `scripts/run_overnight.py --route turtledove` with the native farmer
already embedded and approved by Windows. The controller runs as a normal local
Python process and does not depend on an AI chat or screenshots. Do not start a
second controller while one is running; its PID is in reports/overnight/status.json.

There is no automatic overnight cutoff. The route runs until manually stopped
or a critical failure requires attention. F12 stops the whole route. A duration
is used only if explicitly requested with --hours. The app-owned memory bridge
also has no elapsed-time expiry; closing/releasing the client closes the bridge.

The saved route hunts Turtledoves near (644,570), automatically widens its patrol,
picks up memory-observed drops, heals with Painkiller on F1 and reloads arrows on
F2. Jump travel uses twelve-tile segments where unobstructed, with running below
eight tiles. Jump settling is now 0.50 seconds, 10% faster in cadence than the previous
0.55 seconds. Running cadence is unchanged.

Return thresholds are fewer than 200 total arrows, fewer than three Painkillers,
or a completely full inventory. One pickup no longer triggers a return merely
because fewer than four slots remain. The town sequence visits Pharmacist at
(466,333), sells identified weak healing and mana consumables by dragging them into the shop, refills
Painkiller to fifteen, visits Blacksmith at (452,335), refills LuckyArrow to at
least 1,600 when inventory space permits, closes both panels, then resumes hunting.
Optional supply top-ups preserve four loot slots; depleted supplies still require
restocking. A restarted route closes leftover panels before attempting travel. Each trade is based on a
fresh NPC ID and the open shop's memory item list, price, and GUI geometry. Every
purchase/sale requires a verified inventory and silver change before the next.
DragonBalls, Meteors, special items, all equipment and selected supplies are
retained. Per-instance enhancement/socket fields are not qualified, so ordinary
equipment is also protected; a plain item type does not establish that it is +0. Automatic
upgrades remain outside this initial overnight loop.

NPC IDs change on reconnect. Vendor type, model, name, saved map and tile locate
the current ID. Shop and inventory geometry come from GUI memory. No image
inspection is used. The game must stay visible and unlocked for normal foreground
input. The controller temporarily requests that Windows keep the display/system
awake, and releases that request when it exits. It cannot operate a locked PC.

F12 stops the complete controller. Farming Off also stops the controller while
hunting. Alternatively create .runtime/overnight.stop. Delete that file before
manually starting another run. Transient NPC/shop/inventory changes before any
input are explicitly classified and retried (up to 80 observations). After an
input attempt, receipt reads can retry but the purchase/sale is never reissued
without verification. An uncertain trade still stops the loop. Insufficient funds,
unusable inventory space and unmapped geometry require attention.

Status and trade receipts are saved under reports/overnight. The optional
--first-hunt-seconds argument forces one early town cycle for live validation;
subsequent cycles use the saved supply thresholds. A live early cycle completed: hunting and loot pickup, return to both shops,
verified sale of a looted spear for 34 silver, supply checks, and resumed kills in
the hunting area. Painkiller and LuckyArrow purchases were separately verified
through this same memory-based trade interface before departure. Full suite:
504 passed; warehouse NPC identification and full-inventory policy are covered. Eight-hour endurance and every possible disconnect/error are not
claimed as tested.


The initial overnight run stopped at Blacksmith on September 8 because
"NPC scene changed during observation" was incorrectly fatal before shop input.
The retry classification now crosses the worker bridge, with regression checks
for busy NPC scenes and for never repeating uncertain transactions. That initial
run did not complete eight hours; it stopped after about nine minutes.

After the fix, a live restart completed both vendor checks and resumed the saved
hunting route. Existing supplies were 1,525 arrows and 15 Painkillers with four
free slots, so the optional arrow top-up was skipped to preserve loot capacity.

Recovery validation observed six resumed kills after leaving town.


## Activity and pickup history

The native sidebar now has a current activity line describing the vendor and
supplies being sought, sales, combat, healing and revival. The town controller
publishes a heartbeat; stale travel status is labeled unavailable. A fixed-height
notebook holds Nearby monsters and Pickup history without resizing the game.

Pickup history contains only memory-verified pickups: local timestamp, item name
(or type ID when unknown), and quantity or silver increase. Newest entries appear
first. All new entries are saved to reports/desktop-farming/pickups.jsonl; the UI
loads the latest 200 after reload. Item identity comes from memory; display names
come from the installed client definitions. Historical unrecorded pickups are
not invented.

Stash transfers are not enabled. The stash NPC, item-instance enhancement fields,
stash contents and transfer receipts still need memory qualification. Valuable
items stay protected in inventory meanwhile. Identified low-value consumable sale
IDs: 1000000, 1000010, 1001000, 1001010, 1001020. Painkiller and arrows are retained.


Warehouse memory discovery: Warehouseman at (409,351), model 80, type 0, observed
entity ID 100120. The saved arrival stop is (413,352). Unlike shop NPCs, its type
is zero, so candidates must match the model before name/map/tile verification.
The NPC opened Warehouse through ordinary input selected from fresh memory.
Warehouse and its item grid geometry were read from GUI memory; no screenshots
were used. No eligible valuables were in inventory at that sample. Deposit and
withdrawal receipts remain unqualified, so automated stash transfers stay off.

The new full-inventory return was live-tested: it sold weak consumables at the
Pharmacist with verified UID removal and silver increases. The sidebar displayed
'Selling unwanted loot to Pharmacist'. Cursor/focus rejection before a button
press is now explicitly retryable, without repeating uncertain transactions.


An unconfirmed optional supply top-up no longer ends a stocked route. The
controller takes six further memory observations over three seconds. Only when
silver, supplies and free slots remain unchanged and the route has sufficient
supplies does it defer the top-up and proceed. It never repeats that purchase.
Unexpected changes or insufficient supplies still stop for reconciliation.
Regression tests cover unchanged inventory, unexpected silver debit, item
increase, and insufficient arrows; the overnight test module passes 37 tests.


Automatic refocus (September 8): while Farming is On the desktop app checks
focus every 200ms, starts recovery after 0.5 seconds, and retries at most once
per second. It verifies the selected process/window identity, restores a
minimized client, and uses a temporary foreground input-queue attachment that
is always detached afterward. No screenshots or game memory writes are used.
Farming Off cancels app refocusing. The active overnight route also restores
focus during town travel, when combat is temporarily Off; stopping the route
ends those attempts. Windows may still refuse activation; input continues to
require verified game focus. Full suite: 548 tests passed.


Return-path boundary fix: the native runner now derives its approach boundary
from the entire terrain-validated path, including the departure position, with
12 tiles of padding clipped to the map. The old fixed maximum X=710 rejected
Parasite at (714,496). The validated return from that position to (644,570)
contains 145 points; all are inside the new (632,484,726,582) travel boundary.
Hunting boundaries and terrain checks remain enforced. Unexpected runner exits
preserve their actual reason in the UI and stop the route with a critical error
rather than continuing to report hunting with no active combat runner.
Regression suite: 553 tests passed.


Automatic boundary recovery: on a memory-observed excursion outside the active
hunt or travel boundary on the expected map, the native runner plans a fresh
terrain path to the route's saved hunting_anchor. It clears stale movement and
attack attempts, follows the path with the existing jump/run rules, and keeps
healing, focus, death recovery, and stop checks active. It resumes farming only
at the saved anchor, rather than merely crossing the edge of the hunting area.
The UI reports the return. A map change or genuinely unreachable path remains
an explicit failure; no vision or unchecked straight-line movement is used.


A failed Control-key readiness check before a jump is now a retryable input
cancellation. No mouse-down was sent in this case; Control is released in the
existing finally block, and the loop resamples position/focus before another
attempt. Previously it incorrectly ended the entire farm as
observation_or_input_failure. Regression verifies no click on cancellation,
modifier cleanup, and exactly one click on the subsequent successful attempt.
Full suite: 556 tests passed.


Supply handoff fix: inventory_full, ammo_unavailable, and potions_exhausted are
normal town-return reasons, even if the combat runner detects them before the
route's next inventory sample. They hand off to sell/restock instead of turning
into critical runner failures. Manual Off and unexpected failures remain stops.

When town inventory space is tight, Blacksmith cleanup may sell unequipped
LuckyArrow bundles containing at most 25 arrows, smallest first. Each sale is
revalidated through memory and preserves at least 600 total arrows including
equipped ammo. It stops once four loot slots are free. Full arrow stacks,
potions, gear and valuable items are not affected by this cleanup. Equipment sales now follow the later authorized per-instance + rule below.
Full regression suite: 567 tests passed.


Authorized equipment sales (September 8): following explicit user authorization,
ordinary carried gear (type 100000..599999, quality digit <=6) can be sold only
when the fingerprint-pinned inventory read reports plus==0. The plus byte at
item+0x6b is checked with the rest of the item both before and after sampling,
then rechecked immediately before ordinary vendor dragging. Unknown/nonzero
plus, quality 7+, equipped items, special IDs, and selected supplies remain
protected. Each completed sale requires item UID disappearance and increased
silver. This supersedes the earlier blanket equipment-retention policy.
The base-name formatter evidence is in memory-observation-status.md. A known
nonzero item has not yet been independently observed live; the user explicitly
authorized sales after being told this limitation. Full suite: 584 tests passed.


Restart restocking now checks live supply counts instead of the Farming On/Off
switch. Restarting with adequate arrows, potions and inventory room goes directly
to hunting. Return thresholds remain <200 arrows, <3 potions or zero free slots;
1600 arrows/15 potions are town top-up targets, not return thresholds.


The current loot policy discards carried ordinary +0 equipment and known weak
consumables during native farming, before inventory pressure causes a town trip.
It waits for a confirmed pickup and a healthy, idle action window, then uses
memory-derived Inventory geometry and a normal drag. +/unknown gear, equipped
items, rare-quality gear, valuables, arrows and route potions stay protected.
The exact discarded ground generation is remembered and excluded from pickup.
Ambiguous discards are not repeated. The Items button is read from the client
GUI table; no vision or keyboard shortcut is used.


An unconfirmed discard is now skipped without stopping the whole route. Its UID
remains excluded from repeat discard attempts, and any known ground identity is
ignored. The inventory panel must be cleared before combat continues; closure
can finish after revival or focus recovery. An unverified result is not reported
as a successful drop.

If memory inspection fails before a discard is attempted, cleanup is deferred
for ten seconds so combat can proceed after Inventory closes. The item remains
eligible for a later fresh inspection; no item drag is sent from stale data.
Transient panel closure errors retain the pending cleanup for a fresh retry.

Native hunting movement no longer ends the route after three unchanged position
checks. Each failed movement temporarily excludes its landing and first step
from path planning for 30 seconds, with short running segments for six seconds.
Saved patrol alternatives and bounded local detours are tried if the waypoint
itself is blocked. Healing, defense, death recovery and fresh memory checks keep
their normal priority. If no path is available, navigation waits and retries;
it does not send repeated movement at the same obstruction. UI shows Taking
another path. Discord remains silent for these recoverable movement events.

At internal combat session rollover, the first fresh memory position determines
whether to hunt or plan a new return. The approach from the original departure
is not replayed. These internal sessions do not impose an overnight stop timer.

Expanded patrols now sweep the interior in alternating rows instead of only
visiting boundary corners. The saved Turtledove route uses 24-tile grid spacing,
so open terrain stays within the 16-tile attack range of a sweep waypoint.
Terrain-blocked waypoints are adjusted or skipped; every dispatched segment
still passes terrain and boundary checks. Dead scene records are excluded from
chase destinations. The optimization target is 20 verified kills per minute;
that target is not a measured qualification claim.

The dove anchor was moved to (736,550) after memory observed a group immediately
east of the old maximum boundary. Town paths were regenerated from installed
terrain. Chase candidates now require the same fresh positive-HP check as attack
candidates, including monsters outside the clickable viewport. Lingering scene
records with zero or unreadable HP cannot redirect the patrol toward corpses.

Hosted return travel replans each segment toward the hunting anchor from the
current position. If combat displaces the character, it does not backtrack to
an obsolete intermediate approach waypoint. Terrain and boundary checks still
apply to each planned segment.


Control restart fix (2026-09-08): an obsolete runner finishing with
control_changed now preserves the latest On/Off and target selection. Explicit
Off and emergency stop remain effective. The app checks route-controller health
while Farming On, restarting full route management if only combat was resumed.
A process-held lock prevents duplicate controllers; recent starts are debounced.
UI/bridge intent sources are recorded for diagnosing future control changes.
The live check restarted the route controller from Farming On and automatically
advanced level 32 from WingedSnake to Bandit, with verified movement into the
Bandit area and a confirmed kill. Regression suite: 822 passed.


## Temporary Bandit night

The user-requested session plan in .runtime/session-plan.json pins Bandit and
BanditL33 through level gains and controller/app restarts. It has no timed stop:
the user ends the hold using Resume leveling in Saved routes or by asking to
resume progression. Reusable route templates and normal level brackets are unchanged.

During a required restock, the active night plan checks the Twin City Blacksmith,
Shopkeeper and Armorer, then Phoenix Blacksmith and Armorer. All seven supported
archer slots and normal arrow tiers use the existing live-memory comparison and
purchase/equip receipts. Protected equipment and the supply-money reserve remain.
Return travel uses the saved Conductress trip and portal connection; the farming
route remains Bandit throughout. A full circuit is checked once per newly usable
shop tier, rather than making the trip on every supply refill. An incomplete
review is retried at a subsequent restock. If the circuit consumes essential
supplies, finish a local refill before returning to the hunting area.

Town travel uses a 90-second no-progress deadline, refreshed by live position
changes, instead of a fixed four-minute total limit. Partial walking progress is
not counted as an obstruction. Genuine failed steps still reroute around blocked
tiles. An interrupted equipment circuit resumes after controller restart without
repeating a supply trip when arrows, potions and inventory space are sufficient.

Camera-edge travel keeps projected clicks inside the existing clear ground bounds
and uses visible walking prefixes before rerouting. Circuit checkpoints preserve
visited city stops across an interruption; deferred shops retry at a later restock.

Travel XP inspection is gated by the already-read ready flag and pending Fly
verification, preventing repeated remote scans from delaying movement and healing.
When town travel takes ownership, it reselects the saved route while Off and waits
for the old death-return checkpoint to be cancelled before moving elsewhere.


Night-plan amendment: the user subsequently excluded Twin City for the rest of
this night. Setting upgrade_maps to [1011] keeps the Bandit hold and ordinary
Phoenix restock/equipment checks, while skipping the cross-city circuit (including
its deferred Twin City Armorer check). The UI labels this Phoenix shops only.


2026-09-09 morning incident: the 00:44 runner failure happened at the 25-arrow
reload threshold. trial.sqlite3 records "Window state differs from the foreground
calibration". Native reload called TownTrade from physical DPI coordinates;
reload and its Inventory cleanup now run inside logical_coordinates. Window
minimize/resize guards that occur before input now raise CaptureUnavailable,
allowing fresh observations instead of ending the trial and disabling recovery.
Nine focused regression checks passed. These fixes are staged, not loaded in the
running desktop app: Windows Smart App Control currently rejects NumPy's
numpy.libs/libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll.
Code Integrity event 3077 confirms the signing-policy block. Fresh controller
imports and full-suite validation are blocked. No security settings were changed.
Parasite was revived with full HP in Phoenix; farming was left paused there.
After the Windows block is legitimately resolved, reload the app, run the full
suite and validate a live reserve-arrow reload before resuming unattended farming.


Deployment update: after the user adjusted Windows security settings, NumPy
imports succeeded. The desktop app was reloaded with the logical-coordinate arrow
reload and recoverable pre-input window guards. Full regression suite: 866 passed.
The standalone controller starts again. Farming On and foreground focus were
verified; user mouse priority still pauses automation until two seconds idle.
The character is currently using LuckyArrow; no forced ammunition upgrade was
made during this restart. A natural reserve reload has not yet been observed
since deployment, so the focused reload tests remain the validation for that fix.


Poltergeist savings goal: after repeated Bandit deaths depleted the wallet, the
user requested a 50,000-silver recovery goal. A persistent save_silver session
plan overrides leveling and holds the saved Poltergeist route. Optional equipment
and arrow upgrades and cross-city equipment tours are disabled. Starting live
balance was 961 silver with 722 LuckyArrows and no potions. Below 5,000 silver,
stock targets are 600 LuckyArrows and six Painkillers. With at least 5,000 silver,
the targets rise to 1,600 LuckyArrows and ten Painkillers to reduce supply trips.
Both retain the existing essential return thresholds. Once minimum supplies are covered, discretionary top-ups preserve
200 silver. The actual wallet balance, not gross pickup income, drives progress.
At 50,000 silver the controller returns to the route's town anchor, rechecks the
balance twice and confirms a living town arrival before completing and stopping.
The UI shows the persisted silver progress. No return to Bandits is automatic.

The user subsequently authorized better arrows on the next normal restock.
With allow_iron_arrows enabled in the savings plan, the Blacksmith review can
buy and equip a memory-qualified IronArrow upgrade after refilling healing,
provided the live price leaves at least 3,000 silver. IronArrow refills retain
the same reserve. Bow, armor and other equipment purchases remain disabled;
this permission does not trigger an early town trip.

During the savings run, the original northern patrol spent over a minute without
an attack. Memory-event kill density showed hundreds of kills in y=320-379 and
only one in y=300-319. The saved Poltergeist anchor is now (110,345), with patrol
points through (130,345), (130,365), (110,365), and (90,345), bounded by
(85,325)-(150,380) before the existing 12-tile search expansion. Travel paths were
regenerated against the installed terrain and the route model revalidated.
Live kills resumed after loading it; wallet rose from 9,726 to 10,639, with a
memory-verified healing use and no trial errors in the observed minute.

A later run stopped on the pre-attack freshness guard with "Action expired or
foreground changed", leaving the character exposed despite nine carried potions.
Travel healing restored health and the route resumed. The hosted pre-attack
guard now raises the recoverable observation exception, just like dispatch,
so it discards the pending input and rereads memory instead of terminating.
Focused loop tests cover expiration after target selection, suppressing that
stale click and successfully dispatching only a freshly observed target;
the focus, healing and recovery suites passed all 33 cases.

The user subsequently removed the 50,000-silver stop and requested continuous
play until returning. The active plan now uses silver_target: null. It retains
the Poltergeist hold, essential restocking and authorized affordable IronArrows,
but crossing 50,000 no longer triggers town completion or a farming stop.
The UI describes continuous farming and shows the current silver balance.
Tests exercise balances below, at and above the former limit without completion.

Reload safety: the Reload app button and authenticated reload request now queue
a protected local handoff, including while Farming is On. Startup imports are
checked while the existing farmer is active. The coordinator takes exclusive
route input, retains travel healing/revival, and finds a nearby clear tile from
memory-observed monsters and terrain. It requires 24 tiles of clearance from all
observed monster types (unknown life counts as a threat), at least 60% HP, and
three seconds of stable position/health. Movement is one bounded, visible,
terrain-checked step per observation, with failed destinations avoided. It does
not travel to town just to reload. If the check fails, the old app is retained
and farming resumes; Stop cancels preparation.

The app rechecks current health, monsters, position and game identity immediately
before detaching. A short-lived handoff resumes farming only in the replacement
app attached to the same client. A session-label/schema exception no longer
prevents the control/reload event queue from being processed.

Validation: 905 full-suite tests passed before the final bounded local-step
refinement; the 14 safe-reload cases passed after it. A first live protected
handoff verified (146,310), HP443, with no death. A second app-managed request
while Farming was On replaced app979484 with app973912, automatically resumed
controller968340, and preserved HP418 through the handoff. No new admin consent
or visual inspection was used.

Live savings validation: Poltergeist farming, coin pickups and the natural
25-arrow reserve reload were observed. trial.sqlite3 logged reload_outcome with
outcome=verified and ammo=200, confirming the deployed DPI-context fix. The wallet
increased from 601 after the initial potion purchase to 4,076 silver. The 50,000
silver goal remains active; it has not been marked complete.


Silver banking now runs after the complete restock/shop circuit, before returning
to the hunt. profiles/banking.json enables deposits and a 200-silver reserve
(two verified 100-silver Conductress fares). Before a trip, a wallet shortfall is
withdrawn from storage. At restocking, banked funds can cover only missing arrows,
healing and fares; this does not fund equipment upgrades or trigger early town
visits. Any remaining shopping money is deposited again afterward.

Warehouseman is identified from memory, using the pinned Twin City identity or
an exact current-scene identity in another verified town. Only Twin City has
been live-qualified. Both 100-silver deposit and withdrawal produced exact
opposite wallet/storage changes. The controls use live UI geometry and font
metrics; ordinary numeric input must match the memory amount before clicking.
The client inserts thousands separators (14,021), which are validated and
normalized. A mismatched amount prevents submission; an uncertain submitted
transfer is never retried automatically. Receipts persist under reports/banking.
The continuous savings UI displays carried and last verified banked silver.

Validation: all 916 tests passed before the live thousands-separator discovery;
27 focused banking/warehouse tests passed after its fix, including malformed
amount rejection. Continuous farming remains without a silver stop limit.

Live policy qualification: deposited 14,021 silver with an exact receipt leaving
200 carried and 14,021 stored. Closed the warehouse/inventory and resumed the
Poltergeist controller with 2,053 IronArrows and 10 healing potions. The app loaded
the automatic restock hooks and has no session end timer.

Travel recovery fix: the shared movement dispatcher previously rejected health
below 40%, even when TravelCare had exhausted potions and intentionally continued
toward town. That fatal ValueError stopped the controller in combat. Living
characters can now escape at low HP; a fresh death raises a transient life-state
error so travel returns to observation and revival. The regression test verifies
movement at 1 HP and no movement at zero HP. All 114 recovery, overnight and safe
reload tests passed. Live recovery revived the character to full HP at Twin City
and the existing controller bought 10 potions before loading the fix.

Performance target: user requested 20 verified kills/minute (300 per rolling
15 minutes), including travel/restocking/recovery downtime. This remains an
active optimization goal, not a claim of measured achievement. Runtime evidence
and change timestamps are recorded in reports/performance/goal.json.

The first optimization selects bow auto-attacks for an isolated living target
and retains adaptive Scatter for groups of at least two within the skill's actual
memory-read range. Live isolated Poltergeist kills took one left-click command
and two arrows, replacing approximately five Scatter clicks. Bow range is read
from the equipped item. A second fix prevents one-tile chase steps when the
monster is in world range but still outside the clear input area. The approach
uses current memory draw positions to plan an aimable stopping point; fresh
identity, health, terrain and input checks remain required. All 927 tests passed.
Initial rolling five-minute throughput was 13/minute, below the requested target.

A subsequent live trace found repeated no-target observations at the same tile
while losing HP: defense mode suppressed ordinary patrol but its threat reader
only included selected or adjacent enemies. Threat observation now includes all
living monsters within 12 tiles, without adding them to attack/chase selection.
If no attackable target exists, defense no longer suppresses patrol movement.
Regression tests cover both an unselected ranged threat and movement during
empty-target defense (69 relevant tests passed).

Continuous farming with at least 5,000 total carried/last-verified banked silver
now targets 20 healing potions and starts the town return below five. The old
limited savings mode retains its smaller stocks. The supply/banking/overnight
suites passed 113 tests. App1015400 loaded both fixes, resumed with full HP, and
retained IronArrows. The observed restock successfully withdrew 3,498 silver,
bought healing and 1,000 IronArrows, then deposited 3,000, leaving 200 carried
and 13,523 banked. A death on the preceding long return was automatically revived.

Next performance investigation: client definitions list TwinCityGate (1060020)
as a 200-silver town teleport; an old pharmacist product-memory dump contains
that type. Fresh vendor availability, purchase and use are not yet qualified.
Do not claim a scroll return is active. The 20/minute target remains unachieved.

Return-scroll support is implemented behind profiles/return-scroll.json, which is
currently disabled and unqualified. The specific 1060020 item can be purchased
only at the live Pharmacist's 200-silver price. Ordinary inventory right-click
requires a stable UID/grid/player snapshot, and a persisted submitted record
prevents repeat use after an uncertain result. Qualification requires precisely
one scroll consumed, unchanged money/other items/ammunition, and the same living
character arriving inside Twin City at least 32 tiles from the source. Only a
verified receipt enables automatic use. Source map is restricted to 1002 so this
does not replace Conductress travel between cities. No live scroll was purchased
or used yet. All 936 tests passed; the purchase/use mocks do not constitute live
qualification.

Performance verification now has a reusable read-only measurement script:
scripts/measure_farm_performance.py. It records 60/300/900-second kill-counter
windows and refuses rate-target success before a complete 900-second validation
window. The uninterrupted post-defense-fix window began at 1788963665.7841682.
At 1788963983 the last five minutes contained 123 verified kills (24.6/minute),
with full 15-minute verification still pending. Controller1015760 was live,
HP520/653, ten potions remained, and coin pickups had raised carried silver to
6,469. The goal is still active; do not infer full completion from this sample.

Completed performance validation: at 1788964597.6566, the full preceding 900
seconds contained 379 verified kills (25.27/minute). The sum exactly matched
the monotonic player kill-counter span, with no duplicate counts, trial errors,
route stops or deaths. There were 95 verified pickups totaling 17,079 silver.
Controller1015760 was live and hunting Poltergeists on map1002, Farming On,
HP396/653, IronArrows equipped and ten potions remaining. The existing restock
and banking cycle had separately verified withdrawal, supply purchases and a
3,000-silver deposit, leaving 13,523 in storage. Healing/death recovery remain
enabled; the recent recovery and maintenance tests passed. All 936 tests passed.
The detailed receipt is reports/performance/validation.json. This proves the
measured 15-minute target; rates can vary with spawns and maintenance. The saved
20/minute target and existing periodic notifications remain. Farming continues
without a timer or silver stop. The optional, unqualified return-scroll experiment
is disabled and was not needed to achieve this result.

Warehouse travel recovery: the post-shopping controller stopped at (426,337)
for 90 seconds while repeating a diagonal corner run. The avoided sign-diagonal
tile was not the first cardinal A* edge, so replanning could repeat the same
failed corner. Failed movement now also excludes the actual first path edge.
Banking checks live vendor reachability (distance plus memory-derived clear
input point), and warehouse approach travel finishes when that check succeeds.
A nearby but off-screen vendor still requires travel. Regression suite: 98 passed
in tests/test_banking.py and tests/test_overnight.py. Live recovery reached the
warehouse, verified a 32,690-silver deposit (46,213 stored, 200 carried), then
safely reloaded to app1040052. No visual observations or memory writes were used.

One-hour monitor, first quarter (2026-09-09 16:30–16:45 UTC): 129 verified
kills (8.6/min), no restock or death. Expanded sweep was oscillating along its
empty northern edge. When routing rejected a waypoint, alternatives restarted
at the beginning and the trial cursor retained the rejected waypoint. Patrol
alternatives now follow the current cursor and the cursor tracks the selected
reachable sweep destination. Monster chasing does not change that cursor.
Regression tests cover skipping an unreachable point and continuing forward;
50 relevant native-farm, search and escape tests passed. Safe reload completed
to app1067776 with farming intent restored. Hour monitor records are in
reports/performance/hour-monitor.json; next interval must verify rate improvement.

One-hour monitor second quarter (16:45–17:00 UTC): 8 verified kills, one
restock, no observed death. Warehouse panel open timed out after shopping and
stopped the controller. A fresh reopen succeeded; 61,706 silver deposited,
107,919 stored and 200 carried. Only the exact nonfinancial panel-open timeout
now receives up to three attempts; each checks active panel memory first.
Other failures and all monetary transfer uncertainty still propagate. Poltergeist
patrol now covers [100,340,165,400], concentrating on central/southern tiles with
historical successful attacks and reducing the empty northwest edge. Existing
anchor and town connections preserved; five patrol tiles checked against terrain.
118 banking, overnight, routes and patrol-search tests passed.

Travel-healing regression after diagnostic detach/reattach: the bridge set
native_probe_mode from the requested detached boolean before the UI applied it.
Reattaching an owned native window therefore disabled travel healing even though
its real window geometry still supported native input. Input mode now follows
the applied host state under the bridge lock; owned and detached clients retain
native capability, while actual child embedding does not. Three host-state and
queued-request regressions plus bridge, owned-host and safe-reload suites passed
(45 tests). The affected death was revived using the qualified normal input path,
then the update safely loaded from Twin City to app1124088. No vision used.

Survival monitor restarted 2026-09-09 20:05–21:05 UTC at five-minute intervals.
Initial check found a stopped controller after session-plan.tmp replacement
PermissionError, then death. Revive was verified at Twin City with full 710 HP.
Shared JSON writes now use independent temporary files and bounded retries for
Windows sharing violations. Savings progress display-write errors are nonfatal
and retry on the next reporting tick; this does not suppress policy mutations,
trade errors, or uncertain monetary receipts. 84 savings, Discord and session
plan tests passed, including lock recovery and preserved old data on failure.


## Current Bandit efficiency session (2026-09-09)

The saved Bandit route is anchored at the user's memory-observed prime spot
(341, 441), with Bandit and BanditL33 selected and bosses excluded. The session
plan explicitly starts at the prepared hunting area; subsequent city arrivals
still follow the normal town-arrival workflow.

The route's `jump_scatter` setting alternates Scatter with terrain-checked
8–12 tile jumps toward dense living groups, including diagonals. Landing selection
uses the full fresh memory-observed living group, even when some monsters are
outside the clickable scene. Every crossed terrain tile and diagonal corner is
checked. Sparse groups yield to reachable larger groups. Healing and valuable
loot run first.
No long safe landing means combat continues or normal navigation uses running.
The ground allowlist now excludes every currency type and ordinary Unique gear:
Elite, Super, positive-plus equipment, Meteors, and Dragonballs remain eligible.
Valuable drops are checked before each combat movement or cast rather than
waiting for all monsters to disappear. Eligible drops outside click range or
behind the HUD get a terrain-checked approach; ownership, inventory capacity,
and inventory-gain verification still apply. Pickup history stores timestamp,
quality, plus value, quantity, map and position. Every confirmed pickup is
queued for Discord. User Stop resets displayed kills/hour to zero; historical
events remain available.

Town travel prefers the fewest turns among shortest traversable cardinal paths,
retaining blocked-tile and portal checks. On the installed Phoenix map, the
(341,441) to (191,250) path has 341 tiles in both planners, with turns reduced
from 57 to 8 and movement segments from 76 to 34. The direct diagonal crosses
42 blocked sampled tiles. Live travel completion remains separately verified.

Performance is measured from `kill_verified` counter increments, including
downtime. A complete fifteen-minute window needs at least 300 kills to meet
the user's 20/minute goal. Reports live in `reports/performance/bandit-monitor.json`;
short successful bursts do not qualify as sustained success.


September 9 pickup-history correction: StonePoleaxe +2, inventory UID 292951848,
was verified in memory but had no tracked ground-click receipt. Its history was
recovered with verification time, not an invented pickup time. Every fresh farm
inventory snapshot now reconciles new valuable UIDs independently of ground
reads, so ground-scene changes or direct inventory arrivals cannot silently
skip the history/Discord pipeline. Existing items at startup are a baseline,
and persistent history deduplicates inventory UID plus type across reloads.


September 9 runback correction: a lost-focus race immediately before the travel
healing key was classified as fatal, leaving the character stationary. The two
explicit no-key-sent focus errors now raise TravelStateChanged. The travel loop
reacquires focus and resamples health/inventory before retrying; it does not mark
an unsent potion as used. Uncertain input completion is not blindly repeated.
104 overnight/recovery tests passed, including both focus-race recovery and the
uncertain-input case. The restarted controller reached the Pharmacist and
verified purchases after the original stall at (216,268).

Phoenix banking correction: memory independently identified Warehouseman ID
101358, model 210 at (227,246), map1011. The older discovery only considered
model80 from Twin City and halted an otherwise stocked route. The saved Phoenix
identity now pins its own model/tile; a fresh action-path warehouse-locate read
passed after reload. 29 NPC/banking/warehouse tests passed. The controller was
resumed and progressed beyond the prior runback stopping point with 15 potions.


Current target is 30 verified kills/minute (1,800/hour), with survival and
protecting valuables prioritized over throughput. Bandit supply policy returns
below six Painkillers and restocks to twenty. Travel and normal hunt-area
returns heal below75% HP. A travel potion whose HP gain is masked by damage
is recorded as unconfirmed; it no longer permanently stops escape movement.
Post-revive native recovery verifies three fresh living samples, then delegates
return travel to the main memory farmer, retaining healing and defense. It does
not report return completion until near the stored death area. Town banking
now deposits carried +/Elite/Super equipment, Meteors and Dragonballs by UID;
verification requires exact inventory removal and matching warehouse addition,
including enhancement and durability, before silver banking. Equipment in worn
slots and supplies are excluded. These changes passed234 targeted tests.

Live verification of this safety update: the dedicated Phoenix warehouse trip
and return completed alive. Exact UID deposits were verified for StonePoleaxe+2
292951848, Meteor292954033 and Elite item292957232. The wallet deposit of20,835
silver was verified, retaining200 for transport. Memory at return showed690/834
HP at(345,450), and Scatter farming resumed. The Elite pickup also provided the
first live confirmation of the inventory/history/Discord pipeline. The new
30/min target has not yet passed a full fifteen-minute validation window.


September 9: Jump–Scatter now starts with a cast when a selected living target is already in range. After a successful hunting movement it casts before planning another ordinary jump; sparse groups no longer trigger repeated relocation ahead of a valid shot. Jump intent is consumed only after successful input, so focus retries retain it. Emergency escape clears ordinary jump intent. Confirmed jump landing waits are reduced from 0.50/0.55 seconds to 0.40/0.44 seconds; unconfirmed movement and the 0.8-second Scatter cooldown retain their prior limits. Healing, valuable pickups, observation freshness, manual priority and emergency escape checks remain active. Validated with 93 combat/navigation/healing/recovery tests.

Live cadence validation also found boundary oscillation: the landing scorer counted monsters outside hunt bounds although attack selection rejected them. Landing density now uses only living targets inside the same boundary. The new boundary regression and related cadence tests passed (15 tests).

Town stall watchdog: route steps now return to replanning after1 second without fresh memory position progress, including stalled partial runs, instead of waiting the full5-second arrival budget. Advancing steps retain their arrival budget. Care runs on every poll. Any unsuccessful step immediately enables short running recovery and records source, destination, actual position and reason.111 route/care/reload tests passed.

Safe app reload preparation has a dedicated reloading phase and persistent UI override: Moving to a safe spot for app reload. Discord announces preparation once and announces resumed farming only after fresh live farming is confirmed. Intermediate healing/Fly events cannot masquerade as town restocking.91 notification/UI/reload/controller tests passed.


## September10: exhausted supplies and arrow-panel recovery

The observed trip began with199 arrows,10 Painkillers and19 free slots: the
previous below200-arrow trigger caused it, not a potion shortage. Restocking now
requires zero total arrows, zero healing supplies or a full inventory. All saved
route thresholds and future defaults are1; the runner also enforces zero counts
independently of old saved thresholds. Once a legitimate trip starts, a full
healing stock with no junk or needed return scroll skips the Pharmacist and its
travel detour. Buying/top-up, upgrades, valuable banking and transport reserves
remain part of legitimate town visits.

The subsequent stationary failure was Inventory opening unverified, before an
arrow equip click. That exact pre-equip failure now retries from fresh inventory
and life observations. Uncertain equipment receipts still fail without repeating
the equip action. Validation:189 targeted native-farm, route, restocking and
savings tests passed. Loaded app advertises empty_restock_revision1.

Latest refill quantities: five healing potions and ten arrow packs in total
(10,000 IronArrows or 2,000 LuckyArrows). Existing excess potions are retained.
Warehouse funding uses the increased arrow target; inventory space and purchase
receipts still bound top-ups. Return only on empty supplies or full inventory.

Scatter ammunition correction: fewer than three arrows with no reserve stack
large enough for Scatter triggers restocking. Reload a usable reserve first;
never loop casting Scatter with one or two arrows. Potion target remains five,
arrow refill remains ten packs.
