# Old Spiritual refill cursor reconciliation (source only)

The explicit authenticated `merchant-refill-cursor-reconcile-1078` command is
limited to Spiritual's pending cursor for UIDs 295705626 and 294891157. It is
not scheduled and has no game-input path. It does not enable listing, alter a
merchant control, complete a fifteen-minute check, or advance that check's due
time.

Before changing the journal it requires the existing exact-1078 input fence,
the configured character UID, two matching fresh read-only memory observations
of an owned open Market booth with closed trade/request windows, and the same
process identity that was recorded at Market arrival. The restoration preview
must prove both cursor items were in pre-disconnect inventory and still have
the same UID, type, name, plus, sockets, binding and quantity in current
inventory. All old stock must still be accounted for; an unresolved bot
transaction or delivery blocks the command.

Within one immediate SQLite transaction, the command rechecks the unchanged
refill, return and safety states, unresolved work, and the absence of any
journaled listing transaction or step since this refill attempt began. It then
archives the complete prior refill state with an evidence digest, clears only
`pending` and `cursor`, marks `reconciled_no_listing`, and appends a separate
event. It does not claim that nobody manually listed and later removed an item;
the finding is restricted to the journaled bot path and current memory stock.
Any uncertainty leaves the pending cursor unchanged.

This commit adds source only. It has not been run against the live app or
journal, deployed, or tested beyond syntax and diff checks.
