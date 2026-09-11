## Town NPC reader — 2026-09-08

MemoryNpcReader now validates the known Pharmacist and Blacksmith by map ID,
entity ID, NPC type (+0x7c), species field, model, name, and current world/draw
position. Scene membership, pointers, records, process identity and observation
age are rechecked. It rejects duplicate or recycled IDs and ignores a player
whose name matches a vendor. Ten tests include replay of the saved town memory
fields; this is not fresh live validation.

The embedded app exposes `sample-npcs` through its authenticated read-only
interface. Its callback checks connection before reading player stats, then
checks living character identity and map before and after sampling. Run
`scripts/sample_nearby_vendors.py` after restoring the administrator wrapper.
It sends no inputs and returns `shop_items_qualified: false`. The Pharmacist
profile now stores entity 100102/type 3; automatic purchases remain disabled.

Full suite: 454 passed. Current game and non-administrator wrapper processes
were verified running; no embedded worker exists. Live farming, reconnection,
and active-shop qualification still require restored Windows administrator
access. No additional screenshots were inspected.

## Reconnect implementation — 2026-09-08

One explicitly authorized login-screen inspection was used to map the login
fields. No further visual inspection is authorized. Reconnection uses the
qualified game window caption/class, normal foreground input, and locally
DPAPI-encrypted credentials. Credentials are excluded from patch exports.
The embedded observer and native farm reject disconnected player stats before
reading life memory. Reconnect attempts are bounded and wait for input focus;
farming resumes only after the login screen closes and fresh life observations
are available. The inspected login geometry must match before credentials are
entered. Live reconnect success is not yet verified.

Reload failed because of an indentation error in desktop_app.py. The error is
fixed. Reload now checks imports in a separate child before releasing the client
or exiting the approved parent. Full suite: 442 passed. The replacement desktop
app is running without administrator rights; Windows denies read-only memory
access to the still-running elevated game (WinError 5). Click Embed client and
approve Windows consent to restore the administrator wrapper. The old startup
error report is historical. Farming is currently Off and the game disconnected.

Travel now checks healing and revival during movement feedback. Automatic vendor
purchases remain disabled pending qualification of active shop memory. Nearby
NPC candidates include Pharmacist 100102 at (466,327) and Blacksmith 100104 at
(452,330), map 1002. Equipment upgrades and a complete town supply cycle remain
unfinished; do not claim these are operational.

# Memory observation status â€” September 7, 2026



## Current hosted mode — user policy update



Gameplay observations must use read-only memory. Saving debug screenshots is

allowed; inspecting or analyzing images requires explicit user permission first.

This rule also applies to NPCs and shop items. See `AGENTS.md`.



The hosted runner now loads no templates, creates no capture device, and reads no

screenshots. Target IDs, species and drawing coordinates come from the scene

reader; player HP/death, inventory and arrow feedback come from memory. Foreground

window geometry is used only to deliver normal input. Focus loss preserves On.

The legacy visual CLI/calibration code remains historical and is not authorized.

The local desktop profile now also defaults to memory_only.



One live memory-selected Turtledove attack was followed by an arrow decrease and

one player kill-counter increment. Per-monster death attribution remains

unqualified. Candidate monster HP is read from the inspected attribute table;

zero values are excluded, and recently finished/unresponsive IDs receive a brief

cooldown instead of stopping the entire farm.



Ground records are now read from the scene using object address, creation tick,

item type and tile; the candidate +0x50 identifier is not globally unique. The

new pickup loop checks record disappearance plus silver or inventory growth.

Open-shop item memory is not connected. The old

Pharmacist script is disabled until fresh NPC identity and shop memory can be

verified. `npc.json` lists Pharmacist catalog types 3, 9 and 23; these are not

verified live NPC entity IDs. Remembered screen points are historical evidence,

not permission to buy without a memory NPC/shop reader.



The remainder of this document is historical and predates the hosted runner.





## Current status: memory-only default, farming blocked



The supplied farming profiles now select `memory_only`. Starting `farm-trial`

in this mode stops before camera creation, template loading or worker input.

The dashboard no longer captures the screen in this mode: it shows current HP

as unavailable, while its independent level monitor uses read-only memory and

reconnects through fresh identity checks. No visual fallback is enabled.



This is a fail-closed transition, **not a completed memory-only farmer**.

Current HP, monster alive/dead status, ground-item records and revival state are

still unvalidated. The historical `legacy_visual` path remains in source for

regression tests; it is not selected by either supplied farming profile.



