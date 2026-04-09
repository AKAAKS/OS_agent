"""
Bluetooth diagnostics — pattern library of known BT issues and fixes.
Handles most common Windows BT problems without API calls.
"""

import logging
import subprocess
from dataclasses import dataclass

from src.bluetooth.monitor import BluetoothMonitor, BTEvent

logger = logging.getLogger(__name__)

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


@dataclass
class BTDiagnosis:
    issue: str
    root_cause: str
    fix_description: str
    fix_command: str | None  # PowerShell command to fix, or None if manual
    risk_level: str  # "low", "medium", "high"
    confidence: float
    needs_llm: bool  # True if pattern not matched, needs LLM


class BTDiagnosticEngine:
    """Diagnoses BT issues using local pattern matching before escalating to LLM."""

    # Known issue patterns and their fixes
    KNOWN_PATTERNS = [
        {
            "pattern": lambda events, freq: any(
                freq.get(d, 0) >= 3 for d in freq
            ),
            "check_name": "frequent_disconnects",
            "diagnosis": BTDiagnosis(
                issue="Frequent Bluetooth disconnects",
                root_cause="Power management is turning off the BT adapter to save energy",
                fix_description="Disable power management for BT adapter",
                fix_command=(
                    'powershell -Command "'
                    "$adapter = Get-PnpDevice -Class Bluetooth | Where-Object {$_.FriendlyName -match 'adapter|radio'}; "
                    "if ($adapter) { "
                    "$id = $adapter.InstanceId; "
                    "Set-ItemProperty -Path \\\"HKLM:\\SYSTEM\\CurrentControlSet\\Enum\\$id\\Device Parameters\\\" "
                    "-Name 'SelectiveSuspendEnabled' -Value 0 -Type DWord -ErrorAction SilentlyContinue"
                    '}"'
                ),
                risk_level="medium",
                confidence=0.8,
                needs_llm=False,
            ),
        },
        {
            "pattern": lambda events, freq: any(
                e.event_type == "pair_fail" for e in events
            ),
            "check_name": "pairing_failure",
            "diagnosis": BTDiagnosis(
                issue="Bluetooth pairing failure",
                root_cause="Device may need to be removed and re-paired, or BT cache is stale",
                fix_description="Reset BT device pairing",
                fix_command=None,  # Manual action required
                risk_level="low",
                confidence=0.7,
                needs_llm=False,
            ),
        },
        {
            "pattern": lambda events, freq: not any(
                e.event_type == "connect" for e in events
            ) and len(events) > 0,
            "check_name": "no_connections",
            "diagnosis": BTDiagnosis(
                issue="Bluetooth adapter not connecting to any device",
                root_cause="BT adapter may be disabled or driver issue",
                fix_description="Restart Bluetooth adapter",
                fix_command=(
                    'powershell -Command "'
                    "$bt = Get-PnpDevice -Class Bluetooth | Where-Object {$_.FriendlyName -match 'adapter|radio'}; "
                    "if ($bt) { Disable-PnpDevice -InstanceId $bt.InstanceId -Confirm:$false; "
                    "Start-Sleep -Seconds 2; "
                    'Enable-PnpDevice -InstanceId $bt.InstanceId -Confirm:$false }"'
                ),
                risk_level="medium",
                confidence=0.75,
                needs_llm=False,
            ),
        },
    ]

    def __init__(self, monitor: BluetoothMonitor):
        self.monitor = monitor

    def diagnose(self) -> list[BTDiagnosis]:
        """Run all diagnostic patterns against current BT state."""
        events = self.monitor.get_recent_events(hours=1)
        freq = self.monitor.get_disconnect_frequency()

        # Check actual connected devices (includes classic BT audio)
        connected_devices = [d for d in self.monitor.get_paired_devices() if d.connected]

        diagnoses = []

        for pattern_def in self.KNOWN_PATTERNS:
            try:
                # Skip "no_connections" if we have connected devices
                if pattern_def["check_name"] == "no_connections" and connected_devices:
                    continue
                if pattern_def["pattern"](events, freq):
                    diagnosis = pattern_def["diagnosis"]
                    logger.info(f"BT diagnosis matched: {pattern_def['check_name']}")
                    diagnoses.append(diagnosis)
            except Exception as e:
                logger.debug(f"Pattern check error ({pattern_def['check_name']}): {e}")

        # If no patterns matched but there are genuine error events, flag for LLM.
        # Ignore "info" events — these are normal BT operational log entries.
        error_events = [e for e in events if e.event_type == "error"]
        if not diagnoses and len(error_events) >= 2:
            diagnoses.append(BTDiagnosis(
                issue="Unknown Bluetooth error",
                root_cause="Could not match a known pattern",
                fix_description="Needs deeper analysis",
                fix_command=None,
                risk_level="low",
                confidence=0.0,
                needs_llm=True,
            ))

        return diagnoses

    def check_power_management(self) -> dict:
        """Check if BT adapter power management settings are optimal."""
        result = {"power_saving_enabled": None, "selective_suspend": None}
        try:
            ps_result = subprocess.run(
                [
                    "powershell", "-Command",
                    "Get-PnpDevice -Class Bluetooth | "
                    "Where-Object {$_.FriendlyName -match 'adapter|radio'} | "
                    "Get-PnpDeviceProperty -KeyName 'DEVPKEY_Device_PowerData' | "
                    "Select-Object Data | ConvertTo-Json",
                ],
                capture_output=True, text=True, timeout=10, creationflags=_SUBPROCESS_FLAGS,
            )
            if ps_result.returncode == 0:
                result["power_check_ran"] = True
        except Exception:
            result["power_check_ran"] = False
        return result

    def get_bt_driver_info(self) -> dict | None:
        """Get Bluetooth driver information."""
        try:
            result = subprocess.run(
                [
                    "powershell", "-Command",
                    "Get-PnpDevice -Class Bluetooth | "
                    "Where-Object {$_.FriendlyName -match 'adapter|radio'} | "
                    "Get-PnpDeviceProperty -KeyName 'DEVPKEY_Device_DriverVersion','DEVPKEY_Device_DriverDate' | "
                    "Select-Object KeyName, Data | ConvertTo-Json",
                ],
                capture_output=True, text=True, timeout=10, creationflags=_SUBPROCESS_FLAGS,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json
                return json.loads(result.stdout)
        except Exception:
            pass
        return None
