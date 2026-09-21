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

The following are deliberately not mapped or enabled: item equipped-ammo and
other inventory semantics, map semantics, actor/monster fields, GUI window
context/layout and trade-model semantics, server identity, current-HP change
semantics, restart stability, and every input path. Each needs its own fresh,
read-only evidence before a versioned runtime loader can be considered.
