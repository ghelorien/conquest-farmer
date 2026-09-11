# Latest handoff: memory-only farming

Read AGENTS.md first. User prohibits vision without explicit prior permission;
saving debug screenshots alone is allowed. Hosted combat now uses memory IDs,
world/draw coordinates, life and inventory. No capture or template loading.
Native farming has produced kills. The saved Turtledove area is (644,570), with
7.5-second idle expansion and sixteen-tile attacks. Ground-item memory pickup has live evidence: multiple silver pickups plus
ordinary item pickup, checked through disappeared records and inventory/silver
growth. See reports/memory-loot-live-validation.json. Item amount stores durability
for equipment; the quantity-reporting fix counts new item UIDs instead. NPC and active-shop memory are still missing,
and the historical Pharmacist purchase script is disabled. Preserve Farming On
when focus/observations are temporarily unavailable. Normal foreground input
needs game focus; do not use CUA/image inspection to obtain it without permission.

The ongoing user goal is efficient kills, loot, restock and upgrades with little
downtime. Main priority is the live farmer, not background-input experiments.

The content below is historical and may describe superseded implementations.

# Continue development on another PC

This is a work-in-progress snapshot, published at the user's request before the
memory-only farmer is finished. Follow [other-PC setup](other-pc-setup.md).

## Requirements to preserve

One Archer, foreground input acceptable, all operational observations from
read-only memory. No OCR or screen fallback. F1 healing below 40% has priority;
keep fresh health and level on the dashboard even when farming is stopped.
Select monsters by actual IDs/types/coordinates, follow a bounded route, review
equipment and skills on leveling, revive and walk back after death. Preserve
F11 pause, F12 stop, focus/cursor guards, expiring actions, SQLite statistics,
and evidence-based kill/pickup counts. No memory writes, injection, packet or
client changes, anti-cheat bypasses, evasion or drivers.

The final 30-minute supervised run with movement, combat, healing and selected
pickup has not been completed. Background/minimized support is unqualified.

## Current implementation

The historical `trial.py` loop achieved verified combat/healing but depended on
visual health, targeting and loot. Both bundled profiles and the default now use
`memory_only`, which blocks that loop before capture or input. `legacy_visual`
remains for regression tests, not as the requested final implementation.

Inventory, equipped arrows, position and level readers exist. `record-route`
records map coordinates. The new `sample-entities` reads the scene collection
without images and excludes players/NPCs by object type and kind. Current HP,
monster alive state, ground loot and revival state are still unvalidated.
The dashboard shows unavailable HP rather than opening a camera.

## Memory work to resume

Continuation on this PC repaired player resolution and added `sample-health`.
The old root RVA `0x697970` failed its type check. The inspected singleton getter
at RVA `0x181b30` resolves through RVA `0x69c730`. The existing player profile
uses pointer offsets `[8,0]` to select the shared ownership control block and
preserve its existing field offsets. The new health profile uses `[0,0]` to
select the actual character object, vtable RVA `0x5cef60`, name `+0x94`, maximum
HP `+0x3d0`, position `+0xd8`, and level `+0x6e8`.

The attribute getter at RVA `0x1a9f90` reads the table referenced at actual-player
offset `0x968`. Attribute index 1 is a current-HP candidate; the new diagnostic
decodes its mode-specific storage and validates stable table/header/pointers,
character name, HP bounds, and freshness. A live sample returned 213/213, and
player/inventory diagnostics work with the repaired player profile. This is
candidate evidence only: damage, healing, death, revival and an actual client
restart still require independent verification. Memory-only farming remains
blocked and the health dashboard still reports unvalidated HP as unavailable.

The read-only worker supports bounded `read-block` diagnostics, so further
inspection can reuse it without restarting for each read. 223 tests pass.
The root and offset notes below describe the earlier session and are historical
where superseded by this continuation.

Profiles are fingerprint-specific candidates; actual client-restart validation
remains outstanding. Never reuse the old PC's heap addresses, PID, window handles
or worker connection token. Resolve every object from the current loaded module.

- Player root RVA `0x697970`, pointer offsets `[0,0]`; name `+0xa4`, position
  `+0xe8`, maximum HP `+0x3e0`, level `+0x6f8`, kill counter `+0xa40`.
- Map RVA `0x699564`, observed Twin City ID `1002`; cross-map checks pending.
- Scene root RVA `0x699370`, offsets `[0x18,8,0]`, collection vtable RVA
  `0x5ccc90`. Vector begin/end/capacity `+0x58/+0x60/+0x68`; 16-byte entries,
  actor pointer at entry `+8`.
