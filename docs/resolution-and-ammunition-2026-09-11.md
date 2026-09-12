# Resolution and level 73 ammunition update

The live client changed from 1036 × 793 to 1420 × 1009. Windows client geometry
and the actor's memory draw position independently confirmed the new viewport:
the centered actor moved to (710,504). The Minimap and control bar also moved
in memory; the XP popup can now appear at (682,829), beyond the old reader limit.

The farmer now obtains current logical client dimensions for combat, item/NPC
input, shop GUI bounds, movement projection, travel healing, Fly, and reconnect
input. Physical input still uses the explicit logical-to-physical conversion.
Resizing during a GUI read or an input operation invalidates that observation.
Login fields follow the memory-read Login window instead of fixed desktop
coordinates. Gameplay observation remains memory-only.

The revival anchor is translated from the live, size-checked control bar for
non-default viewports. Runtime renderer bytes at RVA 0x9afb9–0x9b1a9 matched the
pinned local code dump and identify ReviveButton in ##SkillsPopup. The live
popup was (682,829), size 56×56, containing the translated point (710,856).
During the check, memory reported an existing death while farming was Off.
One guarded Revive click restored 1148/1148 HP at (193,266), map 1011;
farming remained Off. No death was deliberately induced. See
reports/performance/resolution-revive-validation.json. Reconnect has unit
coverage but has not been exercised by deliberately disconnecting.

The follow-up audit also removed old bounds from Scatter landing selection,
Market alternate jumps, runback escapes, reload parking, service dialog
scrollbars, warehouse amount typing and upgrade-shop scrolling. Warehouse
typing stops if the viewport changes while entering the amount. Loot and
discard targets now use the actor's memory draw position, including camera
clamping, instead of assuming the center of the viewport.

The normal ammunition preference is LuckyArrow at level 1, IronArrow at 32,
and SpeedArrow at 73. Better usable carried arrows take priority even when a
lower tier is equipped; their equip/reload does not require purchase money.
Refilling allows two physical packs in total, including equipped, partial and
mixed-tier packs. Existing excess is preserved for use. SpeedArrow capacity
was first supplied by the user and then confirmed as 5000 through live inventory
memory. The current desired supply is one equipped pack plus one spare.

Required town visits may withdraw enough warehouse silver for the first
eligible better arrow pack, subject to the cap and the transport/supply reserve.
Quantity targets never override the physical pack cap or trigger proactive
restocking when usable ammunition remains.

Movement recovery now returns to long jumps after at least three tiles of
verified progress, even if a short run stopped before its exact destination.
Temporary avoided landings remain active to prevent replaying the failed jump.

## Validation

- Follow-up full regression: 1198 passed; the new camera-anchor test initially
  put its viewport provider on the observer instead of the fake memory adapter.
  After correcting that fixture, all 94 native-farm/discard/viewport tests
  passed. No gameplay guard was weakened to satisfy the fixture.
- New tests cover two-pack limits, level 73 selection, equipping an owned upgrade
  without purchase funds, switching tiers before combat, relocated GUI windows,
  rejecting mid-read resizing, Fly popup positioning and translated revival
  anchoring.
- App PID 867004 loaded the update. Memory verified 1420 × 1009, SpeedArrows
  equipped and exactly one full spare pack.
- App PID 882968 subsequently loaded the follow-up viewport fixes through the
  safe reload. Memory confirmed the unchanged 1420×1009 viewport, full HP and
  Farming Off. See reports/performance/resolution-final-state.json.
- A bounded town test jumped 12 tiles from (214,259) to (226,259), then jumped
  back. Memory confirmed both arrivals and unchanged 1148/1148 HP. Farming
  remained Off. See reports/performance/resolution-1420x1009-validation.json.
- This diagnostic does not claim an unattended farming endurance test or a
  guaranteed kill rate. Further manual display changes require fresh geometry.
- Fly subsequently passed live validation: memory observed charge 100 and the
  ready flag, the qualified popup center (710,857) was clicked, and the next
  reading showed charge 0 and the flying flag. Farming remained Off during
  this diagnostic. See reports/performance/resolution-fly-validation.json.

## Follow-up route projection evidence

