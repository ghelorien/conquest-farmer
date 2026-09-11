# Loot from the selected monster

The embedded `ControlRuntime` now retains the individual monster ID after an
attack. It runs `KillLootCycle` before selecting another target, including when
the killed monster was the last nearby member of the selected group.

The cycle accepts a confirmed, player-attributed kill for the attacked ID and
object identity. Ground drops must have stable UIDs and an explicit association
with that kill event. Existing items and items attributed to another kill are
excluded. A missing monster, XP gain, and proximity alone do not establish this
association.

All associated item types and silver go through pickup before another attack.
The observer must show both removal of the ground item and an inventory increase
of the expected type/quantity. Stack merges are supported. Multiple and delayed
drops are handled; the default waits for three continuously observed seconds
without a new drop after the kill or last verified pickup. An observation gap
longer than two seconds restarts this wait. A missing uncollected drop, full
inventory, two failed pickup attempts, or uncertain input holds the next target
and reports the reason. Off or a selection change cancels the encounter.

## Integration status

The sequencing is connected to the UI's runtime, but **live farming and pickup
remain unavailable**. The embedded candidate reader still supplies neither
validated life/death transitions nor ground-item-to-kill attribution. Attack
and pickup dispatchers are also unconnected. The runtime now explicitly blocks
attacks when the loot observer or pickup dispatcher is missing, so enabling only
attacks later cannot silently skip this requirement.

The observer adapter must provide a typed `LootObservation` under
`loot_observation`, with process-creation/map context, complete ground-item
observations, confirmed player kill events, and a fresh `InventorySnapshot`.
All event/sample times use the runtime's monotonic clock. `ConfirmedKill` is an
internal adapter contract, not a field already discovered in Conquer memory.
The pickup callback accepts `(GroundLoot, observation_data, input_mode)` and must
validate coordinates, identity, health, and input readiness at dispatch. The
runtime retains the same On/Off and selection revision gate for both callbacks.

Tests exercise the runtime through simulated attack, kill, pickup and inventory
observations. They cover unrelated/replayed drops, wrong monster identities,
delayed/multiple drops, stack merges, silver, full inventory, failed input, and
Off racing a pickup. These are logic checks, not evidence of live game pickups.

## UI refresh fix

The embedded sidebar has a fixed width so dynamic text cannot resize the client
surface. Monster rows move only when their order changes, and unchanged strings
are not rewritten. Missing samples keep the last observed rows/IDs visibly
marked as unavailable instead of repeatedly removing/recreating them. Counts
and status text have reserved space below the tree.

After the first automatic reload, eight live samples kept the client at 1036
by 793, including a transient unavailable observation. After loading the loot
sequencing changes, another six samples stayed at 1000 by 800 in the restored
window. The latest report is `reports/ui-jitter-verification.json`. The full
suite passed 327 tests.


## Money collection during ranged combat

The September 8 correction uses the actual adaptive attack button to decide
whether Scatter should hold loot. Previously any active strategy suppressed
pickup whenever a monster was within attack range, including single attacks.
Single-attack combat now checks allowed loot between attack observations.
Prolonged Scatter can collect one money drop within two tiles every eight
seconds; healing, active defense, and escape checks still precede collection.
Visible allowed drops are considered within twelve tiles instead of six, with
the existing HUD bounds and per-drop memory recheck. Money remains allowed
regardless of the equipment-quality filter or available inventory slots.
A pickup requires both the drop disappearing and a positive silver balance
change (or a matching inventory increase for an item).

Live verification: a money drop of type 1090020 produced a verified 322-silver
increase after deployment, while the Bandit controller remained farming.
Evidence: reports/money-pickup-fix-validation.json. Full suite: 833 passed.


## Ownership rejection feedback

The pinned client system-channel reader follows manager RVA 0x698700, the channel
tree at +0x40, node channel key +0x20 and deque +0x28. Renderer RVAs 0xef7f8,
0x196e20, 0xefa95 and 0xefb5e establish the System channel (2005), message text
at +0x68, channel +0x88 and timestamp +0xa8. The live message is:
`You can`t pick up other player`s loot at the moment. Please wait.`
The exact text was observed twice in the live System queue. Ordinary player
chat is excluded, and message/tree/deque contents are rechecked for coherence.

Take a message baseline immediately before the pickup click. Only a new matching
System record during the pending attempt rejects that ground instance. Remember
its process identity, map, candidate ID, address, type, position and spawn tick in
.runtime/loot-ownership.json, preserving the skip through farmer reloads. A new
spawn or game process does not inherit an old rejection. Never count a rejection
as a pickup; keep disappearance plus balance/inventory confirmation for success.
The UI reports "Skipping another player's loot" and continues farming. Without
a verified rejection, an unconfirmed attempt receives a 60-second cooldown rather
than repeated five-second attempts. An unavailable feedback baseline defers the
click while allowing combat to continue.
