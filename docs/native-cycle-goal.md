# Native cycle goal

Objective: fix the Market handoff and Spiritual refill blockers, finish the
interrupted required trip through the native controller, then complete one
further naturally triggered cycle without AI-driven trade stages. Preserve
memory-only decisions, verified transactions, original visit timing and manual
Stop. Run regression tests only after the first live completion.

## Authoritative starting state

- Installed release: `2026.09.23-1078-cycle-r19`, controller PID1150276.
- The failed route PID1156188 has exited. Failure time1790199942.9663389,
  detail `Unqualified merchant client fingerprint`, before Market handoff grant
  admission. The `reason: outside_gui` status field is stale from an earlier
  reposition event and is not the failure classification.
- Farmer process18532/creation134345064188672222, alive at Market(228,193),
  Farming Off revision6, no manual fence or trade windows, no Stop file.
- Original required restock visit `a34ed8c7e6ce45449fccbd2e58734b7c` remains
  `town_work`, required_at1790199752.0382073. Do not replace its baseline/time.
- Meteor consolidation is `storing_scroll`, exchange verified, carried scroll
  UID296402582. No Market storage receipt or return submission exists yet.
- Market service visit `1ad0481ff9ec446594ca911d75077af5` has expired deadline
  1790199981.5192413. Native fallback must preserve the deadline, with no fresh
  manually granted delivery budget. Merchant route has no active submission.
- Farmer has five selected potions and a fresh spare5000 SpeedArrow pack;
  equipped ammunition is observed separately. No repeat shopping is authorized
  merely because the surrounding restock was interrupted.
- Spiritual booth30/inventory8, refill On, operations Off. Missing historical
  BambooBow295326974 is explained by verified sale360/event9051, gross1000000,
  observed wallet480428→1450428. Unknown losses remain blocked.

## Current implementation work

- Trade agent: build-aware reader in `service_visit.validate_grant`; frozen.
- Banking agent: exact verified-sale reconciliation in restoration/listing
  planning, including prior inventory→verified listing→verified sale; frozen.
- Resume agent: native same-trip continuation after existing Meteor storage and
  return, followed only by unfinished cash/banking/panel-close tail. It must
  record actual completion, not infer it from stocked supplies or replay the
  full restock. Source review is independent; tests remain deferred.
- Root: controller-only deployment helper
  `.runtime/reload_natural_cycle_r20.py`, not yet executed. It preserves the
  original journals and rechecks exact parked identity, Off revision, closed
  windows, manual controls and absence of an active route process.

## Completion evidence required

1. Spiritual successfully fills available slots through native verified listing
   receipts and remains able to reconcile later verified sales.
2. Original town visit reaches complete with verified transaction outcomes and
   a fresh post-return native kill receipt.
3. A subsequent naturally triggered visit completes the appropriate banking,
   supply, delivery/refill and return work without AI advancing input stages.
   No forced visit, reset journal or qualification-only probe counts.
   The goal also requires an actual ordinary automatic delivery and subsequent
   recipient refill; two warehouse fallbacks alone do not prove that path.
4. Targeted regression checks cover the fixes after item2. Long-run monitoring
   includes town downtime; short hunting bursts do not prove sustained rate.

The goal remains active until all evidence is inspected. The earlier paused
coding task must not be resumed.

## Live restart findings

Release r20 deployed with the original four banking journals unchanged. Its
native Start reached the capture guard, then stopped before movement or journal
capture because Spiritual had a pending refill request. The request had no
input owner or grant. This exposed a distinction the guard had missed: a
qualified request waiting for service does not yet own input.

Release r21 adds that distinction without clearing or granting the request.
It also recognizes an exited `needs_attention`/`failed` route as terminal for
the stopped-Market refill check; OS process exit and all fresh safety checks
remain mandatory. The native controller still finishes Meteor banking before
servicing refill. No regression tests have been run in this goal phase yet.

## First native completion

