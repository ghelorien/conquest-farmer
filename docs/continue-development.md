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
