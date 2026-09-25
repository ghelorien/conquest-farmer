"""Read-only health telemetry independent of the farming/action loop."""

import threading
import time

from conquest.capture import DesktopFrames
from conquest.vision import health_ratio


class HealthMonitor:
    def __init__(self, camera_factory, *, clock=time.monotonic, interval=0.25):
        self.camera_factory, self.clock, self.interval = camera_factory, clock, interval
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.value, self.timestamp, self.error = (
            None,
            None,
            "Waiting for a fresh health reading",
        )

    def sample(self, camera):
        try:
            frame = camera.read()
            ratio = health_ratio(frame.image)
            if not 0 <= self.clock() - frame.timestamp <= 1:
                raise ValueError("Health frame is stale")
            with self.lock:
                self.value, self.timestamp, self.error = ratio, frame.timestamp, None
        except Exception as error:
            # Telemetry faults must never preserve a misleading healthy value.
            with self.lock:
                self.value, self.timestamp, self.error = None, None, str(error)

    def snapshot(self):
        with self.lock:
            age = None if self.timestamp is None else self.clock() - self.timestamp
            valid = self.value is not None and age is not None and 0 <= age <= 1
            return {
                "health": round(100 * self.value, 1) if valid else None,
                "health_valid": valid,
                "health_age": round(age, 2) if age is not None else None,
                "health_note": "Live · potion below 40%"
                if valid
                else (self.error or "Health reading is stale"),
            }

    def _run(self):
        camera = None
        try:
            camera = self.camera_factory()
            while not self.stop.is_set():
                self.sample(camera)
                self.stop.wait(self.interval)
        except Exception as error:
            with self.lock:
                self.value, self.timestamp, self.error = None, None, str(error)
        finally:
            if camera is not None:
                camera.close()

    def start(self):
        self.thread = threading.Thread(
            target=self._run, name="health-monitor", daemon=True
        )
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)


class UnavailableMemoryHealth:
    """Do not open a camera or substitute maximum HP for current HP."""

    def start(self):
        pass

    def close(self):
        pass

    def snapshot(self):
        return {
            "health": None,
            "health_valid": False,
            "health_age": None,
            "health_note": "Current HP memory is not validated; visual fallback disabled",
        }


def from_profile(profile_path, worker_info):
    from pathlib import Path
    import yaml
    from conquest.trial import TrialConfig
    from conquest.worker import request

    config = TrialConfig.model_validate(yaml.safe_load(Path(profile_path).read_text()))
    from conquest.progression import CombinedMonitor, from_profile as level_monitor

    if config.observation_mode == "memory_only":
        return CombinedMonitor(
            UnavailableMemoryHealth(), level_monitor(config, worker_info)
        )
    if not config.player_profile:
        raise ValueError("Select the client's exact-build player profile")
    identity = request(worker_info, "health")
    player = yaml.safe_load(Path(config.player_profile).read_text())
    if identity["expected_sha256"] != player["expected_sha256"]:
        raise ValueError("Health monitor client fingerprint mismatch")
    hwnd = identity["window"]["hwnd"]
    health = HealthMonitor(
        lambda: DesktopFrames(
            hwnd,
            config.client_size,
            config.capture_output,
            config.capture_origin,
            require_focus=False,
        )
    )
    return CombinedMonitor(health, level_monitor(config, worker_info))