Release r21 controller1201176 / route1174532 completed original visit
`a34ed8c7e6ce45449fccbd2e58734b7c` at1790202281.2740276 without replacing
its required time or baseline. Six exact Market warehouse receipts include
MeteorScroll296402582 and the five eligible equipment UIDs. Phoenix cash tail
deposited2900 with a verified receipt, retaining200 carried silver.
Fresh native kill row4408424 at1790202280.4302967 counted5 after return.
The regression gate is now open.

Spiritual remains booth30/inventory8 after the Phoenix fifteen-second service
window; it requested a new handoff. This is still unresolved. Next natural trip
and ordinary automatic delivery/refill remain required before goal completion.

## Current deployment and remaining proof

Release `2026.09.23-1078-cycle-r22` is installed, controller1206728,
route1200992. It fixes non-Market town refill grants: the former15-second
window could never satisfy the listing engine's20-second minimum. Earned
1078 listing capability now selects the existing exact-recipient45-second
scope. Market visit deadlines and urgent recovery scope remain unchanged.

Deployment used a protected memory-only parking handoff, retaining the same
game processes. Farmer returned to hunting; fresh kill session1790202716.11206
already had29 verified kills by1790202775. No forced town trip was used.
Spiritual remains booth30/inventory8, refill enabled and waiting for its native
handoff. Dutch remains booth17/inventory0. Trading intent remains Spiritual Off,
Dutch On. Next native handoff is governed by the existing persisted timer,
not a newly forced AI window. No second natural visit has completed yet.

Post-first-live regression runs passed:108 restoration/service/sales/town
tests,47 interrupted-restock/tail tests (20 overlap the former run), and19
handoff tests. Tests were isolated from live game state. No full-suite claim.

Helpers used: `.runtime/deploy_takeover_r22.py` and
`.runtime/resume_takeover_r22.py`. Safe deployment proof remains in
`.runtime/takeover-r22.json`. Read-only observers are
`.runtime/live_cycle_status.py` and `.runtime/native_cycle_progress.py`.
The active goal is not complete: verify native Spiritual listings plus a
subsequent natural trip with actual ordinary automatic delivery/refill and
return. Keep the older coding task paused. Preserve all original banking
receipts and real downtime in performance assessment.

## Scheduled r22 handoff observed

At1790203134.970521 the native hunting controller attempted the pending
Spiritual handoff. It finished `unsafe_deferred` at1790203147.4734251 after
the ordinary12-second safe-parking window; no grant/listing transaction ran.
Farming resumed automatically, control revision5, route1200992 remains live.
Next persisted machine-state handoff check is1790204034.970521. Do not reset
the timer or mistake this deferred attempt for successful refill.

Fresh memory supplies afterward: four Painkillers, equipped SpeedArrow4527,
spare5000, two loose Meteors, inventory7/40. No natural restock is due yet.
An ordinary restock path is ready to collect stored scroll296402582 for Dutch;
a stocked urgent bank trip deliberately skips merchant delivery.

Read-only raw kill journal review: first complete5-minute r22 window
1790202716.11206–1790203016.11206 had298 verified kills (59.6/min). Since the
first completed return, including the subsequent deployment downtime,901
kills over13.7417 minutes (65.57/min) at1790203079.1076326. This is not a
completed long-run optimization comparison. Preserve raw windows and timers.

First full15-minute window from1790202254.6056738 completed with964 verified
kills (64.27/min), including deployment downtime. Its three disjoint5-minute
windows were341/233/390 kills (68.2/46.6/78.0 per minute), so the configured
low15-minute plus two-low5-minute route-reassessment condition was not met.

Read-only parking diagnosis found no demonstrated planner bug; intermediate
parking threats/step positions were not logged. Do not invent a cause or
weaken clearance. A bounded read-only watcher is running as exec session29781
(`.runtime/watch_native_cycle.py 1200992`), until900 seconds from
1790203387.55. It reports exact route process liveness and town/handoff phase
changes. Reuse/poll that session rather than start another watcher. The route
was OS-confirmed live at1790203447.69 with836 kills in the current session.