- Monster actor primary vtable RVA `0x5c5e20` plus kind `2` at `+0x80`.
  Players can share the vtable, so the vtable alone is insufficient.
- Actor ID `+0x78`, name `+0xa4`, world coordinates `+0xe8`, drawing coordinates
  `+0xf8/+0xfc`, maximum HP `+0x3e0`, level `+0x6f8`.

Drawing coordinates are memory values; their input anchor still needs validation.
Collection membership is not evidence of life. The reader rejects changed records
or topology; measure its latency and availability in combat before integration.

**Maximum HP is not current HP.** It stayed constant through damage. Fields with
value 100 are not validated percentages. Previously inspected encoded attributes
did not yield validated current HP. Next: locate current HP, verify damage,
healing and death independently, then integrate the state reader and qualify
bounded combat/healing. Do not enable a visual fallback to pass this milestone.

## Character and route context

The historical character was Parasite, last verified level 12, with Bamboo Bow
equipped at level 9. Recheck everything on the new PC. The leveling plan lists
completed upgrades and due reviews; armor/Archer training remain to review.
Use https://wiki.conqueronline.net/guides/Leveling/Powerleveling for route planning.

The Turtledove route loops near `(668,570)` and `(688,590)`. Direct eastward travel
from `(652,550)` hit a pond; walking around via `(660,570)` worked. Crossing the
town gate required walking through `(439,431)`, `(441,436)`, `(443,440)`; jumping
did not qualify. The recovery profile has a return route but no qualified revive
action. Do not guess a revival click.

185 tests passed in the latest local run. Reports, screenshots, raw memory data,
SQLite sessions, tokens and the game installation are excluded from publication.
Regenerate live evidence locally; tests do not qualify gameplay.


## 2026-09-10: Phoenix Market route and Meteor packing blocker

Bandit Painkiller refill target is 10 (return threshold remains 6). Existing excess potions are consumed, not discarded.

Memory-only live verification: Phoenix Conductress is model287, UID101359 observed, at (228,255), map1011. From approach (228,249), her Market option costs100silver and arrives at (211,196), map1036. Market return NPC is `Mark.Controller`, model417 at(215,220); `Yeah. Thanks.` returns to Phoenix(193,266) for zero silver. Exact dialogs and identities are saved in `profiles/meteor-banking.json`.

Meteor packing is NOT enabled or complete. The expected MillionaireLee model4297 atMarket(203,196) is named HelpNpc on this server and offers only help links. TreasureBank(model86 at180183) requires VIP. ComposeBank(model86 at179187) opens Compose Warehouse, not a verified Meteor exchange. The user has been asked for the correct packing NPC. Do not identify NPCs solely by model or a trailing stale string in its name buffer. No ten-Meteor batch was withdrawn, no MeteorScroll was created, and Market warehouse storage has not been transaction-qualified. The coordinator in meteor_banking.py is a disabled scaffold, not hooked into normal banking; it needs a qualified exchange, restart reconciliation, capacity handling, and integration before activation.

Phoenix warehouse has12Meteors plus8other valuable/user items, all20slots full. One Meteor UID292938757 was withdrawn by LEFT click in the memory-derived six-column40px warehouse grid, independently seen in inventory and absent from storage, then redeposited by exactUID receipt. It was restored to the bank; do not count that transfer as a ground pickup. Withdrawal receipt validation was fixed to reject unchanged inventory/storage. Wallet reserve restored to200silver; bank silver9,986,737 at verification.

Market discovery validates full name/model/type/UID/tile and fresh scene membership while permitting unrelated player reordering. Service opening closes an existing dialog first, preventing reuse of another NPC's choices. Service travel can stop within12tiles with the live NPC click point on screen. Local bridge requests now construct only HTTP handlers, avoiding Windows certificate-store loading on every loopback memory read.178 targeted tests passed. Keep safe reload, manual Stop and valuable protection rules unchanged. Qualification downtime belongs in all rolling performance windows.


## 2026-09-10: warehouse overflow and permanent full-storage shutdown

