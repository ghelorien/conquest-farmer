# Supervised offer abort

This is a separate operator workflow for an exact `offer_verified` supervised
probe with one selected item, reciprocal participants, zero offered silver and
neither side accepted. It does not loosen delivery reconciliation or manually
confirm a transfer. All observations and close-control qualification use memory.

The authenticated desktop bridge supports these exact actions:

1. `probe-delivery-abort-recheck` (only `action`). Read the returned
   `confirmation_reference`, selected probe, immediate ownership and named holds.
2. `probe-delivery-abort-start` with `operator_confirmed: true`, `operator`, and
   that exact `confirmation_reference`. This permits one qualified native close.
   The separate `.abort.json` is fsynced as `cancel_submitted` before the press.
3. If submission is uncertain, use `probe-delivery-abort-reconcile` (only
   `action`). This never clicks. It can finalize only exact closed/restored
   ownership. Never repeat a submitted close. A failed `abort_prepared` attempt
   can be archived and replaced only through a new explicit preview/confirmation.
   After exact cancellation proof is verified, closed observations since durable
   submission remain eligible even if Stop or a read failure delayed verification;
   closed history from before submission never qualifies.
4. After `cancel_verified`, use `probe-delivery-abort-disposition-recheck`, then
   `probe-delivery-abort-disposition-override` with a **new** exact confirmation
   reference, `operator_confirmed: true`, and `operator`. This atomically disposes
   only the two previewed unapproved false admissions into mandatory rebaseline.

The two confirmations expire after 30 seconds. A successful disposition can be
retried idempotently with its original reference after expiry. Harmless concurrent
observer records are accepted only as a fully validated append-only suffix to
the confirmed immutable history prefix. The prepared input capability freezes
the exact refreshed history and cannot survive process restart or worker exit.

Stop, pause, manual mouse ownership, F11/F12, changed control intent, a new or
approved hold, changed process/profile identity, and changed immediate ownership
all deny input. Abort requires the exact already-owned merchant host and never
reconnects, reparents a replacement, changes saved enablement, or borrows ordinary
delivery trust. No acceptance, offer, silver entry or confirmation is permitted.

Historical merchant booth/silver drift is captured without attribution at abort
preparation; further drift before the close is not allowed. The close proves only
the selected item restored to the farmer and unchanged unrelated holdings versus
that immediate baseline. Later booth sales can be recorded in the separate closed
disposition preview without changing the immutable cancellation receipt.

Both overridden sessions retain outcome `unknown`; no sale or delivery receipt
is created. Both processes must then produce two equal closed-window canonical
ownership samples at least five seconds apart. Rebaseline records observation
gaps, replaces sales baselines, and requests fresh planning. A new probe is blocked
before archival/replacement until every bound rebaseline is complete and all
manual holds are clear. Process rollover remains held for operator attention.
