# Native client wrapper

The wrapper launches the installed official client, discovers its window, embeds
that window in its game pane, and runs the memory observation framework beside
it. The game executable and rendering code are not modified.

## Open and use

From the repository root:

```powershell
.\.venv\Scripts\pythonw.exe scripts/start_desktop_app.py
```

- **Launch client** starts the official bootstrapper. An unelevated app requests
  Windows approval and then continues the launch in the approved app. The button
  becomes **Cancel launch** while waiting; canceling stops discovery and does not
  terminate the launcher or client.
- A single new client window is embedded as soon as it appears, including its
  login window. Login remains manual. The character name is not a prerequisite
  for hosting; observations wait until the configured character is available.
- For an already running client, use **Refresh**, select its window in the
  dropdown, and choose **Embed client**. If approval is required, the wrapper
  reopens with the selected PID, creation time, and HWND. It revalidates that
  identity before embedding and refuses to substitute a restarted/different
  client. Multiple candidates are presented for selection rather than
  choosing arbitrarily.
- **Release client** stops its observers and restores the original window
  styles, owner, and placement. The client keeps running. It can be embedded again.
- Closing the client stops observation and switches farming Off, while leaving
  the wrapper available for another client. Closing the wrapper first releases
  its client. Restoration failures keep the wrapper open for retry.
- **Reload app** stops embedded observations, releases the client and starts a
  replacement app with the existing process privilege level. The replacement
  re-embeds the same PID, creation time and HWND. Foreground farming must be
  stopped first; failed restoration prevents the restart.
- Click inside the embedded game to give it keyboard focus. **Show / focus
  Conquer** also hands focus to the client. Sidebar text fields keep their own
  focus when clicked, and the click handoff does not activate a background app.
- **Nearby monster groups** has one row per species, with its nearby-ID count.
  Click a group (or use Space/Enter) to include every observed ID of that species.
  Each observation resolves new spawns automatically. Selected groups remain
  visible with zero nearby IDs after leaving the area, and survive app reloads.
  **Matched nearby IDs** shows the resolved IDs automatically. **Farming On**
  and **Off** control the request; the status separately reports blocked input.
  F11/F12 currently belong to the older foreground farmer, not these controls.

## Lifecycle and identity checks

Discovery uses the configured image path, visible window geometry, PID, and
process creation time. The existing process identities are captured before a
launch. A reused PID with a different creation time is a new process. A successful
bootstrapper exit does not mean the client failed: discovery continues for its
child launcher/client. Nonzero launcher exits and a three-minute timeout are
reported. A second launch is not issued while one is pending.

Embedding checks equal DPI awareness contexts before changing window hierarchy.
The wrapper remembers the original styles/placement and rolls back partial
failure. A closed/reused HWND is forgotten without applying the old state to
another window; access errors retain restoration state. Resizing is tied to the
game pane, and the selected client cannot change while embedded.

## Verified here

309 unit/regression tests pass. These cover launch transitions, successful
bootstrapper handoff, ambiguous windows, PID reuse, timeout/cancellation, closed
or reused HWNDs, access failures, DPI rejection, restoration rollback, keyboard
focus transfer, preserved native edit focus, and thread-input cleanup on failure.
They also cover exact-ID picker updates and the embedded diagnostic connection.

The empty Pheasant list was traced to a misclassified field: actor offset +0x80
contains the species ID (Pheasant 1, Turtledove 2), rather than a generic monster
flag. The reader now accepts the installed monster catalog's type IDs, excluding
town Guard 900 and Patrol 910. Player/NPC type 0 remains excluded. A live read
returned 13 distinct Pheasant IDs and coordinates. Species classification still
does not establish life state or authorize attacks.

The approved wrapper owns an authenticated loopback diagnostic bridge, reusing
its existing memory session. Its connection path is recorded in app-state.json;
tokens are not logged or exported. It closes before the memory session is
released, expires after four hours, and serializes reads and probes. Only bounded
memory reads, validated control updates, a queued app reload and one background-click diagnostic are exposed. Input diagnostics
require farming Off and a request expiring within five seconds. Foreground,
minimized and changed-window checks use the wrapper root for an embedded client.
Reload accepts no command, script or path arguments, requires farming Off, and
queues the existing UI reload method so the HTTP response can finish before
the connection closes. The pointer-positioning diagnostic reports explicitly
that it borrows the desktop cursor, restores it only if the user has not moved
it, and aborts on mouse activity, focus/geometry changes or F12. It is not yet
a qualified attack method.

The first live picker version displayed 12 Pheasants; selecting ID 401247 visibly
checked its row and updated the selected-ID field. The subsequent group picker
is implemented and tested but still needs to be loaded into the real app.

Monster attribute table +0x978, index 1 decoded to 33/33 for ten Pheasants in an
18-second passive watch. No HP transitions occurred, so monster death semantics
remain unqualified. The user additionally requires loot pickup tied to the
specific killed monster ID. Kill attribution, drop attribution and verified
pickup remain pending; proximity alone is not evidence of loot ownership.

The actual `DesktopApp` was also exercised with a separate animated Tk client:

```powershell
.\.venv\Scripts\python.exe scripts/verify_wrapper_app.py
```

The test uses an isolated catalog, report directory, and observer; it does not
select, read, launch, or send input to Conquer. It verifies launch and automatic
embedding before login, resize, exact original-window restoration, observation
shutdown, keyboard focus handoff, reconnect, and client exit while the wrapper remains open. Its report
is `reports/wrapper-app-verification.json`. A separate lower-level hosting test
is available as `scripts/verify_window_host.py`.

The real Conquer client (PID 38336, HWND 12977830) was embedded and rendered its
login screen. Keyboard input initially failed: GetGUIThreadInfo showed focus
remaining on a wrapper control. The updated app explicitly uses SetFocus with
temporary thread-input attachment, verifies the resulting client focus, and
preserves an already focused native child edit control. It checks actual mouse
presses in the foreground game's window hierarchy; it does not relay keystrokes.
See Microsoft's [SetFocus documentation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setfocus).

The fix was loaded into the real approved wrapper (PID 124028), and the same
client was re-embedded. The user confirmed **“Typing works now”** after using
**Show / focus Conquer** and selecting the game's text field. No credentials
were entered by the test tooling. The separate interactive fixture typing
preview was interrupted; only its automated focus handoff was verified there.

## Still pending

Conquer rendering and manual foreground typing are verified in the wrapper.
Input while another application has focus still requires live validation.
Exact-ID controls persist, but their production attack dispatcher remains
blocked by the unresolved HP/life-state and embedded-input checks. The temporary
foreground farmer must be stopped and its client released before embedding.