User-authorized rule implemented in storage_overflow.py and banking.stash_valuables: when town storage is full, carried protected items remain, and the local bank plus carried inventory contain fewer than10Meteors, use the saved Phoenix Conductress / Market Controller round trip and deposit carried overflow into Market. Existing stored items are not withdrawn. Transport funds are reserved before departing. The trip journal supports resuming in Market and after verified deposits, without repeating already-stored UIDs; an uncertain outbound fare is not paid again. The normal shopping balance is reread after the extra trip. The >=10Meteor branch remains the unqualified packing blocker documented above.

If Market storage is full (including a last deposit filling it), storage_halt.py persists .runtime/storage-halt.json before stopping controls. The app closes only the verified ImConquer process, checking path and creation time on the actual termination handle, and disables automatic reconnect, automatic route restart, and bridge-enabled farming. The UI explains the stop and Discord sends an immediate prioritized terminal alert. Manual Farming On clears the latch and resumes any unfinished overflow check; Market must have space before farming continues. This deliberately leaves the farmer app open for the status message, while closing the game connection. No memory writes or vision are involved.

profiles/meteor-banking.json overflow_enabled is independent of disabled Meteor packing. The active route has only a verified Phoenix-to-Market round trip; other origins fail with an explicit unmapped-route error. Market warehouse item transfer and the storage-full disconnect have not been triggered on the live character yet: current Phoenix bank has12Meteors.236 targeted tests passed, covering overflow, partly/full Market storage, exact-process identity, persistent stop, resume guards, notifications, banking and reload regressions. Do not invent a live validation receipt or trigger a real full-storage stop merely to test it.

September 10: MillionaireLee was found by the user at Market (242,242), model4294, UID101390. Two exact dialogue steps are saved in meteor-banking.json. Ten user-supplied Meteors were exchanged under supervision for MeteorScroll UID293029895, type720027, with exact ten removals/one addition and fee0. The scroll was independently verified in Market storage (14/60 slots). Journal: reports/banking/meteor-consolidation.json. No automatic withdrawals were performed for this batch. Long packing dialogue needs vertical scrolling; service-scroll-dialog and clipped-choice rejection now support this. Automatic consolidation remains disabled until the platform crossing and restart/capacity orchestration are integrated. The normal terrain planner considers MillionaireLee's platform disconnected; supervised 12-tile crossings 230240 <->242240 succeeded. Do not leave Market carrying protected valuables; meteor_banking.trip now rejects such returns before any input.

Runback monitor: runback_monitor.py observes every OvernightLoop travel leg and native farm boundary/death return. Reports live under reports/runbacks/{town,hunt}.json with append-only history.jsonl. Captures elapsed/active/paused time, movement distance/rate, stall and recovery counts, HP loss/minimum and observed deaths. User movement and observation gaps never inflate bot speed or create fake stalls. Damage while stationary shortens progress timeout to0.5s. Town travel can choose clear, visible cardinal escape steps away from current memory-observed monsters, preserving healing and manual input priority. Native approach movement uses the same shorter wait when urgent. UI shows runback duration/stalls/minimum HP. Telemetry write failures do not stop movement. These mechanisms prioritize survival but do not prove zero future deaths. Monitor intent remains Off after the Market test; do not restart farming implicitly.

Runback monitor deployment verified: appPID18620, runback_monitor_revision1.224 targeted tests passed. Live safe Market leg186188->194188 covered8tiles in1.3seconds (6.09tiles/s), no stalls/recoveries/HP loss/deaths; minimumHP79.7%. Proof reports/runbacks/live-validation.json. This is not combat-runback qualification. Farming remains Off, character stays in Market; scroll stored, no carried protected valuables at check.

September 10 full Meteor loop integration supersedes the earlier disabled-packing note: meteor_banking.resume reconciles exact withdrawal UIDs, exchange receipt and Market scroll storage, returns through Mark.Controller to Phoenix, then normal city/supplies/hunt management continues. App Farming On routes pending Meteor trips to the controller before native combat. after_shopping checks ten available Meteors even with free bank space; batch consolidation precedes deposits and rereads inventory/bank money after return. Extra Meteors outside the selected batch are banked before departure. Market return checks carried protected items both before travel and immediately before the transport choice. Market-full journal resumes only after manual halt clearance. profiles/meteor-banking.json is enabled, connected approach230240 replaces unreachable platform tile242240, Market bank approach186188. The connected approach was reached live; NPC interaction there and an unattended full cycle remain unvalidated.247 targeted tests passed, including full simulated round trip, partial withdrawal/exchange resumption, missing scroll, duplicate IDs, capacity stop and startup order. Safe reload verified appPID39368 with meteor_loop_revision1 and runback_monitor_revision1. User manual Stop at1789039051.905 was respected; still Off at Market230240, HP695/872. Pressing F10 resumes the stored_in_market journal and returns to Phoenix before Bandits. No automatic withdrawals or new exchange occurred in this turn. Existing banked scroll UID293029895 remains the checkpoint. Never claim a combat no-death guarantee.

