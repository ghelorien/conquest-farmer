"""One ghost-guarded Revive click, with a saved death position and live feedback."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import yaml

from conquest.memory_health import HealthLayout, HealthWorkerSession
from conquest.memory_life import read_life
from conquest.worker import request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true")
    parser.add_argument(
        "--input-mode", choices=("background", "foreground"), default="background"
    )
    args = parser.parse_args()
    state = json.loads(Path("reports/desktop-farming/app-state.json").read_text())
    info = state["worker_info_path"]
    layout = HealthLayout.model_validate(
        yaml.safe_load(Path("profiles/classic-1074-health-candidate.yaml").read_text())
    )
    session = HealthWorkerSession(info, layout.player.expected_sha256)
    before = read_life(session, layout, "Parasite")
    window = request(info, "health")["window"]
    report = {
        "before": asdict(before),
        "window_before": window,
        "revival_verified": False,
        "samples": [],
    }
    if not args.send:
        print(json.dumps(report, indent=2))
        return
    if not before.ghost_candidate:
        raise ValueError("No observed ghost state; no input sent")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = Path(f"reports/embedded-revive-{stamp}.json")
    # Keep run progress separate from reusable route definitions.
    checkpoint = {
        "identity": session.identity,
        "map_id": before.map_id,
        "death_position": list(before.position),
        "phase": "waiting_for_revive",
        "report": str(output),
    }
    checkpoint_path = Path(".runtime/death-return.json")
    if checkpoint_path.exists():
        previous = json.loads(checkpoint_path.read_text())
        if (
            previous["identity"] == session.identity
            and previous["phase"] != "completed"
        ):
            checkpoint["death_position"] = previous["death_position"]
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    try:
        report["input"] = request(
            info,
            "revive-click",
            {
                "health_profile": layout.model_dump(mode="json"),
                "character": "Parasite",
                "expected_size": [1036, 793],
                "expires_at": time.time() + 4,
                "input_mode": args.input_mode,
            },
        )
        consecutive = 0
        for _ in range(20):
            try:
                life = read_life(session, layout, "Parasite")
            except ValueError as error:
                report["samples"].append({"observation_error": str(error)})
                consecutive = 0
                time.sleep(0.25)
                continue
            sample = {"life": asdict(life), "window": request(info, "health")["window"]}
            report["samples"].append(sample)
            recovered = (
                not life.ghost_candidate and life.current_hp >= life.max_hp * 0.4
            )
            consecutive = consecutive + 1 if recovered else 0
            if consecutive >= 3:
                report["revival_verified"] = True
                checkpoint.update(
                    phase="return_pending", revival_position=list(life.position)
                )
                break
            time.sleep(0.25)
    except Exception as error:
        report["error"] = str(error)
        checkpoint["phase"] = "needs_observation"
    finally:
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        checkpoint_path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "report": str(output),
                    "revival_verified": report["revival_verified"],
                    "last": report["samples"][-1] if report["samples"] else None,
                    "error": report.get("error"),
                }
            )
        )


if __name__ == "__main__":
    main()
