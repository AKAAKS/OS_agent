"""
Profile manager — auto-detects active profile (Gaming/Coding/Streaming/Idle)
based on running processes and applies per-profile rules.
"""

import ctypes
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

import psutil

logger = logging.getLogger(__name__)


class ProfileType(Enum):
    IDLE = "idle"
    CODING = "coding"
    GAMING = "gaming"
    STREAMING = "streaming"
    BROWSING = "browsing"
    MEDIA = "media"
    COMMS = "comms"


@dataclass
class ProfileConfig:
    name: ProfileType
    trigger_processes: list[str]
    always_keep: list[str]
    kill_candidates: list[str]
    actions: dict


class ProfileManager:
    """Detects the active user profile and provides process classification rules."""

    # How long a new profile must be consistently detected before we enter it
    ENTRY_DEBOUNCE_SECONDS = 10

    # How long the current profile must NOT be detected before we leave it.
    # Higher values make profiles "stickier" — prevents oscillation on alt-tab.
    EXIT_DEBOUNCE_SECONDS = {
        "idle": 20,
        "browsing": 45,
        "media": 30,
        "comms": 30,
        "coding": 60,
        "gaming": 60,
        "streaming": 60,
    }

    def __init__(self, profiles_config: dict, global_whitelist: list[str], global_blacklist: list[str]):
        self.profiles: dict[ProfileType, ProfileConfig] = {}
        self.global_whitelist = {p.lower() for p in global_whitelist}
        self.global_blacklist = {p.lower() for p in global_blacklist}
        self._active_profile = ProfileType.IDLE
        self._trigger_app = "No activity detected"
        self._last_input_time = datetime.now()
        self._idle_timeout = timedelta(minutes=10)

        # Debounce state — prevents rapid profile switching on alt-tab
        self._pending_profile: ProfileType | None = None
        self._pending_trigger: str = ""
        self._pending_since: float = 0.0
        self._current_last_seen: float = time.monotonic()  # last time current profile was detected

        # Parse profile configs
        for name, cfg in profiles_config.get("profiles", {}).items():
            try:
                profile_type = ProfileType(name)
            except ValueError:
                logger.warning(f"Unknown profile '{name}', skipping")
                continue

            self.profiles[profile_type] = ProfileConfig(
                name=profile_type,
                trigger_processes=[p.lower() for p in cfg.get("trigger_processes", [])],
                always_keep=[p.lower() for p in cfg.get("always_keep", [])],
                kill_candidates=[p.lower() for p in cfg.get("kill_candidates", [])],
                actions=cfg.get("actions", {}),
            )

        # Ensure idle profile exists
        if ProfileType.IDLE not in self.profiles:
            self.profiles[ProfileType.IDLE] = ProfileConfig(
                name=ProfileType.IDLE,
                trigger_processes=[],
                always_keep=[],
                kill_candidates=[],
                actions={},
            )

    # Hardcoded process indicators — used when profiles.yaml doesn't define trigger_processes
    _INDICATORS: dict[str, dict[str, str]] = {
        "gaming": {
            "steam.exe": "Steam", "steamwebhelper.exe": "Steam",
            "epicgameslauncher.exe": "Epic Games", "origin.exe": "EA Origin",
            "battlenet.exe": "Battle.net", "riotclientservices.exe": "Riot Client",
        },
        "streaming": {
            "obs64.exe": "OBS Studio", "obs32.exe": "OBS Studio",
            "streamlabs obs.exe": "Streamlabs", "xsplit.core.exe": "XSplit",
        },
        "coding": {
            "code.exe": "VS Code", "devenv.exe": "Visual Studio",
            "pycharm64.exe": "PyCharm", "idea64.exe": "IntelliJ",
            "webstorm64.exe": "WebStorm", "rider64.exe": "Rider", "clion64.exe": "CLion",
        },
        "browsing": {
            "chrome.exe": "Chrome", "msedge.exe": "Edge",
            "firefox.exe": "Firefox", "brave.exe": "Brave",
            "opera.exe": "Opera", "vivaldi.exe": "Vivaldi",
        },
        "media": {
            "spotify.exe": "Spotify", "vlc.exe": "VLC",
            "wmplayer.exe": "Windows Media Player", "musicbee.exe": "MusicBee",
            "netflix.exe": "Netflix",
        },
        "comms": {
            "discord.exe": "Discord", "teams.exe": "Teams",
            "slack.exe": "Slack", "zoom.exe": "Zoom", "telegram.exe": "Telegram",
        },
    }

    @property
    def active_profile(self) -> ProfileType:
        return self._active_profile

    @property
    def trigger_app(self) -> str:
        """The application that triggered the current profile."""
        return self._trigger_app

    # Background-only profiles — detected by scanning all processes, not foreground
    _BACKGROUND_PROFILES = {ProfileType.GAMING, ProfileType.STREAMING}

    @staticmethod
    def _get_foreground_process_name() -> str | None:
        """Get the exe name of the foreground (focused) window using Win32 API."""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == 0:
                return None
            return psutil.Process(pid.value).name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return None

    def detect_profile(self, exclude_pids: set[int] | None = None) -> ProfileType:
        """Detect active profile using foreground window + background process scan.

        Strategy:
        1. Check foreground window — the app the user is actually using
        2. For background-only profiles (Gaming, Streaming), scan all processes
        3. Fall back to Idle if nothing matches
        """
        fg_name = self._get_foreground_process_name()

        # Build all indicators into a flat lookup: exe_name -> (ProfileType, display_label)
        fg_lookup: dict[str, tuple[ProfileType, str]] = {}
        for ptype_val, indicators in self._INDICATORS.items():
            ptype = ProfileType(ptype_val)
            if ptype in self._BACKGROUND_PROFILES:
                continue  # Gaming/Streaming don't use foreground detection
            for exe, label in indicators.items():
                fg_lookup[exe] = (ptype, label)
        # Also add YAML-configured triggers for non-background profiles
        for ptype in [ProfileType.CODING, ProfileType.BROWSING, ProfileType.MEDIA, ProfileType.COMMS]:
            profile = self.profiles.get(ptype)
            if profile:
                for trigger in profile.trigger_processes:
                    if trigger not in fg_lookup:
                        fg_lookup[trigger] = (ptype, trigger)

        # 1. Foreground window match — highest priority (what you're actually using)
        detected_profile: ProfileType | None = None
        detected_trigger: str = ""

        if fg_name and fg_name in fg_lookup:
            detected_profile, detected_trigger = fg_lookup[fg_name]
        elif fg_name is None and self._active_profile != ProfileType.IDLE:
            # Foreground window unavailable (tooltip, notification, lock screen, etc.)
            # Treat as "no change" — don't let a transient None reading pull us to idle
            return self._active_profile

        # 2. Background-only profiles — scan running processes
        if detected_profile is None:
            running = set()
            for proc in psutil.process_iter(["name"]):
                try:
                    name = proc.info["name"]
                    if name:
                        running.add(name.lower())
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            for ptype in [ProfileType.GAMING, ProfileType.STREAMING]:
                # YAML triggers
                profile = self.profiles.get(ptype)
                if profile and profile.trigger_processes:
                    for trigger in profile.trigger_processes:
                        if trigger in running:
                            detected_profile = ptype
                            detected_trigger = trigger
                            break
                if detected_profile:
                    break
                # Hardcoded indicators
                indicators = self._INDICATORS.get(ptype.value, {})
                min_matches = 2 if ptype == ProfileType.GAMING else 1
                matches = {exe: label for exe, label in indicators.items() if exe in running}
                if len(matches) >= min_matches:
                    detected_profile = ptype
                    detected_trigger = next(iter(matches.values()))
                    break

        # 3. Default to idle
        if detected_profile is None:
            detected_profile = ProfileType.IDLE
            detected_trigger = "No activity detected"

        # Apply debounce — only switch after the new profile is stable
        return self._debounced_switch(detected_profile, detected_trigger)

    def _debounced_switch(self, detected: ProfileType, trigger: str) -> ProfileType:
        """Asymmetric debounce — easy to enter, hard to leave.

        A new profile must be detected for ENTRY_DEBOUNCE_SECONDS (10s) AND
        the current profile must not have been seen for EXIT_DEBOUNCE_SECONDS
        (20-60s depending on profile type) before we commit the switch.

        This prevents rapid thrashing when alt-tabbing (e.g. VS Code ↔ Firefox).
        """
        now = time.monotonic()

        # Same as current profile — reset pending state, refresh "last seen"
        if detected == self._active_profile:
            self._pending_profile = None
            self._current_last_seen = now
            self._trigger_app = trigger
            return self._active_profile

        # New candidate detected — start or continue entry timer
        if detected != self._pending_profile:
            self._pending_profile = detected
            self._pending_trigger = trigger
            self._pending_since = now
            logger.debug(f"Profile candidate: {detected.value} (entry debounce {self.ENTRY_DEBOUNCE_SECONDS}s)")
            return self._active_profile

        # Same candidate as pending — check both entry AND exit conditions
        entry_elapsed = now - self._pending_since
        exit_elapsed = now - self._current_last_seen
        exit_delay = self.EXIT_DEBOUNCE_SECONDS.get(self._active_profile.value, 30)

        if entry_elapsed >= self.ENTRY_DEBOUNCE_SECONDS and exit_elapsed >= exit_delay:
            logger.info(
                f"Profile switched: {self._active_profile.value} → {detected.value} "
                f"(stable for {entry_elapsed:.0f}s, old profile unseen for {exit_elapsed:.0f}s)"
            )
            self._active_profile = detected
            self._trigger_app = self._pending_trigger
            self._pending_profile = None
            self._current_last_seen = now
            return self._active_profile

        # Still debouncing
        return self._active_profile

    def get_active_config(self) -> ProfileConfig:
        return self.profiles[self._active_profile]

    def classify_process(self, process_name: str) -> str:
        """
        Classify a process against the active profile.
        Returns: "whitelist", "blacklist", "keep", "kill", or "unknown"
        """
        name = process_name.lower()

        # Global rules first
        if name in self.global_whitelist:
            return "whitelist"
        if name in self.global_blacklist:
            return "blacklist"

        # Profile-specific rules
        profile = self.get_active_config()
        if name in profile.always_keep:
            return "keep"
        if name in profile.kill_candidates:
            return "kill"

        return "unknown"

    def get_kill_candidates(self) -> list[str]:
        """Get the list of processes to kill in the current profile."""
        return list(self.get_active_config().kill_candidates)

    def get_profile_actions(self) -> dict:
        """Get the actions to perform for the current profile."""
        return self.get_active_config().actions

    def should_process_be_protected(self, process_name: str) -> bool:
        """Check if a process should never be killed."""
        name = process_name.lower()
        if name in self.global_whitelist:
            return True
        profile = self.get_active_config()
        return name in profile.always_keep
