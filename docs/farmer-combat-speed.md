# Per-farmer combat speed

Each farmer loads `profiles/farmers/<character>.yaml`. The file must name that
exact character. Missing files use the previous timing defaults. No character
name is hardcoded into the combat timing logic. Route changes preserve these
settings. Reload safely to apply edited values.

Parasite uses a 0.25-second arrival settle, a 0.28-second jump-to-attack guard,
and a 0.2-second reposition threshold after three arrows are verified consumed.
The separate Scatter recast cooldown remains 0.8 seconds. Other farmers retain
their own settings or the defaults.

Timing parameters are `action_interval`, `scatter_recast_seconds`,
`scatter_receipt_seconds`, `scatter_receipt_arrows`, `jump_arrival_seconds`, `jump_attack_guard_seconds`,
`torn_life_attempts`, and `moving_observation_retry_seconds`. Durations are in
seconds except arrow and attempt counts; bounds reject invalid delays and unbounded retries.

Additional options are independent for each farmer and disabled by default:

- `scatter_during_jump`: cast during a verified progressing jump. An incomplete
  landing leaves this state at the normal 1.5-second settlement deadline;
  partial progress is verified and replanned instead of blocking future movement.
- `coherent_projection`: pair player position and camera anchor in one qualified
  observation.
- `selected_target_refresh`: reread the chosen actor before input, retaining
  complete scene membership, identity, HP, range and foreground checks.
- `packed_monster_records`: decode mapped fields from one bounded read per actor
  (maximum 4 KiB), then reread and require every mapped field to match. Unmapped
  padding is ignored; process, scene, type and expiry checks remain intact.
- `fast_scatter_planning`: check candidate terrain in stable descending score
  order. The chosen destination and tie-break remain unchanged.

`scene_reuse_seconds` defaults to zero and is capped at 0.15 seconds. It permits
reuse only of a successful escape scan from the same combat iteration, aged
from before that scan. Slow or unavailable scans are read again. The chosen
monster is still refreshed before input. Parasite enables these options.

These settings never override manual input, death recovery, verified inventory
receipts, valuable loot protection, or fresh target/position checks. They do
not change saved patrols or jump length. Kill-rate targets require live
measurement including normal downtime; configuration alone is not proof.

`scatter_receipt_arrows` defaults to 3. Kilhiam uses 2 after nineteen recorded
Scatter attempts each showed a stable two-arrow decrease. This only determines
when a cast receipt permits repositioning; it does not change the three-arrow
minimum to attempt Scatter, the recast cooldown, or other farmers' profiles.
