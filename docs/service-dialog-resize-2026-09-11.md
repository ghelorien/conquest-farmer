# Service dialogue resize failure

The route stopped at the Phoenix Conductress before selecting Market. The
reader required a 220-pixel option table and computed two 110-pixel columns.
Live memory instead reported a 356x162 dialog with a 316x44 option table at
(398,130)-(714,174). NPC identity and every dialogue record matched the saved
route. The retained renderer tables independently showed two equal option
columns, distinct from the unequal portrait/text table above them.

Shared dialogue selection now uses the current table width divided into two
columns. It still requires stable records, an unambiguous option, finite
bounded geometry, 22-pixel rows, permitted scroll state, no text input, and
full visibility inside the dialogue. The ordinary trade click retains client
geometry, focus and manual-input guards. Twin City destinations use the same
geometry helper while preserving their exact text, price and option-ID check.

Validation: 57 focused market, transport, banking and overflow tests passed,
including the observed Phoenix rectangle, resized Twin City destinations,
out-of-window geometry, nonfinite values, ambiguous choices and existing
exchange/deposit receipt protections.

The existing departure marker was reconciled separately using the known
pre-input exception, unchanged silver, original map and all ten exact Meteor
UIDs still present. The original journal and inventory are preserved in
reports/performance/service-layout-failure-before.json. No uncertain fare or
exchange was retried. App PID 935164 loaded the fix via safe reload. The live
route then reached Market and verified exchange of the recorded ten Meteors
for MeteorScroll UID 293423295. Further receipts are in
reports/performance/resized-service-route-validation.json and the banking
journal.

Live completion: MeteorScroll 293423295 and the other carried protected item
received verified Market warehouse deposits. The controller returned through
Mark.Controller to Phoenix, completed the journal at 1789177669, and resumed
the hunting runner at 1789177671. HP stayed 1005 across the monitored Market
loop. Several Market movement stalls recovered; this dialogue fix does not
claim to eliminate those pathing inefficiencies.
