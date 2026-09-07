# Memory observation status — September 7, 2026

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
