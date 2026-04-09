"""
Windows toast notifications — surfaces anomalies, profile changes,
and agent actions as native Windows notifications.
"""

import logging
import threading
from enum import Enum

logger = logging.getLogger(__name__)

try:
    from winotify import Notification, audio
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False
    logger.warning("winotify not installed — toast notifications disabled")


class NotifyLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# Map notification levels to winotify audio cues
_AUDIO_MAP = {
    NotifyLevel.INFO: None,
    NotifyLevel.WARNING: audio.Default if _AVAILABLE else None,
    NotifyLevel.CRITICAL: audio.LoopingAlarm if _AVAILABLE else None,
}

# Suppress duplicate toasts within this window (seconds)
_COOLDOWN_SECONDS = 30


class ToastNotifier:
    """Sends Windows toast notifications for agent events.

    Thread-safe — can be called from any thread.
    Rate-limited to prevent notification spam.
    """

    APP_ID = "OS Agent"

    def __init__(self, enabled: bool = True):
        self._enabled = enabled and _AVAILABLE
        self._lock = threading.Lock()
        self._recent: dict[str, float] = {}  # dedup_key -> last_sent timestamp

    @property
    def available(self) -> bool:
        return self._enabled

    def notify(
        self,
        title: str,
        message: str,
        level: NotifyLevel = NotifyLevel.INFO,
        dedup_key: str | None = None,
    ):
        """Send a toast notification.

        Args:
            title: Notification title (bold, top line)
            message: Notification body
            level: Controls audio cue — INFO (silent), WARNING (default sound), CRITICAL (alarm)
            dedup_key: Optional key for rate-limiting. Same key within _COOLDOWN_SECONDS is suppressed.
        """
        if not self._enabled:
            return

        import time

        # Rate-limit duplicates
        if dedup_key:
            with self._lock:
                now = time.monotonic()
                last = self._recent.get(dedup_key, 0)
                if now - last < _COOLDOWN_SECONDS:
                    logger.debug(f"Toast suppressed (cooldown): {title}")
                    return
                self._recent[dedup_key] = now

                # Prune old entries
                cutoff = now - _COOLDOWN_SECONDS * 2
                self._recent = {k: v for k, v in self._recent.items() if v > cutoff}

        # Truncate long messages to avoid winotify issues
        title = title[:128] if title else ""
        message = message[:256] if message else ""

        # Send in a background thread so it never blocks the caller
        threading.Thread(
            target=self._send, args=(title, message, level), daemon=True, name="toast"
        ).start()

    def _send(self, title: str, message: str, level: NotifyLevel):
        try:
            toast = Notification(
                app_id=self.APP_ID,
                title=title,
                msg=message,
                duration="short" if level != NotifyLevel.CRITICAL else "long",
            )

            audio_cue = _AUDIO_MAP.get(level)
            if audio_cue:
                toast.set_audio(audio_cue, loop=False)

            toast.show()
            logger.debug(f"Toast sent: [{level.value}] {title}")
        except Exception as e:
            logger.error(f"Toast notification failed: {e}")

    # ----- Convenience methods for common events -----

    def anomaly_detected(self, anomaly_type: str, summary: str, severity: str):
        """Notify about a system anomaly."""
        level = {
            "low": NotifyLevel.INFO,
            "medium": NotifyLevel.WARNING,
            "high": NotifyLevel.CRITICAL,
        }.get(severity, NotifyLevel.WARNING)

        self.notify(
            title=f"Anomaly: {anomaly_type.replace('_', ' ').title()}",
            message=summary,
            level=level,
            dedup_key=f"anomaly:{anomaly_type}",
        )

    def action_taken(self, action: str, target: str, success: bool):
        """Notify about an agent action."""
        status = "completed" if success else "FAILED"
        self.notify(
            title=f"Action {status}: {action}",
            message=f"Target: {target}",
            level=NotifyLevel.INFO if success else NotifyLevel.WARNING,
            dedup_key=f"action:{action}:{target}",
        )

    def profile_changed(self, old_profile: str, new_profile: str, trigger: str):
        """Notify about a profile switch."""
        self.notify(
            title=f"Profile: {new_profile.capitalize()}",
            message=f"Switched from {old_profile} (triggered by {trigger})",
            level=NotifyLevel.INFO,
            dedup_key="profile_change",
        )

    def audio_suggestion(self, message: str):
        """Notify about an audio routing suggestion."""
        self.notify(
            title="Audio Suggestion",
            message=message,
            level=NotifyLevel.INFO,
            dedup_key=f"audio:{message[:30]}",
        )

    def diagnosis_report(self, anomaly_type: str, root_cause: str, fix: str, success: bool):
        """Notify about a completed diagnosis + fix for high-severity anomalies.

        This is the detailed toast that tells the user exactly what was wrong
        and what the agent did about it.
        """
        status = "Fixed" if success else "Fix FAILED"
        self.notify(
            title=f"{status}: {anomaly_type.replace('_', ' ').title()}",
            message=f"Cause: {root_cause}\nAction: {fix}",
            level=NotifyLevel.INFO if success else NotifyLevel.CRITICAL,
            dedup_key=f"diagnosis:{anomaly_type}:{root_cause[:20]}",
        )

    def bt_issue(self, issue: str, fix: str):
        """Notify about a Bluetooth issue."""
        self.notify(
            title="Bluetooth Issue",
            message=f"{issue}\nFix: {fix}",
            level=NotifyLevel.WARNING,
            dedup_key=f"bt:{issue[:30]}",
        )