September10 Market return stall fixed: warehouse frontage at186188 rejected repeated short walking steps; after moving clear, exact waypoint194188 also caused one-tile sprite clicks from195188. Market return now uses an east exit waypoint194188 near the warehouse with explicit radius2; default travel still requires exact arrival and service identity/dialogue guards are unchanged.126 targeted tests passed. Supervised return through Mark.Controller verified Phoenix1011 arrival193266, HP695/872 unchanged. Final Market transport approach took8seconds,38 observed tiles,1stall/recovery,0damage/deaths. Proof reports/banking/market-exit-validation.json. Normal controller resumes journal returning at Phoenix before supplies/hunt; do not replay Market exchange.

September10 recording review: user explicitly authorized inspection of record_2026-09-10_07-35-59.mp4.175.47s recording sampled across full duration; game surface black, farmer UI visible. Memory trial records show Phoenix->Bandit return195256 to farm boundary took64.4s,54 approach inputs (35runs,19jumps),6stalls,2recoveries,0HP loss/deaths. Root cause: NativeFarmSupervisor.patrol_step(chase=False) still called terrain.path, while town used straight_path. It now uses straight_path and preserves checked path caching/avoidance. Same installed terrain/start/goal341441:331cardinal tiles both, compressed12tile segments71->31,short segments51->5; this is an offline plan comparison, not a measured speedup. After one verified detour of at least3tiles ending at its target, native movement clears6second run fallback while retaining30second failed-tile avoidance. City arrival already within verified town bounds no longer walks to centre solely to satisfy visit bookkeeping; full fresh identity/death/map/bounds checks still apply.186 targeted tests passed. App return_path_revision2 identifies deployment; safe reload queued while farming. Do not force a town run just for benchmark: measure next normal runback against recording-review.json, include downtime and survival. No new visual permission for live gameplay was granted.

September10 review of record_2026-09-10_07-46-52.mp4 explicitly authorized by user.71.43second recording shows real game surface; visible counter264 near start to293 near end (~24/min overall clip). MP4 creation_time is near file completion; do not use it as recording start when correlating logs. Correlation by visible counter values confirms repeated stationary observations at371440/338437/373423. scatter_landing used fixed518396 projection while flying anchor was near510313; chosen diagonal landing could repeatedly fail trial visible-input check (visible_route_delta cannot shorten diagonals). Fix passes current config.player_anchor to scorer, rejects recent failed movement tiles, and checks fresh wounded group identities before mandatory post-Scatter jump. At least2 currently aimable living enemies with HP reduced since the last cast within3seconds and current Scatter range cause another cast before relocation; emergency healing/evasion still precedes this decision. No stale/dead/moved identity is attacked.95 targeted tests passed; safe reload queued, deployment identified by scatter_projection_revision2. Performance improvement still requires a complete measured window. Live gameplay remains memory-only; video permission does not authorize new live visual inspection.

September10 repeated combat blocks/slow jump-Scatter follow-up: logs include jump338461->329458 clicking exactly the selected monster position. Scatter candidates now exclude occupied monster tiles and memory-draw body click boxes, retaining dense nearby groups. Jump never-started timeout reduced1.5->0.8s; moving/partial arrivals keep prior observation budget and emergency care. Native attack dispatch can refresh aim for the same pinned UID+object+name moved <=2tiles; re-read positive HP, selection, fresh life range and clear viewport before adjusting input body, retain Stop/focus guards. Telemetry/strategy use actual refreshed aim. It never substitutes another nearby actor.101 targeted tests passed. Deployment identified by scatter_projection_revision3; safe reload queued with On preserved. Measure postreload verified kills, movement blocks and landing-to-Scatter delay; no sustained improvement claim before a full window.

September10 UI request: replace main stats attacks/ammo counters with level and next-level ETA beside kills and kills/hour. farm_stats renders a compact two-line block. Native XP telemetry now includes the per-level requirement from the existing memory table; ETA=(required-current)/positive net XP-per-hour. It is approximate; no ETA for stale (>15s), nonpositive/missing rate, disconnected or paused state. Experience sample receipt gets a wall timestamp.79 targeted telemetry/experience/native tests passed. UI revision field level_eta_revision1. No gameplay timing/loot/route changes in this update; safe reload used for deployment.