## r23 current state

Second scheduled r22 refill attempt4036.3803–4054.5547 also finished
unsafe_deferred, without a grant or listing. Earned1078 refill requests now
receive up to30 seconds of nearby parking; Market deadline, no town retreat,
24-tile clearance, three quiet stable seconds, manual controls and the900-second
timer are unchanged. One compact native merchant_parking_finished event records
memory observations and bounded movement counters. Independent review passed;
44 targeted parking/handoff/safe-reload tests passed.

Release `2026.09.23-1078-cycle-r23` is installed. ControllerPID1200992
(Windows reused the old route PID), new native routePID1215300. Original game
processes unchanged. Fresh Farming On revision3 and native hunting verified.
Next handoff remains1790204936.3803387. Spiritual booth30/inventory8 and
Dutch booth17/inventory0; no successful refill or second natural trip yet.
New read-only watch exec session13879 follows route1215300 for900 seconds
from1790204621.20. Prior watcher29781 ended its bounded wait normally.

The r23 deployment parked at1011(470,443), HP1234. After restart, two nearby
unknown-life monsters made the reload-resume helper's clear-spot check false.
The deployment Stop marker, Off revision0, exact identity/position/HP and fresh
memory were unchanged. The helper's combat-resume condition was corrected to
require fresh living healthy ownership rather than absence of nearby monsters;
normal Start then restored the prior Farming On intent. Merchant grants and
controller-close parking checks were not relaxed. No forced town trip occurred.

Performance reassessment: r22's first15m had1039 kills (69.27/min). The next
two disjoint5m windows had240 and172 kills (48.0 and34.4/min), with54–55% of
target observations empty and increased search movement. The full following
15m window3616.11206–4516.11206 had478 kills (31.87/min), including r23
deployment downtime. The low-rate diagnosis is established, not hidden by
session resets. Target scarcity/search travel was already present before the
deployment; one~19-second merchant handoff also contributed. Existing region
comparison is interrupted with zero qualified samples, so this is no authority
to declare or replace a winning patrol. Current Bandit north/center/south route
is retained pending controlled comparison after the merchant loop is proved.

Code-review findings and subsequent engineering priorities are in
`docs/native-cycle-review.md`. The goal remains active and incomplete.

## r24 parking correction and execution-efficiency audit

The r23 native parking attempt at1790204937.1881 deferred after31.328 seconds:
three reached steps, two unreached steps, four no-candidate observations, and
three nearby threats at1011(316,387), HP1335. Its final movement error was
"Route movement stopped progressing". No unsafe grant was issued; farming
resumed. The next native check remains1790205837.1881185.

Offline reproduction found an actual escape-planner defect: the old cardinal
path search omitted failed-tile avoidance and checked only its final waypoint.
For source(50,50), threat(49,49), failed tile(54,50), it selected(62,50), crossing
that failed tile. The r24 correction uses checked travel diagonals, propagates
avoidance through the path and waypoint, and verifies the final segment after
native-camera shortening. Unprojectable candidates allow other choices. The
30-second budget,24-tile clearance and three quiet seconds remain unchanged.
65 targeted tests passed and independent review found no actionable issue.
This source defect is not proof of the cause of the preceding live timeout.

The user challenges patrol-path attribution of the low kill rate and observes
wasted attack/movement time. Empty-target counts do not establish spawn scarcity
or a route defect. Keep the saved patrol unchanged and audit execution cadence,
avoidable repositioning, stop/start delays and gaps while usable targets exist.
Source and event-timeline audits are running independently. r24 build is pending;
no successful Spiritual refill or second natural cycle is yet established.

