"""
Startup optimizer — analyzes Windows startup items and scores them by impact.
Queries registry Run keys and Task Scheduler for boot/logon tasks.
Read-only analysis — does not modify anything automatically.
"""

import logging
import subprocess
import winreg
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

# Known low-impact Microsoft processes — always recommend keeping
_TRUSTED_PUBLISHERS = frozenset({
    "microsoft corporation", "microsoft windows", "microsoft",
})

# Known high-impact startup items (common bloatware / resource hogs)
_KNOWN_HEAVY = {
    "onedrive": 75,
    "teams": 70,
    "spotify": 65,
    "discord": 60,
    "skype": 65,
    "adobe creative cloud": 70,
    "steam": 60,
    "epicgameslauncher": 60,
    "cortana": 55,
    "yourphone": 50,
    "gamingservices": 40,
}


@dataclass
class StartupItem:
    name: str
    path: str
    source: str  # "registry_hkcu", "registry_hklm", "task_scheduler"
    publisher: str
    impact_score: float  # 0-100 (higher = more impact / worth disabling)
    recommendation: str  # "keep", "disable", "investigate"


class StartupOptimizer:
    """Analyzes startup items and provides optimization recommendations."""

    def analyze(self) -> list[StartupItem]:
        """Query startup items and return them sorted by impact score (highest first)."""
        items: list[StartupItem] = []
        items.extend(self._query_registry_run(winreg.HKEY_CURRENT_USER, "registry_hkcu"))
        items.extend(self._query_registry_run(winreg.HKEY_LOCAL_MACHINE, "registry_hklm"))
        items.extend(self._query_task_scheduler())

        # Deduplicate by name (case-insensitive)
        seen: set[str] = set()
        unique: list[StartupItem] = []
        for item in items:
            key = item.name.lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)

        unique.sort(key=lambda x: x.impact_score, reverse=True)
        return unique

    def _query_registry_run(self, hive, source_label: str) -> list[StartupItem]:
        """Read startup entries from a registry Run key."""
        items = []
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"

        try:
            with winreg.OpenKey(hive, key_path) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                        score, rec = self._score_item(name, value)
                        items.append(StartupItem(
                            name=name,
                            path=value,
                            source=source_label,
                            publisher=self._guess_publisher(value),
                            impact_score=score,
                            recommendation=rec,
                        ))
                        i += 1
                    except OSError:
                        break
        except OSError:
            pass  # Key doesn't exist or access denied

        return items

    def _query_task_scheduler(self) -> list[StartupItem]:
        """Query Task Scheduler for boot/logon startup tasks."""
        items = []
        try:
            result = subprocess.run(
                ["schtasks", "/query", "/fo", "CSV", "/v"],
                capture_output=True, text=True, timeout=15,
                creationflags=_SUBPROCESS_FLAGS,
            )
            if result.returncode != 0:
                return items

            lines = result.stdout.strip().splitlines()
            if len(lines) < 2:
                return items

            # Parse CSV header
            header = lines[0].replace('"', '').split(",")
            try:
                name_idx = header.index("TaskName")
                trigger_idx = header.index("Trigger")
                cmd_idx = header.index("Task To Run")
                author_idx = header.index("Author")
                status_idx = header.index("Status") if "Status" in header else -1
            except ValueError:
                return items

            for line in lines[1:]:
                parts = line.replace('"', '').split(",")
                if len(parts) <= max(name_idx, trigger_idx, cmd_idx, author_idx):
                    continue

                trigger = parts[trigger_idx].lower()
                if "logon" not in trigger and "startup" not in trigger and "boot" not in trigger:
                    continue

                # Skip disabled tasks
                if status_idx >= 0 and len(parts) > status_idx:
                    if parts[status_idx].lower() == "disabled":
                        continue

                name = parts[name_idx].split("\\")[-1]  # strip path prefix
                cmd = parts[cmd_idx]
                author = parts[author_idx]

                score, rec = self._score_item(name, cmd)
                items.append(StartupItem(
                    name=name,
                    path=cmd,
                    source="task_scheduler",
                    publisher=author,
                    impact_score=score,
                    recommendation=rec,
                ))

        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.debug(f"Task scheduler query failed: {e}")

        return items

    def _score_item(self, name: str, path: str) -> tuple[float, str]:
        """Score a startup item by impact (0-100) and recommend action."""
        name_lower = name.lower()
        path_lower = path.lower()

        # Check known heavy items
        for keyword, score in _KNOWN_HEAVY.items():
            if keyword in name_lower or keyword in path_lower:
                return score, "disable"

        # Microsoft items are generally necessary
        if "microsoft" in path_lower or "windows" in path_lower:
            return 10, "keep"

        # Security software — keep
        if any(kw in name_lower or kw in path_lower for kw in [
            "security", "antivirus", "defender", "malware", "firewall",
        ]):
            return 5, "keep"

        # Hardware drivers/utilities — usually keep
        if any(kw in name_lower or kw in path_lower for kw in [
            "nvidia", "amd", "intel", "realtek", "corsair", "logitech", "razer",
        ]):
            return 30, "investigate"

        # Unknown — default medium impact
        return 50, "investigate"

    @staticmethod
    def _guess_publisher(path: str) -> str:
        """Guess publisher from the executable path."""
        path_lower = path.lower()
        if "microsoft" in path_lower:
            return "Microsoft"
        if "google" in path_lower:
            return "Google"
        if "adobe" in path_lower:
            return "Adobe"
        if "nvidia" in path_lower:
            return "NVIDIA"
        if "steam" in path_lower:
            return "Valve"
        return "Unknown"
