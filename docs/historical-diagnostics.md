# Classic Conquer Archer: foreground combat milestone

The user changed priority to foreground farming. **Foreground movement, jumping,
bow attacks, and potion use now work.** Recent Pheasant sessions confirmed 83 and 61 kills; a subsequent Turtledove trial also confirmed combat. The complete farming system and 30-minute acceptance run remain unfinished.

`farm-trial` uses a fingerprint-pinned read-only memory worker for character and
position observations, DXCam for fresh foreground images, and OpenCV for exact
Pheasant-name recognition and health-bar observations. It reuses the already
elevated worker, so individual attacks do not require another Windows prompt.

```powershell
.\.venv\Scripts\python.exe -m conquest farm-trial --worker-info .runtime/worker-live.json --profile profiles/pheasant-foreground-trial.yaml --output reports/trial --seconds 30
```

Run from the repository root with the calibrated game visible on the monitor
selected in the profile (currently the right monitor, DXCam output 2).
**F11 pauses/resumes; F12 stops.** Add `--observe-only` for no input.
Trials last at most 30 minutes; the current profile allows 600 attack attempts.
**Below 40% health, F1 potion use takes priority over combat and movement.**
Consumption and increased health must be observed before continuing. If health
remains below 40%, another potion is attempted after the one-second cooldown.
Focus loss pauses input. Invalid observations, depleted supplies, death, and
departure from the configured boundary stop the trial.

The independent read-only dashboard monitor samples health approximately every
250 ms, including while farming is paused or stopped. It can read a visible,
unfocused client, but rejects covered/minimized windows. Readings older than one
second are displayed as unavailable. Health is measured from bar fill without
OCR; current HP has not yet been validated in memory.

```powershell
.\.venv\Scripts\python.exe -m conquest dashboard --database reports/live-grind/trial.sqlite3 --profile profiles/pheasant-foreground-trial.yaml --worker-info .runtime/worker-live.json --port 8765
```

Open http://127.0.0.1:8765/ for health, supply counts, verified session kills, and level milestones. An independent read-only memory monitor samples level every two seconds, including while farming is stopped; readings expire after five seconds. Level changes are also logged by the farmer. The leveling plan separates due reviews from completed upgrades and qualified monster routes. Bamboo Bow has been purchased and equipped at level 9; automatic future shopping, skill training, and route transitions remain unqualified.
The 40% trigger, healing priority, repeated healing/cooldown, and stale telemetry
are covered by automated tests. Live F1 threshold crossings are verified by both potion consumption and HP increases.

SQLite records observations, attempts, duration, and stop reasons. Saved attack
images support review. Kills are counted from observed memory-counter increments
following attacks; pickups remain unqualified. Inventory, equipped arrow quantities,
and required-potion checks now come from memory, with a live arrow-reload test.
The trial includes bounded patrol, potion healing, map-ID checks, and movement
retries. Stancher pickup logic is implemented but awaits a live verified pickup. Automatic revival/return and full acceptance remain unfinished.

**Calibration correction:** player-object offset `0x3e0` is maximum HP, not
current HP. It changed with level-ups but not with damage. The player profile and
sample output now explicitly call it `max_hp`; current health for foreground
trials comes from the verified visible bar. Earlier HP candidate reports are
historical and must not be used as a death guard.

**Observation policy:** prefer validated read-only memory fields. The temporary
HUD OCR prototype has been removed; the runtime has no OCR dependency. Existing
visual health checks measure the bar's color and fill, and target checks use
calibrated image templates. These remain temporary where memory semantics are
unresolved. A matching number in memory alone does not establish its meaning.
See `docs/memory-observation-status.md` for the latest calibration findings.

Read inventory and equipped ammunition without images or input:

```powershell
.\.venv\Scripts\python.exe -m conquest sample-inventory --worker-info .runtime/worker-live.json --player-profile profiles/classic-1074-player-candidate.yaml --inventory-profile profiles/classic-1074-inventory-candidate.yaml --output reports/inventory.json
```

Exit code 3 means the sample succeeded but the integration remains a candidate:
real restart validation is still required. The worker must be live; its saved
connection file does not establish that. Item ordering and ammunition quantities
were checked across a live reload from 170 to 200 equipped arrows, preserving
the previous stack in inventory. Supply checks reject stale or inconsistent reads.