The new `sample-entities` command follows a module-relative scene pointer chain,

then reads actor IDs, kinds, names, world coordinates, drawing coordinates,

maximum HP and level. Drawing coordinates are values read from the client;

they do not require screen capture. The profile records offsets, not heap addresses.

Player and NPC objects can share a primary vtable with monsters; the reader

requires the observed monster kind as well. A vtable match alone is insufficient.



The reader bounds collection sizes, rechecks object membership, identities,

coordinates, pointer topology and process identity, and expires slow samples.

Changed scenes fail the whole sample. Membership does not establish life state,

so `current_hp` and `alive` explicitly remain null. It cannot trigger actions.

The current-session diagnostic traced 26 Turtledoves before implementation; a

later live reader sample in town returned zero monsters among 1,240 scene objects.

The game then exited, which the worker rejected instead of reading a new process.

Live monster-field validation with this reader and an actual client restart

remain outstanding. The expanded test suite currently passes 185 tests.



## Earlier calibration history



The user requested memory observations instead of OCR. The unused HUD OCR

prototype was removed. No farming runtime imports it or requires OCR packages.

The existing foreground trial uses a calibrated colored health bar and image

templates, not OCR. These checks remain necessary until replacement memory

fields have been validated against controlled changes.



The current candidate profile resolves the player object through a module-relative

pointer path on each sample. Character name, coordinates, and maximum HP have

live supporting evidence. Real client restart validation remains outstanding.

Current HP, map ID, and entity lists remain unresolved. Inventory layout evidence

was added later in this session, as recorded below.

Offset `0x3e0` is maximum HP: damage did not change it. Treating it as current

health would allow attacks after injury or death.



At approximately 17:11 UTC the live display showed HP 117/117, position

(466,336), 170 equipped arrows, silver 4420, and 20 Stanchers. These were

supervised visual calibration observations, without OCR. A subsequent read-only

scan examined 473,921,280 bytes across all eligible regions without read failures.

The integer HP and ammo candidate lists each reached the 2,000-match cap.

Floating-point HP searches returned 105 float32 matches and one float64 match;

none is semantically validated. Full region coverage does not imply complete

candidate lists or coverage of memory excluded by the scanner's region policy.

Evidence is saved locally in `reports/hp-117-process-scan.json`.



A 4-KiB player-object sample found 4420 at offset `0xa90`, a money candidate.

It still needs a controlled balance-change test. Current HP was not identified

in the object by comparing the earlier low-health and full-health samples.

Next validation must distinguish changing current HP from maximum HP and from

stale copies, then establish stable pointer resolution before runtime adoption.



During the preceding supervised low-health approach, a manually selected target

click resulted in movement into monsters and the character died. One manual

revival returned the character to town. Twenty Stanchers were then purchased

for 100 silver, with each purchase verified visually. No automatic resurrection

or restocking was implemented. The character was alive at full HP in town at

the observation above; no farming loop was running during this memory scan.



## Inventory and equipped ammunition



The player object's deque at offsets `0xb88` through `0xba0` contains ordered

inventory item references. The separate lookup count at `0xbb8` agreed. Item

records have vtable RVA `0x5cf220`, UID at `0x8`, type ID at `0x10`, current amount

at `0x62`, and limit at `0x64`. The equipped arrow item pointer is at player

offset `0xc28`. These offsets are kept in a separate fingerprint-specific

candidate profile; no heap addresses are saved into that profile.



Before reloading, memory returned 27 slots: seven 200-arrow reserve stacks and

20 Stanchers. Equipped arrows were 170. A supervised right-click on inventory

slot zero reloaded arrows. The game display changed to 200; a fresh memory read

showed the same original slot-zero UID now equipped, and the previous equipped

UID with 170 arrows now in the final inventory slot. Other slots shifted in the

same order shown by the client. Total arrows stayed 1570 and potions stayed 20.

This validates the current-session item identity, ordering, and arrow quantity

interpretation across an actual equipment change. Potion consumption and client

restart validation remain outstanding. Reports: `inventory-before-reload.json`

and `inventory-after-reload.json` under the ignored `reports` directory.



`sample-inventory` now reads these values without images or OCR. It validates

container bounds, item types, uniqueness, pointer topology and amounts, then

rechecks the records and process identity. Concurrent changes invalidate the

sample. This is an optimistic consistency check, not an atomic game snapshot.

The combat trial now requires this profile and stops on stale supply readings,

missing/empty equipped arrows, missing required potions, or full inventory.



