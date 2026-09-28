"""On-demand Python thread sampler (diagnostics only: no input, no game reads).

Create the character's .runtime/thread-profile.request (optionally JSON
{"seconds": 20}) and the app samples every thread's stack for that long, then
writes reports/desktop-farming/thread-profile.json: per thread, how often each
innermost frame and each short stack was seen, plus the OS thread id to match
against per-thread CPU time.

Toxic 2026-09-28: the app process ran at one full core with its main thread
at 68% and the combat thread at 13%; a 3 ms inventory read took 86 ms live.
"""

import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from conquest.character_context import state_path

REQUEST = ".runtime/thread-profile.request"
REPORT = "reports/desktop-farming/thread-profile.json"
INTERVAL = 0.005
DEPTH = 8
TOP = 25
_running = threading.Lock()


def _stack(frame, depth=DEPTH):
    stack = []
    while frame is not None and len(stack) < depth:
        code = frame.f_code
        stack.append(f"{Path(code.co_filename).name}:{frame.f_lineno} {code.co_name}")
        frame = frame.f_back
    return stack


def sample(seconds, interval=INTERVAL, *, clock=time.monotonic, sleep=time.sleep):
    """Count innermost frames and short stacks of every other thread."""
    me = threading.get_ident()
    totals, leaves, stacks, native = Counter(), {}, {}, {}
    started = clock()
    while clock() - started < seconds:
        threads = {t.ident: t for t in threading.enumerate()}
        for ident, frame in sys._current_frames().items():
            if ident == me:
                continue
            thread = threads.get(ident)
            name = thread.name if thread is not None else str(ident)
            native[name] = getattr(thread, "native_id", None)
            stack = _stack(frame)
            totals[name] += 1
            leaves.setdefault(name, Counter())[stack[0] if stack else "?"] += 1
            stacks.setdefault(name, Counter())[" < ".join(stack)] += 1
        sleep(interval)
    return {
        "seconds": round(clock() - started, 3),
        "interval": interval,
        "threads": {
            name: {
                "native_id": native.get(name),
                "samples": count,
                "leaves": leaves[name].most_common(TOP),
                "stacks": stacks[name].most_common(TOP),
            }
            for name, count in totals.most_common()
        },
    }


def poll(request=None, report=None):
    """Start one bounded sampling run when requested; never raises."""
    try:
        request = Path(request or state_path(REQUEST))
        output = Path(report or state_path(REPORT))
        if not request.exists() or not _running.acquire(blocking=False):
            return False
    except Exception:
        return False
    try:
        try:
            text = request.read_text(encoding="utf-8").strip()
            seconds = float(json.loads(text).get("seconds", 20)) if text else 20.0
        except (OSError, ValueError, AttributeError):
            seconds = 20.0
        seconds = min(max(seconds, 1.0), 120.0)
        request.unlink(missing_ok=True)

        def run():
            try:
                result = sample(seconds)
                result["finished_at"] = time.time()
                output.parent.mkdir(parents=True, exist_ok=True)
                temporary = output.with_suffix(".tmp")
                temporary.write_text(json.dumps(result, indent=1), encoding="utf-8")
                temporary.replace(output)
            except Exception:
                pass
            finally:
                _running.release()

        threading.Thread(target=run, name="thread-sampler", daemon=True).start()
        return True
    except Exception:
        _running.release()
        return False