Event-timeline audit supports investigating execution, not attributing the
decline to patrols: passing5m windows had298/360/381 verified kills from52/56/61
attack attempts and65/60/72 movement attempts. Low windows had240/172 kills
from64/59 attacks and91/91 movements. Median interattack gaps were3.85–4.87s
despite a configured0.8s recast floor; that floor is not a promised attack rate.
All14/19 gaps of at least5s in the low windows included movement and positive
target observations. Positive observations alone do not prove attackability.
Recorded movement elapsed rose to157/141s per5m from96–108s, but overlaps
mid-jump attacks and therefore is not exclusive idle time. At1790204074.70,
four nearby targets were observed and an expired-observation/foreground guard
deferred attack; the preceding attack gap spanned approximately9s and three
movements. Source scheduling and stale-observation handling are under review.

r24 deployed with protected parking at1011(383,368), HP1239. New controller
1217284 and route1209588; unchanged game identities and normal Farming On
revision1 restored. Original handoff timer remains1790205837.1881185. Previous
watcher13879 ended on old-route exit. This deployment changes parking only;
combat scheduling corrections are not yet installed.

r24 native scheduled refill succeeded: attempt1790205839.0809705 earned a
listing_1078 grant ending1790205895.241318, released1790205884.249752, then
native hunting resumed with Farming On revision5. Exact validated listing
receipt `booth-list1078-refill-064028574e2d48eb8b884cc3e8f11cbf` observed at
1790205873.6196835 proves Spiritual listed BronzeArmor+1 UID295632031 for
120000 in its own booth102581, slot29. This was the highest-priced known
queued item. Booth29→30 and inventory8→7. No direct probe, timer reset or
manual trade advancement was used. The next native check is1790206739.0809705.
Spiritual refill is now proven for this scheduled listing. The required next
naturally triggered ordinary delivery/town/return cycle remains unproven.

## r25 combat scheduling correction

Independent source review confirms expensive ground-item/ownership work ran
after target observation but before its0.35-second dispatch guard. In the
ten-minute1790204900–5500 trace,117 attacks and167 movements had a4.325s
median interattack gap;41 gaps of at least5s included movement and positive
target observations. Sixteen attack-expiry pauses immediately followed a
positive-target event. This demonstrates wasted retries without establishing
that every delay shares that cause.

For coherent native profiles, r25 moves valuable-loot work before strategy and
final target observation. Real pickup input rereads native life/projection and
requires unchanged tile, anchor and window origin before the existing fresh
drop/life/inventory/Stop/foreground checks. Changed projection causes a no-input
full reobservation. Target scan age is measured from the actual read; its guard
is unchanged. Profiles lacking coherent native projection retain old scheduling.
Patrol route, post-cast jumping policy and0.8s cooldown are unchanged.
110 focused tests passed in26.94s; independent review found no remaining
definite blocker. Source changes are trial.py and test_trial_scatter_cadence.py.
r25 build is running; live performance improvement is not yet established.

Read-only fixed-wall-window helper `.runtime/compare_execution_windows.py`
reports real journal kills, attack gaps, movements and pause reasons. r24's
first5m starting1790205569.897656 had275kills/55KPM,52attacks,58movement
attempts and median4.45s attack gap. This includes startup and native handoff;
it is an operational baseline, not a controlled patrol comparison. Never reset
raw performance windows to hide deployment or merchant downtime.

r25 installed successfully. Deployment required native safe parking toward
town at1011(212,274), HP1215, verified1790206254.348453. It did not initiate
banking/restocking or count as a natural cycle. Controller1225876, route1227652,
Farming On revision1 restored; native return toward the hunting area observed
at1790206362.3044345. Game processes and next native refill6739.0809705 are
unchanged. Previous watcher23396 ended on old-route exit. No r25 performance
gain has yet been measured; startup and return travel remain in raw metrics.

Read-only watcher session20496 follows route1227652 for900 seconds from
1790206379.4111009. At1790206413.3191 the initial89.38-second operational
window had40 verified kills and seven attacks, confirming native combat resumed.
No attack-expiry pause occurred in that small sample; two action-expiry pauses
and one target-scan-expiry remained. This is not enough evidence for a sustained
throughput claim or a complete five-minute comparison. The active goal still
requires the next naturally triggered ordinary delivery/town/return cycle.

