"""Tests for the scheduler service."""

import tempfile
from pathlib import Path

import pytest

from src.services.scheduler_service import SchedulerService


def test_default_config():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SchedulerService(config_file=Path(tmpdir) / "config.json")
        status = svc.get_status()
        assert status["enabled"] is True
        assert status["interval_hours"] == 6
        assert status["running"] is False


def test_set_interval():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / "config.json"
        svc = SchedulerService(config_file=config_file)
        svc.set_interval(12)
        status = svc.get_status()
        assert status["interval_hours"] == 12
        assert config_file.exists()


def test_interval_clamped():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SchedulerService(config_file=Path(tmpdir) / "config.json")
        svc.set_interval(0)
        assert svc.get_status()["interval_hours"] == 1
        svc.set_interval(100)
        assert svc.get_status()["interval_hours"] == 24


def test_set_enabled_disable():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SchedulerService(config_file=Path(tmpdir) / "config.json")
        svc.set_enabled(False)
        assert svc.get_status()["enabled"] is False


@pytest.mark.asyncio
async def test_set_enabled_enable():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SchedulerService(config_file=Path(tmpdir) / "config.json")
        svc.set_enabled(False)
        svc.set_enabled(True)
        assert svc.get_status()["enabled"] is True
        svc.stop()


@pytest.mark.asyncio
async def test_start_and_stop():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SchedulerService(config_file=Path(tmpdir) / "config.json")
        svc.start()
        assert svc.get_status()["running"] is True
        assert svc.get_status()["next_run"] is not None
        svc.stop()
        assert svc.get_status()["running"] is False


def test_start_disabled():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SchedulerService(config_file=Path(tmpdir) / "config.json")
        svc.set_enabled(False)
        svc.start()
        assert svc.get_status()["running"] is False