## 2026-09-10: Market warehouse click recovery

Market Warehouseman UID101388, model87, tile182180 needs draw Y minus64, not the town-vendor minus32. At player182184, old638281 failed;638249 opened Warehouse twice through memory, including ordinary short click timing. Shared interaction_point now supplies open-bank and vendor-status so approach reachability matches the actual input.61 targeted tests pass. App154948 loaded market_warehouse_click_revision1; actual open-bank succeeded after reload. Proof reports/banking/market-warehouse-click-validation.json.

Seven carried Meteor UIDs293101846 through293101852 deposited with exact UID receipts, journal updated. No protected items remain carried. Latest exchanged MeteorScroll293101544 is absent from bag and current Market bank (older scroll293029895 is present). User has been asked whether they moved/used/unpacked it. Journal remains storing_scroll; do not repeat exchange or silently bypass expected-scroll verification. Remain safe in Market pending reconciliation. Diagnostic .runtime/overnight.stop remains; remove only that diagnostic stop when recovery is authorized and reconciled.


User confirmed "i used it" for missing MeteorScroll293101544. Recorded scroll_disposition (used_by_user, explicit confirmation) and cleared only that outstanding scroll_uid expectation; original exchange snapshots and consumed UID preserved. Verified no carried valuables and all seven deposited Meteors present before restarting controls. Controller157224 resumed Market return; do not treat the reconciled scroll as lost or repeat its exchange.

Market return recovered: generic service travel stopped12tiles from Mark.Controller and no usable matching dialog followed. Qualified closer212214 produced exact saved free-return dialogue. Saved return approach212214; Market exit trip now reaches that approach instead of stopping on generic NPC reachability. A repeated open immediately after closing an already-open dialog failed; a subsequent fresh open at214218 and exact Yeah. Thanks. selection verified map1011 arrival193266 with unchanged silver. All valuables had already been banked. Journal return_verified recorded; controller159004 reopened Phoenix bank and marked journal completed, then began Bandit runback.11 Meteor coordinator tests pass. No source app reload needed for new standalone controller changes.


Latest Market recovery: second automatic10Meteor exchange created scroll293124583. Old186188 bank approach stalled at190189 for over100seconds. Replaced bank approach with186184 then182184, no early NPC visibility stop, close active dialog first. Reached both tiles and opened warehouse; exact receipts banked scroll293124583 and Elite UIDs293112564/293120479. Shared helper used by Meteor and overflow trips. Eastbound departure waypoint now194184 (observed clear landing), avoiding blocked194188.34 targeted tests passed. No app reload required; restarted standalone controller reads new source. Current east comparison sample marked invalid for geometry ranking due navigation repair; retained full operational downtime and added replacement east15min stage within120min budget.

Market exit follow-up: generic travel stalled at204193. Verified three ordinary12tile axis jumps204193->216193->216205->216217. Exit interaction initially walked to214218 without opening Dialog, then reopened successfully there. Saved exit corridor204193,216193,216205,216217 and final214218. open_saved_service now retries only initial NPC opening at most3times with fresh memory reads (2sec per attempt), rejects unexpected dialogue, never replays options/fares.36 targeted tests pass. Exactfree return toPhoenix193266 verified; reopenedbank and journal markedcompleted. User-requested farm restored with5potions/10975arrows. These checks never captured or inspected the game visually.


## September 10: 40–50 kills/min target and manual-pause attribution

User raised the target to 40 minimum, 50 stretch. Policy, AGENTS, route workflow, the existing five-minute heartbeat and runtime performance check now read/use that target. Overall elapsed time includes recovery and restocks; two non-overlapping low five-minute windows plus low full fifteen-minute rate trigger diagnosis/reassessment.

At 17:44 UTC the last runner stopped with kill_counter_discontinuity after a 19-second manual mouse pause; a later memory sample showed death. User subsequently revived and resumed. Far-east stage 3 is non-comparable and unsafe. East replacement started, then was interrupted for safe deployment. Read the current experiment journal for activation and pending reload state. Do not claim a repeated winner yet.

