# Reusable hunting routes



The native app's **Saved routes** selector loads a named template and its monster

group. All matching nearby IDs, including later spawns, are covered automatically.

Selection persists across app reloads; farming starts Off. Changing the nearby

group selection clears the active route selection without modifying its template.

**Save copy** saves a separately named template for future use. Duplicate IDs and

names are rejected; existing definitions are never silently overwritten.



Definitions live in `profiles/routes/*.yaml`. Pheasant and Turtledove routes are

included. Each stores its map, monster types, suggested level range, hunting

boundary, patrol, outbound and return waypoints, supply thresholds, equipment

review levels and ordered tasks:



1. Travel to the hunting area.

2. Hunt and collect kill-linked loot.

3. Return to town when supplies or inventory space require it.

4. Restock, review equipment and resume the hunt.



These tasks describe the saved loop. **The full loop is not yet enabled.** The

foreground runner executes travel and combat; background attacks, attributed

loot and vendor interactions still need qualification. `qualification: planned`

refers to the complete cycle. Level bands are initial guidance, not measured

XP/hour.



The current native foreground runner executes Pheasant/Turtledove travel and

combat, matches visual targets to nearby monster IDs, and measures confirmed

kills/hour. Death detection reads the first status flag (including a dead player

with positive HP), waits for the actual Revive gate, confirms revival and jumps

back to the saved death tile. The app uses an owned borderless top-level game

window because child-window embedding prevented Ctrl from producing jumps.

Live native and owned-host tests both recorded jump motion 130.



The local foreground profile now uses the visually verified F1 Stancher shortcut

below 70% HP and F2 for spare arrows. Healing requires both potion consumption

and an HP increase. Computer Use state is not a farming control: focus loss and

death preserve the On switch. Foreground input still requires the game to have

focus. Vendor restocking and complete kill-attributed loot remain unfinished;

the current visual pickup path recognizes Stancher only.



Each run plans a new map-checked path from the observed position. Per-run progress

and evidence are written to timestamped reports, never to the route template.

The installed terrain hash must match, portal tiles are avoided, and movement

requires arrival feedback. Failed or uncertain movement does not retry itself.

Scene overlays are conservatively excluded until their transforms are verified.



Bounded travel diagnostics, while farming is Off and the embedded client is

unfocused:



```powershell

.\.venv\Scripts\python.exe scripts/walk_planned_route.py --route pheasant --maximum-segments 3

.\.venv\Scripts\python.exe scripts/walk_planned_route.py --route pheasant --phase return --maximum-segments 3

```



Default return thresholds are fewer than 200 total arrows (equipped plus spare

stacks), fewer than 3 healing potions, or fewer than 4 free inventory slots.

Restock targets are 1600 arrows and 15 potions. Targets do not themselves trigger

a return: a character carrying 1178 arrows and 10 potions can keep hunting.

These values are editable in the saved YAML. RouteLibrary validates and saves

definitions atomically; an explicit `replace=True` is required to replace one.



Tests cover persistence, replacement protection, malformed definitions, map

changes, replanning, supply boundaries and native selector behavior.





The verified Twin City Pharmacist standing point is (466,333), map 1002.

Both saved hunting routes now retain it as `restock_anchor`; the user's exact

manual journey was not recorded. `profiles/pharmacist-stop.yaml` saves the vendor,

shop calibration, and a separately planned approach. Use `travel_hosted_route.py

--route turtledove --phase restock` for an observed foreground trip while the

native farm executor is Off. Dynamic obstructions still require arrival checks.



The current healing supply is Painkiller (1000020): 250 HP for 60 silver, no level

requirement. Fifteen were purchased with inventory and silver confirmation, and

F1 was visually verified as Painkiller. The local profile heals below 60% HP.

The saved selection policy is the cheapest usable potion that restores at least

one current maximum-health bar; the current 213 HP does not benefit from paying

for 500 HP potions. Vendor travel/purchase helpers exist; automatic invocation

from the live farm's supply thresholds is still unfinished.



Native movement uses up to twelve clear tiles, avoids HUD/chat landing points,

jumps only for segments of at least eight tiles, and runs shorter segments.