First complete r25 operational5m6323.9349039–6623.9349039:356 verifiedkills,
71.2/min,51attack attempts,74movement attempts, median4.408s interattack gap.
There were zero attack-observation-expiry pauses, six generic action-expiry and
two target-scan-expiry pauses. This includes startup/return travel. It is a
single window with changing spawn waves, not proof of sustained60/min or a
causal KPM gain. The retained4.4s cadence warrants direct phase timing.

Next source work is bounded: correct native hunting's second projection pass
which assumes viewport center after a landing was checked at actual camera
anchor, and add one aggregate timing event per minute without extra memory
reads. Timing will separate first observed exact arrival from later movement
verification and account for synchronous memory/guard/logging work. No patrol,
cooldown, safety-delay or kill-verification changes are proposed.

Second complete r25 operational5m6623.9349039–6923.9349039:367verifiedkills,
73.4/min,47attacks,45movement attempts; median4.871s attack gap. Two attack
expiry pauses remain. The native refill attempt6740.3624 deferred after30.641s:
12 reached moves, zero stalled moves,13 no-candidate observations,22 nearby
threats at1011(406,474),HP1204. No unsafe grant; farming resumed. Next native
check7640.3624065. The geometric stall fix does not guarantee a clear location
in every crowded scene; this deferral is not a completed refill.

User correctly reports merchants not embedded. At~6856 bridge layout wasempty
and both attachment.attached flags false despite fresh memory observation.
Read-only native HWND audit also confirms both owner=0, still separate visible
top-level windows for exact unchanged PIDs632952/635124. restore_hosts requires
FarmerOff+Market1036+routeexit, while auto_show_selected skips1078 observation
observers. Qualified native input bypass explains why listing could succeed
without hosting. This remains an implementation gap against automatic embedding.
Merchant agent is fixing automatic owned hosting under fresh safe handoff,
independent of trading/refill switches, preserving explicit release/manual
controls and existing timer. r26 movement/timing patches are reviewed and
frozen, but r26 build is held for this hosting fix. `.runtime/inspect_native_hosts.py`
checks native owner/PID/geometry without visual inspection or input.

Completed r25 operational15m6323.9349039–7223.9349039:1037 verifiedkills,
69.133/min including initial return and deferredrefill. Three disjoint5m
windows356/367/314 (71.2/73.4/62.8).161attacks,186movement attempts, median
4.608s attackgap, two attack-expiry pauses. This passes that full15m performance
window but is not a controlled causalcomparison or routewinner declaration.
Hosting code/review remain pending, so clients are still not embedded in r25.

The r26 automatic hosting implementation subsequently passed independent review:
safe parked field/startup hosting, native owner checks, exact lease presentation,
and separate host scheduling preserve saved trade/refill controls. It remains
undeployed. Failed host handoffs retain their own 900-second retry interval;
minimized clients are deferred. No screenshots or visual inspection were used.

Scheduled refill attempt1790207642.4177375 earned a safe listing grant after
native parking, but a monster approached the parked Farmer during the item drag.
Spiritual request booth-list1078-refill-4d1a0ff5a2f24e37b483268948a3b61b became
uncertain before any confirm_press or cancel_press. UID295202501 (+1 helmet,
price89100) remains in inventory; booth30/inventory7 and all saved ownership
fields were unchanged in fresh memory at1790208268.0366907. The exact native
price dialog remains open with that UID and an empty price buffer. This is not
a listing success. Existing reconciliation cannot settle a request with neither
confirmation nor cancellation marker, so waiting alone will not recover it.

The bounded r26 recovery change reuses the existing exact-request once-only
Cancel worker under a fresh normal scheduled safe grant, followed by unchanged
ownership reconciliation. Host intent must defer while any transaction remains
unresolved. Original preflight snapshots encode closed_modal/trade_open/
request_open rather than trade/request; compatibility must retain that proven
closed-window baseline without relaxing identity or asset comparisons. A normal
controller reload preserves the unresolved journal and game clients; no forced
transaction stage, refill timer reset, or game disconnect is authorized.

