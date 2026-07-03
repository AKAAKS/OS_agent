"""
Agent action tools — safe system operations the agent can perform.
All destructive actions require HITL approval before execution.
"""

import logging
import os
import shlex
import signal
import subprocess
import time

import psutil

from agentkernel.sanitize import CommandSanitizer

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

logger = logging.getLogger(__name__)

# Allowlisted executables that run_command may invoke.
# Anything not on this list is blocked before reaching the shell.
_ALLOWED_EXECUTABLES = frozenset({
    # Windows built-ins / utilities.
    # NOTE: raw shell interpreters (powershell.exe, cmd.exe) are intentionally
    # NOT allowlisted — allowlisting a Turing-complete interpreter defeats the
    # allowlist entirely (e.g. `powershell -Command "Remove-Item -Recurse ..."`
    # would sail through a substring denylist). Boot/partition tools (bcdedit,
    # diskpart) are excluded for their unrecoverable blast radius. Structured
    # actions (kill_process, restart_service, set_power_plan) cover real needs.
    # State-mutating admin tools (sc, net, wmic, taskkill) are also excluded:
    # process kills go through kill_process, service control through
    # restart_service, and there is no autonomous need to create/delete
    # services or spawn processes from a free-text command. What remains is a
    # read-only diagnostics set.
    "tasklist", "tasklist.exe",
    "systeminfo", "systeminfo.exe",
    "ipconfig", "ipconfig.exe",
    "netstat", "netstat.exe",
    "sfc", "sfc.exe",
    "dism", "dism.exe",
    "chkdsk", "chkdsk.exe",
    # Power management
    "powercfg", "powercfg.exe",
    # Common diagnostic tools
    "nvidia-smi", "nvidia-smi.exe",
    "ping", "ping.exe",
    "tracert", "tracert.exe",
    "nslookup", "nslookup.exe",
})

_SANITIZER = CommandSanitizer(_ALLOWED_EXECUTABLES)


def _sanitize_command(command: str) -> str | None:
    """Validate a command string before execution.

    Kernel sanitizer bound to this module's read-only diagnostics allowlist.
    Returns the command unchanged if it passes all checks, or None if blocked.
    """
    return _SANITIZER.sanitize(command)


class ActionResult:
    def __init__(self, success: bool, message: str, details: dict | None = None):
        self.success = success
        self.message = message
        self.details = details or {}


