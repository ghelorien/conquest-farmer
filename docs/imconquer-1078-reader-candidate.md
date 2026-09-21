# ImConquer 1078 read-only candidate

This is evidence for a candidate only. No production loader selects it, and the
existing `c2b53437...` fingerprint guard remains the only runtime-supported
layout. No game input is authorized by this record.

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

The following remain deliberately unavailable: equipped ammo and all farming
 inventory semantics, map/entity/monster semantics outside this manual snapshot,
GUI layout/input semantics, counterpart trade-acceptance/silver semantics,
restart qualification, and every input path. A
farmer's zero booth-owner state is carried explicitly and is valid only while
the booth model is closed and its deque is empty; it never synthesizes an
owned booth.