Another naturally triggered urgent-bank cycle completed under r25 without AI
input: town56c9afbcb75b431da71d1cdd0368eff8, required1790207764.0949092,
complete1790207918.8445663. The +1 bow type500064 inventoryUID296424787 triggered
the trip and was verified in warehouse1790207823.223. Eleven loose Meteors
were individually stored; silver deposit33773 was verified. Supplies remained
7127 arrows/two potions before and after; no purchase event occurred. The
post-return kill_verified row4425452 at7916.9066522 counted6 and supplied the
town completion proof. This validates stocked urgent banking/return, not an
ordinary restock/delivery cycle. The intervening merchant grant could not clear
the pending listing, so it is not counted as merchant success.

r26 production froze after the legacy closed-modal compatibility fix and
host-intent suppression during unresolved transactions. Combined focused
hosting, handoff, cancellation, listing, timing and combat geometry checks:
168 passed in30.88s. Release build is in progress; no deployed success yet.

r26 installed: controller1224256, route1238224, resumed saved Farming On.
Safe deployment parking1011(289,283),HP1287 at1790208611.7742333. No game client
disconnect and pending listing journal unchanged. Live integration exposed
another data-shape mismatch: TradeMemory1078 annotates each inventory/booth item
with derived category while saved native preflight does not. Exact unchanged
native preflight passed but the enriched observer snapshot failed admission.
r27 removes only this derived annotation from a copy for cleanup admission;
native Cancel ownership checks remain unchanged. Both actual live snapshot
paths passed a SQLite mode=ro admission dry run1790208821.29, with no input or
journal changes. 65 focused checks passed. r27 building, embedding still blocked
until the exact unfinished request settles.

Initial r26 timing event1790208839.0156364 (60.016s) attributes11.063s to
supervisor,5.421s inventory/player/projection,7.530s care/skill,11.659s escape
scene reads and14.529s post-verification. Event commits2.642s, movement dispatch
2.952s and other dispatch3.096s. Moving_* are subsets and arrival metrics overlap;
do not sum them. Seven sampled arrivals averaged~0.165s from first observed
exact arrival to verifier entry. This supports auditing repeated synchronous
observation work before attributing all low throughput to route geometry.
No new combat changes are authorized by this observation alone; complete
merchant recovery/hosting first and retain all survival/Stop freshness checks.

r27 installed controller1242608. Deployment parking proof1011(245,264),HP1274
at1790209065.3731933 was insufficient: Farmer died at that position during
the controller restart gap (fresh HP0/ghost on startup). This is outside the
saved PhoenixCity boundary y<=258. Do not reuse a temporary quiet field as
unattended deployment safety. Future deployment needs verified city arrival
near the saved restock anchor191,250 before close, with normal care/Stop guards.
The strict recovery helper restored prior Farming On only for unchanged exact
process, Off revision0, our deployment Stop marker, same-position native ghost
and qualified Revive. Native revival succeeded to1490HP; no direct revive click
was sent by the helper.

Native scheduled cleanup then succeeded. The exact old Spiritual request is
aborted with cancel_verified/stock_unchanged true, listing_submitted false,
one cancel_press marker1790209232.3256965 and no confirm_press. Its queue cursor
was released without counting a listing or sale. Ordinary next handoff remains
1790210124.3283322; no timer was reset.

Both merchant clients subsequently embedded automatically. Native audit shows
Spiritual HWND474678740/PID632952 and Dutch HWND1310752/PID635124 owned by app
HWND216204330/PID1242608, attached true; unselected panes remain hidden. Thus
embedding is now a live verified outcome, not merely a source/test claim.

