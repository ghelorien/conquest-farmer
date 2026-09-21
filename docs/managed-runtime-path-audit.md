# Managed runtime path audit

Audited `src/` and `scripts/` for `.runtime` and `reports` path literals, their
write/read consumers, and the scripts invoked by the desktop app and background
services. This change does not access installed releases, live profiles, game
memory, or screenshots.

## Corrected managed paths

| Component | Stateful artifact | Destination |
| --- | --- | --- |
| Market safety guard | `reports/merchants/market-guard.json` | Machine state |
| Farmer viewport setting and both readers | `.runtime/farmer-view.json` | Selected profile |
| Account diagnostic UI lookup | `.runtime/account-diagnostic-<character>.json` | Selected profile, matching the existing worker publisher |
| Reserved-request cancellation | `reports/merchants/reserved-request-cancellation.json` | Machine state, including the injected cancellation-worker output |
| Reserved-request source read | `reports/banking/merchant-route.json` | Selected profile |
| Empty-delivery cancellation | `reports/merchants/empty-delivery-cancel.json` | Machine state |
| Notification-health display | Farmer and shops webhook/status paths | Selected profile / machine state respectively |
| Standalone merchant relist worker | `.runtime/merchant-relist.lock`, `reports/merchants/script-worker.json` | Machine state |
| Standalone merchant scan import | `reports/merchants/market.json` | Machine state |

The corrected sites resolve `character_context.state_path` when used. They
retain their existing relative destinations when no managed data root is set.
The cancellation helpers retain logical constants and resolve them when the
operation starts, so these new fixes do not bind a namespace during import.

## Existing safe paths and startup order

The managed desktop launcher calls `profile_bootstrap.initialize` and
`select_profile` before importing `desktop_app` and its behavior engines.
Child workers inherit `CONQUEST_DATA_ROOT` and `CONQUEST_PROFILE_ID` before
importing their engines. Existing `Path(state_path(...))` module constants and
function defaults rely on this ordering; they were audited and left unchanged.
Importing engines before selecting a profile, or changing those environment
variables in a running process, is outside that startup contract.

The farmer/shops notification launchers, overnight controller, merchant
diagnostic launcher, bridge, journal, price history, and route-controller
launch/error/lock paths already use the namespace API or receive a resolved
destination. `farmer_preferences`, notification handover paths, and legacy
qualification constants are resolved by their consumers. Profile secrets and
qualification artifacts constructed under `context.state_dir` or the explicit
managed root are already correctly scoped.

`profiles/` policy/default files, engine configuration templates, and game
definition assets are read-only inputs, not runtime reports. Migration source
paths, staged migration destinations, the explicit legacy namespace, and
release inventory/verification code intentionally use their explicit roots;
they must not be silently redirected into the active profile.

## Operator/source tools outside managed runtime

The following scripts retain their explicit caller/repository/CWD diagnostic
destinations. They are not launched by the managed desktop runtime and were not
executed in this audit:

- Memory investigation: `scan_hp_baseline`, `inspect_runtime_code`,
  `inspect_player_candidate`, `inspect_scene_candidate`, `inspect_input_calls`,
  `inspect_vendor_memory`, `inspect_monster_health`, `decode_hp_candidate`,
  `decode_attribute_candidates`, `trace_player_root`, `trace_player_references`,
  `find_code_references`, `disassemble_local`, `sample_nearby_vendors`,
  `inspect_merchants`, `start_memory_worker`, `start_input_probe_worker`.
- Explicit movement/input diagnostics: `probe_embedded_jump`,
  `probe_embedded_step`, `probe_embedded_status`, `probe_embedded_target`,
  `probe_embedded_revive`, `probe_foreground_point`, `watch_embedded_motion`,
  `walk_planned_route`, `travel_hosted_route`, `restock_pharmacist`.
- Operator validation/reporting: `verify_wrapper_app`, `verify_window_host`,
  `measure_farm_performance`, `monitor_unified_validation`,
  `monitor_resolution_validation`, `report_unified_validation`.

These are `.py` source tools, not supported managed background entry points.
Their existing output behavior is preserved; manually invoking them from an
installed release is not covered by the immutable managed-runtime guarantee.
No non-Python script in `scripts/` contained additional runtime/report literals.

## Regression coverage

`test_managed_runtime_paths.py` starts a fresh Python process with a synthetic
release CWD and the actual desktop launcher. Only the GUI entry is replaced.
Real bootstrap/profile selection precedes engine imports; real file and SQLite
I/O exercises the guard, notification loops, sales schedule, bridge, merchant
journals/history, UI preferences/diagnostic lookup, cancellation failure
receipts, and standalone merchant scripts. Client input, process discovery,
web collection, and outgoing notifications are never invoked. The test compares
every release directory and file, including content hashes and modification
times, before and after. All expected artifacts must be under the synthetic
managed machine/profile roots.

`test_memory_only_start.py` verifies that memory-only foreground Start and
calibration fail before client discovery or elevation, regardless of apparent
client count. The message directs users to the existing exact identity-bound
embedding path. Missing or misplaced pin arguments fail before GUI creation;
the existing resolver rejects absent, replaced, and ambiguous identities.
