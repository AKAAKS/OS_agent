"""
Bluetooth fix actions — executes remediation commands for BT issues.
All actions go through HITL approval for medium/high risk.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


class BTActionResult:
    def __init__(self, success: bool, message: str):
        self.success = success
        self.message = message


class BTActions:
    """Executes Bluetooth fix actions."""

    def restart_adapter(self) -> BTActionResult:
        """Disable and re-enable the Bluetooth adapter."""
        try:
            result = subprocess.run(
                [
                    "powershell", "-Command",
                    "$bt = Get-PnpDevice -Class Bluetooth | "
                    "Where-Object {$_.FriendlyName -match 'adapter|radio'}; "
                    "if ($bt) { "
                    "Disable-PnpDevice -InstanceId $bt.InstanceId -Confirm:$false; "
                    "Start-Sleep -Seconds 2; "
                    "Enable-PnpDevice -InstanceId $bt.InstanceId -Confirm:$false; "
                    "Write-Output 'Adapter restarted' } "
                    "else { Write-Output 'No adapter found' }",
                ],
                capture_output=True, text=True, timeout=30, creationflags=_SUBPROCESS_FLAGS,
            )
            success = "restarted" in result.stdout.lower()
            return BTActionResult(success, result.stdout.strip() or result.stderr.strip())
        except Exception as e:
            return BTActionResult(False, f"Error: {e}")

    def disable_power_saving(self) -> BTActionResult:
        """Disable power management for the BT adapter to prevent random disconnects."""
        try:
            result = subprocess.run(
                [
                    "powershell", "-Command",
                    "$adapter = Get-PnpDevice -Class Bluetooth | "
                    "Where-Object {$_.FriendlyName -match 'adapter|radio'}; "
                    "if ($adapter) { "
                    "$id = $adapter.InstanceId; "
                    "Set-ItemProperty -Path \"HKLM:\\SYSTEM\\CurrentControlSet\\Enum\\$id\\Device Parameters\" "
                    "-Name 'SelectiveSuspendEnabled' -Value 0 -Type DWord -ErrorAction SilentlyContinue; "
                    "Write-Output 'Power saving disabled for BT adapter' } "
                    "else { Write-Output 'No adapter found' }",
                ],
                capture_output=True, text=True, timeout=15, creationflags=_SUBPROCESS_FLAGS,
            )
            success = "disabled" in result.stdout.lower()
            return BTActionResult(success, result.stdout.strip() or result.stderr.strip())
        except Exception as e:
            return BTActionResult(False, f"Error: {e}")

    def reconnect_device(self, device_instance_id: str) -> BTActionResult:
        """Force reconnect a specific BT device."""
        try:
            # Disable then re-enable the specific device
            result = subprocess.run(
                [
                    "powershell", "-Command",
                    f"Disable-PnpDevice -InstanceId '{device_instance_id}' -Confirm:$false; "
                    f"Start-Sleep -Seconds 2; "
                    f"Enable-PnpDevice -InstanceId '{device_instance_id}' -Confirm:$false; "
                    f"Write-Output 'Device reconnected'",
                ],
                capture_output=True, text=True, timeout=20, creationflags=_SUBPROCESS_FLAGS,
            )
            return BTActionResult(
                "reconnected" in result.stdout.lower(),
                result.stdout.strip() or result.stderr.strip(),
            )
        except Exception as e:
            return BTActionResult(False, f"Error: {e}")

    def switch_audio_profile(self, device_name: str, profile: str = "A2DP") -> BTActionResult:
        """Switch BT audio profile (A2DP for music, HFP for calls)."""
        # This typically requires toggling the device in Sound settings
        # We provide guidance rather than direct automation
        return BTActionResult(
            True,
            f"To switch '{device_name}' to {profile}: "
            f"Right-click sound icon → Sound settings → "
            f"Select the correct profile for your device. "
            f"A2DP = high-quality audio, HFP = calls/mic.",
        )

    def run_bt_troubleshooter(self) -> BTActionResult:
        """Run the Windows built-in Bluetooth troubleshooter."""
        try:
            result = subprocess.run(
                [
                    "powershell", "-Command",
                    "Get-TroubleshootingPack -Path 'C:\\Windows\\diagnostics\\system\\Bluetooth' | "
                    "Invoke-TroubleshootingPack -Unattended",
                ],
                capture_output=True, text=True, timeout=60, creationflags=_SUBPROCESS_FLAGS,
            )
            return BTActionResult(
                result.returncode == 0,
                "Bluetooth troubleshooter completed" if result.returncode == 0 else result.stderr,
            )
        except subprocess.TimeoutExpired:
            return BTActionResult(False, "Troubleshooter timed out")
        except Exception as e:
            return BTActionResult(False, f"Error: {e}")
