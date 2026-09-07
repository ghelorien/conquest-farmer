"""CLI for reproducible diagnostics; stdout is JSON, stderr is JSON logging."""

import argparse
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from conquest.diagnostics import Report, diagnose, finish
from conquest.win32 import WindowsBackend


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        })


def sha256(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise argparse.ArgumentTypeError("Expected a 64-character SHA-256 hexadecimal digest")
    return value.lower()


def process_id(value: str) -> int:
    result = int(value)
    if not 0 < result <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("PID must be between 1 and 4294967295")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Classic Conquer feasibility diagnostics")
    commands = parser.add_subparsers(dest="command", required=True)
    entities = commands.add_parser("sample-entities", help="Read candidate monster IDs and positions from memory; sends no input")
    entities.add_argument("--worker-info", required=True, type=Path)
    entities.add_argument("--profile", required=True, type=Path)
    entities.add_argument("--output", type=Path)
    route = commands.add_parser("record-route", help="Record map-coordinate travel from memory; sends no input; F12 ends recording")
    route.add_argument("--worker-info",required=True,type=Path)
    route.add_argument("--profile",required=True,type=Path,help="Fingerprint-specific player memory profile")
    route.add_argument("--character",required=True)
    route.add_argument("--output",required=True,type=Path)
    route.add_argument("--seconds",type=float,default=60)
    route.add_argument("--spacing",type=float,default=3)
    dashboard = commands.add_parser("dashboard", help="Show a small read-only localhost farming dashboard")
    dashboard.add_argument("--database", required=True, type=Path)
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--profile", type=Path, help="Farming profile for independent live health tracking")
    dashboard.add_argument("--worker-info", type=Path)
    inventory = commands.add_parser("sample-inventory", help="Read candidate inventory and equipped ammo from memory; sends no input")
    inventory.add_argument("--worker-info", required=True, type=Path)
    inventory.add_argument("--player-profile", required=True, type=Path)
    inventory.add_argument("--inventory-profile", required=True, type=Path)
    inventory.add_argument("--output", type=Path)
    trial = commands.add_parser("farm-trial", help="Bounded supervised foreground farming; F11 pauses and F12 stops")
    trial.add_argument("--worker-info", required=True, type=Path)
    trial.add_argument("--profile", required=True, type=Path)
    trial.add_argument("--output", required=True, type=Path)
    trial.add_argument("--seconds", type=float, default=10)
    trial.add_argument("--observe-only", action="store_true")
    diagnostic = commands.add_parser("diagnose", help="Inspect process identity and ordinary read-only access; sends no input")
    diagnostic.add_argument("--exe", default="ImConquer.exe", help="Exact executable basename")
    diagnostic.add_argument("--pid", type=process_id, help="Select one matching process")
    diagnostic.add_argument("--expected-sha256", type=sha256, help="Reject a changed or unsupported executable")
    diagnostic.add_argument("--output", type=Path, help="Also save the JSON report to this file")
    calibration = commands.add_parser("calibrate", help="Discover or refine read-only value candidates; sends no input")
    calibration.add_argument("--pid", required=True, type=process_id)
    calibration.add_argument("--observations", required=True, type=Path, help="YAML containing independently observed values and executable SHA-256")
    calibration.add_argument("--previous", type=Path, help="Refine candidates from this JSON file in the same process session")
    calibration.add_argument("--output", required=True, type=Path)
    calibration.add_argument("--max-mib", type=int, default=512)
    calibration.add_argument("--max-seconds", type=float, default=20)
    calibration.add_argument("--near", help="Search near this candidate field in --previous, rather than refining that file")
    calibration.add_argument("--radius", type=int, default=2048, help="Bytes around each still-matching --near anchor")
    probe = commands.add_parser("probe-input", help="Send one calibrated left click to the unfocused game window; verify its effect separately")
    probe.add_argument("--pid", required=True, type=process_id)
    probe.add_argument("--hwnd", required=True, type=process_id)
    probe.add_argument("--expected-sha256", required=True, type=sha256)
    probe.add_argument("--point", required=True, nargs=2, type=int, metavar=("X", "Y"))
    probe.add_argument("--expected-size", required=True, nargs=2, type=int, metavar=("WIDTH", "HEIGHT"))
    probe.add_argument("--watch", required=True, type=Path, help="Session-pinned candidate report containing hp_u32 and position_u32 for before/after checks")
    probe.add_argument("--output", required=True, type=Path)
    worker = commands.add_parser("worker", help="Run a short-lived localhost calibration worker for one client")
    worker.add_argument("--pid", required=True, type=process_id)
    worker.add_argument("--hwnd", required=True, type=process_id)
    worker.add_argument("--expected-sha256", required=True, type=sha256)
    worker.add_argument("--info", required=True, type=Path)
    worker.add_argument("--lifetime", type=int, default=1800)
    observe = commands.add_parser("observe", help="Record candidate values to SQLite without sending game input")
    observe.add_argument("--pid", required=True, type=process_id)
    observe.add_argument("--watch", required=True, type=Path, help="Session-pinned candidate report")
    observe.add_argument("--field", action="append", dest="fields", help="Numeric candidate field to record; repeat as needed")
    observe.add_argument("--database", required=True, type=Path)
    observe.add_argument("--seconds", type=float, default=60)
    observe.add_argument("--interval", type=float, default=0.25)
    player = commands.add_parser("sample-player", help="Resolve candidate player fields from a module pointer through the diagnostic worker")
    player.add_argument("--worker-info", required=True, type=Path)
    player.add_argument("--profile", required=True, type=Path)
    player.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    logger = logging.getLogger("conquest")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    try:
        if args.command == "sample-entities":
            import yaml
            from conquest.addressing import WorkerPointerSession
            from conquest.memory_entities import EntityLayout, MemoryEntityReader
            try:
                layout = EntityLayout.model_validate(yaml.safe_load(args.profile.read_text()))
                reader = MemoryEntityReader(WorkerPointerSession(args.worker_info, layout.expected_sha256), layout)
                encoded = json.dumps(reader.report(), indent=2)
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(encoded, encoding="utf-8")
                print(encoded)
                return 3
            except (ValueError, OSError, RuntimeError) as error:
                logger.error("entity_observation_failed", extra={"fields": {"detail": str(error)}})
                return 2
        if args.command == "record-route":
            from conquest.route_recording import run_recording
            try:
                print(json.dumps(run_recording(args.profile,args.worker_info,args.output,
                                                args.character,args.seconds,args.spacing)))
                return 0
            except (ValueError,OSError,RuntimeError) as error:
                logger.error("route_recording_failed",extra={"fields":{"detail":str(error)}})
                return 2
        if args.command == "dashboard":
            from conquest.dashboard import serve_dashboard
            if bool(args.profile) != bool(args.worker_info):
                parser.error("Dashboard live health requires both --profile and --worker-info")
            monitor = None
            if args.profile:
                from conquest.health_monitor import from_profile
                monitor = from_profile(args.profile, args.worker_info)
            serve_dashboard(args.database, args.port, monitor=monitor)
            return 0
        if args.command == "sample-inventory":
            import yaml
            from conquest.addressing import PlayerLayout, WorkerPointerSession
            from conquest.memory_inventory import InventoryLayout, MemoryInventoryReader
            player_layout = PlayerLayout.model_validate(yaml.safe_load(args.player_profile.read_text()))
            inventory_layout = InventoryLayout.model_validate(yaml.safe_load(args.inventory_profile.read_text()))
            session = WorkerPointerSession(args.worker_info, player_layout.expected_sha256)
            report = MemoryInventoryReader(session, player_layout, inventory_layout).report()
            encoded = json.dumps(report, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(encoded, encoding="utf-8")
            print(encoded)
            return 3
        if args.command == "farm-trial":
            from conquest.trial import run_trial
            print(json.dumps(run_trial(args.profile, args.worker_info, args.output,
                                       args.seconds, logger, args.observe_only)))
            return 0
        if args.command == "sample-player":
            return player_command(args, logger)
        if args.command == "observe":
            return observe_command(args, logger)
        if args.command == "worker":
            from conquest.worker import serve
            try:
                serve(args.pid, args.hwnd, args.expected_sha256, args.info, args.lifetime)
                return 0
            except (OSError, ValueError) as error:
                logger.error("worker_failed", extra={"fields": {"detail": str(error)}})
                return 2
        if args.command == "calibrate":
            return calibrate_command(args, logger)
        if args.command == "probe-input":
            return probe_command(args, logger)
        logger.info("diagnostic_started", extra={"fields": {"exe": args.exe, "pid": args.pid}})
        try:
            report = diagnose(WindowsBackend(), args.exe, args.pid, args.expected_sha256)
        except OSError as error:
            report = Report()
            report.add("platform", "failed", str(error), error)
            finish(report)
        payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=True)
        print(payload)
        if args.output:
            try:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload + "\n", encoding="utf-8")
            except OSError as error:
                logger.error("report_write_failed", extra={"fields": {"path": str(args.output), "detail": str(error)}})
                return 1
        logger.info("diagnostic_finished", extra={"fields": {"gate": report.gate, "autonomous_actions_enabled": False}})
        return 2 if report.gate == "blocked" else 3
    finally:
        logger.removeHandler(handler)
        handler.close()


