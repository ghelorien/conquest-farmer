# Bandit stability and attack continuity — September 12, 2026

Review branch: `codex/portable-character-ui`. Behavior integration baseline remains
`a431fb6`; this work does not replace the separate behavior checkout or publish a
new launcher. Runtime-generated shop catalog changes are excluded from commits.

## Failures and changes

- The previous run ended with `Life state changed during observation` after an
  evasive jump. A retryable town observation from healing could escape the combat
  recovery path. Healing now retries explicitly pre-input failures and performs
  inventory cleanup separately, preserving verified receipts and uncertain errors.
  Terminal errors retain tracebacks for further diagnosis.
- The native target scan was judged against a geometry timestamp taken before
  other work. A fresh scan now has its own start time; a scan exceeding 350 ms
  still blocks both attack and patrol input. Fresh identity, target, position,
  geometry, inventory and manual-input checks remain in force.
- The 2056×1236 client could place a reachable Phoenix warehouse at x=1092.
  `vendor-status` incorrectly used a fixed x<976 bound. It now uses the actual
  viewport and shared HUD exclusion rules.
- `jump_scatter` forced right-click attacks even when the existing adaptive engine
  had recorded `survived_three_damaging_scatters` and selected normal bow attacks
  for this character/equipment context. Route movement no longer overrides that
  decision; Scatter repositioning runs only when Scatter is selected.
- A later sample exposed repeated movement from [343,339] to [343,349], followed
  by fresh memory returning to [343,339]. The loop lasted roughly a minute
  because each outward arrival alone counted as success. The cause of the
  return itself is unproven. After two recent identical acknowledged moves
  followed by returns to the source, patrol now blocks that segment temporarily
  and uses the existing terrain-checked detour. History is bounded and scoped
  to the map; this does not change the saved regional route.

No Bandit patrol coordinates, region order, trust identities, pricing rules,
restock thresholds or input ownership rules were changed. The `bandit` route
retains north → center → south → north. It remains a provisional route, not a
newly qualified optimization winner.

## Automated validation

Full suite after the final movement correction: **2073 passed**. Regressions include transient
healing reads, cleanup failure without duplicate potion use, uncertain receipts,
fresh versus expired target scans following slow checks, enlarged vendor
viewports and HUD exclusions, adaptive attack choice with jump-Scatter enabled,
warehouse deposits, manual input, recovery and region rotation.

Native recovery previously bypassed the trial's death/revival event path. The
supervisor now reports each observed death and memory-confirmed revival to the
trial before its recovery wait. Both events are journaled and included in trial
totals. Tests cover repeated dead samples, verification still pending, a second
death, and journal/totals persistence while ordinary combat is waiting.

## Live evidence

Gameplay actions and outcome checks used process memory. The user's supplied
25-second recording was reviewed with their explicit permission to diagnose the
reported behavior; no live screenshots or visual gameplay fallback were used.

- Character Kilhiam, level 42, America, verified process creation identity;
  native client viewport 2056×1236. Selected/held route: `bandit`.
- The character was dead when the app reopened; existing revival recovered him
  to Phoenix at full health before restocking and returning to the route.
- Healing on the first patched run produced verified consumption/HP receipts
  without reproducing the fatal stop.
- Final code loaded into app PID 35620. Hunting began at Unix time
  1789261065.316489. Subsequent monitoring includes town downtime and does not
  replace missed samples with zeros or count attempted attacks as kills.
- At 1789261186.425207, zero healing supplies triggered the normal town return.
  Five Painkillers were purchased at 60 silver each. Existing excess arrow
  packs were retained; no extra ammunition was purchased on this trip.
- Phoenix warehouse approach finished at 1789261246.000226: **6.3 seconds,
  0 stalls**, stopping from fresh reachability rather than requiring the exact
  saved tile. At 1789261248.1738884, a **7602 silver deposit** was verified,
  leaving 200 carried and 325697 stored.
