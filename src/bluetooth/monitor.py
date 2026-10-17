"""
Bluetooth event monitor — watches Windows Event Logs and WMI
for BT connectivity issues, disconnects, and pairing failures.
"""

import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Hide console windows from subprocess calls on Windows
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


@dataclass
class BTDevice:
    name: str
    address: str
    connected: bool
    device_type: str  # "audio", "input", "other"
    status: str


@dataclass
class BTEvent:
    timestamp: datetime
    event_type: str  # "connect", "disconnect", "pair_fail", "error"
    device_name: str
    message: str
    event_id: int


class BluetoothMonitor:
    """Monitors Bluetooth connectivity via Windows Event Log and PowerShell."""

    # After this many consecutive PowerShell failures (usually 10s timeouts),
    # stop querying for _PS_BACKOFF_SECONDS instead of hammering a hung
    # provider every cycle.
    _PS_FAILURE_THRESHOLD = 3
    _PS_BACKOFF_SECONDS = 1800

    def __init__(self):
        self._event_history: list[BTEvent] = []
        self._disconnect_counts: dict[str, int] = {}
        self._max_history = 100
        self._ps_failures = 0
        self._ps_backoff_until = 0.0
        # Event logs Windows has told us don't exist on this machine; never
        # re-queried (a cold powershell launch per cycle for a missing log was
        # pure waste and an occasional timeout source).
        self._dead_logs: set[str] = set()

    def _run_ps(self, command: str, timeout: int):
        """Run a PowerShell query with consecutive-failure backoff.

        Returns the CompletedProcess, or None when the query failed or
        queries are currently backed off.
        """
        import time as _time

        if _time.monotonic() < self._ps_backoff_until:
            return None
        try:
            result = subprocess.run(
                ["powershell", "-Command", command],
                capture_output=True, text=True, timeout=timeout,
                creationflags=_SUBPROCESS_FLAGS,
            )
            self._ps_failures = 0
            return result
        except Exception as e:
            self._ps_failures += 1
            if self._ps_failures >= self._PS_FAILURE_THRESHOLD:
                self._ps_backoff_until = _time.monotonic() + self._PS_BACKOFF_SECONDS
                self._ps_failures = 0
                logger.warning(
                    f"Bluetooth PowerShell queries keep failing ({e}) — "
                    f"pausing them for {self._PS_BACKOFF_SECONDS // 60} min"
                )
            else:
                # Recoverable: the next cycle retries, and the backoff above
                # engages if failures persist. Not an ERROR.
                logger.warning(f"Bluetooth query failed (recoverable): {e}")
            return None

    def _query_event_log(self, log_key: str, command: str, timeout: int):
        """Run an event-log query, skipping logs Windows has reported missing.

        Caches a 'log does not exist' result so a nonexistent channel is
        queried at most once for the life of the process.
        """
        if log_key in self._dead_logs:
            return None
        result = self._run_ps(command, timeout=timeout)
        if result is None:
            return None
        if result.returncode != 0 and result.stderr and \
                "not an event log" in result.stderr.lower():
            self._dead_logs.add(log_key)
            logger.info(f"Bluetooth event log '{log_key}' not present — skipping it from now on")
            return None
        return result

    @staticmethod
    def _as_list(value) -> list:
        """Normalize ConvertTo-Json output (single object -> one-item list)."""
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        return value if isinstance(value, list) else []

    def get_paired_devices(self) -> list[BTDevice]:
        """Get all paired Bluetooth devices via a single PowerShell launch.

        Both the classic PnP Bluetooth devices and the BT audio endpoints
        (speakers/headphones on classic A2DP) are fetched in one combined
        query. Get-PnpDevice is slow and was previously launched twice per
        cycle — the dominant source of the per-cycle timeouts.
        """
        devices: list[BTDevice] = []
        seen_names: set[str] = set()

        result = self._run_ps(
            "$bt = @(Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
            "Select-Object FriendlyName, InstanceId, Status); "
            "$au = @(Get-PnpDevice -Class AudioEndpoint -Status OK -ErrorAction SilentlyContinue | "
            "Where-Object { $_.FriendlyName -match 'Bluetooth|BT|Wireless' -or "
            "$_.InstanceId -match 'BTHENUM' } | "
            "Select-Object FriendlyName, InstanceId, Status); "
            "[pscustomobject]@{ Bluetooth = $bt; Audio = $au } | ConvertTo-Json -Depth 4",
            timeout=20,
        )
        if not (result and result.returncode == 0 and result.stdout.strip()):
            return devices

        import json
        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse BT device JSON")
            return devices
        if not isinstance(data, dict):
            return devices

        # Classic PnP Bluetooth devices (skip the adapter/radio itself).
        for item in self._as_list(data.get("Bluetooth")):
            name = item.get("FriendlyName", "Unknown")
            if any(kw in name.lower() for kw in ["adapter", "radio", "host"]):
                continue
            seen_names.add(name.lower())
            devices.append(BTDevice(
                name=name,
                address=item.get("InstanceId", ""),
                connected=item.get("Status", "") == "OK",
                device_type=self._classify_device(name),
                status=item.get("Status", "Unknown"),
            ))

        # BT audio endpoints (Status OK == active).
        for item in self._as_list(data.get("Audio")):
            name = item.get("FriendlyName", "Unknown")
            if name.lower() not in seen_names:
                seen_names.add(name.lower())
                devices.append(BTDevice(
                    name=name,
                    address=item.get("InstanceId", ""),
                    connected=True,
                    device_type="audio",
                    status="OK",
                ))

        return devices

    def get_recent_events(self, hours: int = 1) -> list[BTEvent]:
        """Get recent Bluetooth events from Windows Event Log."""
        events = []
        try:
            # Query BT-related event logs
            time_filter = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
            result = self._query_event_log(
                "Microsoft-Windows-Bluetooth-BthLEEnum/Operational",
                f'Get-WinEvent -FilterHashtable @{{'
                f"LogName='Microsoft-Windows-Bluetooth-BthLEEnum/Operational';"
                f"StartTime='{time_filter}'"
                f'}} -MaxEvents 20 -ErrorAction SilentlyContinue | '
                f'Select-Object TimeCreated, Id, Message | ConvertTo-Json',
                timeout=15,
            )

            if result and result.returncode == 0 and result.stdout.strip():
                import json
                try:
                    data = json.loads(result.stdout)
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Failed to parse BT event log JSON")
                    data = []
                if isinstance(data, dict):
                    data = [data]
                for item in data:
                    msg = item.get("Message", "")
                    event_type = self._classify_event(item.get("Id", 0), msg)
                    # Parse timestamp safely — PowerShell may return various formats
                    ts = datetime.now()
                    raw_ts = item.get("TimeCreated", "")
                    if raw_ts:
                        try:
                            cleaned = str(raw_ts).replace("/Date(", "").rstrip(")/")
                            ts = datetime.fromisoformat(cleaned)
                        except (ValueError, TypeError):
                            pass  # Keep datetime.now() fallback
                    events.append(BTEvent(
                        timestamp=ts,
                        event_type=event_type,
                        device_name=self._extract_device_name(msg),
                        message=msg[:200],
                        event_id=item.get("Id", 0),
                    ))
        except Exception as e:
            logger.debug(f"Error reading BT event log: {e}")

        # Also check system event log for BT errors
        try:
            result = self._query_event_log(
                "System/BT",
                f"Get-WinEvent -FilterHashtable @{{"
                f"LogName='System'; ProviderName='BTHUSB','bthport';"
                f"StartTime='{time_filter}'"
                f"}} -MaxEvents 10 -ErrorAction SilentlyContinue | "
                f"Select-Object TimeCreated, Id, Message | ConvertTo-Json",
                timeout=15,
            )
            if result and result.returncode == 0 and result.stdout.strip():
                import json
                try:
                    data = json.loads(result.stdout)
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Failed to parse system BT event JSON")
                    data = []
                if isinstance(data, dict):
                    data = [data]
                for item in data:
                    msg = item.get("Message", "")
                    events.append(BTEvent(
                        timestamp=datetime.now(),
                        event_type="error",
                        device_name=self._extract_device_name(msg),
                        message=msg[:200],
                        event_id=item.get("Id", 0),
                    ))
        except Exception as e:
            logger.debug(f"Error reading system BT events: {e}")

        # Track disconnect frequency
        for event in events:
            if event.event_type == "disconnect":
                key = event.device_name or "unknown"
                self._disconnect_counts[key] = self._disconnect_counts.get(key, 0) + 1

        # Store history
        self._event_history.extend(events)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history:]

        return events

    def get_disconnect_frequency(self) -> dict[str, int]:
        """Get disconnect count per device."""
        return dict(self._disconnect_counts)

    def is_adapter_enabled(self) -> bool:
        """Check if the Bluetooth adapter is enabled."""
        try:
            result = self._run_ps(
                "Get-PnpDevice -Class Bluetooth -FriendlyName '*adapter*','*radio*' | "
                "Select-Object Status | ConvertTo-Json",
                timeout=20,
            )
            if result and result.returncode == 0 and result.stdout.strip():
                import json
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    data = [data]
                return any(d.get("Status") == "OK" for d in data)
        except Exception:
            pass
        return True  # Assume enabled if we can't check

    def _classify_device(self, name: str) -> str:
        name_lower = name.lower()
        if any(kw in name_lower for kw in ["headphone", "earbuds", "airpod", "speaker", "audio", "buds"]):
            return "audio"
        if any(kw in name_lower for kw in ["mouse", "keyboard", "controller", "gamepad"]):
            return "input"
        return "other"

    def _classify_event(self, event_id: int, message: str) -> str:
        msg_lower = message.lower()
        if "disconnect" in msg_lower or "removed" in msg_lower:
            return "disconnect"
        if "connect" in msg_lower or "added" in msg_lower:
            return "connect"
        if "pair" in msg_lower and ("fail" in msg_lower or "error" in msg_lower):
            return "pair_fail"
        if any(kw in msg_lower for kw in ["error", "fail", "timeout", "reset"]):
            return "error"
        return "info"

    def _extract_device_name(self, message: str) -> str:
        # Try to find a device name in the message
        for marker in ["device:", "Device:", "name:", "Name:"]:
            if marker in message:
                rest = message.split(marker, 1)[1].strip()
                return rest.split("\n")[0].split(",")[0].strip()[:50]
        return ""