A later attempt to close the inventory panel did not visibly close it, so

closed-panel validation is **not** claimed. Subsequent input needs fresh cursor

and geometry checks. The character remained alive at full health in town.

Tests after the inventory and supply integration: 131 passed.



### September 8: navigation wait and interrupted-server relog

The patrol fallback at (702,637) repeatedly selected an unreachable waypoint.
The supervisor now tries the remaining saved patrol points, still requiring
terrain-valid paths wholly inside the active boundary. Navigation waits have a
separate UI/Discord status and no longer masquerade as loss of foreground focus.

Reconnect now reads the qualified c2b53437 application ErrorModal state at
module RVA 0x698be0 + 0x6e8 (bounded std::string, open flag +0x708).
Only the exact interrupted-server/re-login message is automatically dismissed.
The active ImGui modal and its final OK button cursor geometry are checked twice,
then ordinary foreground input clicks OK. The open flag must clear before saved
credentials are entered. Unknown errors or changed layouts are not clicked.
Three exhausted login attempts now report that attention is needed instead of
remaining indefinitely at login_submitted. Starting farming at login defers until
reconnection without reading missing player stats.

Live verification: the interruption message was read from memory; after loading
the fix the modal closed and connection_restored arrived on the first attempt.
Fresh memory showed Parasite alive at 266/290 HP, then the saved Turtledove loop
resumed and position advanced from (699,621) to (608,588). No visual inspection,
memory writes, or new elevation were used. Full suite: 540 tests passed.


### Item names and plus display: September 8 memory investigation

Read-only live investigation on client SHA c2b53437 confirmed inventory item
names directly in item objects (vtable RVA 0x5cf220). Name is an MSVC string at
+0x18, length +0x28, capacity +0x30; external buffer when capacity >15.
Examples read live: BronzeDagger, ThornWhip, BambooBow, ShortHook.

The live client formatter at RVA 0xaf8cc reads the item name from the same
+0x18/+0x30 layout, then at 0xaf8f6 reads byte [item+0x6b]. If nonzero it
uses the format string at RVA 0x5c6130, "%s(+%d)", passing that byte as the
integer suffix. Thus the displayed plus is composed from a separate item field,
not embedded in the stored base name. This is direct client-code evidence,
not an inference from item-type quality digits.

Sixteen carried equipment records sampled with stable UID/type/vtable and
rechecked +0x6b all read zero. A known nonzero plus item still needs independent
live validation before changing automatic equipment-sale protections. No gear
was sold and no screenshots inspected during this investigation. The existing
Inventory Item model has not yet been extended with name or plus fields.


Follow-up: the user explicitly authorized selling these items. Inventory Item
now includes plus (None for layouts without the field). The pinned profile
reads a u16 at +0x6b and masks its low byte, rechecking the full sampled word
before accepting the snapshot. Only known +0, quality <=6 carried equipment is
eligible. Unknown/+ gear and quality >=7 remain protected. The current live
batch includes a quality-7 ShortHook, which is excluded from sales.

Live sale verification: 20 authorized +0/ordinary-item candidates were sold
through the shop. Every sale recorded UID disappearance and positive silver;
combined sale receipts totaled 1,511 silver. A final memory snapshot found none
of the candidate UIDs remaining and confirmed the protected ShortHook remained.
After supply top-ups there were 14 free inventory slots; the route resumed its
hunting phase. Full regression suite: 584 tests passed.


Nonzero equipped-item observation: player+0xbf8 points to a LightNecklace
UID 292479029 (type 120004); the same item+0x6b name-formatting byte reads 2.
Other equipped pointers observed were Coat +0 at +0xc08, BambooBow +0 at
+0xc18, arrows at +0xc28, and PorcelainRing +0 at +0xc38. This confirms a
live nonzero value, but the user mentioned two equipped plus items; the second
has not yet been identified/independently compared. Equipped pointers are
outside the carried-item deque used for automatic sales.


## Warehouse item deposit verified (2026-09-08)

Normal foreground drag deposited Meteor UID 292575188 (type 1088001) at
Twin City's Warehouseman. Two independent post-input reads confirmed the exact
UID/type in warehouse storage and absent from carried inventory. Silver and all
other carried item identities/amounts were unchanged. The warehouse had zero
items before, one afterward, and capacity 20. No vision or client memory writes
were used. The dove loop resumed after closing both panels.

