# Stored MeteorScroll delivery

`warehouse-withdraw-scroll` admits exactly one UID of type `720027` from fresh
rich Market warehouse memory. It requires the current bound farmer profile,
character UID and process creation identity, a living farmer on map 1036, a free
inventory slot, no carried loose Meteors, no conflicting modal and the current
authenticated town input guard. Binding, quantity, plus and sockets must all
match a single unbound MeteorScroll.

The `/town` bridge request contains `action`, `operation_id`, `uid`, and an
`expires_at` no more than five seconds ahead (plus the normal profile/token
binding). Submit that operation once. On an uncertain response, use only
`warehouse-reconcile-scroll` with the same operation ID and UID. This separate
read-only endpoint takes no expiry and cannot admit an absent operation.

If the journey crashed before database admission, reconciliation uses fresh
exact bank ownership to durably close that old operation as `no_transfer`,
without gameplay input. This tombstone prevents a late original request from
clicking. A new operation ID may be admitted only after the journal history
proves that no input boundary occurred. Possible-input and blocked operations
never receive a new automatic admission.

Operations share `reports/banking/protected-withdrawals.sqlite3` and its route,
reload, delivery and recovery holds. The durable phases are `prepared`,
`input_maybe_sent`, `reconciling`, and the outcomes `withdrawn`, `no_transfer`,
`operator_overridden` or `blocked`. `blocked` permits only read-only settlement
or an incident-specific operator override, never another click. Exact success
requires the UID absent from the warehouse and present in the bag, all seven
ownership fields matching, other items and equipped ammunition conserved, and
unchanged silver. A withdrawal receipt is not a merchant transfer receipt.
Route startup permits only the exact typed scroll hold named by its pending
journey to reach read-only reconciliation before any ordinary input. Other
holds retain their existing stop behavior, and an unresolved recovery read
stops before route input.

For native journey use, `delivery_journey.start_market(loop, stored_scroll_uid)`
starts from Market even when the bag has no eligible loot. It performs no
funding or transport action. `start(loop, stored_scroll_uid=...)` uses an already
qualified town-to-Market round trip. Both paths persist the exact selection and
operation ID, bank leftover loose Meteors before withdrawal, and pass the
retrieved inventory through the existing bilateral delivery route. Calling the
entry again for a pending journey resumes read-only withdrawal reconciliation
before movement; it does not repeat the withdrawal submission. An unresolved
trade always prevents warehouse fallback input.

The ordinary post-shopping caller forwards a verified completed consolidation's
stored scroll UID, so a bag containing no eligible loot can still start the
delivery. That historical record is delivery intent only: fresh rich Market
warehouse memory authorizes the actual withdrawal. The requested scroll is
reserved during merchant planning even when higher-priority gear competes for
the last slot. Current process and exact UID ownership are revalidated after
withdrawal and before trade; interruption before warehouse close is recovered
before new trade input. Completion records an exact bilateral delivery receipt
or an explicit, freshly verified `deferred_rebanked` disposition. Missing
readiness or unresolved requested ownership leaves the journey pending.
Trade request IDs are recorded against the current withdrawal before submission;
only their exact bilateral receipts can resolve its delivery, including recovery
when a native result was lost. Historical receipts for the same UID cannot
resolve a later journey. If a crash interrupts fallback after the bank receipt
or disposition was saved, resume reopens the warehouse and reads fresh rich
ownership before completing, without attempting another withdrawal or trade.

For supervised staged delivery, withdraw/reconcile through the authenticated
bridge, then use the existing request, accept, offer and confirm probes with
fresh bilateral evidence. The withdrawal endpoint never initiates a trade.
