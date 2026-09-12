# Gameplay observation rule

The user requires memory-only gameplay decisions. Use read-only process memory
for monsters, NPC IDs (including the Pharmacist), shop items, inventory, health,
death, movement, and combat feedback. Never substitute image matching, OCR,
visual health bars, screenshots, or computer-use observations for a missing
memory reader. Saving screenshots for debugging is permitted; inspecting or
analyzing them requires explicit permission from the user first.

Legacy visual code and historical screenshot calibrations are not authorization
to use vision. Ask before any visual inspection, including debugging and UI
computer use. Normal foreground input and window geometry APIs are permitted;
window focus must not change the user's Farming On/Off intent.

Do not claim memory-only loot, vendor detection, shop reading, or per-monster
death verification is complete until the corresponding reader is implemented
and validated. A remembered screen coordinate is not NPC identification.

# Standing route-optimization requirement

Latest performance goal: sustain 40–50 verified kills per minute, with 40 as
the minimum and 50 as the stretch target (not a speed cap). Include normal
travel, shopping, loot and recovery downtime. Review every five minutes;
a complete fifteen-minute rate below 40/min plus two non-overlapping low
five-minute windows requires diagnosis and route/pathing reassessment. Preserve
manual control and survival; do not disguise failures by resetting metrics or
counting unverified kills. Reuse measured safe routes, and continue improving.

For every future new fighting area, the user authorizes 60–120 minutes of
memory-only comparisons of different saved patrol areas/pathing, then selection
of the safest high-throughput route. Read profiles/route-optimization.json,
docs/route-optimization.md and .runtime/route-optimization-state.json. New areas
are queued by the route controller; the scheduled efficiency monitor conducts
the experiments. Do not treat a five-minute burst as a qualified winner.
Preserve manual Stop, survival, valuable loot and restocking. Save each variant
and the measured winning route for future reuse. Continue farming after the
comparison budget ends; the budget is not a farming stop timer.


# Restocking trigger (latest user instruction)

Return to town when fewer than three usable arrows remain, selected healing
supplies are exhausted, or inventory is completely full. Reload a usable reserve
stack before returning. Scatter must not be attempted with only one or two arrows.
Do not reintroduce proactive 200-arrow / six-potion return thresholds. Saved
thresholds are three arrows and one potion. Necessary city
transitions and valuable-storage safety remain separate obligations. During a
legitimate town trip, skip the Pharmacist when healing stock is already full,
there is no identified junk to sell and no required return scroll to replenish.

# Supply refill quantities

Latest user preference: refill to five selected healing potions and two arrow
packs maximum: one equipped and one spare, counting partial packs across tiers.
Prefer SpeedArrows from level 73 (5,000 per pack; 10,000 total), then IronArrows
from level 32, then LuckyArrows. Never buy while carrying two or more packs;
preserve existing excess for use. Use memory-qualified owned upgrades before
buying, and fund the best eligible normal tier on required town visits.
Keep existing excess potions to use normally; do not discard them to reach five.
Keep warehouse funding, available inventory space, verified purchases and the
empty-supplies/full-inventory town trigger. Do not restore old 1,600-arrow or
10/20-potion targets, including savings mode.

Reload ammunition only below the active attack minimum (three for Scatter),
not proactively at 25 arrows. Recycle spent one- or two-arrow remnants during
an already required town visit. Do not trigger a special trip to clear them.

At the Market warehouse, stop approaching as soon as memory vendor-status
confirms reachability; do not insist on occupying a crowded exact waypoint.
Manual Farming Off must also stop any active town controller.

For bank/town and hunting-return travel, use terrain.travel_path and checked
travel_waypoint diagonals rather than cardinal-only detours. Retain solid-tile,
corner and portal guards, fresh position/HP checks, and manual input priority.
Use8-12-tile visible jumps; run only shorter constrained segments. Clear temporary
walking recovery after verified progress instead of holding it for six seconds.

# Latest loot and urgent banking rule

Do not pick up Elite-only gear (+0/unknown). Keep Super gear, all verified + gear,
Meteors and Dragonballs eligible; Elite gear with a verified + remains eligible.
As soon as carried inventory contains a Dragonball or +2-or-higher equipment,
stop combat and go directly to the warehouse. Equipped + gear does not trigger
a trip. Verify deposits, preserve overflow/Market protections and manual Stop,
then return to monsters. This banking obligation is separate from empty-supply
restocking; do not shop during a stocked urgent bank trip.

# Region rotation comparison

The user requests a north -> center -> south -> north rotation, advancing when
empty-target observations are high. The saved bandit-region-rotation candidate
uses patrol_search.regions and memory-only RegionRotation. Nearby living targets
prevent rotation; unavailable observations and inter-region travel do not count
as empty. Preserve survival and valuable-loot priority during transitions.
Read reports/performance/region-rotation-comparison.json before changing Bandit
patrols: the current comparison alternates this route with northern fields for
two complete samples each, then validates the selected route. Do not treat the
initial shakedown or a single good spawn wave as a qualified winner. The existing
efficiency heartbeat coordinates stages and reports the completed comparison.
