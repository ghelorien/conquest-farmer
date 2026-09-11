# Native app status — 2026-09-07

Latest wrapper work: see [client wrapper](client-wrapper.md). The current source
adds selection of existing clients, automatic embedding of new login windows,
launch cancellation/failure handling, and cleanup/reconnect after client exit.
309 tests and the native-app fixture lifecycle/focus checks pass. Embed supports
an approved restart pinned to the selected PID, creation time, and HWND. The
real Conquer client PID 38336 was embedded in updated wrapper PID 124028.
The user confirmed typing works after the keyboard focus handoff fix was loaded.
Rendering and manual typing are verified; embedded background attacks remain
unverified. The entries below retain the earlier diagnostic history.

Run `pythonw scripts/start_desktop_app.py` from this checkout. The local launcher
`outputs/Start-ConquestFarmer.ps1` opens without elevation by default; its
explicit `-Elevated` option requests Windows consent. An already running app
is reused rather than launching another copy.

The native Start button now also requests elevation once when necessary and
passes `--start` (or `--calibrate`) to the elevated app. Approval continues the
requested action; cancellation leaves the original app stopped without retry.
An unelevated `--start` invocation never requests elevation again, preventing
a consent loop. This does not bypass Windows consent.

The actual Tk desktop app opened and discovered Parasite (PID 3304). Clicking
Start minimized the bot and requested Conquer foreground focus. The live run
stopped before capture/input because OpenProcess(QUERY_INFORMATION | VM_READ)
returned WinError 5. The preceding elevated launch waited at consent and Windows
reported that it was canceled. No automatic elevation retry was made. Zero
attacks or kills were recorded in this session's native-app trial.

The app includes Start/Stop, F11/F12 support through the existing foreground
loop, counters, calibration, official launcher, Embed/Release, and persisted
exact monster ID controls. Foreground observation waits on focus loss without
turning the run off. A run is bounded to 30 minutes and stops on missing
supplies, low health, or uncertain observations.

The foreground adapter normalizes physical frames to a calibrated logical
client size and scales input back while preserving physical origin guards.
The local profile is specific to the currently observed 125% display scaling,
1536×793 logical client, and Parasite's route from town to Pheasants. Potion
and reload hotkeys are disabled because this client's keys are unbound. This
profile's live calibration and actual farming remain unverified due to access
denial. The status panel was still open in the last inspected game frame; it
must be closed and the play area checked before any route clicks are enabled.

The wrapper uses standard SetParent with matching DPI contexts, saved original
styles/owner/placement, PID creation identity checks, and rollback. It reads
memory directly in the owning app and runs the existing control framework
alongside the embedded client. It refuses foreground farming while embedded.
Memory ID attack dispatch remains blocked pending health/life and embedded
input verification. Embedding/rendering/input have not been tested against the
live elevated client. Launching a fresh official client and subsequent login
have not been tested. Do not describe background farming as complete.

Validation: 265 tests pass, including new input scale/origin preservation,
bottom/center-relative HP strip checks, embedding rollback, retained ownership
after failed restoration, and DPI mismatch rejection. Existing healing/revival
loop fakes were updated for the explicit frame-size argument.

Follow-up validation: 270 tests now pass. A real separate-process Tk test window
was embedded, resized, and restored with identical styles, owner, and placement
(`reports/window-host-verification.json`). This verifies Win32 hosting and
restoration, not Conquer rendering or background input.

A focus-loss race immediately before input now raises a recoverable observation
exception, including through the worker protocol. The foreground loop pauses
and obtains fresh memory/pixels before resuming. A regression test verifies a
different target point is used after resuming, and verifies that geometry errors
still stop the run. The most recent game screenshot shows the Status panel
closed, Parasite at (446,398), HP 213/213, and 119 arrows. No native farming kills
have yet been observed. The user explicitly requested another access request;
an elevated `--start` launch was issued and Windows consent was confirmed open.

The explicitly requested administrator launch subsequently succeeded (native
app PID 96816). Start brought Conquer HWND 263256 forward but failed its geometry
check: SW_RESTORE had changed the game from maximized 1536×793 logical to its
default 1024×540 client (1280×675 physical). The corrected source preserves
maximized state and maximizes a mismatched foreground client before validating
the profile. A new Reload app button launches a child with the existing token,
so future approved-app reloads need no fresh runas operation.

The corrected build's elevated launch was canceled according to Windows.
Consequently PID 96816 still has the earlier code loaded and remains stopped;
the fixed source is not yet running elevated. No attacks or kills occurred.
A non-elevated SetWindowPos diagnostic was also rejected with access denied,
so the current game geometry cannot be repaired from this tool process.

The farming sidebar now shows nearby monster groups and pickup history together
in stacked panels. Pickup history fills spare height, newest first; client tools
and raw matched IDs are expandable. The embedded sidebar remains fixed at 500
pixels so updates do not resize the game. A transparent isolated UI geometry
check at 1500x800 confirmed both lists fit without screenshots or game inspection.

Mouse priority: the app samples physical cursor position and button state every
25 ms and rechecks before injected input. External motion or held buttons pause
automation for two seconds after the last activity, without changing Farming
On/Off intent. Its own input is tracked under a shared lock so it does not pause
itself. Focus recovery yields too; cleanup releases only buttons/keys the bot
pressed. This uses desktop APIs and no game hooks. An already-running game
auto-attack may continue while the bot sends no new input.
