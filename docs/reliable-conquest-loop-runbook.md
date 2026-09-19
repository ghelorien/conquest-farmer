# Reliable Conquest Loop operator runbook

This runbook is for a supervised operator. It does not authorize autonomous
gameplay, visual inspection, OCR, screenshots, or computer-use observation.
All decisions about game state remain dependent on qualified read-only process
memory. A failed or unavailable reader is a hold, never a reason to substitute
a screen observation.

## What automated evidence establishes

The automated test suite exercises the domain and integration contracts with
synthetic, memory-shaped snapshots. In particular it covers durable manual
request bindings, approval and timeout races, one-shot decline claims, global
approved-session input fences, bot-delivery priority, exact visitor permissions,
settlement sale exclusion/baselines/replans, profile migration integrity,
immutable-release manifests, and Meteor archive/replan crash boundaries.

This is implementation evidence only. It does **not** prove a native reader is
valid for a particular live client, that an input control affects that client,
that a trade, listing, delivery, sale, warehouse action, reconnect, or Discord
message succeeded in game, or that an operator's memory observation was fresh.
Do not label a test result as a live validation or a sale receipt.

## The seven supervised live validations still required

Run these only one at a time with Farming Off unless the item explicitly says
otherwise. Record the exact selected profile, full process identity, map, UTC
time, operator, memory-reader qualification, pre/post ownership evidence, and
the resulting durable receipt or hold. Stop immediately on a reader mismatch,
unexpected window, missing receipt, changed process identity, or any input that
cannot be reconciled.

1. **Unknown visitor prompt and verified timeout decline.** On a qualified
   merchant, show one unknown visitor request and record its exact immutable
   binding. Let its timeout create the durable decline intent; verify the claim
   is written immediately before the one qualified native decline attempt and
   reconcile the declined result from fresh memory.
2. **Approved low-value manual trade and zero automated trade input.** Approve
   one deliberately low-value manual trade from the displayed exact binding.
   During the approved interval, prove through the input/audit records that no
   automated accept, offer, confirm, cancel, listing, repricing, refill,
   recovery, calibration, or farmer trade input occurred. Settlement is not a
   sale receipt.
3. **Restart during an active session in the same game process.** Restart the
   app while an approved manual session remains open, retain the same full game
   process identity, and confirm the durable fence/session resumes from fresh
   memory without an input replay, a new permission, or an inferred outcome.
4. **Stop/pause during approval and settlement.** Exercise explicit Stop and
   saved pause after approval and again while settlement is stabilizing. Confirm
   both intent changes survive restart/settlement and neither releases the
   manual fence nor starts ordinary merchant or farmer work.
5. **Profile migration plus successful profile management.** With the app and
   workers offline, migrate the legacy profile state, then successfully add,
   edit, import, and export profiles. Verify manual permissions, delivery
   admissions/pending receipts, credentials, diagnostics, and terminal session
   history remain profile-scoped and intact; unresolved work must still block
   unsafe edits.
6. **Full memory-only hunt, bank, Meteor, delivery, and return loop.** Using
   qualified read-only memory only, supervise a complete hunt → bank → ten
   Meteor consolidation → automatic MeteorScroll/stock delivery to owned
   merchants → bank leftovers → return-to-hunting cycle. Verify exact IDs and
   receipts at each boundary; loose Meteors must not be delivered.
7. **Receipt/audit/control and replay inspection.** Inspect automated bilateral
   delivery receipts, the manual audit and ownership delta, the sales baseline,
   saved control intent, and restart/recovery records. Confirm manual activity
   did not become a sale and that no claimed decline, fare, exchange, deposit,
   trade, or other uncertain input was replayed.

## Additional supervised checks (not replacements for the seven gates)

After all seven required validations, retain these separate checks as relevant:

- Merchant capacity refill: validate the fifteen-minute history-priced refill,
  unknown-value queueing, highest-value-first order, Pause refill, and Global
  Stop independently from trading/repricing intent.
- Unexpected merchant reconnect/Market return: validate the memory-qualified
  Conductress/fare, vacant-stall claim, verified-price restoration, and the
  five-second no-progress protective-disconnect rule. Do not initiate a routine
  disconnect for this check.
- Shops alerts and release rollback: validate four-hour receipt-based reports
  (including zero sales), persistent-failure/recovery notifications, immutable
  release activation, launch from its local interpreter/external state root,
  and rollback to a retained verified release.

## Audit and receipt review

Before and after each supervised validation, retain the relevant local evidence:

- `reports/merchants/journal.sqlite3`: `manual_audit`, immutable manual
  evidence/declines/claims, sales baselines, `sales_observation_gap`, and
  `manual_replans`. A completed manual session has `sales_receipt=false`; it
  cannot be counted as a sale.
- Merchant and farmer delivery/warehouse journals: exact UID receipts and
  terminal state. An unresolved or uncertain receipt is a hold, not success.
- The encrypted local credential files stay in managed state. Never copy their
  contents into a report, release, or chat.
- The shops alert state and durable sale receipts: confirm the period and
  lifetime totals exclude listing/reprice activity and label observation gaps.
- `active-release.json` under the managed data root plus the selected release's
  `release-manifest.json`: verify the pinned digest before launching.

Use the UI's recheck/override flow only for the exact displayed incident. An
override preserves evidence but does not manufacture a transfer, sale, payment,
or baseline. Never delete, edit, or re-use a decline claim to clear a hold.

## Safe release and rollback

With all automation stopped and the managed application lock free, build and
verify a release from a clean checkout, then activate it:

```powershell
py scripts/release.py build C:\src\Conquest-farmer C:\releases 2026.09.19
py scripts/release.py activate C:\releases\2026.09.19
py scripts/release.py verify C:\releases\2026.09.19
py scripts/release.py launch
```

The release and managed state roots must be separate. A changed, added, missing,
or reparse-point file invalidates the release; do not bypass that check. If the
activated build needs to be reverted, stop the app and use:

```powershell
py scripts/release.py rollback
```

Rollback retains releases and atomically selects only a previously verified
manifest. It does not erase or rewind journals, manual-session evidence,
credentials, delivery receipts, or sales totals. Reconcile those separately
from fresh memory before resuming any work.
