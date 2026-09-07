# Archer leveling route

Primary planning reference, requested by the user:
[Classic Conquer Leveling/Powerleveling Guide](https://wiki.conqueronline.net/guides/Leveling/Powerleveling),
read September 7, 2026. The page's monster bands inform the proposed progression;
they are not proof that this character can safely fight each target.

| Order | Target | Guide monster band | Guide HP |
| --- | --- | --- | --- |
| 1 | Pheasant | 1–7 | 33 |
| 2 | Turtledove | 7–12 | 81 |
| 3 | Robin | 12–17 | 183 |
| 4 | Apparition | 17–22 | 303 |
| 5 | Poltergeist | 22–27 | 548 |

These early stages remain in Twin City/Wild Plain. The guide subsequently moves
through Phoenix Castle, Ape City, Desert City, and Bird Island. Its suggested
early Bird Island powerleveling requires assistance; it is not a solo combat
route for an unequipped low-level Archer. Group-based bonus experience is not
assumed in the bot's performance estimates.

## Applying the guide to this client

`profiles/leveling-plan.yaml` records the staged plan. The initial Pheasant area
is the only area with live combat evidence. Observed spawns were around
(405,451) through (431,465); the foreground trial boundary is (405,442)–(435,472).
Eighteen total kills were observed during supervised tests. A subsequent
low-health approach caused death, so healing and movement verification must be
working before longer farming runs.

The proposed character review levels use the start of each guide band as a
planning heuristic. They are not automatic promotion thresholds. Before a
transition, verify current level and gear, observe the new targets and actual
spawn positions, record traversable waypoints, and compare experience gained
per minute with arrow and potion consumption. Retain the current stage when the
new area causes excessive damage, unverified attacks, movement failures, or
poorer experience rates. Never infer navigable coordinates from a monster name
or table row.

Route qualification requires successful movement, attacks, healing, and selected
pickup with fresh observations. New maps also require a map-change check.
Automatic party joining, messaging players, buying powerleveling, and automatic
travel between uncalibrated areas are not part of this plan.

The complete farming loop and supervised 30-minute acceptance run remain
unfinished. Inventory and equipped arrows are read from memory; current
HP still uses the calibrated visible bar without OCR.


## September 7 live progression update

Parasite reached level 10 through verified Turtledove kills. Bamboo Bow was bought
for 204 silver and equipped at level 9; displayed attack rose from 16-18 to 23-26.
The level-10 skill panel shows Bow proficiency 2 at 0.176%, with the message that
it may improve after level 10. See `reports/progression-level-9.json` and
`reports/progression-level-10.json`. Future purchases/training require checking
current attributes, proficiency, funds, and the private server trainer.

Turtledove labels were calibrated from `reports/turtledove-calibration.png`.
Combat succeeded near (566,541), (578,517), and (634,550). The first two patrols
were sparse; the current candidate profile explores (624-642,550-566). This route
still needs a sustained efficiency and supply-use comparison and is not marked
qualified. Level review thresholds are recommendations, not unconditional route
switches. No new Archer attack skill has been learned or bound yet.