Arrival feedback allows the combat loop to resume after 0.5 seconds instead of

an unconditional 1.5-second delay. Stationary HP loss starts an eight-second

renewable defense window, prioritizing nearby recognized Pheasants/Turtledoves

over patrol and loot. This identifies nearby threats, not a proven attacker ID.





## Memory-only route update



The user-selected Turtledove anchor is now (644,570), map 1002. The saved route

starts in (620,546)-(668,594), searches twelve tiles farther on each edge after

7.5 seconds without an attack, and permits four expansions. Attacks reset the

idle timer. Search movement follows terrain paths toward selected scene monsters.

The hosted bow range is sixteen world tiles where the target lies inside the

clickable viewport; the legacy pixel-distance cutoff is not used for memory

positions. Ground-money pickup is prioritized before ordinary items, with

record-disappearance and silver/inventory-increase checks.



All older visual calibration statements in this document are historical. The

user requires permission before any image inspection or analysis. The old

Pharmacist screen-coordinate purchase script is disabled until NPC identity and

active shop items are validated from memory. Existing inventory reading and F1

Painkiller healing continue to use memory feedback.





## Level 18 Apparition route



`profiles/routes/apparition.yaml` targets only installed monster group 4

(Apparition, catalog level 17, max HP 303), for character levels 17–22. The

initial hunting anchor is (242,558), map 1002, with a bounded adaptive patrol.

This coordinate began as a planning hint from

https://conquer-reborn.com/guides/leveling and requires live memory confirmation

on this server. The shared native controller handles loot, supply returns,

revival and travel back to the saved hunting area. Run with

`python scripts/run_overnight.py --route apparition`; there is no time limit

unless `--hours` is supplied.



The DMap reader now applies scene collision cells, allowing bridge crossings

that were previously excluded as solid squares. Each part's signed tile offset

is relative to its bottom-right anchor, and its row-major access cells replace

the base grid. Dimensions, payload length and map bounds are checked before

application, and portal avoidance is reapplied afterwards. Format references:

https://github.com/SantasCode/Tiled2Dmap/blob/master/Dmap/SceneFile.cs and

https://github.com/SantasCode/Tiled2Dmap/blob/master/Preview/Render/DmapFileRender.cs.

Movement still requires observed position feedback; a planned path is not proof

that every bridge tile was crossed successfully.



Native target configuration now supports the catalog's five initial leveling

groups. A newly supported target cannot fall through to legacy image matching.



Long travel uses a 250,000-node planning budget, while local target/patrol searches

retain the smaller 10,000-node budget. Checked travel paths are reused only while

the observed position remains on the path and the terrain, map, destination,

boundary and dynamic obstruction set match. If planning outlives the input

observation, the native runner reads fresh memory and retries without input.


The first live outward journey crossed bridgeA successfully: memory positions
advanced from (646,672) to (545,676) after short-corner input was fixed. Short
runs now target up to four checked path steps ahead so clicks clear the player
sprite; these stay below jump distance and let the client walk around corners.

Live validation now confirms arrival in the saved Apparition hunting area and
verified attack/kill-counter progress against group 4. The template is marked
`travel_verified`; a full return/restock cycle on this route and comparative
experience efficiency remain unqualified. Local evidence is recorded in
`reports/apparition-route-validation.json` (not included in the export).

The first combat validation raised Parasite from level 18 to 19. A concurrent
level-up restored full HP without consuming the pending potion; the native
runner now resumes after that unverified potion attempt only when fresh health
is at least 99%, without incrementing the verified-heal counter. Lower-health
unverified healing retains its existing failure behavior.


## Apparition throughput optimization

The first extended run killed 34 in 740 seconds (2.76/minute). All 34 were in
the first 285 seconds (7.16/minute); the rest of the run repeatedly tried the
western river edge around (195–209,595–608), accumulating 100 movement failures.
The revised template moves the anchor to (300,605), covers (252,570)–(340,644),
and permits only one eight-tile expansion. This avoids that failed river edge.
Its first short trial killed 20, with much higher potion use in close swarms;
this trial exposed and helped fix both remaining short-corner town input guards.