Hosting exposed one final bookkeeping race: automatic safe-Off attachment
completed before the route's host grant; the pre-admission rejection was followed
by release of a nonexistent host request, failing route1243576 with Handoff
request mismatch1790209279.797. Exact fresh no-owner/no-grant/no-manual checks,
route OS exit, unchanged profile/process, Off revision6 and prior On proof
allowed normal explicit Start (resume_after_host_r27.py). Current route1240764
started1790209457.1503894, Farming On revision9, alive1490HP and returning toward
hunting. Merchant agent is fixing the specific rejected-host-grant race in
source; not deployed yet. No general transport-error bypass is permitted.

r28 source frozen: exact pre-admission host-grant rejection now accepts only
fresh same-process attached-host/no-owner/no-grant/no-manual evidence before
normal guarded resume; unknown lost acknowledgements still revoke. Independent
review passed. A separate require_city parking mode validates saved city terrain
and anchor, follows checked native travel, then requires quiet stable living
memory inside city before deployment. Phoenix target resolves191,249; short
merchant handoffs retain their existing local park mode. Combined focused
parking/host/handoff checks82 passed in2.84s. r28 build underway.

Performance8600–9500 retained362 verifiedkills/24.133min over900seconds, including
the two deployment gaps, death recovery and host-grant failure; no metric reset
or route-winner claim. The later complete window1790209457.1503894–9757.1503894
had420 verifiedkills/84min,57attacks,68moves and4.646s medianattackgap. This is
one five-minute operational sample including return travel, not sustained proof.
Current supplies around9880:6275arrows,onepotion,31freeslots. No natural ordinary
restock trigger yet; do not manufacture one to complete acceptance.

r28 built (manifest c8b4104468bae5955aac13cba6feb193407aa9c0bffbebbf86665fbf24ea6d2d)
but remains uninstalled. Two bounded city-parking attempts stopped before app
close with TravelStalled; the r27 controller1242608 remains active. Attempt2
trace is .runtime/takeover-r28-travel.jsonl. It repeatedly reached194,255,
failed the checked short run toward191,252, then recovered through198,257 and
204,259 and repeated the same approach. The final nine tiles of movement did
not improve on the earlier best position: do not loosen the oscillation guard.
The native projection audit found flat32/16 projection with no elevation operand;
DMap elevation alone is not evidence for a pixel correction. No death occurred
during attempt2. Exact prior-On/Off-revision16, own Stop marker, exited helper
and route, unchanged game/controller identity and fresh healthy memory permitted
normal explicit Start via resume_failed_deploy_r28_attempt2.py. Route1250824
started1790210826.867; fresh worker health confirms On revision19, HP1471/1490,
position319,323 and native hunting execution.

Between those attempts, ordinary native refill succeeded while embedded:
receipt booth-list1078-refill-7bc91eb5d71641738cae6172117efabc,
Spiritual UID295202501 (+1 ShiningHelmet111636), price89100, exact memory listing
verified, booth30 to31/inventory7 to6. Normal grant attempt1790210161.5343447,
deadline1790210210.6747582; Farmer resumed normally afterward. This was a new
request after verified cancellation of the prior interrupted request, not replay.
Both merchant HWNDs again verified owned by controller1242608; their unselected
tabs were hidden. Later merchant bridge status calls timed out twice while the
Farmer worker remained responsive; bounded read-only diagnosis is ongoing.
Ordinary automatic delivery/restock/return acceptance remains outstanding.

Next goal turn: merchant status recovered in0.281s at1790210926, with fresh
snapshots and no owner; UI historical max tick gap27.5s explains delayed status
but does not identify the busy callback. Status invokes Tk window queries.
Then native route1250824 failed1790210944.2357 on a read-only health request:
GetCursorPos WinError5. Its finally block sent authenticated Off revision20;
app intent timestamp1790210944.242091, no manual Stop revision or stop marker.
After fresh idle/healthy/exact-identity checks, resume_cursor_gap_r27.py restored
On revision21 at1790211093.3180063. Route1254856 started1790211114.0594735.
Normal refill attempt1790211116.6921158 safely deferred at1790211146.9985824
because parking did not qualify; next native check1790212016.6921158. Hunting
resumed. Do not turn that deferral into a listing-success claim.

