# Back2Classic: level a new archer to Scatter

Branch `back2classic`. The mode rides on the existing automatic leveling: the
route process reads the character's level from memory every 5 s and moves to
the monster bracket in `profiles/leveling-presets.json`.

## What the mode adds

- **Goal.** "Back2Classic: level to [23]" with Start goal / Stop goal in the
  route panel. The goal is saved in `.runtime/level-goal.json` per character.
  Starting it clears a route hold or savings plan, sends no input, and does not
  switch Farming On. At the target level the farmer walks back to its restock
  town, parks, and the route ends (`level_goal_reached`, Discord status
  "Level goal complete"). Scatter itself is learned by hand at the Archer
  trainer; the default target of 23 is the retail Scatter level.
- **Gear every 5 levels.** Every restock already reviews the blacksmith and
  the armor and accessory shops and equips strictly better, level-eligible
  archer gear while keeping 3,000 silver for supplies (`equipment.py`). With the
  goal active, the farmer also returns to town once every 5 levels
  (`GEAR_STEP`) even when supplies are not exhausted, so upgrades happen at
  levels 6, 11, 16, 21 for a level 1 start.
- **Potions for the level.** At the Pharmacist the farmer buys the smallest
  potion that restores at least half of its maximum HP, among those the shop
  sells and it can afford 5 of. It falls back to the strongest affordable tier,
  then the cheapest. Tiers come from the client's `ini/itemtype.json`:
  Stancher 70 HP (5), Resolutive 100 (18), Painkiller 250 (60), Amrita 500
  (120), Panacea 800 (240). Combat and travel healing use whichever usable tier
  is carried, preferring the smallest that covers the missing HP. Tiers below
  the active one are sold as junk. Without a goal, Painkiller stays the only
  tier bought, exactly as before.
- **Healing earlier.** With the goal active the combat heal threshold is at
  least 60% HP.
- **Jump away from significant damage.** With the goal active, jump-away
  escape is on for every route (Pheasant and Turtledove had it off). It jumps
  8-12 tiles to open, walkable ground when 10% of max HP or more was lost in
  the last 1.25 s, or when two monsters stand adjacent. Smaller hits keep the
  attack going, so leveling stays efficient.

## Fixed on the way

`leveling_routes.read_level` read the level at `+0x6E8`, the retired legacy
build's offset. Build 1078 keeps it at `+0x6F8`
(`profiles/classic-1078-player-candidate.yaml`), which `equipment.py` already
uses. Before this fix automatic route changes could not see the real level.
This still needs one live read-only check on a running client.

## Limits

- **One farmer at a time.** Input is foreground only and guarded by a
  machine-wide input lock, and `manual_reader_registry_1078` accepts one Farmer
  profile. Two characters must take turns (run one to the goal, then the other).
- **Routes 12-16 (Robin), 22-26 (Poltergeist)** and most higher brackets are
  `qualification: planned`: travel is planned from terrain but a full hunt,
  town and return cycle has not been verified live. Only Apparition (17-21) is
  travel-verified. The first live run on each bracket needs supervision.
- Death recovery (native revive) is unchanged. The mode lowers the risk of
  dying; it cannot make it impossible.

## Tests

`tests/test_level_goal_back2classic.py` lists the failure modes first. Its
ladder test writes `back2classic-ladder.json` (level, hunt bracket, gear trips,
potion tier from level 1 to 23) into the test's temp folder.
