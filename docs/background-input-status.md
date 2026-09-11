# Background input comparison — September 7, 2026

The requested background farmer is unfinished. The dashboard preserves On/Off
and selects monster groups with automatically matched entity IDs. Production
attacks and loot pickup remain unconnected. Background **scene movement** now has
two successful live tests, while background HUD panel input has failed.

## Verified scene movement, September 8

At 01:19:33 UTC, one window-directed WM_MOUSEMOVE/button pair moved Parasite
from (452,449) to exactly (453,449) on map 1002. All eight immediate position
samples showed the requested destination. At 01:20:25 UTC, the reverse click
moved from (453,449) to exactly (452,449), again stable in all eight samples.
The reverse test also checked a stationary 600 ms baseline before input.

Both tests targeted embedded game PID 38336 at 1036×793 logical pixels. Chat
HWND 525270 stayed foreground; the desktop cursor stayed at (927,577). The
reverse test sampled foreground and cursor throughout its position checks.
Neither test moved the desktop cursor, activated the game, or used SendInput.
The projected points were (550,412) and (486,380): anchor (518,396) plus the
isometric delta ((dx-dy)×32,(dx+dy)×16). These observations establish those two
scene movement effects; they do not establish attack hit boxes or HUD input.

Evidence: reports/embedded-message-step-20260908T011933Z.json and
reports/embedded-message-step-20260908T012024621984Z.json. The script preserves
timestamped reports and never repeats an uncertain click automatically.

`BridgeSceneStepper` in src/conquest/scene_input.py makes this method available
to a map-backed route planner. It checks the player/map/HP, a fixed calibrated
viewport, an explicit permitted map rectangle, and a waypoint delta no greater
than six tiles. It checks position feedback after one click and returns an
uncertain or unreached outcome when validation fails. It does not prove that
the route segment is walkable; the route planner must establish that separately.
The existing diagnostic bridge still requires farming Off and another window
foreground. Production farming has not been switched to this adapter.

## Live comparison

The input-capable revision-7 worker was successfully launched for PID 3304.
Parasite showed level 12 and HP 213/213. The worker's logical client size was
1536 × 793; the game screenshot was 1920 × 1020 including its title bar.
Status was calibrated to client point (826, 751).

At 22:03:43 UTC, one background PostMessage click was sent at that point while
Notepad HWND 787944 remained foreground. The sampled desktop cursor stayed at
(775, 408) before and after. The Status panel did not open. Player memory moved
from (434, 391) to (437, 394) during the sample, and the subsequent screenshot
showed (446, 398), still with HP 213/213. The click therefore failed its intended
effect despite stable sampled focus and cursor position.

A later foreground SendInput baseline at the same client point opened the
Status & Skills panel. The cursor moved from (768, 390) to (827, 775), and the
game remained foreground. This validates the point for foreground input; it
does not establish background compatibility. Dependence on the real or cached
cursor remains a hypothesis, not a proven implementation detail.

A planned comparison with differing supplied and real-cursor points did not
send input: the unfocused-window guard rejected it. The final attempt found
that the diagnostic worker had reached its original 15-minute deadline.
No new elevation was attempted. The launcher now uses the existing four-hour
worker limit for future starts, to avoid repeated short-session approvals.

Local evidence is in ignored reports/background-status-scaled.json and
reports/foreground-status-scaled.json. Neither report qualifies autonomous input.

## Embedded follow-up

On September 8 at 00:32:02 UTC, a bounded Status-button test sent WM_MOUSEMOVE,
waited 200 ms, then sent the button pair to embedded Conquer PID 38336. The
window was 1136×793 logical pixels and the point was (626,751). Codex HWND 525270
remained foreground and the sampled cursor stayed at (1035,440). The Status
panel was closed at the subsequent inspection; a successful intended effect
was not verified. The report is reports/embedded-background-status.json.

A separate diagnostic briefly positioned and restored the actual cursor at
01:11:24 UTC. Notepad HWND 262312 remained foreground, and the sampled cursor
was restored. The Status panel still did not open at the subsequent inspection.
This did not establish HUD compatibility and is not a cursor-independent method.
Evidence is in reports/embedded-positioned-status.json.

## Ghost state and Revive follow-up

The visible ghost at (435,453) still had a current-HP candidate of **3/213**.
Positive HP therefore does not establish that this character is alive. A fresh
read of the actual player object found status +0x30 = 0x420, appearance +0xc0 =
98, and the Revive gate +0xae8 = 0. A historical living snapshot had status
0x200 and appearance 0. `memory_life.read_life` reads these fields with repeated
identity/topology checks and exposes explicit ghost and revive-ready candidates;
it does not infer life from a nonzero HP value or from an absent ghost candidate.