The fingerprint-pinned reader follows the shared object at module+0x69c730
(getter RVA 0x181b30), then deque map/capacity/start/count at +0x1008 through
+0x1020 and warehouse capacity at +0x1030. Renderer RVA 0x116f08 iterates this
deque; RVA 0x117061 accepts the dragged inventory UID over the warehouse child.
The UI gold Deposit button is unrelated and is never clicked. The bounded town
warehouse-deposit action accepts only a specified carried Meteor/DragonBall UID,
rechecks inventory, NPC and GUI geometry, and sends one ordinary drag. Ambiguous
receipts never cause an automatic second drag. The Meteor path has live evidence;
DragonBall deposits, withdrawals, other warehouses and restart stability are not
yet independently qualified. This adds an explicit deposit action; it does not
add a new trigger that interrupts farming for every valuable drop.


## Discard strategy implementation (2026-09-08; bow drop verified)

Ordinary carried equipment with memory-qualified +0 and known weak/unwanted
consumables are discard candidates. Equipped items, unknown/+ enhancements,
quality 7+ equipment, gems, Meteors, DragonBalls, arrows and route potions remain
protected. Normal input opens Inventory, drags the exact current slot into the
world, and requires both the inventory removal and a newly observed matching
ground record. The action preserves all other carried records and silver.
The attempt is journaled before input and is never blindly repeated.

The inventory button was mapped from client renderer RVA 0x09a740: Items is the
top button in the second column of ##Control's six-column ##Buttons table.
GImGui tables pool is context+0x4338, stride 536; the current table ID is
0x02a99238 with flags 0x482010, column stride 104. Memory reads its current
column WorkMinX/WorkMaxX and table rectangle; at qualification it resolves to
(656,753). No keyboard shortcut or visual calibration is used.

Discarded ground identities use map, candidate UID, type, tile and spawn tick,
ignoring object address changes. The journal survives app reloads. This rejects
the same observed ground generation, rather than suppressing all future drops
of the same equipment type. A new ground generation after a scene refresh may
need inspection again; global server-ground identity remains unqualified.


Live discard proof: inventory UID 292587845, type 500004, +0 was removed by one
normal world drag. Read-only memory verified the matching new ground record at
map 1002, (430,381), candidate UID 2, spawn tick 71747114, while all other carried
items and silver were unchanged. Inventory was closed afterward. The journal
marks this attempt verified and the looter excludes its exact ground key. A
combat-thread logical-coordinate scope was added after its calibration guard
rejected a direct UI call before item input; the regression test checks that
scope. Native farming was reloaded with the new discard path. Ground candidate
IDs alone are not globally unique; the full key is always required.


### Uncertain discard recovery (2026-09-08)

Five native automatic discards were verified before UID 292588938 (type 410015)
failed its combined inventory/ground receipt within three seconds. The previous
code propagated that receipt timeout as a fatal farm error and left Inventory
open. The route stopped after 40 verified kills; the character subsequently died
while idle. This was recovered with memory-driven revival and panel closure.

An attempted drag whose result cannot be confirmed is now journaled as
unverified, with the last available bag-presence/removal check and ground
candidates. It is never dragged again. Inventory closure is attempted in a
finally block, and any pending closure takes priority before further combat,
even at low HP or after revival. Only verified receipts are reported as successful
drops. The uncertain item is skipped and farming continues after panel cleanup.
This initial fix did not waive failures before an item drag. Regression tests cover uncertainty, no repeat
drag, death during cleanup and clearing the panel without inventory candidates.
The full suite passed 628 tests after this fix.

The next live stop exposed a pre-drag ground-read timeout: no drag was sent,
but the optional cleanup error still terminated farming after 39 kills. These
ValueError/OSError/CaptureUnavailable failures now defer cleanup for ten seconds,
without recording an attempted UID, and close Inventory before continuing.
Post-input uncertainty still quarantines the UID permanently. Pending panel
closure errors retry through the normal fresh-observation path instead of
terminating the route. Regression tests cover all three pre-input failures,
combat availability during cooldown and pending closure retries. All 634 tests
pass. This does not automatically restart arbitrary input errors.

### Dynamic movement obstruction recovery (2026-09-08)

The 12:28 Discord alert followed a 12:27:27 movement_failure_limit stop after
200 confirmed kills. Memory remained at (593,564) for three identical jump
attempts to (581,564). Static terrain planning repeated the same destination
without incorporating the failed movement feedback. This does not establish
whether the underlying obstruction was collision, another entity or input loss.
Native movement now records temporary map-specific obstructions and replans
after the first failed movement. Short detours run, and long jumps resume after
the recovery interval. Tests simulate three failed moves and subsequent combat,
alternate departure directions, blocked approach endpoints, boundary adherence,
obstruction expiration and waiting when every departure is blocked. All decisions
remain memory-only; no live screen inspection was used.