def player_command(args, logger):
    import yaml
    from conquest.addressing import PlayerLayout, sample_player
    try:
        if args.profile.stat().st_size > 65536:
            raise ValueError("Player candidate profile exceeds 64 KiB")
        layout = PlayerLayout.model_validate(yaml.safe_load(args.profile.read_text(encoding="utf-8")))
        report = sample_player(args.worker_info, layout)
        status = 3
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        report = {"schema_version": 1, "stage": "player_sample_failed", "qualified": False,
                  "autonomous_actions_enabled": False, "error": str(error)}
        status = 2
    payload = json.dumps(report, indent=2, allow_nan=False)
    print(payload)
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    except OSError as error:
        logger.error("report_write_failed", extra={"fields": {"detail": str(error)}})
        return 1
    logger.info("player_sample_finished", extra={"fields": {"stage": report["stage"], "qualified": False}})
    return status


def observe_command(args, logger):
    import ctypes as c
    import math
    import sqlite3
    from conquest.memory import MemorySession
    from conquest.observation import CandidateReader, load_candidates
    from conquest.recording import Recorder, record
    from conquest.win32 import bind

    logger.info("observation_started", extra={"fields": {"pid": args.pid, "input_enabled": False}})
    try:
        # Reject bad bounds before opening a process or creating an output database.
        if not math.isfinite(args.seconds) or not 0 < args.seconds <= 3600:
            raise ValueError("--seconds must be greater than 0 and at most 3600")
        if not math.isfinite(args.interval) or not 0.05 <= args.interval <= 10:
            raise ValueError("--interval must be between 0.05 and 10 seconds")
        candidates = load_candidates(args.watch)
        expected = candidates.get("expected_sha256", "")
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("Candidate report needs a valid executable fingerprint")
        with MemorySession(args.pid, expected) as session:
            reader = CandidateReader(session, candidates, args.fields or ("hp_u32", "position_u32"))
            key_state = bind(session.backend.user, "GetAsyncKeyState", [c.c_int], c.c_short)
            args.database.parent.mkdir(parents=True, exist_ok=True)
            recorder = Recorder(args.database)
            try:
                report = record(reader, recorder, {"process_identity": session.identity,
                                                  "expected_sha256": expected, "qualified": False},
                                seconds=args.seconds, interval=args.interval,
                                should_stop=lambda: bool(key_state(0x7B) & 0x8000))
            finally:
                recorder.close()
        status = 2 if report["stop_reason"] == "invalid_observation" else 3
    except (OSError, ValueError, TypeError, sqlite3.Error) as error:
        report = {"schema_version": 1, "stage": "observation_failed", "qualified": False,
                  "autonomous_actions_enabled": False, "error": str(error)}
        status = 2
    print(json.dumps(report, indent=2, allow_nan=False))
    logger.info("observation_finished", extra={"fields": {"stage": report["stage"], "qualified": False}})
    return status