trial.py now discards only the counter baseline and pending attack during manual mouse/F11 pause. It retains confirmed totals and elapsed time, excludes manual kills, and keeps the normal discontinuity guard for unpaused observations. Regression simulates a 100-kill manual increase then a single bot kill. 35 focus/route-optimization tests passed (old supervisor fixtures updated with last_target). Safe reload was requested; verify current PID and reload result before claiming the fix is live.

Safe handoff verified: new app PID 202704, controller 198160 resumed east at 1789062929.3078232; manual-pause source fix loaded. A fresh replacement sample includes the return from the safe parking spot. Earlier reload/interruption downtime remains in overall windows.


At 18:12 UTC, southeastern fallback also starved (14 verified kills/461 seconds; zero in the last full five-minute window). It was ended early and retained as non-comparable under the latest underperformance adaptation instruction. New saved exploratory variant bandit-east-ridge extends to anchor477477, base441441513513, expanded429429525525. Its 12-tile cardinal connector to the existing verified town path was terrain checked. Activated1789063926.163197 including transition; sample due1789064826.163197. Fresh memory then confirmed many normal Bandit/BanditL33 near x470–510/y420–490 and32 verified kills in the first49 seconds including travel. This is an early response, not sustained proof. Maximum original test budget still applies; no farming stop timer. Do not select a qualified winner from this single short sample.


At 18:27 UTC the east-ridge full fifteen-minute result was216 verified kills (14.4/min), no deaths. Initial spawn burst did not persist; most later observations were empty. Level59->60 means it is not equivalent to earlier samples; no manual pause was present in this interval. Activated prepared bandit-eastern-corridor at1789064859.8280623 (read exact actual start from the journal). Its comparison ends at the original hard budget1789065633.7784944; only about12.9minutes remained, so this cannot qualify as a full repeated sample. Farming continues after that budget. The final potion was consumed naturally, triggering normal restock; current town runback recorded2 recovered stalls,0HP lost and0deaths while making progress. Include this restock in elapsed metrics; do not bypass it to improve the rate.


The user again reported barely killing. The original comparison ended at its two-hour cap without a qualified winner; production_adaptation now tracks continuing improvement separately, with all raw downtime retained. Current bandit-wide-circuit rotates the previously observed Bandit fields and excludes the eastern Ratling region. Its efficacy remains unproven.

Monster HP reader correction: stop requiring stale camera draw coordinates to match in an HP observation. Stable actor ID/type/vtable, world position, attribute pointer/header/table, max HP and fingerprint checks remain. Attack dispatch still independently checks projection and input readiness. Tests for camera scroll, recycled actor IDs, world movement, pointer changes and existing scene-input/native-farm paths pass:79 tests.

Initial safe reload deferred after finding no quiet spot and exhausting potions. Normal town recovery started. Assistant briefly enabled combat while town recovery owned input, causing Travel care cannot share input with farming; this was an assistant recovery error, not a new unexplained route failure. After verifying the controller exited and input was released, started only the normal town coordinator, leaving combat Off until restock. The return reached Phoenix with315HP, no subsequent HP loss and no deaths. A bounded watcher waits for natural restock completion near town before requesting the safe reload again. Verify the live PID before claiming the HP fix loaded.


## Wide Bandit circuit: first fixed validation after low-kill report

The production adaptation started at 1789065745.4480925. Its first exact
900-second window ended at 1789066645.4480925: 673 verified kills,
44.87/min (2,692/hour projected, not an actual hour). This includes reload
deferrals, input-ownership recovery and normal town/restock downtime.
Retain the wider circuit and continue five-minute reviews; this single window
is not a qualified sustained winner. Do not reopen the completed comparison
budget or churn the route based on a short quiet patch.

The natural-town safe handoff also deferred. App PID202704 remains alive with
the manual-pause kill-counter fix but without the camera-scroll HP-reader fix.
No further reload was attempted in combat. Current controller PID221128 resumed
hunting; fresh memory at 1789066604 showed alive, HP631/968, position440,498.
Do not turn combat On during a normal town/reload controller-owned Off phase.
The exact validation is persisted under production_adaptation.initial_validation
in reports/performance/bandit-route-experiment.json and the per-area runtime state.

The read-only heartbeat now records fixed fifteen-minute production windows
using conquest.route_optimization.fixed_route_windows (11 route-optimization
tests passed, including zero-kill downtime, incomplete windows and event boundaries).
This monitor change is active immediately and requires no client reload. At
1789066804 farming/controller remained live, HP583/968, 3 potions,9561 arrows;
Meteors293142812 and293143015 were in inventory. Last5m260kills52/min,
last15m602kills40.13/min, actualhour1187. Retain circuit while measuring.
The pending camera-scroll reader change still requires a safe app handoff.


