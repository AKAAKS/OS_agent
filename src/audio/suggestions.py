"""
Smart audio routing suggestions — detects misrouted audio
and suggests fixes without any API calls.
"""

import logging
from dataclasses import dataclass

from src.audio.monitor import AudioMonitor, AudioSnapshot

logger = logging.getLogger(__name__)


@dataclass
class AudioSuggestion:
    message: str
    action: str  # "switch_output", "unmute", "info"
    details: dict


class AudioSuggestionEngine:
    """Generates smart audio suggestions based on device/session state."""

    def __init__(self, monitor: AudioMonitor):
        self.monitor = monitor
        self._last_suggestions: list[AudioSuggestion] = []

    def check(self) -> list[AudioSuggestion]:
        """Check for audio issues and return suggestions."""
        if not self.monitor.available:
            return []

        suggestions = []
        snapshot = self.monitor.take_snapshot()

        suggestions.extend(self._check_muted_active(snapshot))
        suggestions.extend(self._check_no_output(snapshot))
        suggestions.extend(self._check_bt_available(snapshot))

        self._last_suggestions = suggestions
        return suggestions

    def _check_muted_active(self, snapshot: AudioSnapshot) -> list[AudioSuggestion]:
        """Detect apps that are playing audio but are muted."""
        results = []
        for session in snapshot.sessions:
            if session.state == "active" and session.is_muted:
                results.append(AudioSuggestion(
                    message=f"{session.process_name} is playing audio but is muted",
                    action="unmute",
                    details={"pid": session.pid, "app": session.process_name},
                ))
        return results

    def _check_no_output(self, snapshot: AudioSnapshot) -> list[AudioSuggestion]:
        """Detect if audio is playing but no output device is active."""
        active = [s for s in snapshot.sessions if s.state == "active" and not s.is_muted]
        if active and not snapshot.output_devices:
            return [AudioSuggestion(
                message="Audio is playing but no output device detected",
                action="info",
                details={"active_apps": [s.process_name for s in active]},
            )]
        return []

    def _check_bt_available(self, snapshot: AudioSnapshot) -> list[AudioSuggestion]:
        """Detect if BT headphones are connected but audio goes to speakers."""
        bt_devices = [
            d for d in snapshot.output_devices
            if "bluetooth" in d.name.lower() or "bt" in d.name.lower()
            or "airpod" in d.name.lower() or "buds" in d.name.lower()
        ]
        if bt_devices:
            default = next((d for d in snapshot.output_devices if d.is_default), None)
            if default and not any(
                kw in default.name.lower()
                for kw in ["bluetooth", "bt", "airpod", "buds", "headphone"]
            ):
                bt_name = bt_devices[0].name
                return [AudioSuggestion(
                    message=f"Bluetooth device '{bt_name}' is connected but audio goes to '{default.name}'",
                    action="switch_output",
                    details={"current": default.name, "suggested": bt_name},
                )]
        return []