Read-only inspection of the existing runtime code provides direct UI evidence:
the string `ReviveButton` at RVA 0x5c51e0 is referenced at 0x9b18c. The surrounding
code at 0x9b094 reads the actual player's status +0x30, masks bit 0x400, compares
it with constant 0x400 at RVA 0x5bb720, and checks +0xae8 before enabling the
button. A successful button result calls player method RVA 0x17a560. No internal
method was called by the diagnostics; no client code or memory was modified.

At 01:49 UTC, the fixed Revive-button message test also failed its intended
effect. The ghost moved from (435,453) to (443,461), remained a ghost with HP
3/213, and did not revive. Chat focus and the cursor remained unchanged. The
scene consumed that click while the HUD did not. No second message click was
sent. The queued input and interrupted observation are recorded in
reports/embedded-revive-20260908T014927760445Z.json; a subsequent stable read in
reports/life-after-background-revive.json confirms the moved ghost state. The saved
death-return checkpoint remains (435,453); the failed probe's movement must not
replace it. Revival now requires a separately verified ordinary foreground UI
path before any background combat test can proceed.

## Ctrl movement and embedding investigation

The background Ctrl-click from (430,380) to (434,380) reached the destination,
but the sampled motion IDs were 120/121 and included intermediate tiles. The
installed `ini/ActionSound.ini` maps those IDs to running; it maps 130/131 to
jumping. The evidence in reports/embedded-jump-20260908T015716137278Z.json
therefore establishes movement, not a jump.

A later ordinary foreground recovery watch also recorded walking/running,
including sequential tiles (430,380) through (430,384), without any 130/131
samples. Its requested 100 Hz produced about 33 samples per second because of
bridge overhead; input locking caused gaps up to 0.672 seconds. The report is
reports/foreground-recovery-motion-20260908T021313618237Z.json. Absence of a
sample alone is not proof that a short animation never occurred, but the
observed sequential movement is direct evidence of walking/running segments.

The current foreground input instrumentation confirms both Ctrl and left Ctrl
are down at mouse-down, and the game child has keyboard focus. Its active
top-level HWND is still the wrapper. This narrows the failure beyond a simple
missing keyboard-focus handoff; it does not establish that the game consumes
the modifier internally. No DirectInput mechanism has been demonstrated.

Read-only inspection found no DirectInput import in the executable or its
graphic.dll, Role3D.dll, and GraphicData.dll dependencies. Packed executable
imports and possible dynamic loading limit this negative result. The ImGui
keyboard message handler at RVA 0x3997e polls VK_CONTROL and other modifiers
through a Win32 import slot and queues modifier state. The shell handler at
0xc0220 calls ImGui and then the current game-state virtual message handler.
Its own WM_ACTIVATE branch at 0xc02f6 updates a field associated with the
configured background frame-rate reduction; a gameplay activation gate has
not been proved.

The installed help specifies Ctrl plus left click. Parasite's character setup
at `LOG/64/Classic_US/3014211881881262240/setup.json` has `option.hotkey: null`
and `option.run: true`; global `ini/setup.json` contains no jump-key override.
No configuration change was made.

The next useful comparison is one ordinary foreground Ctrl-click after
releasing the client to its original top-level window, with a clear empty
destination and the same motion sampling. If that produces a verified jump,
an owned borderless top-level client positioned over the wrapper's game pane
can be evaluated as an alternative hosting mode. It preserves the client's
ability to become the actual foreground window. Forwarding activation
notifications would not address direct foreground-HWND comparisons or a
foreground input device's cooperative-window requirements.

Windows documents that activation belongs to top-level windows and that
activating a child activates its top-level parent in
[Window Features](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features).
Any activation forwarding experiment must follow actual wrapper activation
and deactivation; it must never tell the game it is active while another app
is foreground. No activation messages or hosting changes were sent as part
of this static investigation.

## Other execution environment (earlier investigation)

A separate Windows VM could keep the game and farmer focused inside the guest
while the host chat remains usable. This changes the execution environment and
still requires testing this client, its graphics, and the memory-only farmer.
It is not a verified solution for this installation.

QEMU documents guest-directed keyboard and pointer events through its QMP
[input-send-event command](https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html).
No VirtualBox, VMware, or QEMU installation was found in the checked command
paths or uninstall entries. No VM or software installation has been started.