class AgentTools:
    """Collection of system actions the agent can take."""

    # PIDs and process names that must never be killed
    _PROTECTED_PIDS = frozenset({0, 4})  # System Idle, System
    _PROTECTED_NAMES = frozenset({
        "csrss.exe", "wininit.exe", "winlogon.exe", "smss.exe",
        "services.exe", "lsass.exe", "svchost.exe", "dwm.exe",
        "explorer.exe", "system",
    })

    # User-facing apps that should never be auto-killed — they hold unsaved
    # state (tabs, documents, sessions) and closing them disrupts the user.
    _NEVER_KILL_USER_APPS = frozenset({
        "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
        "opera.exe", "vivaldi.exe",                     # browsers
        "winword.exe", "excel.exe", "powerpnt.exe",     # Office
        "onenote.exe", "outlook.exe",
        "notepad++.exe", "code.exe", "devenv.exe",      # editors/IDEs
        "pycharm64.exe", "idea64.exe", "webstorm64.exe",
        "claude.exe",                                    # Claude desktop
    })

    def kill_process(self, pid: int, force: bool = False) -> ActionResult:
        """Kill a process by PID."""
        if pid in self._PROTECTED_PIDS:
            return ActionResult(False, f"Refused to kill system-critical PID {pid}")
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            if name.lower() in self._PROTECTED_NAMES:
                return ActionResult(False, f"Refused to kill system-critical process {name} (PID {pid})")
            if name.lower() in self._NEVER_KILL_USER_APPS:
                return ActionResult(False, f"Refused to kill user app {name} (PID {pid}) — has unsaved state")

            if force:
                proc.kill()
            else:
                proc.terminate()
                proc.wait(timeout=5)

            logger.info(f"Killed process: {name} (PID {pid})")
            return ActionResult(True, f"Successfully killed {name} (PID {pid})")

        except psutil.NoSuchProcess:
            return ActionResult(False, f"Process {pid} no longer exists")
        except psutil.AccessDenied:
            return ActionResult(False, f"Access denied killing PID {pid} — may need admin")
        except psutil.TimeoutExpired:
            if not force:
                return self.kill_process(pid, force=True)
            return ActionResult(False, f"Failed to kill PID {pid} even with force")

    def set_process_priority(self, pid: int, priority: str) -> ActionResult:
        """Set process priority. priority: 'low', 'normal', 'high', 'realtime'."""
        priority_map = {
            "low": psutil.IDLE_PRIORITY_CLASS,
            "below_normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
            "normal": psutil.NORMAL_PRIORITY_CLASS,
            "above_normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
            "high": psutil.HIGH_PRIORITY_CLASS,
            "realtime": psutil.REALTIME_PRIORITY_CLASS,
        }

        if priority not in priority_map:
            return ActionResult(False, f"Invalid priority '{priority}'. Use: {list(priority_map.keys())}")

        try:
            proc = psutil.Process(pid)
            name = proc.name()
            proc.nice(priority_map[priority])
            logger.info(f"Set priority of {name} (PID {pid}) to {priority}")
            return ActionResult(True, f"Set {name} (PID {pid}) priority to {priority}")

        except psutil.NoSuchProcess:
            return ActionResult(False, f"Process {pid} no longer exists")
        except psutil.AccessDenied:
            return ActionResult(False, f"Access denied setting priority for PID {pid}")

    def restart_service(self, service_name: str) -> ActionResult:
        """Restart a Windows service."""
        try:
            stop_result = subprocess.run(
                ["net", "stop", service_name],
                capture_output=True, text=True, timeout=30, creationflags=_SUBPROCESS_FLAGS,
            )
            if stop_result.returncode != 0:
                logger.warning(f"Service stop returned non-zero for '{service_name}': {stop_result.stderr}")

            start_result = subprocess.run(
                ["net", "start", service_name],
                capture_output=True, text=True, timeout=30, creationflags=_SUBPROCESS_FLAGS,
            )

            if start_result.returncode == 0:
                logger.info(f"Restarted service: {service_name}")
                return ActionResult(True, f"Restarted service '{service_name}'")
            else:
                return ActionResult(False, f"Failed to restart '{service_name}': {start_result.stderr}")

        except subprocess.TimeoutExpired:
            return ActionResult(False, f"Timeout restarting service '{service_name}'")

    def create_restore_point(self, description: str = "OS Agent — pre-action checkpoint") -> ActionResult:
        """Best-effort System Restore checkpoint before a hard-to-reverse action.

        Requires admin + System Protection enabled, and Windows rate-limits
        restore points to one per 24h by default, so this often no-ops. Failures
        are non-fatal — the caller decides whether to proceed. Runs powershell
        directly (a tool-internal privileged op), not via the run_command
        allowlist.
        """
        try:
            result = subprocess.run(
                [
                    "powershell", "-NonInteractive", "-Command",
                    f"Checkpoint-Computer -Description '{description}' "
                    "-RestorePointType MODIFY_SETTINGS",
                ],
                capture_output=True, text=True, timeout=90, creationflags=_SUBPROCESS_FLAGS,
            )
            if result.returncode == 0:
                logger.info(f"Created System Restore point: {description}")
                return ActionResult(True, f"Created restore point '{description}'")
            msg = (result.stderr or result.stdout or "unknown error").strip()
            logger.warning(f"Restore point not created: {msg[:160]}")
            return ActionResult(False, f"Restore point not created: {msg[:160]}")
        except subprocess.TimeoutExpired:
            return ActionResult(False, "Restore point creation timed out")
        except Exception as e:
            return ActionResult(False, f"Restore point error: {e}")

    def defer_windows_update(self) -> ActionResult:
        """Pause Windows Update for 7 days."""
        # Registry mutation — capture a restore point first (best-effort).
        self.create_restore_point("OS Agent — before deferring Windows Update")
        try:
            result = subprocess.run(
                [
                    "powershell", "-Command",
                    'Set-ItemProperty -Path "HKLM:\\SOFTWARE\\Microsoft\\WindowsUpdate\\UX\\Settings" '
                    '-Name "PauseUpdatesExpiryTime" '
                    f'-Value "{__import__("datetime").datetime.now().__add__(__import__("datetime").timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")}"',
                ],
                capture_output=True, text=True, timeout=15, creationflags=_SUBPROCESS_FLAGS,
            )
            if result.returncode == 0:
                return ActionResult(True, "Deferred Windows Update for 7 days")
            return ActionResult(False, f"Failed to defer updates: {result.stderr}")
        except Exception as e:
            return ActionResult(False, f"Error deferring updates: {e}")

    def trim_working_sets(self, exclude_pids: list[int] | None = None) -> ActionResult:
        """Trim working sets of idle processes to free RAM."""
        exclude = set(exclude_pids or [])
        trimmed = 0

        for proc in psutil.process_iter(["pid", "name", "status"]):
            try:
                if proc.info["pid"] in exclude:
                    continue
                if proc.info["status"] == "idle" or proc.info["status"] == "sleeping":
                    # EmptyWorkingSet via ctypes
                    import ctypes
                    handle = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, proc.info["pid"])
                    if handle:
                        ctypes.windll.psapi.EmptyWorkingSet(handle)
                        ctypes.windll.kernel32.CloseHandle(handle)
                        trimmed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                continue

        logger.info(f"Trimmed working sets of {trimmed} processes")
        return ActionResult(True, f"Trimmed working sets of {trimmed} idle processes")

    def run_command(self, command: str, timeout: int = 30) -> ActionResult:
        """Run a sanitized shell command (allowlisted executables only, HITL required).

        Commands are validated against an allowlist of safe executables and
        checked for dangerous shell patterns before execution.
        """
        safe_cmd = _sanitize_command(command)
        if safe_cmd is None:
            return ActionResult(
                False,
                f"Command blocked by safety filter: {command[:120]}",
                details={"blocked": True, "original_command": command},
            )

        try:
            # Use shell=False with explicit token splitting for safety
            try:
                tokens = shlex.split(safe_cmd, posix=False)
            except ValueError:
                tokens = safe_cmd.split()

            _MAX_OUTPUT = 1024 * 1024  # 1 MB cap on captured output
            result = subprocess.run(
                tokens, shell=False, capture_output=True, text=True, timeout=timeout, creationflags=_SUBPROCESS_FLAGS,
            )
            # Truncate output to prevent OOM on huge command output
            if result.stdout and len(result.stdout) > _MAX_OUTPUT:
                result = subprocess.CompletedProcess(
                    result.args, result.returncode,
                    stdout=result.stdout[:_MAX_OUTPUT] + "\n... [truncated]",
                    stderr=result.stderr,
                )
            return ActionResult(
                success=result.returncode == 0,
                message=result.stdout or result.stderr or "Command completed",
                details={"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
            )
        except subprocess.TimeoutExpired:
            return ActionResult(False, f"Command timed out after {timeout}s")
        except FileNotFoundError:
            return ActionResult(False, f"Executable not found: {command.split()[0]}")

    def cleanup_temp_files(self, min_age_hours: int = 24) -> ActionResult:
        """Delete old temporary files to free disk space.

        Only deletes files older than min_age_hours to avoid breaking running apps.
        """
        import tempfile
        import shutil
        from pathlib import Path

        temp_dirs = [
            Path(tempfile.gettempdir()),                # User %TEMP%
            Path(r"C:\Windows\Temp"),                   # System temp
        ]

        freed_bytes = 0
        deleted = 0
        errors = 0
        cutoff = time.time() - (min_age_hours * 3600)

        for temp_dir in temp_dirs:
            if not temp_dir.exists():
                continue
            try:
                for item in temp_dir.iterdir():
                    try:
                        stat = item.stat()
                        if stat.st_mtime > cutoff:
                            continue  # Too recent, skip
                        if item.is_file():
                            size = stat.st_size
                            item.unlink()
                            freed_bytes += size
                            deleted += 1
                        elif item.is_dir():
                            size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                            shutil.rmtree(item, ignore_errors=True)
                            freed_bytes += size
                            deleted += 1
                    except (PermissionError, OSError):
                        errors += 1
                        continue
            except PermissionError:
                errors += 1

        freed_mb = round(freed_bytes / (1024 * 1024), 1)
        msg = f"Cleaned {deleted} items, freed {freed_mb} MB"
        if errors:
            msg += f" ({errors} items skipped — in use)"
        logger.info(msg)
        return ActionResult(True, msg, details={"freed_mb": freed_mb, "deleted": deleted})

    def find_large_files(self, path: str = None, min_size_mb: int = 500, top_n: int = 10) -> ActionResult:
        """Find the largest files in common bloat locations.

        Returns an informational report (no deletion). Safe, low risk.
        """
        import os
        from pathlib import Path

        search_dirs = [
            Path(os.environ.get("USERPROFILE", r"C:\Users")) / "Downloads",
            Path(os.environ.get("USERPROFILE", r"C:\Users")) / "Desktop",
            Path(os.environ.get("TEMP", r"C:\Temp")),
        ]

        min_bytes = min_size_mb * 1024 * 1024
        large_files: list[tuple[str, float]] = []

        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            try:
                for item in search_dir.rglob("*"):
                    try:
                        if item.is_file():
                            size = item.stat().st_size
                            if size >= min_bytes:
                                large_files.append((str(item), round(size / (1024 * 1024), 1)))
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                continue

        large_files.sort(key=lambda x: x[1], reverse=True)
        top = large_files[:top_n]

        if top:
            report = "; ".join(f"{name} ({mb}MB)" for name, mb in top)
            msg = f"Found {len(large_files)} files >= {min_size_mb}MB. Top: {report[:200]}"
        else:
            msg = f"No files >= {min_size_mb}MB found in common locations"

        logger.info(msg)
        return ActionResult(True, msg, details={"large_files": top})

    # ---- Power plan management ----

    POWER_PLAN_GUIDS = {
        "high_performance": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
        "balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
        "power_saver": "a1841308-3541-4fab-bc81-f71556f20b4a",
    }

    def set_power_plan(self, plan: str) -> ActionResult:
        """Switch Windows power plan via powercfg."""
        guid = self.POWER_PLAN_GUIDS.get(plan)
        if not guid:
            return ActionResult(False, f"Unknown power plan: {plan}. Use: {list(self.POWER_PLAN_GUIDS.keys())}")
        try:
            result = subprocess.run(
                ["powercfg", "/setactive", guid],
                capture_output=True, text=True, timeout=10,
                creationflags=_SUBPROCESS_FLAGS,
            )
            if result.returncode == 0:
                logger.info(f"Power plan switched to: {plan}")
                return ActionResult(True, f"Switched to {plan} power plan")
            return ActionResult(False, f"powercfg failed: {result.stderr}")
        except Exception as e:
            return ActionResult(False, f"Power plan switch error: {e}")

    def get_process_info(self, pid: int) -> dict | None:
        """Get detailed info about a process for LLM context."""
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():
                parent = proc.parent()
                try:
                    parent_name = parent.name() if parent else "none"
                    parent_pid = parent.pid if parent else None
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    parent_name = "exited"
                    parent_pid = None
                return {
                    "pid": pid,
                    "name": proc.name(),
                    "exe": proc.exe(),
                    "cmdline": " ".join(proc.cmdline()),
                    "cpu_percent": proc.cpu_percent(),
                    "memory_mb": round(proc.memory_info().rss / (1024**2), 1),
                    "status": proc.status(),
                    "parent": parent_name,
                    "parent_pid": parent_pid,
                    "create_time": proc.create_time(),
                    "num_threads": proc.num_threads(),
                    "username": proc.username(),
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
