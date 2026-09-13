# Per-farmer combat speed

Each farmer loads `profiles/farmers/<character>.yaml`. The file must name that
exact character. Missing files use the previous timing defaults. No character
name is hardcoded into the combat timing logic. Route changes preserve these
profile settings. Reload safely to apply edited values.

`Parasite.yaml` currently reduces jump arrival settling to 0.25 seconds and the
jump-to-attack guard to 0.28 seconds. Three arrows verified consumed allow
repositioning after 0.2 seconds; the separate 0.8-second recast cooldown remains.
Torn life reads receive up to three complete attempts, and moving-frame
observation retries use 0.01 seconds. Other profiles retain the prior defaults
unless configured independently.

Parameters: `action_interval`, `scatter_recast_seconds`,
`scatter_receipt_seconds`, `jump_arrival_seconds`, `jump_attack_guard_seconds`,
`torn_life_attempts`, and `moving_observation_retry_seconds`. All durations are
seconds. Validation bounds prevent zero/negative delays and unbounded retries.
These settings do not override manual input, death checks, inventory receipts,
loot protection, or fresh target/position checks. The 80-kills/minute target
requires live measurement; configuration changes alone do not prove it.
