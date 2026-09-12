# Native stale-state recovery

At 1789207987–1789207990, three consecutive samples exceeded the 0.85-second
inventory freshness threshold. The legacy three-failure stop terminated the
native combat supervisor; the route then treated persistently_stale_state as
a terminal fault and switched Off. The later live check found the character
dead at (403,433), with fresh memory available and revival ready. Restarting
recovery revived the character to 1226/1226 and resumed combat.

The native loop now rejects expired (and future-dated) snapshots and retries
without terminating its supervisor. Every retry passes through the existing
fresh life/revival and manual Stop checks. No stale snapshot authorizes combat,
movement or healing; session counters and elapsed time are preserved. The
legacy unsupervised trial retains its bounded stop. Recovery-wait and resumed
events distinguish a prolonged read delay from normal operation in the log.

Regression tests simulate four stale samples followed by a recovery step,
fresh data and a verified healing-item consumption. They also exercise manual
Stop during the stale sequence. These simulated failures do not induce a live
death or weaken the freshness limit.

Live loading and resumed farming are recorded in
reports/performance/stale-state-recovery-2026-09-12.json.
