# Archer equipment and saved leveling plans

The three shops were visited using memory-identified NPCs. Live inventories contained 75 Blacksmith, 33 Shopkeeper and 68 Armorer products. Names, IDs and prices below come from those observations; required levels were joined by the same item ID and name to the installed client catalog. The next regular restock refreshes complete metadata directly from live shop memory. No visual inspection was used.

## Shop equipment brackets

| Slot | Vendor | Required level: item |
| --- | --- | --- |
| Bow | Blacksmith | 1: LuckyBow; 8: BambooBow; 15: HuntingBow; 20: MulberryBow; 25: PineBow; 30: HardBow; 35: SpeedBow; 40: ScarletBow; 45: HornBow; 50: QinBow; 55: LongBow; 60: HerderBow; 65: IronBow; 70: GooseBow; 75: TigerBow; 80: HardwoodBow; 85: CarvedBow; 90: StarBow; 95: MoonBow |
| Arrows | Blacksmith | 1: LuckyArrow; 32: IronArrow; 44: DeftArrow; 73: SpeedArrow |
| Ring | Shopkeeper | 1: IronRing; 10: CopperRing; 20: SilverRing; 30: GoldRing; 40: AmethystRing; 50: IvoryRing; 60: JadeRing; 70: PearlRing; 80: AgateRing |
| Boots | Shopkeeper | 10: OxhideBoots; 20: DeerskinBoots; 30: SoftBoots; 40: EagleBoots; 50: LightBoots; 60: CrocodileBoots; 70: SnakeskinBoots; 80: TigerBoots |
| Necklace | Shopkeeper | 7: LightNecklace; 17: HeartNecklace; 27: ThreadNecklace; 37: JadeNecklace; 45: CrystalNecklace; 52: GoldNecklace; 67: PlatinaNecklace; 82: BasaltNecklace |
| Armor | Armorer | 1: Coat; 12: Dress; 15: DeerskinCoat; 27: FoxCoat; 42: WolfCoat; 57: LeopardCoat; 67: ApeCoat; 77: Gambeson; 87: SharkCoat; 97: RhinoCoat |

Those Twin City inventories did not include archer headgear. Phoenix Armorer stocks archer hats (BadgerHat at 22, CatHat at 37, JackalHat at 52 and later tiers). Phoenix Blacksmith stocks arrows and melee weapons, without bows; Phoenix Armorer does not stock archer body armor. Live catalogs are now saved separately by city and vendor role. Armor has two item-ID forms; the upgrade selector preserves the form of the currently worn armor.

## Upgrade behavior

During a normal supply/inventory restock, review the Blacksmith, Shopkeeper and Armorer inventories. Buy the highest usable level tier that improves the relevant attack or defense stats, then equip it with ordinary input. Keep 3,000 silver after purchases for supplies. Each purchase requires a new inventory UID and the exact silver debit; each equip requires that UID in the expected equipment slot and the old item returned to inventory. A level change alone does not cause a town trip.

Current level-33 memory snapshot: HardBow (30), IronArrow (32), FoxCoat (27), GoldRing (30), SoftBoots (30), ThreadNecklace (27), BadgerHat (22). These were already equipped when observed; the automated review did not buy them in this validation. Reviews cover all seven mapped slots and record why each shop cannot provide an upgrade.

Never replace worn + items, Unique/Elite/Super gear, socketed gear or an item with an unknown plus value with plain shop gear. An uncertain purchase/equip is recorded and not repeated for the same item tier at the same character level. Purchases/equips for future tiers have not yet been exercised live. Normal arrow upgrades are enabled for LuckyArrow, IronArrow and SpeedArrow using live level, attack and price fields. Special ammunition remains excluded. Combat, reserve counts and restocking follow the equipped normal tier; if it is empty, a usable carried tier is selected. Reloads use the memory-identified inventory UID through ordinary input, so the farmer does not depend on F2 pointing to LuckyArrow. Optional top-ups preserve 3,000 silver once enough ammunition is carried to hunt; essential refills may use that reserve. The game still checks any additional attribute/weapon-skill requirements when equipping.

## Controls and named plans

F10 switches Farming On for the selected supported route. F11 retains pause/resume, and F12 stops. The mouse yields to physical user activity until two seconds after the mouse becomes idle.