## Historical background diagnostics

The initial live preflight on September 7, 2026 found the selected client but
Windows denied `OpenProcess(PROCESS_VM_READ)` with **WinError 5: Access is denied**.
A follow-up inspection found that the game was elevated while the diagnostic was
not. Running the same read-only diagnostic through Windows' administrator prompt
successfully opened the read handle for the same client.

**The access blocker is resolved when the diagnostic runs as Administrator.**
Game-state and background-input compatibility remain unqualified. A background
Status-button probe queued one click, but the session was interrupted and the
desktop cursor moved during the measurement. A later screenshot showed the
Status panel closed and the character dead. The cause of death is undetermined;
all further gameplay input was stopped. No candidate offsets have been promoted
to a validated integration profile.

The user subsequently revived the character, and administrator calibration
resumed. A guarded background Status-button probe then produced position changes
while the panel remained closed. HP stayed at 51/51. The game had no remaining
mouse capture and neither physical mouse button was down at the cleanup check.
**Background click qualification failed. Background support is now deferred.**
See `docs/client-qualification.json` for the current feasibility result.

Later live calibration successfully read maximum HP and position through the administrator
worker, confirmed the character as an InternArcher, and equipped the starter bow,
coat, and arrows. A module-relative candidate pointer path was found. See
`docs/live-calibration-2026-09-07.md`; this does not qualify background input.

## Run

Requires Windows 10 version 1709 or later (for `IsWow64Process2`) and Python 3.12+.
Process diagnostics use dataclasses and ctypes. Calibration also uses Pydantic
and PyYAML to validate independently observed values.