## Continued wide-circuit observation (no gameplay mutation)

Five bounded live-controller checks through1789067132 confirmed app202704 and
controller221128 alive/hunting. Last5m eased45.4 ->43 ->38.8 ->34.8 ->32.6/min
while overlapping15m rose46.87 ->48.67 ->49.6 ->51 ->52.33/min as earlier town
downtime left that window. Do not interpret the rising15m alone as improving
current density. Next fixed15m ends1789067545.4480925; keep observing before
route churn unless safety requires action.

reports/performance/bandit-encounter-check.json compares adjacent5m periods:
222kills/110attacks/40%empty then153kills/94attacks/49.4%empty. A brief manual
mouse pause also occurred in the latter. These are encounter observations, not
proof of spawn limits or exclusive attribution of the performance decline.
Memory at1789067235: aliveHP797/968; normal automatic potion use reducedstock
from3to2. Meteors293142812 and293143015 retained; +1item293145120 picked up at
1789067103.758341 and confirmed in inventory/pickups.jsonl. No reload/route
control change was made during these checks. The bounded monitor session91233
completed normally. The camera-scroll HP-reader fix remains pending safe reload.


## Protected town reload completed / reader fix now loaded

Heartbeat confirmed app234372 started1789067700.417 after monster_health.py
mtime1789065840.617396 (SHA2566bd128065fbc385c57ce2000e5ae9562545177f6c7b8cfc5bfefc9e9195ce0a7).
Town helper232248/session90413 exited0. It returned to town with TravelCare,
restocked, verified storage for Meteors293142812/293143015/293145937 and
+1item293145120, then completed the existing safe reload handoff. Fresh app
automatically resumed at1789067704.161807, controller234988 hunting. No death
was recorded during these legs. Do not use town.json last15.7s shop leg as
the full farm-to-town measurement; history.jsonl contains the whole sequence.

At1789068037 HP738/968, potions5, arrows9813, no carried protected valuables.
Full last15m193kills12.87/min includes the maintenance trip; actualhour1339.
The second fixed route window was376kills25.07/min, so the earlier44.87/min
window did not hold. The loaded reader repair gets a separate validation ending
1789068604.161807 before further throughput route churn; all earlier downtime
remains counted. Repair/source proof is in report.repairs.camera_scroll_hp_reader.
At1789068091, since-resume156kills/387.3s24.17/min, not yet at goal.
The heartbeat respects controller failure/manual control immediately and only
defers throughput reassessment until this repair validation is complete.


## Northern field adaptation after completed HP-reader validation

The exact post-reload900s window ended1789068604.161807 with218kills14.53/min
including return travel. Reader correction alone did not solve poor throughput.
About66% of observations after resume were empty. A group encountered during
the northern runback produced21kills atplayercell352,352; aim targets included
ordinary Bandit HP817 at358,367 and361,390. Saved terrain-checked candidate
bandit-northern-fields, anchor373,377,boundary329,337,457,417, with direct
terrain-derived outbound/return waypoints. Adjusted blocked patrol tile437,405
to walkable437,404. RouteLibrary reload validation passed.

At1789068629.5691855 switched from wide circuit to northern fields through the
existing ownership-safe route helper. No client reload or forced restock.
Archived old adaptation, preserved allrawcounts and completedcomparison budget.
New15mvalidation ends1789069529.5691855. Do not churn the new route based on
rolling windows containing the failed old route; safety/manual/failure checks
stilltakeprecedence. Checker nowlabels this validate_current_route_adaptation.

At1789068729 after99.9seconds149verifiedkills89.46/min (burst only), HP766/968,
player445,341, attacks/movement fresh in SQLite, potions1. No deaths observed.
All three initial bridge summary life:null values were scene-availability flags;
fresh health memory and event history confirmed active movement/combat. Do not
restart based on those transient scene observations.


## Combined Bandit fields activated after northern decline

Northern route first900s609kills40.6/min, but by1789069627 full15m468kills31.2/min
and latest5m54kills10.8/min; low preceding5m confirmed the reassessment trigger.
No dead controller, manual input, supply exhaustion or storage latch. HP662/968.
Saved/validated bandit-combined-fields joining previously observed northern and
southern fields, anchor405,413,boundary329,337,513,525,36 terrain-checked grid
waypoints and direct terrain-derived town connections. Activated via the existing
ownership-safe helper at1789069636.7284296. No new town trip or reload. Original
comparison remains complete_inconclusive; both old adaptations archived.
Full15mvalidation ends1789070536.7284296. Keep counts/downtime and wait for
that window before judging this change; safety/manual control remain immediate.