An offline comparison used the actual Phoenix terrain and the same travel
planner at both viewport sizes. From the verified revival position (193,266)
to the saved Bandit anchor (373,377), the new viewport required 16 commands,
all 8–12-tile jumps, versus 35 commands (six jumps, 29 short runs) at the old
size. The Bandit-to-warehouse approach used 15 commands (12 jumps, three runs)
versus 22 (three jumps, 19 runs). This establishes that the planner uses the
extra visible space instead of preserving the old short-step restriction.
It does not measure actual travel time: actor camera clamping, dynamic actors
and early vendor reachability can change the live sequence. No game movement
was issued for this comparison. See
reports/performance/resolution-path-planner-comparison.json.

The most recent historical farm session ended after an out-of-bounds click:
130 verified kills in 155.563 seconds, approximately 50.14/min. Its short
duration and terminal failure do not establish sustained performance. Live
endurance and route comparisons remain outstanding. After the Fly validation,
the saved Bandit route was resumed for monitored validation; see
reports/performance/resolution-live-run.json for the fresh run boundary.

The first complete five-minute live window recorded 580 verified kills
(116/minute), including departure from town, with no observed deaths or input
errors. The app reported xp_fly_verified during autonomous combat. At the
follow-up reading the player had 864/1148 HP and Farming remained On. This
window exceeds the standing 40–50/min target, but does not replace the longer
endurance check or natural restock/reconnect verification. See
reports/performance/resolution-five-minute-validation.json.

## Full-loop validation and remaining reconnect check

The extended run recorded 1350 verified kills in approximately 913 seconds
(88.7/min), including a natural restock, banking and return to Bandits. No
deaths or input errors were observed in that run. The potion-zero trigger
bought five Painkillers, kept the two existing SpeedArrow packs without buying
more, equipped the level-72 ApeHat, banked three Meteors and two +1 items,
and retained 200 transport silver. See resolution-restock-validation.json in
reports/performance. These measured results supersede the earlier statement
that live endurance and natural restocking were still outstanding.

Safe reload parking now switches from unsuccessful local escape searches to
a fixed checked town approach after 15 seconds, while retaining healing and
clearance verification. A terminal controller whose PID is unqueryable may
attempt the exclusive controller lock; movement still requires acquiring it.
Discord rate summaries now read the configured 40/min minimum and 50/min
stretch target instead of displaying the older 30/min goal.

The controlled reconnect test was performed after parking alive at (244,264),
HP 873/1148, with valuables already banked and Farming Off. It exposed incorrect
login offsets: the old clicks hit labels/footer instead of the input fields
and Login button. The fix qualifies the live pinned renderer and ImGui footer
layout, then uses field centers at window-relative (104,33), (104,71) and the
Login button at (104,153). Resizing or a changed layout rejects input.

The app reloaded to PID 899936 with the login fix, but the disconnected-client
handoff failed to re-embed the client. The experimental startup handoff was
removed from source. The game remains at Login, Farming Off; a manual Embed
client action is needed to reconnect the observer and finish live validation.
Do not report reconnect as verified or farming as resumed yet.

Focused regression after these changes: 109 passed across reconnect, safe
reload, foreground guards, Discord formatting and viewport tests.
The subsequent complete regression suite passed: 1205 tests in 53.05 seconds.
Both the app and client processes remained present; the app was still Off
without an embedded worker. Live reconnect and resumed farming remain pending.

## Resumed client, Scatter selection and Descend popup

The user subsequently launched and embedded a new client (PID 908148). Login
was restored with zero recorded automatic submissions, so this is not proof
of automatic credential entry. The initial run exposed a separate defect:
the right-click skill ID was zero, while the loop logged Scatter attempts.
Arrows and monster HP did not change. Read-only qualification located window
registry entry 1, its control vtable at RVA 0x5c5a38 and selected skill ID at
object+0xf8. The Skills menu uses actor vectors +0x1980 and +0x19b0; the latter
contained the learned Scatter (8001). No process memory was written.

The investigation briefly paused in an unsafe hunting location and the
character died. This was a diagnostic mistake, not a successful survival
test. Automatic revival restored full health. The later safe-parking routine
reached (305,395) with 1140/1148 HP before further menu work. Scatter selection
was subsequently observed as 8001; the selecting action is not attributed to
automation because the user was also interacting at that time.