`kite_when_surrounded` enables a memory-only escape between kills after observed
stationary HP loss: at least two health-checked enemies must be within four tiles.
Choose a clear cardinal jump of 8–10 tiles, inside the hunting boundary, ending
at least six tiles from those threats. Heal first, let the jump settle, and
resume attack selection. Unverified or stale monster records cannot trigger it.
The policy remains disabled for other saved routes.

Town travel can finish near a known vendor approach point when its projected
interaction position is inside the client. TownTrade still identifies the live
NPC and validates the opened shop before buying or selling; the approach point
alone never identifies a vendor. Short diagonal runs remain at most four checked
walking steps; diagonal jumps and blocked detours are rejected.


The latest comparison tests an eastern circuit anchored at (342,626), spanning
(308,594)–(380,652), with one eight-tile expansion. Its patrol endpoints and all
expanded patrol points connect inside the hunting boundary on installed terrain.
An earlier southwest candidate is preserved in local benchmark evidence; the
final route is selected from measured runs, not assumed optimal from density.

Additional throughput fixes preserve normal ranged target selection when no
close defensive target is available, enter hunting mode upon reaching the saved
hunting boundary, and hold a last-observed chase location for at most two seconds
during transient target-read gaps. The location commitment only affects scouting
movement; every attack still requires fresh monster identity and positive HP.


Final selection for this optimization: retain the broader southeast circuit,
anchor (300,605), boundary (252,570)–(340,644), plus one eight-tile expansion.
It recorded 30 verified kills / 161.469 seconds (11.15/minute), with two verified
heals. The tighter eastern circuit with movement commitment recorded 28 /
150.641 seconds (11.15/minute), with three heals. These are hunting-session
measurements, not full-cycle averages; sustained 20/minute remains unverified.
The shared movement and ranged-combat fixes are retained on the final route.


Surround escape now preempts pending combat, potion verification, loot and patrol,
including travel through the hunting region. Two health-checked living monsters
within four tiles trigger escape without waiting for HP loss. Choose a clear
cardinal 8–12-tile jump inside the active route boundary that moves at least six
tiles from the surrounding threats and reduces the number of enemies within
four tiles of the landing point. Other observed living groups affect the choice.
A 0.9-second cooldown and 0.55-second settle interval allow repeated escapes
without issuing attacks mid-jump. Fresh player kill-counter increments are
accounted independently of the interrupted attack, preserving late projectile
kills. Focus loss before revival input is retryable; geometry mismatches remain
explicit failures.

Normal travel jumps also reserve the 0.55-second landing interval before an
escape can interrupt them. Every movement dispatch rechecks the fresh player
position against its planned origin; a changed origin requests reobservation
before sending input. This prevents overlapping jumps and stale-origin clicks.

Ground-loot recovery: stable zero-identifier scene records are excluded from
pickup candidates instead of invalidating every other drop. Unresolved records
remain unclicked; the existing type, tile, scene consistency and pickup receipt
checks remain required. Optional junk disposal checks ground memory before
opening Inventory, so a ground-reader failure cannot repeatedly flash the bag.

Ground pickup allowlist: money, Meteor (1088001), DragonBall (1088000),
equipment of Unique/Elite/Super quality (type digit 7/8/9), or equipment with
a verified ground enhancement of +1 through +12. Ordinary/refined +0 gear,
unknown-plus ordinary gear, consumables, arrows, gems and other unlisted
items are skipped before any pickup click. Existing carried-item protections
and warehouse rules remain in effect.


## Conductress departures and Phoenix region (2026-09-08)

Twin City departures toward Phoenix now use the memory-identified Conductress
at (435,440), approaching (438,444). The dialog reader checks the actor's option
deque, exact prompt, option IDs, live GUI table geometry, and unchanged records.
A normal foreground click selects Phoenix Castle; a 100-silver decrease and
fresh living memory arrival at (958,555) confirm the trip. No vision or memory
writes are used, and uncertain payment is never automatically repeated.

Live portal entry verified map 1002 portal 7 at (963,557) -> map 1011 at
(11,376); return portal 0 at (5,376) -> map 1002 at (958,555). These observations
and both installed terrain hashes are saved in profiles/map-connections.json.
The Conductress trip is saved separately in profiles/conductress-routes.json.
Other Conductress destinations are recognized by the dialog reader but are not
yet recorded as verified travel connections.