The UI level-preset selector contains named plans covering levels 1-140. Pheasant, Turtledove and Apparition have saved runnable routes. All other brackets are explicitly marked as needing a survey and cannot silently replace the current route; they have no invented travel coordinates.

## Scatter

The embedded farmer now issues right-click attacks using the selected Scatter ability. It reobserves memory targets between casts and limits cast attempts to one per 0.8 seconds. Kill totals still come from the character kill counter; target positions, survival, supplies and pickups remain memory-based. Left-click input remains available for movement, loot and town interactions, but is not used for attacks.

Live validation on September 8: a three-minute sample recorded 60 kills and 60 right-click attack attempts, with zero left-click attack attempts and multi-kill counter increases up to five. This is a measured sample, not a guaranteed sustained rate.

## Scatter range and retreat correction

Live memory on September 8 identifies learned Scatter type 8001, level 0. The learned-skill vector is at character +0x1968, with 16-byte shared-pointer entries; the qualified skill vtable is module +0x5cff78. In that object, +0x48 is skill level, +0x60 is the Range field (8), and +0x64 is Distance (15). The equipped PineBow item has attack range 12 at +0x70. Loader instructions at RVAs 0x19e7b3 and 0x19e2b4 establish that Range and Distance are separate fields. These are client values; do not claim a server hit cutoff or percentage based on a field name alone.

The native farmer now reads those records at startup and caps Scatter engagement at its shorter Range field. It approaches toward a distant group to about six tiles instead of landing directly on a monster. Attack events include memory-read target HP, allowing subsequent hits to be checked without vision. A later farmer restart rereads learned skill levels and equipped bow range.

Continue casting while a living target remains in range. Defer moving to loot until the local group is clear. A global kill-counter increase no longer puts the aimed-at monster on an eight-second exclusion: Scatter may have killed a different monster. An incoherent target-memory read does not authorize a patrol jump.

Retreat only after a fresh HP loss, or when at least two memory-verified living enemies are within one tile. One HP-loss observation authorizes at most one retreat; the longer stationary-defense timer does not authorize repeated escape jumps. Escape events record adjacent-enemy count and whether fresh damage caused the jump.

After the range/retreat update, a 68-second memory sample recorded 24 kills and 37 right-click cast attempts, with no retreat events. Repeated aim-target samples showed HP declining (including 303 -> 204 -> 97 -> 4). This confirms repeated damaging casts at observed distances up to six tiles; it does not establish the exact server cutoff at eight tiles. The complete suite passed 741 tests.


Adaptive Scatter: the native runner tracks the aimed monster by entity ID and object address. Three temporally confirmed damaging Scatter casts leaving that monster alive select left-click attacks for its group. Misses, scene disappearance, and global kill counts do not count as per-ID survival evidence. Other players may contribute HP loss; this is a conservative attack-choice heuristic, not damage ownership proof. The decision persists in .runtime/attack-strategy.json and is re-evaluated after a verified character level, Scatter level, or equipped combat-property change. Ordinary durability loss and arrow consumption do not reset it. Tests cover the fourth attack switching to left and retaining nearby targets before looting.

XP skill: the pinned client HUD at RVA 0x994d0 reads actor+0x3cc and renders XP out of 100. Status bit 0x10 enables the ready XP popup; 0x08000000 is flying. The popup renderer iterates actor+0x1998, independently of the learned skill list. The runner requires a single ready Fly (8002) entry, self-target flag 2, and an active 56x56 memory-identified popup before clicking its center through guarded foreground input. Flight is only reported as verified after the status bit changes. Live activation verified on September 8: at full XP the ordinary popup click activated Fly; fresh memory showed status 0x08000000 and charge 0, while the route continued. Evidence: reports/fly-auto-validation.json.

Live adaptive validation: Poltergeist entity 449347 had observed HP 548 -> 388 -> 237 -> 96 following three damaging casts (two additional attempts showed no HP decrease and were not counted). The next attack used the left button. Evidence: reports/adaptive-scatter-validation.json. Full suite: 764 passed.


IronArrow validation on September 8: UID 292710037 equipped with 1,000 arrows; the previous 792-arrow stack returned to inventory. Total remained 2,792 and silver was unchanged. Evidence: reports/iron-arrow-reload-validation.json. Farming remained Off.