def calibrate_command(args, logger):
    from conquest.calibration import load_observations, refine, scan, scan_near
    from conquest.memory import MemorySession
    import yaml

    logger.info("calibration_started", extra={"fields": {"pid": args.pid}})
    try:
        observations = load_observations(args.observations)
        previous = None
        if args.previous:
            if args.previous.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("Candidate file exceeds 16 MiB")
            previous = json.loads(args.previous.read_text(encoding="utf-8"))
            if not isinstance(previous, dict) or previous.get("schema_version") != 1:
                raise ValueError("Unsupported candidate file format")
        if args.near and previous is None:
            raise ValueError("--near requires an existing --previous candidate file")
        with MemorySession(args.pid, observations.expected_sha256) as session:
            if args.near:
                report = scan_near(session, observations, previous, args.near, args.radius,
                                   max_bytes=args.max_mib * 1024 * 1024, max_seconds=args.max_seconds)
            elif previous is not None:
                report = refine(session, observations, previous)
            else:
                report = scan(session, observations, max_bytes=args.max_mib * 1024 * 1024,
                              max_seconds=args.max_seconds)
        status = 3  # Candidate discovery never qualifies autonomous farming.
    except (OSError, ValueError, yaml.YAMLError, KeyError, TypeError) as error:
        report = {"schema_version": 1, "stage": "calibration_failed", "qualified": False,
                  "error": str(error), "winerror": getattr(error, "winerror", None)}
        status = 2
    payload = json.dumps(report, indent=2, ensure_ascii=True)
    print(payload)
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    except OSError as error:
        logger.error("report_write_failed", extra={"fields": {"detail": str(error)}})
        return 1
    logger.info("calibration_finished", extra={"fields": {"stage": report["stage"], "qualified": False}})
    return status


