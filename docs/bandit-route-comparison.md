# Bandit route comparison — September 10, 2026

Four alternate patrol areas were tested live against the preceding five-minute original-route baseline. Decisions and kill verification used read-only memory. Each comparison below uses exactly the first 300 seconds, including its route-selection transition. Combat, IronArrows, valuable-loot selection, healing and town shopping rules were retained.

| Route | Verified kills / 5 min | Kills / min | Projected kills / hour | Empty target checks | Deaths |
|---|---:|---:|---:|---:|---:|
| bandit-original | 6 | 1.2 | 72 | 240/268 | 0 |
| bandit-east | 203 | 40.6 | 2436 | 46/319 | 0 |
| bandit-south | 95 | 19.0 | 1140 | 140/279 | 0 |
| bandit-southeast | 140 | 28.0 | 1680 | 74/320 | 0 |
| bandit-far-east | 208 | 41.6 | 2496 | 45/324 | 0 |

The original western patrol spent most checks without an attackable target. The eastern routes reached more ordinary Bandit/BanditL33 spawn groups. The southern route exhausted its initial groups and spent too much time in empty travel. The southeast trial used 24-tile sweep spacing, matching two full 12-tile jumps, but did not outperform the broader eastern areas.

The selected active route is **Bandit eastern spawn fields**, copied into `profiles/routes/bandit.yaml`. Its hunting anchor is (405,477), initial boundary (381,429)–(441,525), and one idle expansion reaches (369,417)–(453,537). It uses 24-tile sweep spacing. Original and alternate definitions remain separately saved. New town paths were checked against installed terrain; the inter-patrol approach was observed, but a complete town round trip for this new anchor is still awaiting live validation. The runback monitor and all survival/valuable banking protections remain active.

The 208 versus203 result is a near tie in short sequential tests, not proof of a global optimum. Other players and spawn timing were uncontrolled. Hourly figures in the table are projections, not completed-hour totals. Keep the selected route active for a full15-minute check and a complete hour including a natural restock; do not reset measurement windows to hide downtime.

Detailed timestamps, raw event counts and subsequent validation state: `reports/performance/bandit-route-experiment.json`. Routine five-minute monitoring and the existing15-minute Discord reports continue.

Validation: all saved definitions and patrol waypoints validated;18 route/patrol tests passed.
