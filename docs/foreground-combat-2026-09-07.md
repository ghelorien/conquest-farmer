# Foreground combat qualification, September 7, 2026

The user explicitly deprioritized background support in favor of immediate
foreground gameplay. The same selected x64 ImConquer.exe client remained running
throughout: PID 47420, creation time 134332610042002510, SHA-256
`c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396`.

The existing administrator worker (PID 28836, protocol 2, input revision 4) was
reused for every action. No additional elevation prompt was needed in this turn.
No persistent elevation mechanism or Windows security setting was changed.

## Live evidence

- Ctrl + left click jumped off the city wall and across grass. Ordinary left
  click on a Pheasant started the bow attack and continued until the target died.
- The reference location (388,444) was near a FlyingRooster. Actual Pheasants
  were found south/east, around (405,451) through (431,465).
- Three manually selected Pheasant kills raised the visible counter 0 → 3.
- Automatic trials subsequently raised the counter 3 → 4 → 7 → 13 → 18.
  Arrows fell from 200 before combat to 170 at the end; experience and several
  level-up messages supported the counter evidence. Not every click was a hit.
- HP changed with damage while object offset 0x3e0 stayed fixed, then that field
  increased on level-up. This establishes **maximum HP**, not current HP. The
  candidate profile and sample output were renamed accordingly. Current-HP
  memory decoding remains unresolved.
- One remaining Stancher was right-clicked in inventory, increasing visible HP
  from 27/99 to 97/99. It disappeared from inventory. No potions remain known.
- Fresh DXCam foreground images matched the game display. Health-bar fill
  measurements matched 97/99, 93/117 and subsequent damage. Name recognition
  matched both white and green Pheasant labels and required a live red bar.
- A transient DXGI poll returned no new frame after an attack. The first trial
  stopped. Capture now retries for at most 100 ms for a new frame; no previous
  frame is reused for action decisions.
- The last 45-second trial reached 18 total kills. After it ended, nearby
  monsters continued causing damage. Two supervised jumps retreated to
  (413,445), where visible HP was stable at 24/117. This demonstrates why a
  stopped input loop alone is insufficient protection while surrounded.

## Current implementation boundary

`farm-trial` is a bounded foreground combat prototype (1–60 seconds, at most 30
configured attempts, default 15). It is not v1 acceptance. F11 pauses/resumes;
F12 and Ctrl+C stop. The worker also supports F12. Reads resolve the candidate
module-relative player pointer each iteration and pin the process fingerprint.
The desktop capture rejects focus loss, minimization, covered windows, and
uncalibrated geometry. Trials stop below the configured health threshold.

SQLite and structured JSON logs record observations, attempts, timing, and stop
reasons. Kill/pickup counts remain null in automatic logs because they are not
yet read by the runtime; the totals above were independently inspected from the
game's own counter. The saved images record selected targets before dispatch.

Still required: patrol and route recording, automatic recovery/retreat, healing
and pickup verification, ammo/inventory/supply checks, map-ID observation,
progress verification between attacks, real client restart qualification, and
the supervised 30-minute acceptance run. Background and minimized operation
remain deferred. Test suite: 114 passed.

The live worker is short-lived (maximum one hour), not a permanent service.
Launching another elevated session can still require Windows approval. Reuse
the authenticated connection file without displaying its token.
