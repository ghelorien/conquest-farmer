# Parasite combat-speed qualification

A fixed 3,600-second live Bandit run recorded **6,699 verified kills (111.65/minute)** against the requested 80/minute hourly-average target. The window was Unix time 1789288251.8501172 through 1789291851.8501172 (exclusive end).

An independent read-only SQL recount of `kill_verified.count` matched the monitor. Credited counters were strictly increasing; no counter-gap events occurred. All elapsed time remained in the denominator, including travel, storage, two safe reloads, a banking interruption and recovery, and one observed death. Metrics were not reset to remove downtime.

There were 1,454 cast attempts, including 1,087 recorded during jumps. Median time between cast attempts was 1.849 seconds; this is not an isolated jump-to-input latency measurement. Saved Bandit patrol points remained unchanged; Parasite's opt-in cluster approach and bounded regional expansion were enabled.

Five-minute kill rates, in order: 123.2, 95.6, 84.0, 69.6, 104.0, 72.2, 123.0, 158.8, 166.0, 93.2, 98.2, 152.0. The hourly target was met; not every shorter interval exceeded 80/minute.

One death was observed. Its cause remains unconfirmed. Automatic revival and return reached the hunting area about 41.2 seconds after the last healthy sample; the return segment took 17.6 seconds with no recorded stalls. Recovery subsequently reached completed state. The farmer and route controller were alive and hunting at the final audit.

An urgent valuable-storage visit deposited its items, then encountered a warehouse money-control geometry error before submission. Reopening the qualified warehouse restored valid geometry and a 98-silver deposit was verified. The published bounded retry requires unchanged balances and inventory and never retries an uncertain financial submission. Its guards passed tests; a future automatic recurrence has not yet been observed.

This is qualification of the live Parasite installation and conditions, not a performance guarantee for other characters or PCs. The publication checkout retains its newer registry-based ground-item identity reader; the measured live installation used a separately qualified stable creation-token reader. They are not byte-identical installations. Other farmer speed profiles retain their own settings.

Local evidence is retained under `reports/performance/parasite-speed-hour/`: `ground-fix-hour-outcome.json`, `ground-identity-hour-validation.json`, `ground-fix-hour-manifest.json`, `death-47-minute-audit.json`, and `warehouse-money-recovery.json`, together with the original trial database. Runtime files and credentials are not published.
