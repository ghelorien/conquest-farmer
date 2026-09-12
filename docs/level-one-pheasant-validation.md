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
