# Socket and ground-item memory validation — 2026-09-12

Scope: read-only memory of the installed `ImConquer.exe`, SHA256
`c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396`.
No screenshots, process writes, injected code, drops or pickups were performed
by the agent during this investigation. Farming remained manually stopped.

## Full item records: corrected sockets

The item object with vtable RVA `0x5cf220` stores socket/gem bytes at **+0x67
and +0x68**, not +0x69/+0x6a. Zero means no socket; 255 means an empty socket.
Other supported values describe the inserted gem. Plus remains +0x6b.

Evidence from the runtime code:

- Tooltip predicate RVA `0xb1710` checks +0x67/+0x68 for nonzero values.
- Formatter RVA `0xb1730` counts those nonzero fields, then formats resource
  10099 (`0x2773`). The installed `ini/StrRes.ini` identifies this resource as
  `Socket {0:d} gem(s)`.
- At `0xb17eb` the formatter skips both zero and 255 before looking up a gem
  definition from its byte code plus 700000. The second slot follows at
  `0xb19a6`. This distinguishes an empty socket from an inserted gem.
- Socket insertion code compares +0x67/+0x68 to 255 at `0xb647d`/`0xb650a`
  and passes slot numbers 1/2 to the item operation.
- Incoming full-item updates write these bytes at `0x1e0f73`/`0x1e0f7f`.

Live verification: Kilhiam's equipped Super ScarletBow, item UID 293092845,
type 500069, returned **255, 0** in three independent observations. Each
rechecked the character, equipment pointer and process identity. The user
independently reported that the bow text says one socket. The former reader
returned 0, 0 because it read the wrong offsets. Its result was incorrect.

`equipment.item_details` is shared by equipped-item protection and merchant
inventory/booth reads. Tests now exercise socket bytes through that reader and
the merchant comparison key, including zero, empty and gem-filled cases and a
changing socket. Two sockets and inserted gems have fixture/code-path coverage;
they were not separately exercised on live items during this session. Existing
cached owned-stock observations must be refreshed before relying on their
socket classification. This session did not run merchant operations.

## Ground records: corrected identity, sockets still unqualified

The former ground reader mistook shared-holder +0x50 (actor +0x40) for a UID.
The constructor initializes this field to zero at `0x15db98`; the renderer
increments it at `0x15de40` through `0x15de47`. It is a render counter. It must
neither identify a drop nor invalidate an otherwise stable observation.

The world singleton getter RVA `0x96fc5` resolves RVA `0x699360`. Its ground
manager's vector header is at RVAs `0x6994d8`, `0x6994e0`, `0x6994e8`.
Each 16-byte vector entry contains a registry-record pointer and its shared
holder. The holder has vtable RVA `0x5ccc08`; the record is holder +0x10.

| Record offset | Value |
| --- | --- |
| +0x00 | uint32 ground UID |
| +0x04 | uint32 item type |
| +0x08/+0x0c | uint32 x/y |
| +0x10 | actor pointer |
| +0x18 | actor shared-holder pointer |

Creation function RVA `0x145200`, including stores at `0x145388` onward,
establishes these fields. The reader verifies the registry owner, actor/holder
relationship, actor type and tile, then rechecks records, vector, root pointers
and process identity. The existing half-second freshness limit is preserved.
Rendering counters and reference counts are excluded from identity checks.

One user-dropped ordinary AmethystRing (type 150075) was observed at (133,231),
ground UID 2068692127, actor-holder address 1388603328, plus zero. This validates
the corrected live mapping; it is not a pickup receipt. Two subsequent external
bridge samples exceeded the freshness limit and correctly failed. Ground regression tests cover
counter advancement, zero initial counters, changed actual UIDs/tiles/actors,
creation time, empty registries, type-zero placeholders and stale observations.

The inspected decoded ground message (type 1101/`0x44d`) has seven declared
fields: UID, type, x, y, plus, action, and a display flag. Its parser starts at
`0x22a5a0`; the creation caller is `0x1c8d45`. The actor stores plus at +0x48
and the flag at +0x49. Formatter `0x15e2a5` uses the flag to prepend
`Legendary` (string RVA `0x5c6120`). This does **not** qualify it as a socket
field. The parser retains unknown protobuf fields generically; inspecting its
seven declared fields is not proof that no additional data can ever arrive.

No ground socket count or gem contents have been validated. The corrected full
item reader cannot be applied to the smaller ground actor. A controlled
same-item comparison remains necessary before claiming socket-only ground
selection works. The offered Super bow remained equipped and safe. No socket
values were guessed and no ground loot policy was expanded. The identity bug
could suppress valid drop observations, but this investigation cannot establish
how many historical drops it affected.

Local raw-memory evidence is retained outside Git under
`%LOCALAPPDATA%\Conquest\diagnostics\ground-sockets`; client code dumps are not
repository artifacts.

## Deployment and regression checks

The full suite passed 2,089 tests. A final preservation of the 256-record read
bound then passed all 59 focused ground/equipment tests, including its new
pre-read rejection case. Desktop startup import checks passed.

The native app safely reloaded in Market after fresh memory confirmed a clear
area, no external input owner, and Farming Off. New app PID 28972 retained game
PID 25124. Its own `town/gear` response now reports ScarletBow UID 293092845 as
gem1=255, gem2=0. Its `town/ground-items` reader returned a valid empty ground
snapshot at (131,233), map 1036. Farming remained Off. This confirms deployment
and the equipped socket fix; it does not claim a live pickup or ground socket
qualification. The safe reload did not restart the game.
