"""Bluetooth monitor efficiency + resilience.

These cover the 2026-10 refactor that cut the per-cycle PowerShell launches
(the source of ~1-4 Get-PnpDevice / Get-WinEvent timeouts per day) by:
  - collapsing get_paired_devices into a single combined query,
  - skipping an event log permanently once Windows reports it missing,
  - logging handled (recoverable) timeouts at WARNING rather than ERROR.
"""

import json
import logging
import subprocess

import pytest

from src.bluetooth.monitor import BluetoothMonitor


class FakeCompleted:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


@pytest.fixture
def recorder(monkeypatch):
    """Record every powershell command run, with a programmable response."""
    calls = []
    responses = {"default": FakeCompleted(stdout="")}

    def fake_run(args, **kwargs):
        command = args[-1] if isinstance(args, (list, tuple)) else str(args)
        calls.append({"command": command, "timeout": kwargs.get("timeout")})
        for needle, resp in responses.items():
            if needle != "default" and needle in command:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        default = responses["default"]
        if isinstance(default, Exception):
            raise default
        return default

    monkeypatch.setattr("src.bluetooth.monitor.subprocess.run", fake_run)
    return {"calls": calls, "responses": responses}


def test_get_paired_devices_uses_a_single_powershell_call(recorder):
    """Classic BT + BT-audio devices must come from ONE launch, not two."""
    recorder["responses"]["default"] = FakeCompleted(stdout=json.dumps({
        "Bluetooth": [
            {"FriendlyName": "Intel Bluetooth Radio", "InstanceId": "USB\\X", "Status": "OK"},
            {"FriendlyName": "WH-CH710N", "InstanceId": "BTHENUM\\Y", "Status": "OK"},
        ],
        "Audio": [
            {"FriendlyName": "Living Room speaker", "InstanceId": "BTHENUM\\Z", "Status": "OK"},
        ],
    }))

    monitor = BluetoothMonitor()
    devices = monitor.get_paired_devices()

    assert len(recorder["calls"]) == 1, "expected a single combined PowerShell launch"
    names = {d.name for d in devices}
    assert "WH-CH710N" in names                     # classic BT device kept
    assert "Living Room speaker" in names           # BT-audio endpoint kept
    assert "Intel Bluetooth Radio" not in names     # adapter/radio filtered out


def _install(monkeypatch_target_calls, responder):
    """Point monitor.subprocess.run at `responder`, recording calls."""
    import src.bluetooth.monitor as mod

    def run(args, **kwargs):
        command = args[-1]
        monkeypatch_target_calls.append({"command": command, "timeout": kwargs.get("timeout")})
        return responder(command)

    mod.subprocess.run = run


def test_missing_operational_log_is_probed_once_then_never_scanned(recorder):
    """A BT operational log absent from the one-time probe is never scanned."""
    def responder(command):
        if "ListLog" in command:
            return FakeCompleted(stdout="[]")          # no operational BT logs present
        return FakeCompleted(stdout="")                # System query: empty, fine
    _install(recorder["calls"], responder)

    monitor = BluetoothMonitor()
    monitor.get_recent_events()
    monitor.get_recent_events()

    probes = [c for c in recorder["calls"] if "ListLog" in c["command"]]
    assert len(probes) == 1, "operational logs are probed once and cached"
    bthle_scans = [c for c in recorder["calls"]
                   if "FilterHashtable" in c["command"] and "BthLEEnum" in c["command"]]
    assert len(bthle_scans) == 0, "a missing operational log is never scanned"
    system_scans = [c for c in recorder["calls"] if "BTHUSB" in c["command"]]
    assert len(system_scans) == 2, "the System BT log is still scanned every cycle"


def test_present_operational_log_is_scanned(recorder):
    """A BT operational log the probe reports as present IS scanned."""
    def responder(command):
        if "ListLog" in command:
            return FakeCompleted(stdout='"Microsoft-Windows-Bluetooth-BthLEEnum/Operational"')
        return FakeCompleted(stdout="")
    _install(recorder["calls"], responder)

    monitor = BluetoothMonitor()
    monitor.get_recent_events()

    bthle_scans = [c for c in recorder["calls"]
                   if "FilterHashtable" in c["command"] and "BthLEEnum" in c["command"]]
    assert len(bthle_scans) == 1, "a present operational log is scanned"


def test_handled_timeout_logs_warning_not_error(recorder, caplog):
    """A single recoverable timeout is WARNING-level noise, not an ERROR."""
    recorder["responses"]["default"] = subprocess.TimeoutExpired(cmd="powershell", timeout=20)

    monitor = BluetoothMonitor()
    with caplog.at_level(logging.DEBUG, logger="src.bluetooth.monitor"):
        monitor.get_paired_devices()

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], \
        "handled single timeout should not log at ERROR"
    assert [r for r in caplog.records if r.levelno == logging.WARNING], \
        "handled timeout should be recorded at WARNING"


def test_pnpdevice_query_timeout_is_generous(recorder):
    """Get-PnpDevice is slow under load; its timeout must be >= 20s."""
    recorder["responses"]["default"] = FakeCompleted(stdout=json.dumps({"Bluetooth": [], "Audio": []}))

    monitor = BluetoothMonitor()
    monitor.get_paired_devices()

    assert recorder["calls"][0]["timeout"] >= 20