scatter_selection.py now qualifies renderer bytes, reads the selected skill,
and restores Scatter using ordinary clicks on memory-qualified menu/table
rectangles before right-click combat. It verifies the selected ID afterward,
supports skill levels without hardcoding a particular Scatter level, and
keeps healing/ammunition handling ahead of selection. It never reports input
attempts as successful selection without the memory receipt.

The user's Descend report identified a second obstruction: ##SkillsPopup grows
to 122x56 while Fly and Descend share the row, at (649,829) in the 1420x1009
viewport. The previous general bottom cutoff at y883 allowed world clicks on
that popup. clear_scene now reserves the center HUD popup region starting
180 pixels above the bottom, plus the adjacent right-edge Skills popup region.
Movement planning, target selection and loot approach all share that predicate;
explicit skill/revival UI clicks retain their separate guards.

App PID 914976 loaded both fixes through an ordinary safe reload, parked at
(270,279) with unchanged 1125 HP, and automatically resumed the saved route.
Full regression: 1216 passed in 104.71 seconds. The resumed client reached
level 74, and memory confirmed autonomous Fly after the reload. One blocked
jump at screen point (1286,440), outside the HUD, recovered on the next path.
See descend-fix-loaded.json, descend-fix-monitor.json and
descend-fix-validation.json in reports/performance for live evidence.

The complete five-minute audit of that first popup fix recorded 503 kills
(100.6/min), six recovered movement stalls, and one remaining movement click
at (710,832) inside the HUD exclusion. It did not establish a complete fix.
The cause was clear_route_point, a separate rectangular-bounds predicate used
by the shortening fallback. It could accept the same point that clear_scene
had rejected. It now shares clear_scene's exclusions. Trial dispatch also
rejects every world click over the HUD; only explicit skill/healing UI actions
use the separate UI path. A regression recreates the exact (710,832) failure
and verifies a shorter nine-tile landing outside the popup.

App PID 915936 loaded the follow-up through safe reload and resumed farming
with unchanged 1177/1178 HP. Live validation is recorded separately in
descend-fallback-monitor.json so the earlier failed audit is preserved.
The follow-up complete regression passed 1218 tests in 104.99 seconds.
The final live audit covered 182.1 seconds from the resumed trial boundary:
187 verified kills (61.6/min), zero movement stalls, zero world clicks in
excluded HUD regions and zero trial errors. Fly activated autonomously;
farming remained On with 1172/1178 HP. See descend-final-validation.json.
This is a short validation window, not a promise of sustained hourly throughput.

The controlled reconnect test subsequently passed on the first login attempt:
login state observed at 1789174569.413, submission at 1789174572.926, restored
at 1789174573.927. HP remained 1025/1178 at (295,294). Its final On request
exposed a separate queued hosting race: On could arrive before reattachment,
and the UI rejected the reattach because farming was enabled. The report now
distinguishes the accepted On request from actual resumed farming. Ordered
reattachment restored combat at 1789174645; telemetry later recorded 243 kills.

Embedding recovery now reuses the existing observer/runtime and pinned HWND.
Queued reattachment is allowed with On intent, under the observer/input lock;
it never changes the intent, and the queued farm start rechecks manual Stop.
Detach still rejects enabled intent or a runner that has not finished stopping.
Repeated Embed is idempotent; failed reattachment preserves the connection for
retry. Farm startup restores detached hosting before combat. Reload restores
hosting before its safe-spot and exact-client handoff. Bridge health exposes
the applied window_mode, separately from a request being queued.

Embedding regression suite: 76 focused tests passed, followed by all 1226
tests in 120.55 seconds. Safe reload replaced app PID 915936 with 923800
while retaining game PID 908148, its creation time, HWND 7078016 and the
1420x1009 viewport. HP was unchanged at 944/1178 across the handoff, and
the native farm runner resumed. A live reattach request with Farming On was
accepted and applied idempotently without changing the control revision,
client identity, viewport, or active runner. Actual detached/On ordering and
manual Stop races were covered in tests, not deliberately recreated during
live hunting. See embedding-recovery-loaded.json, embedding-reattach-live.json
and embedding-recovery-monitor.json in reports/performance.

Final embedding live check at 1789175304: attached, On, native runner active;
117 verified kills in 128.3 seconds (54.7/min), HP unchanged at 944/1178.
The bounded monitor recorded one movement stall followed by continued travel
and kills, with no trial errors. This short check validates resumed operation,
not a guarantee of future throughput or zero stalls.
