# Standing optimization workflow

The user requests a one-to-two-hour comparison whenever moving to a new fighting
area. This is a continuing requirement, not a Bandit-only experiment. The active
policy is profiles/route-optimization.json. Current Bandit trials remain a
provisional existing comparison; the new protocol applies to subsequent areas.

The route controller queues newly encountered map/monster-family combinations in
.runtime/route-optimization-state.json on startup and level-route transitions.
Alternative patrols of the same family are one experiment, not new areas. This
bookkeeping never sends input, changes Farming On/Off, or interrupts combat.
The scheduled efficiency monitor performs the memory survey, saves candidates,
executes the comparisons, and records/selects the winner. This requires the Codex
monitor to remain enabled; it is not a separate autonomous in-game optimizer.

1. Survey the current area with fresh memory IDs, living target readings, terrain,
   and recorded encounters. Retain the current route as a fallback and save two
   alternatives by default. Keep normal monster variants and exclude bosses.
   Check each candidate's town connection, restock anchor and recovery boundary.
2. Use conquest.route_optimization.comparison_plan. Three routes receive two
   separate 15-minute samples each, followed by 15 minutes on the winner: 105
   minutes total. Two candidates take 75 minutes; four take 120 minutes. Revisit
   candidates in a different order so the first fresh spawn wave is insufficient.
3. Record actual activation timestamps and distinct sample IDs. Count only
   verified kill-counter increments. Preserve elapsed wall time, zero-kill
   periods, travel, restocks, looting and recovery. Also record deaths, HP loss,
   healing, supplies, stalls, user/focus pauses and relevant gear/level changes.
   Never use attack attempts or actor disappearance as kills. Label hourly rates
   extrapolated from short windows as projected, separate from actual last-hour
   counts. Do not force a town trip merely to fill a sample.
4. Manual Stop suspends benchmarking and does not authorize a restart. Keep raw
   interrupted windows in history, mark them non-comparable for route selection,
   and repeat only within the testing budget after authorized resume. Material
   gear changes, a different area, missing telemetry or unreliable attribution
   also invalidate a comparison. A user-selected new area supersedes the current
   test; do not trap the character in an obsolete level bracket to finish it.
5. Use conquest.route_optimization.rank_candidates on complete, comparable,
   memory-verified samples. Two full samples per safe candidate are required;
   aggregate kills divided by aggregate elapsed time decides throughput. Reject
   a candidate after a death or unsafe sustained damage rather than continuing
   it to satisfy the timer. Keep healing, safe jumps/Scatter, valuable pickups,
   banking, reconnect, refocusing and recovery enabled throughout all samples.
6. Stop experimentation after at most 120 minutes of available test runtime.
   Select/save the best supported safe route and keep farming. If evidence is
   incomplete, retain the safe fallback, report uncertainty, and do not fabricate
   a winner. A near tie should favor safer, more reliable movement. Do not claim a
   global optimum or sustained hourly performance from these short comparisons.
7. Persist the winner, samples, scope/gear, start/end times, selection rationale
   and validation result per area. Reuse the saved winner next visit; revisit it
   when new gear, monster availability or persistent underperformance changes
   the conditions. Preserve existing major Discord changes/critical failures,
   verified-drop notifications and 15-minute status reports without minor spam.

The comparison budget never stops farming. No farming time or money cap is added.

## Current performance target

The September 22 user target supersedes the earlier 40/min target: sustain
at least 60 verified kills/minute; 75/min is an aspirational stretch target
(3,600–4,500/hour), not a speed cap.
Read both values from the policy. Review five-minute windows and the full
fifteen-minute average, and keep actual last-hour counts separate from projected
hourly rates. Below 60/min for a full fifteen minutes, confirmed by two
non-overlapping five-minute windows, requires reassessment. Diagnose telemetry,
stops, supplies, movement stalls and competition before changing patrols.
Do not churn candidates during an otherwise valid ongoing comparison; finish
its repeated samples unless safety or invalid telemetry interrupts it. Above
75/min is welcome; never slow down merely to stay in the target band.

The scheduled monitor applies this policy; the app itself does not autonomously
compare and choose whole routes. Manual activity defers route changes. A failed
controller is a failure requiring recovery, not evidence of poor spawn density.


Production adaptation monitoring also retains the latest four complete,
non-overlapping fifteen-minute windows, anchored to actual activation.
`fixed_route_windows` keeps zero-kill downtime, excludes the incomplete current
window and counts boundary events exactly once. The heartbeat persists these
under `production_adaptation.ongoing_validation` in the Bandit experiment report.
These rates are throughput evidence only: safety, telemetry continuity and manual
activity still need review. One passing interval does not establish sustained
performance or select a comparison winner. Normal hunting may retain a prior
transient controller detail; the monitor only labels it a block in needs_attention.