def probe_command(args, logger):
    from conquest.input_probe import MessageTarget, click_probe
    from conquest.memory import MemorySession
    from conquest.probe_watch import read_probe_watch, require_observed_values, watch_changes

    logger.info("input_probe_started", extra={"fields": {"pid": args.pid, "hwnd": args.hwnd}})
    try:
        if args.watch.stat().st_size > 1024 * 1024:
            raise ValueError("Watch report exceeds 1 MiB")
        candidates = json.loads(args.watch.read_text(encoding="utf-8"))
        if not isinstance(candidates, dict):
            raise ValueError("Watch report must be a JSON object")
        with MemorySession(args.pid, args.expected_sha256) as session:
            before_game = read_probe_watch(session, candidates)
            require_observed_values(before_game, candidates)
            report = click_probe(MessageTarget(args.pid, args.hwnd), *args.point, args.expected_size)
            after_game = read_probe_watch(session, candidates)
            report["game_before"] = before_game
            report["game_after"] = after_game
            report["changed_candidate_fields"] = watch_changes(before_game, after_game)
            if report["changed_candidate_fields"]:
                report["outcome"] = "candidate_game_state_changed_inspect_before_any_further_input"
            session.assert_identity()
        status = 3
    except (OSError, ValueError) as error:
        report = {"schema_version": 1, "stage": "input_probe_failed", "qualified": False,
                  "error": str(error), "note": "If any messages were queued, inspect the game before retrying."}
        status = 2
    payload = json.dumps(report, indent=2)
    print(payload)
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    except OSError as error:
        logger.error("report_write_failed", extra={"fields": {"detail": str(error)}})
        return 1
    logger.info("input_probe_finished", extra={"fields": {"stage": report["stage"], "qualified": False}})
    return status
