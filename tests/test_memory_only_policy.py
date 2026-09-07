import logging
from pathlib import Path

import pytest
import yaml

from conquest import trial, health_monitor, progression


def fail(*args, **kwargs):
    pytest.fail("Memory-only mode must not start visual capture or input")


def test_memory_only_farming_blocks_before_camera_worker_or_templates(tmp_path, monkeypatch):
    monkeypatch.setattr(trial, "DesktopFrames", fail)
    monkeypatch.setattr(trial, "WorkerPointerSession", fail)
    monkeypatch.setattr(trial.cv2, "imread", fail)
    with pytest.raises(ValueError, match="Memory-only farming is not qualified"):
        trial.run_trial(Path("profiles/turtledove-foreground-trial.yaml"), "missing-worker", tmp_path,
                        30, logging.getLogger("test"))


def test_memory_only_dashboard_never_opens_camera(monkeypatch):
    monkeypatch.setattr(health_monitor, "DesktopFrames", fail)
    monkeypatch.setattr(progression, "from_profile", lambda *args: health_monitor.UnavailableMemoryHealth())
    monitor = health_monitor.from_profile("profiles/turtledove-foreground-trial.yaml", "missing-worker")
    monitor.start()
    assert monitor.snapshot()["health_valid"] is False
    assert monitor.snapshot()["health"] is None
    monitor.close()


def test_old_profiles_without_mode_default_to_memory_only():
    data = yaml.safe_load(Path("profiles/pheasant-foreground-trial.yaml").read_text())
    del data["observation_mode"]
    assert trial.TrialConfig.model_validate(data).observation_mode == "memory_only"