Raw fixed window1790210200–1790211100:581 verified kills/38.733 per minute.
Non-overlapping five-minute windows:460/92 per minute,0/0,121/24.2. This
triggers the low-rate diagnosis rule. The zero window contains the failed
deployment approach and recovery wait; the last includes cursor-error downtime.
Retain those gaps in metrics. The checked travel trace justifies retaining
failed-edge exclusions through a retreat; a route change or elevation pixel
correction remains unsupported. The source fix keeps the existing best-distance
watchdog and clears failed edges only after genuinely improved remaining path.
Thirteen focused checks passed, including detour retention and Manual Stop.

Another proven ordinary-delivery blocker: consolidate() can overwrite the
completed Meteor journal before completed_stored_scroll() selects its older
undelivered scroll. A durable exact-receipt scroll queue is being implemented;
bank-only equipment is outside that change. Cursor-access-gap handling is also
being fixed to preserve readable health and fence input until fresh idle recovery.
r29 deployment helpers are prepared, but no r29 release has been built or
activated yet. No live journals, timers, supply triggers or trade stages forced.

r29 build subsequently completed and verified: release2026.09.23-1078-cycle-r29,
manifest5f4882267034f6f274677ee04a7a73026cb191a056f83410ccf30fe8f160a303,
5419 files. Production differs from installedr27 in ten modules: the two r28
host-race modules and city parking, plus cursor health/guard handling, travel
failed-edge retention, Meteor selection and the new stored-scroll queue.
Cursor regression suite106 passed; queue/Meteor/policy suite63 passed;
travel focused13 passed. Queue selection refuses unfinished consolidation and
keeps exact stored receipts before replacement, with per-UID cooldown and durable
terminal transfer evidence. A broader journey-suite fixture failure is being
compared against installedr27 before activation; no broad green-suite claim.
Live route1254856 remains hunting. Complete raw five-minute window1790211200–
1790211500 had461 verified kills/92.2 per minute,65 attacks and4.167s median
attack gap. This does not replace the earlier low full fifteen-minute result.

r29 activated successfully. Bounded deployment helper1258992 retained failed
edges and reached exact Phoenix city parking191,249: final checked detour ran
through183,262 →182,250 →183,246 →188,246 →190,249 →191,249. Quiet living proof
at1790211905.9446256 had1309/1490HP and verified city terrain. No death occurred.
New controller1260820 launched Off at1790211921.4455519; fresh health preserved
exact position/HP and game identity. Both merchant HWNDs automatically attached
to app HWND160368220/PID1260820. Strict resume_takeover_r29.py restored prior On;
native route1260032 started1790211997.9342566. Its first normal timer grant began
1790212017.9076965 (next1790212917.9076965), deadline1790212066.954226.

Spiritual filled its final slot automatically during that grant:
booth-list1078-refill-141cb73e286d455285ddabdc9d395547, UID295631255,
+1 ShiningHelmet type111637 at89100silver, created1790212027.084671,
verified1790212054.712224, exact_memory_listing_verified=true.
Booth31→32, inventory6→5; refill now booth_full, pendingfalse,
next1790212956.484665. Farmer automatically resumed On revision5, fresh memory
at330,373/1309HP with external native execution. No AI listing/cancel/timer input.
Merchant status had a transient startup timeout and recovered; do not claim
that underlying UI callback delay is diagnosed. Current read-only watcher is
exec session40049 for confirmed-live native route1260032 (bounded900s).

Three representative broader journey fixture failures reproduced identically
against installedr27 and worktree source: their fixture omits the required saved
farmer transfer preference. No broad suite pass claimed. The r29 city diagnostic
attempted service-locate Pharmacist, which is unsupported by that Market-service
API; its optional trace failed harmlessly, and no NPC hit-region conclusion was
drawn. The ordinary NPC reader uses vendor-status for that memory identity.
Acceptance remains incomplete for naturally required ordinary restocking,
stored-scroll merchant delivery and post-trip hunting. Maintain that scope.
