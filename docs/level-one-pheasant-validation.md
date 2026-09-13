# Level-one Pheasant startup validation — 2026-09-12

Tested the portable review build with the America character Kilhiam. All
gameplay observations below came from the authenticated memory bridge and
durable runtime records; no screenshots were inspected.

## Failures and corrections

- Native maximization had not completed when embedding checked the pane.
  Attachment now waits for stable, mapped geometry with a bounded timeout.
  Successful live attachment measured a 2056 × 1236 pane. Diagnostics retain
  measured and required dimensions; the bridge reports a pending attachment
  while layout is still settling.
- The character's learned-skill vector was `(0, 0, 0)`. The existing range reader
  rejected an empty vector and required Scatter even for ordinary bow attacks.
  Explicit optional-Scatter reads now support validated empty vectors, retain
  identity/stability checks, and report Scatter as absent. The route adapter
  selects the existing single-arrow behavior and blocks Scatter preferences
  for that character. Default callers still require learned Scatter.
- The active Pharmacist window measured 288 × 430 and its grid 248 × 362,
  instead of the previous fixed heights of 396 and 328. Widths and padding were
  unchanged. The item-point guard now permits vertical resizing while retaining
  qualified widths, padding, row pitch and visible-row checks.

## Live result

The pinned memory reader reported bow type 500301, range 8, and no Scatter.
The Pharmacist was vendor 100184; Painkiller was type 1000020 at 60 silver.
Five individual purchases were verified by the existing transaction engine.
The route continued to the Pheasant hunting area and recorded five confirmed
kills in six attack attempts using single attacks. Farming was left running
under the user's requested route.

Optional equipment reviews encountered unavailable shop/scene observations
and were deferred by the existing engine. This pass verifies route startup,
supply purchasing, travel and initial combat; it does not qualify every
equipment vendor, sustained performance or an entire depletion/recovery cycle.

Full regression suite: 2,047 passed in 110.72 seconds. New cases cover native
resize timing, cancelled attachment, pending bridge responses, empty/malformed
and changing learned-skill vectors, preserved Scatter behavior, and shop
geometry invariants.

Changes are isolated to the portable UI review worktree. The other behavior
checkout and its newer merchant update were not overwritten.

## Extended Pheasant and supply-cycle validation

The longer test exposed behavior not covered by the initial startup pass:

- Automatic leveling left Pheasants for Turtledoves at level seven. The existing
  session-plan mechanism now supports a saved-route hold using that route's own
  restock town. Kilhiam's local hold remains on Pheasants until explicit Resume
  leveling; it does not expire into a more dangerous zone while the user is away.
- F1 healing was not verified on this character and the run stopped. A shared
  memory-identified inventory action now consumes a specific carried Painkiller
  and requires inventory consumption plus an HP increase before reporting a
  verified heal. Combat and travel reuse that action. No shortcut binding or
  screen observation is assumed. Focus and changed-item guards remain enforced.
- Three live combat heals were verified, followed by an automatic zero-potion
  return, essential-funds withdrawal and five verified replacement purchases.
- There were deaths during earlier combat and town portions. Revival worked;
  the earlier town death's cause remains unestablished. The final bounded
  validation below covers the subsequent stable run and another full town trip.

Current local preferences: heal below 70%, kite when surrounded, Pheasant hold.
The character reached level nine while the hold remained active. The profile's
credentials and observed capabilities remain local and separate from templates.

Regression coverage after healing/hold changes: 2,051 tests passed in the full
run; five Tk tests encountered a transient Tcl initialization failure and all
five passed on an isolated rerun. Targeted combat/healing/recovery tests: 207
passed. Live read-only samples are in the ignored `reports/portable-pheasant-*`
files; runtime receipts remain in the character's local journals.

## Final live audit

The requested bounded farming/restocking test passed on the running build:

- Pheasant hold stayed active through level 11. The latest stable hunt recorded
  423 verified kills over approximately 27 minutes before needing arrows.
- Combat heals at Unix times 1789252905.792 and 1789253538.878 each consumed a
  Painkiller and raised HP from 120 to 174, verified through memory.
- The earlier zero-healing-supply trigger completed a return, funds withdrawal
  and five replacement purchases. The final arrow cycle independently topped
  healing supplies back up to five.
- At 1789253680.251 the script detected two total arrows and requested town.
  It withdrew essential funds, bought three Painkillers at 60 silver each, and
  bought two 200-arrow LuckyArrow packs at 200 silver each. The arrow receipts
  were recorded at 1789253740.625 and 1789253742.068.
- Restock completed at 1789253785.417 with 400 arrows, five Painkillers and
  200 carried silver. The warehouse balance was verified at 3,095 silver.
- Combat resumed with the purchased arrows equipped. Live samples from
  1789253822.322 through 1789254004.074 cover just over three minutes: kills
  increased from 4 to 82, level rose from 10 to 11, HP ended at 204/204,
  and five Painkillers remained. The final sample had 112 equipped arrows
  and one 200-arrow reserve pack.
- Neither the combat journal nor the route journal recorded deaths, revival
  events or failures after the stable run began at 1789252038, including the
  final town trip. Transient focus/observation waits recovered automatically.
- Farming remains On in the running app, with no end-time limit. The existing
  combat loop renews duration/action-limited sessions while intent remains On.
  Pheasants remain selected until explicit Resume leveling.

Evidence: character-local `reports/desktop-farming/trial.sqlite3`,
`reports/overnight/events.jsonl`, banking receipts, authenticated live worker
responses, and ignored `reports/portable-pheasant-refill-final-watch.jsonl`.
This is a completed live Pheasant/supply-cycle test; remote-PC qualification
and the broader portable-profile publication work retain their separate scope.