Live validation after reload: a move from (577,527) to (576,527) failed.
The new path reported movement_recovery and verified a running detour to
(577,526), then resumed long jumps through (576,514) and (576,502).
Local detour steps oscillated briefly during the six-second running interval,
but the route continued without intervention and confirmed four kills afterward.
Full regression suite: 638 passed. Persistent obstacles and route efficiency
still need longer observation; this is evidence of one live recovery.

The subsequent status check found a separate session-rollover navigation stall:
the 14:01 combat session reused an approach computed before earlier patrols,
while current memory position was (603,576). The path was walkable, but the old
waypoint and corridor could not be reused from this departure. Hosted sessions
now start from their first fresh position sample, immediately hunt if inside
the saved area, or compute a new checked return to its anchor. Tests cover an
outside-area position inside the stale corridor and a position already hunting.

### Kill-rate investigation (2026-09-08)

At the user's slow-kill report, the preceding 900 seconds contained six verified
kill increments and about 770 movement steps. Expanded patrol visited only outer
corners; a memory snapshot showed Turtledoves east of its maximum boundary.
The reusable route now sweeps interior rows and uses anchor (736,550), with
terrain-planned town paths. Health-checked chase candidates exclude lingering
zero-HP and unreadable records, including offscreen candidates. Ground-record
changes rejected before pickup now defer that candidate for one second rather
than repeatedly preempting combat. Foreground/life/input guards are unchanged.

Discord counts verified increments across sessions over actual 900/60-second
windows, instead of displaying a resettable session count as the only metric.
The target is 20 kills per minute. A live sample after relocation and chase fixes
was eight verified kills per minute, so this target is not qualified. Full suite
after the pickup retry fix: 657 passed. Vision was not used for this investigation.

The first eastern-route restock exposed two further blockers. Temporary movement
exclusions cut the only town corridor, and the inventory deque logical start
advanced to 67 while its map size remained 64. The raw read showed 23 inventory
entries and lookup count 23. Removing the incorrect start<map-size assumption
allowed a stable read of 23 unique item UIDs; all pointer, identity, quantity and
raw-header rechecks remain enforced. Town routing now retries its terrain-valid
corridor with running steps if only temporary exclusions make it unreachable.
Parasite revived automatically to (430,380) with full health after the failed
return. This additional recovery means the 20/min sustained target is still open.

Final uninterrupted check after loading the fixes: 41 verified kills over 231.3
seconds (10.64/min average), with a sampled rolling minute reaching 16 and the
last sampled minute at five. Farming remained active at 95.3% health. This is
an improvement over the original six kills per 15 minutes, but does not meet
the requested 20/min target. Current result is in reports/kill-rate-benchmark.json.

Ground enhancement mapping: the scene record at vtable RVA 0x5cdaf0 is a
shared holder; constructor RVA 0x1452b2 places its actor at holder+0x10.
The ground-name formatter at 0x15e2e3 reads byte actor+0x48, then formats
it using the seven-byte string at 0x5cdad8, read live as "(+{:d})".
MemoryGroundReader therefore reads holder+0x58 and rechecks it with the
identity/type/tile fields. Values outside 0..12 remain unknown. This mapping
is backed by client code; a live naturally dropped nonzero-plus pickup has
not yet been observed for independent validation. Tests cover +1, +12,
unknown/invalid values, changed enhancement during sampling, quality/type
allowlisting, and skipping nearer ordinary gear in favor of a +1 item.


### September 9: reconnect retry exhaustion

A disconnect left the client at its login screen with the exact interrupted-server
modal still active and reconnect_exhausted after three submitted logins. The
connection was subsequently restored before this fix loaded; it is not credited
to the new code. Submitted login attempts now retry with 15/30/60-second backoff,
capped at 60 seconds. Three consecutive unsafe/failed submissions still stop for
attention, and unknown dialogs remain protected. Retry reconnect clears that
exhausted state without changing Farming On/Off intent. The authenticated bridge
can queue the same bounded action; it cannot accept credentials or arbitrary input.
The UI separately reports waiting for login input and exhausted recovery. Tests
cover continued transient retries, explicit retry after exhaustion, intent
preservation and the unchanged memory-only dialog guards.