- Restock completed at 1789261249.6172533 with **3337 arrows and 5 potions**.
  Memory subsequently confirmed hunting and full health back on map 1011.
- Recovery checkpoint `.runtime/death-return.json` confirms a subsequent death
  at **[375,372]**, followed by one revival and return. The last combat sample
  before recovery was at 1789261663.904; the first resumed sample was at
  1789261687.582. The earlier trial incorrectly reported zero deaths because
  native recovery bypassed its journal. The exact cause of the sudden damage
  is not established. This is **not** a death-free or safety-qualified sample.
- The first three non-overlapping five-minute windows from 1789261065.316489
  recorded **50, 72, and 60** verified kills: **182 / 15 min = 12.1/min**,
  including the restock and revival downtime. This triggered the required
  performance reassessment. There were four brief movement stalls; automatic
  alternate-path recovery proceeded. Logs show multiple arrows/damaging hits
  per target, including the stronger Bandit variant. Target/position read
  races deferred input rather than terminating the farmer.
- Region events at 1789261755, 1789261823 and 1789261850 confirm the complete
  **north → center → south → north** rotation. No region coordinates were
  rewritten to hide the underperformance. The requested prior comparison file
  was absent locally, so no historical winner or new optimized winner is
  claimed. The existing selected route is retained while fixes are observed.
- Commit `611cd13` adds the recovery journal correction on top of `1df1faf`.
  Safe reload began at approximately 1789262250, returned through town and
  automatically resumed in app PID **17000** at 1789262336.625. A temporary
  one-second read-only life monitor supplements the thirty-second summaries.
- Commit `a73b66b` adds the repeated-reversal guard. It loaded in app PID
  **66104** after a safe reload; the existing controller refilled five potions,
  completed warehouse banking and resumed hunting by 1789263108.506. Existing
  partial arrow packs were retained rather than buying surplus ammunition.

The hour observation record is local under the character's
`reports/desktop-farming/smoothness-monitor-1789260635.jsonl`. It contains
read-only status samples and event counts, with no credentials or bridge token.
The first windows include intentional reloads and are not uninterrupted
comparisons. The requested hour completed at 1789263729; both temporary monitors
exited normally. Farming remained On at the end of the watch. A subsequent
manual stop was preserved.

The final build ran for 644 seconds and recorded 134 verified kills, zero
recorded deaths, zero terminal errors and zero stuck movements. Its two complete
five-minute windows recorded 47 and 76 kills (123 in ten minutes, 12.3/min).
Median interval between attack attempts was 3.79 seconds. Two healing receipts
were verified and level 43 was observed. The reversal guard did not trigger in
this final live window; its behavior is regression-tested, not live-qualified
by a forced reversal. These results do not establish the 40–50 kills/minute goal.
Final read-only health was 585/681, with 606 arrows and three potions remaining.
The local structured summary is `smoothness-summary-1789263729.json` alongside
the observation files.

## Drop-monitoring limitation identified after the hour

The user's follow-up asked about no items after approximately 1,000 kills. The
specific monitored hour contains 546 verified kill increments. No durable
pickup receipt exists for this character, and no loot/pickup events are present
in that hour's trial journal. Native loot notifications generally update the
latest app state; only successful pickup receipts have a dedicated durable
history. Ground candidates rejected by the policy, ownership, capacity, distance
or cooldown checks are not individually journaled. Therefore zero receipts does
**not** establish that no eligible drop appeared, or that no eligible drop was
missed. The monitor was insufficient to resolve that question retrospectively.

The implemented allowlist retains verified + equipment, Super equipment,
Meteors and Dragonball types; ordinary gear and Elite +0/unknown remain excluded
under the standing user policy. Three subsequent read-only ground samples
returned one empty scene, one changing-scene rejection and one expired
observation. These external bridge samples add transport overhead and do not
establish the in-process reader's failure rate during farming. End-to-end loot
qualification still requires durable drop/skip observations and a live verified
eligible pickup; no successful loot cycle is claimed from this watch.