## Market warehouse reachability and arrow overbuy fix

Loaded in app PID249768 after a protected reload with farming manually Off.
Market position183,185 is already within memory-confirmed reach of warehouse
NPC101388 at182,180. approach_market_warehouse now checks vendor-status before
movement and passes vendor_type=0 / arrival_radius=2 to corridor travel.
The bank-open operation still independently verifies the NPC. A live open
attempt returned Game lost focus; no input sent. Do not claim a bank transaction
was completed. Proof: reports/performance/warehouse-reachability-fix.json.

Arrow surplus came from proactive reload at23–25 remaining, leaving many
physical partial packs. Reload now waits until below the active attack minimum.
Physical pack count includes mixed tiers, partial packs and equipped ammunition
with UID deduplication. Both supply controller and final town purchase guard
block buying at ten packs, and upgrade review / banking budgets respect the cap.
Spent1–2-arrow remnants can be recycled during a legitimate shop visit. Existing
22 carried IronArrow packs were retained; no disposal of usable excess occurred.

UI/F10 Farming Off now writes the overnight controller stop flag even when no
farm thread exists; internal town control updates remain separate.229 relevant
tests passed across arrow upgrades, supplies, banking, meteor banking, telemetry,
overnight, market services, memory NPCs and protected reload.

Current Market consolidation journal is storing_scroll, scroll UID293152528,
origin1011. Preserve this receipt and do not repeat exchange or leave Market
with valuables. Manual Off remains authoritative; no automatic farming restart.
The combined Bandit route trial began1789069636.7284296 but was interrupted by
restocking, Market banking and manual Off. Keep raw elapsed rates and mark the
comparison interrupted; never restart the completed original experiment budget.


## Explicitly consumed MeteorScroll receipt

User confirmed "I used the scroll" for UID293152528. Journal preserves original
exchange/UID evidence and records user_confirmed_scroll_consumption for that exact
UID. Market banking permits this explicit disposition instead of requiring its
storage receipt; all remaining valuables still must be deposited before return.
Mismatched IDs or inferred consumption remain blocked.21 meteor banking tests
passed. The failed controller was restarted after reconciliation with no manual
stop flag present; no game reload was needed because this controller is a fresh
process. Verify actual return outcome in current status before claiming success.


## Direct bank and hunting-return travel (September10)

User requested removal of right-angle detours and repeated walking/pauses.
TerrainMap.travel_path now uses eight-direction routing plus checked line
shortcuts. clear_segment checks every raster cell and diagonal corner sides;
portal exclusions remain blocked. Town travel and native hunting return use
travel_waypoint, with8-12-tile jumps where clear/visible and short runs otherwise.
Bridge route input permits checked diagonals and repeats pre-input life/focus/
manual guards. Short corner runs retain the bounded client-pathfinder behavior.

Town path suffixes are reused while actual memory position remains on the path.
Failed landings remain excluded after progress, a partial advancing step no
longer starts six seconds of walking, and a successful recovery immediately
restores long jumps. Verified run arrival waits0.1s; jumps0.4s with two distinct
memory observations. Survival checks continue throughout arrival polling.

247 tests passed across navigation, route input/recovery, scene input, overnight,
native farming, trial recovery, banking, world travel and Meteor banking.
Installed Phoenix planned steps: bank230,250 to hunt341,441 reduced302->198;
return345,418 to pharmacist191,250 reduced322->196; pharmacist191,250 to bank
approach227,243 reduced67->53. These are terrain path lengths, not elapsed-time
claims. Protected safe reload changed app249768 to290436. Fresh controller293112
used diagonal native jumps on its bank trip; input log confirms Ctrl and live
position progress. Full live validation is in reports/performance/direct-travel-validation.json.

Live validation complete: natural bank/shopping/return resumed hunting. New
return28.8s,170 observed tiles, one recovered stall, no HP loss or death. Earlier
return29.3s,274 observed tiles, but arrival boundary points differ; do not claim
a matched speed improvement from these two runs. Remaining short walks are
terrain/viewport constrained. Preserve this caveat in performance reporting.
