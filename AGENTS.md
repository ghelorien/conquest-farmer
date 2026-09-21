# Gameplay observation rule

Merchant listing priority: for Spiritual and Dutch, fill available booth slots
with eligible inventory in descending order of the freshly computed total listing
price. Unknown/unreliable prices remain deferred; never guess value to fill a
slot. Keep excess inventory queued in the same value order. This applies to
one-time listing, new deliveries and recurring scans.
Check each merchant's booth capacity every fifteen minutes in the native
script. Fill free slots from inventory using saved comparable price history,
without another undercut or requiring a fresh website scan. Preserve socket/plus
matching, highest-value-first order, owned-shop matching, explicit refill pause/Global Stop and
safe input handoffs. Persist check times; unknown values remain queued.
Refill is enabled by default and independent of merchant operations: pausing
trading/repricing does not pause refill, and refill never enables trades, repricing,
login or travel. Its separate Pause refill and Global Stop survive app restart.

Spiritual and Dutch must never compete with each other. Exclude both from
independent seller counts and outlier calculations. If either has the lowest
valid comparable price (including ties), match the lowest owned price without
another discount. Prefer fresh verified booth memory over delayed website data.
Only undercut outside sellers when their valid price is below our owned floor.

Evaluate every inventory item on every merchant scan. A single comparable live
seller is sufficient; only run the half-median outlier test with at least three
other sellers. If no exact live match exists, reuse the last observed equivalent
price without discounting it again. With no exact live/history quote, value +2
equipment at three times the equivalent +1 live or historical unit price. Keep
type, sockets, currency and quantity comparable. For + items, Fixed, Normal,
Refined, Unique and Elite share a pricing group; Super remains separate.
Unplussed items retain exact quality matching. Never compare unsocketed with
socketed items; both socket count and contents must match, including history
and the +1-to-+2 fallback. User-mentioned prices
were examples, not hardcoded references. Never invent a price without these data.

Send Discord #shops a sales update every four hours, including zero-sale periods,
with per-merchant and combined verified item/silver totals for the period and
since tracking began. Use the dedicated encrypted shops webhook and durable
sales receipts, never listing/reprice counts as sales. Label observation gaps
and unavailable earlier sales. Preserve the farmer's existing notification policy.
The four-hour report timer runs inside the Conquest app, never an AI checker.
Merchant failures and requests for help must also notify Discord #shops. Send
urgent alerts for uncertain transactions/auto-paused failures, and one alert
after 60 seconds for persistent problems. Send recovery only after fresh checks
confirm resolution. Normal manual pause/input and farmer handoff waits stay
quiet. Use the independent local shop-alert process so app crashes can alert;
persist incident/queue state across restart and keep webhook secrets encrypted.
Automatically embed, focus and qualify merchant booth input under a safe handoff;
do not require a manual Embed & verify click. Preserve manual Stop and input
priority. Focus recovery may click only the verified native Conquest title bar,
never guessed game controls, and must confirm actual foreground ownership.

After merchant disconnect/reconnect, treat Twin City as a transit stop: travel
directly to its memory-identified Conductress, choose Market at the verified fare,
then follow checked terrain to a vacant Market stall and restore the shop.
Return takes priority over scans, trades and ordinary inventory work. Preserve
manual pause/Stop and safe farmer input ownership. ShopFlag names alone do not
prove vacancy (occupied flags retain that name). Require live-qualified occupancy
and claim controls; never announce recovery on login alone. Persist transfer and
shop intent, reconcile uncertain results before retrying, restore highest-value
listings first at their verified previous prices, and notify #shops if blocked.

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
As soon as carried inventory contains a Dragonball, bow, bag, ring, bracelet,
necklace or boots, stop combat and go directly to the warehouse. Other equipment,
including +2-or-higher armor and weapons outside those categories, follows the
ordinary eligible merchant/town flow. Equipped gear does not trigger a trip.
Verify deposits, preserve overflow/Market protections and manual Stop, then
return to monsters. This banking obligation is separate from empty-supply
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

# Merchant safety and recovery (latest user clarification)

Never initiate a manual or routine merchant disconnect without explicit approval.
After an unexpected crash/disconnect, reconnect and return immediately to Market.
Recovery bypasses routine fifteen-minute refill scheduling, retaining exclusive
input and safe farmer parking. If recovery makes no verified progress for five
seconds (stalled movement, failed NPC interaction, or unreadable recovery state),
the user authorizes a protective disconnect. Stop automatic retries afterward.
Track real improving movement toward the Conductress, not clicks or oscillation.
Arrival in Market ends the unsafe-transit watchdog; booth restoration remains a
separate recovery step. Do not apply this disconnect exception to a normally
connected merchant manually positioned outside Market. Preserve trade journals.

# Meteor deliveries (latest user instruction)

Do not deliver loose Meteors to merchants. Bank them, consolidate verified batches
of ten into MeteorScrolls, and deliver scrolls. Bank leftovers before departing
for merchant delivery. Preserve all pickup eligibility and warehouse fallback.

# Unapproved trade requests (latest user instruction)

Cancel an unapproved incoming request after five seconds using the existing
exact-request, journaled once-only decline path. A transient request-memory read
failure must not permanently strand an otherwise unchanged unapproved request:
reobserve safely, then cancel the exact still-present request or verify closed
 windows and unchanged ownership before continuing. Never auto-approve visitors,
 replay an uncertain decline, bypass manual Stop, or dismiss an approved session,
 unapproved open trade, changed process identity, or ownership discrepancy.

# Operator manual handoff

An explicit global Manual handoff fences attached Farmer and merchants before
its native-memory baseline. It sends no game input and never changes saved
Farming, merchant, refill, banking or delivery controls. Wait for active input,
bot transactions, fresh closed windows, five seconds stable ownership and mouse
idle before release; read gaps retry while identity changes remain held. Manual
handoff changes are observation gaps/baselines, never invented sales receipts.
