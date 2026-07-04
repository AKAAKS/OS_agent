"""Monitor resilience: repeated PowerShell failures must trigger backoff
instead of hammering a hung query every cycle (159 ten-second timeouts
were logged in June 2026 before this existed)."""

import subprocess

import pytest

from src.bluetooth.monitor import BluetoothMonitor


@pytest.fixture
def timing_out_run(monkeypatch):
    calls = {"n": 0}

    def fake_run(*args, **kwargs):
        calls["n"] += 1
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=10)

    monkeypatch.setattr("src.bluetooth.monitor.subprocess.run", fake_run)
    return calls


def test_bt_backoff_after_repeated_timeouts(timing_out_run):
    monitor = BluetoothMonitor()

    # Keep querying until the failure threshold engages the backoff
    for _ in range(BluetoothMonitor._PS_FAILURE_THRESHOLD):
        monitor.get_paired_devices()
    calls_at_threshold = timing_out_run["n"]
    assert calls_at_threshold >= BluetoothMonitor._PS_FAILURE_THRESHOLD

    # Once backed off, no further subprocess calls are made
    monitor.get_paired_devices()
    monitor.is_adapter_enabled()
    monitor.get_recent_events()
    assert timing_out_run["n"] == calls_at_threshold


def test_bt_backoff_expires(timing_out_run):
    monitor = BluetoothMonitor()
    for _ in range(BluetoothMonitor._PS_FAILURE_THRESHOLD):
        monitor.get_paired_devices()
    calls_at_threshold = timing_out_run["n"]

    # Simulate the backoff window having elapsed
    monitor._ps_backoff_until = 0.0
    monitor.get_paired_devices()
    assert timing_out_run["n"] > calls_at_threshold
