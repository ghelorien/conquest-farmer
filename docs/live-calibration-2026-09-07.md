# Live calibration: September 7, 2026

The administrator worker successfully opened the running `ImConquer.exe` client
after Windows approval. PID 47420 and process creation identity remained unchanged.
Read-only sampling returned character `Parasite`, HP 51, and position (441, 387),
matching the live display. These observations support the candidate fields; they
do not qualify the complete game-state reader or autonomous farming.

## Character setup verified on screen

The Status panel identifies level 1, job `InternArcher`, initially with empty
equipment slots and physical attack 2–2. Foreground SendInput clicks successfully
opened Status and Inventory. Right-clicking each starter item equipped it:

| Item | Observed result |
| --- | --- |
| Starter bow | Bow in the weapon slot; physical attack became 5–7 |
| Coat | Armor in its slot; defense became 2 |
| LuckyArrow | Arrows in the ammunition slot; counter 200; attack became 10–12 |

The remaining inventory contains two Stanchers and additional LuckyArrow stacks.
The hovered Stancher tooltip states HP +70, amount 1. Potions have not been used
or assigned to hotbar keys. No combat or farming input was sent in this session.

## Candidate player pointer path

The exact executable hash is
`c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396`.
Read-only scans and follow-up reads found this candidate path:

1. Read the 64-bit pointer at `ImConquer.exe + 0x697970`.
2. Read another 64-bit pointer at that returned address to obtain the object.
3. Require the object's first pointer to equal `ImConquer.exe + 0x5cef40`.
4. Candidate fields relative to the object: UTF-8 name `+0xA4`, uint32 HP
   `+0x3E0`, and adjacent uint32 X/Y `+0xE8`.

The candidate profile is `profiles/classic-1074-player-candidate.yaml`. The
resolver rereads all pointers per sample and rejects mismatched fingerprints,
out-of-module RVAs, null pointers, changed object types, and pointer changes during
resolution. There are no saved heap addresses in the profile. Tests simulate ASLR
and object replacement, but **a real client restart has not been tested**.

Local ignored reports contain the evidence: `player-object-pointers.json`,
`player-manager-pointers.json`, and `resolved-player-candidate.json`. One preliminary
object inspection crossed an unreadable page and returned WinError 299. A narrower
inspection within the readable page succeeded; the failed read was not treated as
data.

## Input status

Foreground SendInput mouse diagnostics work. Previous background mouse messages
failed to open the intended panel and remain unqualified. The Status & Equipment
hotkey was inspected and reads `None`. A Computer Use F11 press in the assignment
dialog did not register; the dialog was canceled, leaving the binding unassigned.

Worker protocol version 2 adds one bounded foreground or background F1–F11 key
diagnostic with a live name/positive-HP guard. F12 is excluded and remains the
worker stop key. The background path uses window-directed key messages without
activating the game or moving the physical cursor. These new keyboard diagnostics
have unit coverage but still require live baseline and background tests.

No minimization, map-change, entity/inventory reader, route, kill/pickup accounting,
or supervised 30-minute farming qualification has been completed.

## Subsequent keyboard test and limits

The version-2 worker successfully started after the administrator prompt was
reissued, and the original worker was shut down. TeamViewer was brought to the
foreground after Calculator activation twice returned `failed to activate captured
window`. Background F11 and F10 key messages left TeamViewer focused and the cursor
unchanged at the before/after samples, but neither key appeared in the game's
assignment dialog. Foreground virtual-key SendInput baselines also did not capture
either key. The assignment was canceled and Status & Equipment remained `None`.

This is an **inconclusive keyboard compatibility result**, not proof that every
background keyboard approach is unsupported: the foreground baseline did not
establish a working comparison. The diagnostic code now includes explicit hardware
scan codes and `KEYEVENTF_SCANCODE`, following Microsoft's
[KEYBDINPUT documentation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput).
This correction is not loaded into the already-running worker; health reports
`input_revision: 2` only after an updated worker starts. It has not yet had a live
client test. Do not repeat tests assuming source edits changed a running worker.

A screenshot during the interval showed the character dead; a later screenshot
showed revival at (430, 380). These changes were not produced by the diagnostics.
The intervening memory sample and screen capture were not synchronized, so the
sequence does not prove death-detection semantics for the HP candidate. The pointer
path subsequently returned (430, 380), matching the revived character's display.
The remaining inventory reference is one Stancher and silver 4,520. Historical
files with `dead` in their names must not be interpreted as synchronized death
labels: `player-health-neighborhood-dead.json` was sampled after revival.
