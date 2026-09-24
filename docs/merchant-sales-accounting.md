# Merchant sales accounting

The original detector required a booth item's asking price to exactly equal the
silver balance increase. America booth receipts instead show a 3% deduction, so
the detector classified genuine sales as unconfirmed and displayed zero earned.

The detector now records the actual silver gain after checking departed booth
UIDs, unchanged inventory and remaining listings, process identity, observation
continuity, and absence of trading or concurrent listing transactions. Expected
net proceeds allow floor/ceiling rounding of 97% per listing; historical balance
evidence contains both directions. No estimated amount is credited. A stack's
asking price is counted once, independent of its item quantity.

Stock and balance updates may arrive separately. A durable five-second receipt
window handles either order and survives restart. Expired, conflicting, or
disconnected observations remain unconfirmed. Memory reads also check that the
silver balance did not change while the inventory and booth were sampled.

The September 12 repair reconciled all 41 legacy departure records (49 items)
against saved memory balances and known listings in nine batches:

| Merchant | Items | Observed net silver |
| --- | ---: | ---: |
| Spiritual | 28 | 15,240,874 |
| Dutch | 21 | 5,206,087 |
| Combined | 49 | 20,446,961 |

These are historical balance reconciliations, distinguished from continuously
verified receipts. Aggregate amounts remain aggregate: uncertain fractional
silver is not allocated to individual historical items. A batch crossing a
four-hour report boundary is included in cumulative totals only, with a notice.
Already-delivered Discord reports are preserved; future native reports use the
corrected totals. Earlier sales before tracking remain unavailable.

Local audit files are `reports/merchants/sales-repair-evidence.json` and
`sales-repair-result.json`, with a SQLite backup identified in the result file.
The one-time repair tool `sales_recovery.reconcile` (removed after
`r38-baseline`; recover it from that tag if needed) defaulted to a dry run and rejected overlapping repairs,
balance mismatches, changed identities, unresolved operations, unknown listings,
and items still present. The repair does not use missing inventory as sale
evidence and does not resolve the separate experimental input incident.