From the repository directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\conquest.exe diagnose --output reports\client-preflight.json
.\.venv\Scripts\python.exe -m pytest -q
```

The local `.venv` has already been created and the package installed. You can also
run the command through `.\.venv\Scripts\python.exe -m conquest diagnose`.

For the currently elevated game, run the diagnostic from a PowerShell window
opened with **Run as administrator**. The CLI does not automatically elevate.
The successful administrator report is `reports/client-preflight-admin.json`;
the initial standard-permission report is retained for comparison.

## Calibration and input qualification

Refresh the YAML observations from the actual game display before each scan. The
checked-in town samples document past observations; they must not be treated as
the current character state. Run memory operations from an administrator terminal
while the current elevated client is running:

```powershell
.\.venv\Scripts\conquest.exe calibrate --pid 47420 --observations profiles\town-observations.yaml --output reports\town-candidates.json
.\.venv\Scripts\conquest.exe calibrate --pid 47420 --observations profiles\town-moved-observations.yaml --previous reports\town-candidates.json --output reports\town-refined.json
```

Replace the PID with the current diagnostic result. Scans are bounded to 512 MiB,
20 seconds, and 2,000 candidates per field by default. They inspect committed,
writable, non-executable private/image regions. Reports mark incomplete coverage,
failed reads, and truncated candidate lists explicitly. The scanner handles
unaligned values and patterns crossing chunk boundaries. It records matching
addresses and the provided reference values, not raw process-memory dumps.

Refinement rejects candidate files from a different process session or executable
fingerprint. Even one remaining match is not automatically validated game state.
Changed observations, controlled gameplay, and restart-safe resolution are still
required. The initial scan read 458,445,243 bytes without read failures and found
25 position candidates and five character-name candidates. HP candidates reached
the configured cap. A subsequent position refinement produced zero matches; the
character moved again around the observation/read interval, so that result does
not establish a position layout.

For a narrower search, add `--near player_name_utf8 --radius 2048` with an existing
`--previous` report containing character-name candidates. This scans only eligible
memory within the selected radius of anchors that still match. Overlapping
neighborhoods are merged. The executable fingerprint and process session must
still match; the output remains unqualified. This can reduce unrelated numeric
matches without retaining a raw memory dump.

`probe-input --help` describes the diagnostic click command. It requires an exact
PID, HWND, executable fingerprint, client size, and a freshly calibrated point.
It also requires `--watch` with a session-pinned candidate report containing
untruncated `hp_u32` and `position_u32` candidates. All watched values must match
their reference before the click, and all HP candidates must be positive.
Candidates are read again after the click and changes are recorded; these guards
do not certify that the candidate fields represent game state correctly.
It refuses foreground or minimized targets and queues only one left-button
sequence to that HWND. Button-up is attempted on interruption. It never calls
`SetCursorPos`, `SetForegroundWindow`, or `SendInput`. It samples desktop state
before and after; concurrent physical mouse input invalidates that comparison.
Queue acceptance cannot establish a successful in-game action.

**The current background mouse-message method failed qualification.** Do not use
it for farming or repeat the failed test without a revised approach. A new input
method must independently pass the same validation before farming can proceed.
Do not probe a dead character or continue from an unknown prior action outcome.
Escape pressed in any application interrupts the assistant's Computer Use helper.

Options:

- `--exe ImConquer.exe`: exact, case-insensitive executable basename.
- `--pid NUMBER`: select a specific matching process when several are running.
- `--expected-sha256 HASH`: reject an executable that differs from a previously
  inspected build before checking memory access.
- `--output PATH`: save the report as UTF-8 JSON; its parent directory is created.

Standard output contains the JSON report. Standard error contains structured JSON
log events. Exit code **2** means preflight is blocked, **3** means preflight
passed but gameplay compatibility remains unqualified, and **1** means report
output failed. Argument errors also use argparse's exit code 2. No execution path
currently certifies the client for farming or returns a ready-to-farm status.

## What the diagnostic verifies

- Selects one matching process using a Win32 process snapshot; it never silently
  chooses among multiple clients.
- Lists its top-level windows, client dimensions, visibility, minimized state,
  and whether a window is foreground, without activating it.
- Queries the actual running image path, process creation time, and architecture
  with `PROCESS_QUERY_LIMITED_INFORMATION`.
- Computes the on-disk executable's SHA-256 and checks its PE architecture. This
  is an identity for the file at the queried process path, not a claim that loaded
  process memory equals the on-disk image.
- Attempts to obtain a handle with only `PROCESS_VM_READ`, then closes it.
  Opening that handle successfully would establish access, not validate game state.
- Rechecks process identity to reject a process exit or PID reuse during the check.
- Reports state validation, background input, minimized support, and the 30-minute
  farming test as `not_run`, never as implicitly successful.

The process/memory adapters do not enable debug privileges, request write access,
elevate, inject code, or change client files. `probe-input` posts the diagnostic
mouse messages described above. The optional calibration worker also exposes an
explicit foreground-only SendInput baseline; it does not qualify background input.

## Continuous memory observation

The read-only `observe` command records selected candidates from an existing scan.
It pins the executable fingerprint and process creation identity, checks that
identity before and after every sample, and stops on invalid reads. It does not
require the numeric values to remain equal to their historical scan references:
the purpose is to measure how each candidate changes during controlled gameplay.

```powershell
.\.venv\Scripts\conquest.exe observe --pid 47420 --watch reports\player-neighborhoods.json --database reports\observations.sqlite --seconds 60 --interval 0.25
```

Run it at the same privilege level as the client. `--field NAME` can be repeated
to select other numeric candidate fields. The defaults are `hp_u32` and
`position_u32`. Truncated lists, duplicate addresses, mismatched process sessions,
and non-finite floating-point values are rejected. No old value is substituted
when a read fails. F12 or Ctrl+C stops recording, and SQLite retains completed
samples with the session stop reason and duration. A hard process termination
can leave a session marked `running`; that database label is not proof of liveness.

Output distinguishes successful reads from validated game semantics. A group of
sequential reads is not an atomic game snapshot. Changed values do not establish
which candidate means current position versus destination; constant values do not
establish that minimized updates have stopped. Those conclusions still need
controlled reference observations. These recordings cannot enable input or farming.

The `observation_sessions` and `observation_samples` SQLite tables hold session
metadata and timestamped JSON samples. The command returns a JSON summary with
per-address change counts; exit code 3 means recording ended but candidates remain
unqualified, and exit code 2 indicates failed access, invalid data, or invalid
arguments. These tables are diagnostic data, not kill or pickup statistics.

## Optional calibration worker

`worker --help` describes a short-lived worker tied to one PID, HWND, and exact
fingerprint. It supports bounded numeric samples, candidate scans, nearby module
pointer inspection, and an explicit foreground-click baseline guarded by character
name and positive HP candidates. The foreground diagnostic activates the game and
moves the desktop cursor; it must never be used as the background farming backend.
The worker has not yet been qualified against the live client.

It binds only to localhost and requires a random authentication token held in the
ignored `.runtime` connection file. It never exposes a shell, Python evaluation,
memory writes, or injection. Do not publish the connection file. The worker exits
after its configured lifetime, a shutdown request, or F12 between requests. A
scan can occupy the worker for up to its 20-second scan limit. A stale connection
file alone is not evidence that a worker is running. Starting the worker does not
elevate it automatically; normal Windows administrator approval is still required
when the game is elevated.

Protocol version 2 also supports `foreground-key` and `background-key` for one
F1–F11 press/release pair. Both require the same live character guard. F12 cannot
be sent through these diagnostics. The background key path refuses a foreground
or minimized client and records focus/cursor snapshots before and after. A queued
key is not proof of an in-game effect. Worker code is loaded at startup: an older
live worker does not gain new operations merely because files changed.

`sample-player` uses a version-2 worker to resolve the candidate player pointer
from the current module base and read name, HP, and coordinates:

```powershell
.\.venv\Scripts\conquest.exe sample-player --worker-info .runtime\worker-v2.json --profile profiles\classic-1074-player-candidate.yaml --output reports\resolved-player-candidate.json
```

It rechecks the pointer path around each sample and still returns `qualified:
false`. This candidate profile is specific to the inspected executable. Real
restart testing and additional semantic validation remain necessary.

## Observed client

The saved local reports are `reports/client-preflight.json` and
`reports/client-preflight-admin.json` (ignored by Git because they contain
machine-specific paths, process IDs, and window titles).

| Observation | Result |
| --- | --- |
| Process | `ImConquer.exe`, PID 47420 at inspection time |
| Window | `[Parasite - ClassicConquer]` |
| Image path confirmed from process | `C:\Program Files\Classic Conquer 2.0\bin\64\ImConquer.exe` |
| Architecture | x64 |
| SHA-256 | `c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396` |
| Window state | Visible, unfocused, not minimized |
| Query access | Available |
| Read-only memory access, standard permissions | Denied, WinError 5 |
| Read-only memory access, Administrator | Passed: read handle opened and closed |
| Feasibility gate, Administrator | Unqualified: game-state and input validation pending |

Token inspection confirmed medium integrity and an unelevated token for the
original diagnostic, versus high integrity and an elevated token for the game.
The subsequent administrator preflight passed at 14:12 UTC on September 7, 2026
with the same PID, process creation time, path, and executable SHA-256.
Background-input compatibility and minimized operation are still unknown.
Passing the access preflight does not automatically enable gameplay actions.

## Work deferred by the gate

With ordinary administrator read access available, the next milestone is to validate actual
HP, position, map, entities, and inventory against controlled observations and
test calibrated background movement, attack, potion, and pickup actions. A
profile must be tied to the verified executable fingerprint and must not contain
guessed offsets, key bindings, or coordinate transformations.

The guarded input test is recorded in `reports/guarded-status-probe.json`.
Although the messages were accepted, the Status panel did not open and the
character moved. The foreground window remained the same at the two sample
times, but cursor movement prevented confirmation of desktop-input isolation.
The memory-only/background-input combination has therefore not passed the
feasibility gate. No alternative input method was silently substituted.

The candidate recorder supports further calibration without enabling autonomous
actions. After those checks pass, remaining work includes the validated game-state reader,
DXCam/OpenCV observations, farming input backend, validated gameplay profiles,
route recording, Archer state machine, SQLite statistics, pause/stop hotkeys, and
the observation/farming commands. Pydantic and YAML support are installed;
NumPy, OpenCV, DXCam, and pywin32 are not yet installed for the farming runtime.

The researched starting target is documented in `docs/route-research.md`.

The remaining acceptance test is a supervised 30-minute single-area farming run.
Minimized compatibility must be qualified separately. Neither acceptance test has
been performed.

## Validation and API references

The pytest suite covers denial and success of the access preflight, malformed and
changed executables, fingerprint rejection, process selection and restart,
missing windows, machine-readable CLI output, and handle cleanup. Windows
integration tests inspect only the test runner process, not the game.

The Win32 adapter follows the documented
[OpenProcess access contract](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess),
[process image query](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-queryfullprocessimagenamew),
and [architecture query](https://learn.microsoft.com/en-us/windows/win32/api/wow64apiset/nf-wow64apiset-iswow64process2).
