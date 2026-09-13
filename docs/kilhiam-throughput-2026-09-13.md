# Kilhiam throughput comparison, September 13, 2026

This bounded comparison integrates the per-farmer speed implementation from
`4054d97` with the portable UI/merchant integration at `1072f68`. Timing changes
are in `profiles/farmers/Kilhiam.yaml`; Parasite's file is unchanged (SHA-256
`1b389f0971852b18c40076019cb7ec5f28daa04767ef7c7ebf52b70e21debc59`).

Kilhiam began at level 43 on the existing Bandit north/center/south rotation.
No patrol, combat strategy, loot, supply, or merchant policy was replaced.
All gameplay observations came from read-only memory through the native app.
Kills are sums of verified counter increments, not attacks or disappeared actors.
The full measurement starts at Unix time `1789276383.0174708`; startup failures,
deaths, reloads, travel, and restocks remain in its denominator.

## Reliability fixes

- A route failure from an older app session no longer appears as the current
  failure after verified successful reattachment. Its original record remains.
- A temporary foreground/input handoff during route movement now retains its
  typed transient error across the bridge. The controller checks Stop and reads
  fresh state before retrying movement. Transactions are not blindly retried.
- A changing GUI registry during farmer panel cleanup now defers the observation
  rather than terminating farming. Identity and freshness guards remain active.

## Timing evidence

The default baseline ended after 194.8 seconds and 43 kills because of the GUI
registry race. It is not a completed comparison. Candidate A used 0.25-second
arrival settling, a 0.28-second jump guard, three life-read attempts, and
0.01-second moving-observation retries. Its uninterrupted 516.8-second hunting
trial recorded 123 kills (14.28/min). Candidate B shortened arrival/guard delays
to 0.15/0.18 seconds: 74 kills over 320.2 seconds (13.87/min), with 65 kills in
its first complete five minutes. More aggressive delays did not demonstrate an
improvement. The A repeat included a death and is not a clean safety comparison.

Nineteen isolated Scatter input windows each showed two arrows consumed.
Candidate C retains A's arrival/guard values and sets a character-specific
two-arrow receipt for repositioning after 0.2 seconds. The default remains three;
the minimum stock to attempt Scatter remains three for every character. The
recast cooldown is unchanged. The existing engine still chooses the attack type.

Read-only target scans took approximately 20–25 ms after the cold read; life
reads took approximately 0.7 ms. In A's stable sample, median attack-to-kill was
about 1.15 seconds and kill-to-next-attack about 2.26 seconds. Faster memory reads
alone therefore do not explain the remaining throughput gap.

## Measurement limits

Four deaths occurred before candidate C: two during gaps after terminal failures
and two abrupt deaths whose attacker/cause was not established. Recovery events
are retained. No claim of a safe global optimum or 40 kills/min is supported by
these short, unequal trials. The existing route was preserved rather than
declaring a new route winner from incomplete samples.

## Final result

The exact one-hour window ended at `1789279983.0174708`: **675 verified kills,
11.25/min overall**. Its twelve consecutive five-minute counts were 0, 43, 41,
77, 48, 42, 65, 62, 54, 81, 88, and 74. Nothing was removed for downtime.

Candidate C recorded **357 kills in 23.719 minutes (15.05/min)**, including a
successful ammunition restock and return, with no recorded deaths. Its first
15 minutes recorded 212 kills (14.13/min); its four complete five-minute windows
were 79, 52, 81, and 91. The final 15 minutes of the overall experiment recorded
243 kills (16.2/min). Observed level rose from 43 to 44 during C, so the faster
later windows cannot be attributed solely to timing. These are unequal samples,
not a statistically established winner. C is retained as the measured Kilhiam
configuration with conservative arrival/guard timing and the observed receipt.

The 40/min goal was not achieved. Reliability improved after the transient-error
fixes; a large throughput improvement was not demonstrated. Normal native
farming continues after the comparison; no benchmark stop timer was added.

Raw timestamped events, profile phases, and health observations are retained
locally under `%LOCALAPPDATA%\Conquest\diagnostics` and the character's reports.

## Validation

The integrated checkout passed **2,387 tests** (`python -m pytest -q`, 125.76 s),
run after the measured hour. Coverage includes per-character timing isolation,
unchanged three-arrow attack minimum, default receipt behavior, manual Stop on
movement retry, bridge error typing, GUI registry races, and existing farmer,
merchant, and portable UI regressions. A final live memory check confirmed the
app and route controller alive with Kilhiam hunting after the measurement.