Automatic level checks select WingedSnake at 27-31 and Bandit at 32-36, with
FireSpirit at 42-46. Their terrain paths use installed client help coordinates.
Cross-map selection requires a verified return to the restock map before leaving
an existing hunt. Ratling and later unnamed travel connections remain pending;
merely listing a bracket is not proof that its travel route works.

Map-edge movement now reads the actor's draw position at +0xe8 with position
at +0xd8, instead of assuming screen center. Both directions of the Phoenix
portal were reached with this projection. Farming shortens checked straight
segments when necessary to keep the click clear of the HUD; fewer than eight
tiles uses running. An unavailable direction causes replanning, not a complete
farm stop. Restocking travel uses the same memory projection.

Live follow-up: at level 29, the WingedSnake route recorded kills and increasing
experience in its saved area. Adaptive combat switched to left attacks after
three damaging Scatters left a WingedSnake alive. Evidence is in local
reports/conductress-wingedsnake-validation.json. Full regression suite: 777 passed.


## Every-bracket checks and destination towns (2026-09-08)

The controller checks memory level every five seconds. Each bracket entry is
recorded once per run, and all 28 brackets (levels 1-140) have boundary tests.
Saved routes must agree with their bracket's exact level range and monster ID.
The old overlapping Pheasant/Turtledove/Apparition labels now end at 6/11/21.
A missing surveyed route remains explicitly pending; higher-city travel has not
been silently marked verified.

Before a cross-map departure, the destination town must have a terrain-checked
profile. After each portal arrival, the controller visits that city's town and
verifies living character identity, map, fresh position, and town bounds before
hunting. An atomic checkpoint survives app/controller reloads, avoiding repeated
town visits within the same city. A new map arrival invalidates that checkpoint.
Town profiles cover Twin, Phoenix, Ape, Desert and Bird Island using the installed
region.json boundaries. Other cities still require verified travel connections.
UI activity identifies city/town travel while farm combat input is temporarily Off.

Phoenix restocking now stays on map 1011. Live memory identified Pharmacist
(type 10014, model 230, tile 189,252), Blacksmith (10013,220,197,226), and Armorer
(11,116,202,242). Service roles are mapped to these city-specific identities;
shop inventory and every purchase still require fresh memory observations.
A complete local refill verified 1,694 arrows and 15 Painkillers, followed by
return to WingedSnake farming and a kill. Level 30 was subsequently observed.
The Phoenix Shopkeeper and Warehouseman have not yet been qualified.

Low HP no longer prevents a living character from buying healing in a known
town. If travel exhausts potions, it reports this and keeps moving toward town
instead of stranding the character. Existing death/revive and manual-input guards
remain in effect.

Regression validation after these changes: 818 tests passed.


## Ordinary level variants

Saved routes include an explicit monster family from profiles/monster-families.json.
Bandit selects types 7 (Bandit) and 66 (BanditL33); WingedSnake selects 6 and 65;
FireSpirit selects 9 and 68. The catalog records exact type IDs, names and levels
from the installed client. Variant suffixes must agree with the catalog level,
stay within the route bracket and be no more than four levels above the base.
This is a saved allowlist, not substring matching at combat time. BanditL97,
BanditKing, BanditLeader, aides and messengers are excluded from the level-32 family.
Future saved definitions inherit the recorded family for their primary type;
this does not qualify travel for routes still awaiting a survey.

Live memory entity IDs/type IDs and names continue to gate targeting. The target
name check now accepts exact saved variant names, and adaptive attack mode and
range are chosen separately per variant. Kings, queens, bosses, leaders and
chieftains are also excluded from reactive attack selection, while remaining
observable for surrounding-enemy escape. Healing and escape still precede attacks.


Twin City return preference: the user requires Conductress travel for future
returns too. Map travel now requires a verified Conductress trip before a return
to Twin City and cannot silently substitute the long walking route. Only the
outbound Twin City to Phoenix trip is currently memory-qualified; the return NPC,
dialog and arrival still require a memory survey before such a trip is enabled.
Tonight's Phoenix-only plan does not need that pending connection.
