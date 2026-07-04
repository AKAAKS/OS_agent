"""
Audio session monitor — uses pycaw to enumerate active audio sessions,
output devices, and microphone usage on Windows.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AudioSession:
    pid: int
    process_name: str
    volume: float  # 0.0 - 1.0
    is_muted: bool
    state: str  # "active", "inactive", "expired"


@dataclass
class AudioDevice:
    name: str
    device_id: str
    is_default: bool
    flow: str  # "render" (output) or "capture" (input)


@dataclass
class AudioSnapshot:
    sessions: list[AudioSession]
    output_devices: list[AudioDevice]
    input_devices: list[AudioDevice]
    default_output: str
    default_input: str


class AudioMonitor:
    """Monitors active audio sessions and device routing via Windows Core Audio API."""

    def __init__(self):
        self._available = False
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            self._AudioUtilities = AudioUtilities
            self._CLSCTX_ALL = CLSCTX_ALL
            self._available = True
        except ImportError:
            logger.warning("pycaw not installed — audio monitoring disabled")

    @property
    def available(self) -> bool:
        return self._available

    def get_sessions(self) -> list[AudioSession]:
        """Get all active audio sessions (which apps are playing audio)."""
        if not self._available:
            return []

        sessions = []
        try:
            for session in self._AudioUtilities.GetAllSessions():
                # A session's process can exit between enumeration and access —
                # skip that session instead of aborting the whole scan.
                try:
                    proc = session.Process
                    if proc is None:
                        continue

                    volume_ctl = session.SimpleAudioVolume
                    state_val = session.State
                    state_str = {0: "inactive", 1: "active", 2: "expired"}.get(state_val, "unknown")

                    sessions.append(AudioSession(
                        pid=proc.pid,
                        process_name=proc.name(),
                        volume=round(volume_ctl.GetMasterVolume(), 2) if volume_ctl else 0.0,
                        is_muted=bool(volume_ctl.GetMute()) if volume_ctl else False,
                        state=state_str,
                    ))
                except Exception as e:
                    logger.debug(f"Skipping audio session (process gone?): {e}")
        except Exception as e:
            logger.error(f"Error enumerating audio sessions: {e}")

        return sessions

    def get_output_devices(self) -> list[AudioDevice]:
        """Get all audio output (render) devices."""
        if not self._available:
            return []
        return self._get_devices("render")

    def get_input_devices(self) -> list[AudioDevice]:
        """Get all audio input (capture) devices."""
        if not self._available:
            return []
        return self._get_devices("capture")

    def _get_devices(self, flow: str) -> list[AudioDevice]:
        """Get audio devices using pycaw's GetAllDevices (proper FriendlyName)."""
        devices = []
        try:
            from pycaw.pycaw import AudioUtilities, AudioDeviceState

            # Render device IDs start with {0.0.0, capture with {0.0.1
            flow_prefix = "{0.0.0" if flow == "render" else "{0.0.1"

            # Get default device ID for marking
            default_id = None
            try:
                flow_val = 0 if flow == "render" else 1
                import comtypes
                from pycaw.constants import CLSID_MMDeviceEnumerator
                from pycaw.pycaw import IMMDeviceEnumerator
                enumerator = comtypes.CoCreateInstance(
                    CLSID_MMDeviceEnumerator,
                    IMMDeviceEnumerator,
                    comtypes.CLSCTX_INPROC_SERVER,
                )
                default = enumerator.GetDefaultAudioEndpoint(flow_val, 0)
                default_id = default.GetId()
            except Exception:
                pass

            for d in AudioUtilities.GetAllDevices():
                if d.state != AudioDeviceState.Active:
                    continue
                if not d.id.startswith(flow_prefix):
                    continue
                devices.append(AudioDevice(
                    name=d.FriendlyName,
                    device_id=d.id,
                    is_default=(d.id == default_id),
                    flow=flow,
                ))

        except Exception as e:
            logger.error(f"Error enumerating {flow} devices: {e}")

        return devices

    def take_snapshot(self) -> AudioSnapshot:
        """Take a complete snapshot of audio state."""
        sessions = self.get_sessions()
        outputs = self.get_output_devices()
        inputs = self.get_input_devices()

        default_out = next((d.name for d in outputs if d.is_default), "Unknown")
        default_in = next((d.name for d in inputs if d.is_default), "Unknown")

        return AudioSnapshot(
            sessions=sessions,
            output_devices=outputs,
            input_devices=inputs,
            default_output=default_out,
            default_input=default_in,
        )

    def get_active_audio_apps(self) -> list[dict]:
        """Simple helper: get just the apps currently producing audio."""
        sessions = self.get_sessions()
        return [
            {
                "app": s.process_name,
                "pid": s.pid,
                "volume": f"{s.volume * 100:.0f}%",
                "muted": s.is_muted,
            }
            for s in sessions
            if s.state == "active"
        ]
