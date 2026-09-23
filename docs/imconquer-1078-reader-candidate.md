# ImConquer 1078 read-only candidate

This is the original reader-qualification record. Its observation-only
conclusion below describes that checkpoint, not the later 1078 farmer runtime.
See the live-runtime update at the end before changing current input gates.

The separate `reader_1078` and manual-reader registry now support read-only
operator handoff observation for this exact build. The normal farming, banking,
merchant input, travel and recovery readers remain qualified only for 1074;
their fingerprint guard is unchanged. No game input is authorized by this record.

Live user-operated calibration verified the exact Painkiller transfer between
Parasite and Spiritual, unchanged transfer silver, a one-silver booth listing,
closed-window ownership stability, and Parasite's HP1454/1460 and level93.
These calibration observations are not automated delivery or sales receipts.
The app integration uses a separate input-denied registry and durable global
Manual handoff sessions; it never installs a 1074 controller on a 1078 client.
App-managed transfer/restart acceptance must be reported separately from reader
calibration. Unknown automated capabilities remain unavailable.

## Shared warehouse reader

`MemoryWarehouseReader` now explicitly selects the exact-build warehouse read
layout. All other `MemoryGui` callers retain their 1074-only default. On 1078,
the actor warehouse deque is `+0x1030`, capacity `+0x1058`, bank silver
`+0x106c`, and active warehouse model22 has vtable `0x5e7250`.
The user confirmed 24 items, 80 slots and 44,671,743 bank silver. After a
user-performed Painkiller deposit plus separately acknowledged withdrawal and
purchases, the existing shared reader returned two matching live observations:
25 items, 80 slots and 44,621,743 bank silver. The closed window was correctly
rejected before reopening. No automated input or clean deposit-only receipt
was claimed. Money controls, protected-withdrawal receipts and normal 1078
banking remain unqualified.

The user also confirmed equipped SpeedArrow UID295704431 at quantity4734 and
CarvedBow UID257871078 at +2. Equipped arrows were read at actor `+0xc40`
(wrapper `+0xc50`). Reload/combat semantics are not qualified by a static count.

The installed 1078 executable has SHA-256
`be9dd723cad8eb9068da792b5cb8ceec0d330f08aacb8c948e6f412d1520c4e0`,
PE64 image base `0x140000000`, and image size `0x2a26000`. The 1074 root
`0x69c730` is null in each of the three isolated processes, so the old layout
cannot be reused by replacing a fingerprint.

Read-only, three-process candidate evidence found:

- actual-player root `0x6bcef0`, offsets `[0, 0]`, vtable `0x5ea748`, inline
  name `+0x94`, position `+0xd8`, level `+0x6f8`;
- candidate health attributes at `+0x978` and maximum value at `+0x3e0`;
- wrapper vtable `0x5ea728`, reached through `[8, 0]`; inventory deque header
  `+0xbb0/+0xbb8/+0xbc0/+0xbc8`, independent count `+0xbe0`, silver `+0xab8`,
  and item vtable `0x5ea9f8`;
- map candidates `0x6b9d40/0x6b9d44` and entity collection root `0x6b9b50`
  through `[0x18, 8, 0]`, collection vtable `0x5e8a60`.
- GUI model registry candidate `0x6b8e48`, with a stable 33-key map and models
  `14` (trade, vtable `0x5e6a80`), `15` (confirmation, `0x5e0148`), and `25`
  (booth, `0x5dd9c0`).
- GUI context `0x6b5ef0`: the `+0x3e38` frame, window `+0x97` visibility flag,
  `+0x248` last-rendered frame, and `+0x18` geometry were coherent on two
  samples for all three clients. Model `25` was active only for the two booth
  clients; models `14` and `15` were inactive in those closed-window samples.
- server-string candidate `0x6b7fc0` contains `Classic_US` in the captured
  module data; it was then re-read across all three isolated clients.
- manual request/trade evidence: confirmation model 15 uses title `+0x48`,
  message `+0x68`, actor participant name/UID `+0x1000/+0xff8`; trade model
  14 uses actor participant `+0xfb0/+0xfac`, own/other deque headers
  `+0xf50/+0xf78`, and the locally rendered acceptance flag `+0x98`.
- the manual request, open trade, one-item offer, local confirmation and closed
  inventory transfer were each observed on the exact build.  The offered item
  was checked by UID on both sides before close and in the recipient inventory
  after close; no app action participated in that calibration.
- owned booth fields: actor `+0x32a0`, booth deque `+0x34b0`, and model 25
  selected owner `+0x4c`.  A user-listed Painkiller was observed in that deque
  with its exact UID/type/name/quantity and price `+0x9c`; it was absent from
  the merchant inventory during the same stable sample.

`conquest.merchants.reader_1078` is an explicit, exact-SHA, read-only module
for these manually qualified ownership fields. It is not imported by any normal
observer, controller, input bridge, farming, town, travel, or delivery path.
It does not select a profile automatically and exposes no input/focus methods.
The normal 1074 loader remains unchanged.

The shared warehouse reader also has an exact-1078 read-only layout for the
observed active Warehouse model, stored-item deque, capacity and bank balance.
It does not qualify money amount-entry semantics, vendor interaction, deposits,
withdrawals, protected-withdrawal auditing, or any banking input. Item-transfer
receipts now require the observed bank balance to remain unchanged, but 1078
banking remains disabled pending the full protected receipt path.

The exact-build readers now cover read-only player life, inventory/equipment
(including ammo), scene entities, ground headers, GUI windows/tables, shop and
dialog observations, warehouse inventory/balance/amount text, and closed
merchant ownership snapshots. User-performed 100-silver deposit and withdrawal
observations matched both bag and bank deltas; a user-entered `123456` amount
and cancellation were read without changing stock or silver.

The 1078 desktop checkpoint is observation-only: it can attach the verified
client and expose a read-only bridge, but reports automation unavailable. Native
farming, combat, refill, banking transfers, reconnect, route recovery, delivery
and every game input remain disabled. Open-trade counterpart acceptance/silver,
protected banking receipts, restart recovery, natural death/revive, and pickup
semantics remain unqualified. A farmer's zero booth-owner state is carried
explicitly and is valid only while the booth model is closed and its deque is
empty; it never synthesizes an owned booth.

## Later native farmer runtime evidence (September 23)

The managed `2026.09.23-1078-ammo-r2` farmer worker used a writable embedded
bridge on the exact 1078 process. Its durable `reports/desktop-farming/trial.sqlite3`
contains `trial_started` at Unix time `1790160004.214` with
`observe_only=false`, 120 movement attempts and 109 movement-verification
receipts, 107 Scatter attack attempts and 111 memory kill-counter receipts.
The first recorded movement targeted `[277,281]` and was observed at that
position about one second later. The overnight route log records positions
`[269,273]` through `[495,520]`, arrows `5002` to `4676`, and the run's kill
count `0` to `795`. A healing receipt records potion UID `296219399` consumed
and HP `771` to `1021`. Individual kill-counter receipts do not prove that the
specific aimed monster died.

These are evidence of native 1078 farmer movement, combat and one healing
outcome, not of a fully safe loop. A death was detected immediately after that
healing receipt. The same run has zero verified revivals; the operator later
revived Parasite manually. Its completed town visit inherited an earlier
operator-supervised restock, so it does not prove an autonomous 1078 purchase
or full restock. Merchant listing/refill and automated trade remain disabled
on 1078 pending their separate input qualification. The observer's former
`read_only_worker=true` label was stale; the authenticated bridge `health`
response is authoritative for whether that worker permits input.
