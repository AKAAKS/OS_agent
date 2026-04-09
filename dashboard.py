"""
OS Agent — Live Terminal Dashboard
Standalone viewer: shows system metrics, top processes, and agent log in real-time.
Run separately from the agent — does not interfere with the background service.

Usage:  python dashboard.py
"""

import json
import os
import re
import sys
import time
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psutil
import yaml
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.profiles.manager import ProfileManager

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AGENT_DIR = Path(__file__).parent
LOG_FILE = AGENT_DIR / "logs" / "agent.log"
REFRESH_INTERVAL = 2  # seconds
DIAGNOSIS_INTERVAL = 600  # 10 minutes in seconds

# Load profiles config and create shared ProfileManager
with open(AGENT_DIR / "config" / "profiles.yaml") as _f:
    _profiles_config = yaml.safe_load(_f)
_profile_manager = ProfileManager(
    _profiles_config,
    global_whitelist=_profiles_config.get("global_whitelist", []),
    global_blacklist=_profiles_config.get("global_blacklist", []),
)

# Hide subprocess windows
_SUBPROCESS_FLAGS = (
    subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, "CREATE_NO_WINDOW")
    else 0
)

# ---------------------------------------------------------------------------
# Diagnosis snapshot state
# ---------------------------------------------------------------------------

@dataclass
class DiagnosisEntry:
    timestamp: str
    issue: str
    root_cause: str
    diagnosis: str
    fix: str
    confidence: str
    risk_level: str


class DiagnosisTracker:
    """Parses agent log for BT/system diagnoses, snapshots every 10 min.
    High-risk issues bypass the 10-minute window."""

    def __init__(self):
        self.entries: list[DiagnosisEntry] = []
        self.last_snapshot_time: float = 0
        self._last_log_pos: int = 0
        self._last_raw_entries: list[DiagnosisEntry] = []

    def update(self, force: bool = False) -> list[DiagnosisEntry]:
        now = time.time()
        new_entries = self._parse_new_log_lines()

        # Check for high-risk — immediate update
        high_risk = [e for e in new_entries if e.risk_level.lower() == "high"]
        if high_risk:
            self.entries = high_risk + self._last_raw_entries
            self.entries = self.entries[:8]  # cap display
            self.last_snapshot_time = now
            self._last_raw_entries = list(self.entries)
            return self.entries

        # Accumulate
        self._last_raw_entries = new_entries + self._last_raw_entries

        # 10-minute window
        if force or (now - self.last_snapshot_time >= DIAGNOSIS_INTERVAL):
            if self._last_raw_entries:
                # Deduplicate by issue text
                seen = set()
                deduped = []
                for e in self._last_raw_entries:
                    key = e.issue
                    if key not in seen:
                        seen.add(key)
                        deduped.append(e)
                self.entries = deduped[:8]
            self.last_snapshot_time = now

        return self.entries

    def _parse_new_log_lines(self) -> list[DiagnosisEntry]:
        """Read new lines from agent.log and extract diagnosis info."""
        if not LOG_FILE.exists():
            return []

        entries = []
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                size = f.tell()
                # On first read, grab last 16KB; after that, read from last position
                if self._last_log_pos == 0:
                    f.seek(max(0, size - 16384))
                elif self._last_log_pos < size:
                    f.seek(self._last_log_pos)
                else:
                    self._last_log_pos = size
                    return []

                content = f.read()
                self._last_log_pos = f.tell()

            for line in content.splitlines():
                entry = self._parse_diagnosis_line(line)
                if entry:
                    entries.append(entry)
        except Exception:
            pass

        return entries

    def _parse_diagnosis_line(self, line: str) -> DiagnosisEntry | None:
        """Extract diagnosis info from a log line."""
        ts_match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        ts = ts_match.group(1) if ts_match else ""

        # BT diagnosis lines: "BT issue: X → Fix: Y"
        bt_match = re.search(r"BT issue: (.+?) → Fix: (.+)", line)
        if bt_match:
            return DiagnosisEntry(
                timestamp=ts,
                issue=bt_match.group(1).strip(),
                root_cause="Bluetooth",
                diagnosis="BT pattern match",
                fix=bt_match.group(2).strip(),
                confidence="75%",
                risk_level="Medium",
            )

        # BT LLM diagnosis
        if "BT LLM diagnosis:" in line:
            content = line.split("BT LLM diagnosis:", 1)[1].strip()
            # LLM response is the actual fix recommendation
            return DiagnosisEntry(
                timestamp=ts,
                issue="Bluetooth (LLM)",
                root_cause="Unknown BT pattern",
                diagnosis="LLM analysis",
                fix=content[:60],
                confidence="LLM",
                risk_level="Medium",
            )

        # Anomaly lines from observer
        if "anomaly" in line.lower() or "alert" in line.lower():
            # Extract what we can
            content = line.split("] ", 2)[-1] if "] " in line else line
            risk = "High" if any(kw in line.lower() for kw in ["critical", "crash", "leak"]) else "Low"
            return DiagnosisEntry(
                timestamp=ts,
                issue=content[:60],
                root_cause="System monitor",
                diagnosis="Anomaly detected",
                fix="See agent actions",
                confidence="—",
                risk_level=risk,
            )

        # Decision/action lines
        if "decision" in line.lower() or "action:" in line.lower():
            content = line.split("] ", 2)[-1] if "] " in line else line
            return DiagnosisEntry(
                timestamp=ts,
                issue=content[:60],
                root_cause="Agent",
                diagnosis="Action taken",
                fix="—",
                confidence="—",
                risk_level="Low",
            )

        return None

    def time_until_next(self) -> int:
        """Seconds until next snapshot."""
        elapsed = time.time() - self.last_snapshot_time
        remaining = max(0, DIAGNOSIS_INTERVAL - elapsed)
        return int(remaining)


# Global tracker instance
_diag_tracker = DiagnosisTracker()


# ---------------------------------------------------------------------------
# Data collectors
# ---------------------------------------------------------------------------

def get_cpu() -> tuple[float, list[float]]:
    overall = psutil.cpu_percent(interval=0)
    per_core = psutil.cpu_percent(interval=0, percpu=True)
    return overall, per_core


def get_ram() -> tuple[float, float, float]:
    mem = psutil.virtual_memory()
    return mem.percent, mem.used / (1024 ** 3), mem.total / (1024 ** 3)


def get_disk() -> float:
    return psutil.disk_usage("C:\\").percent


def get_gpu() -> tuple[float | None, float | None]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
            creationflags=_SUBPROCESS_FLAGS,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            return float(parts[0].strip()), float(parts[1].strip())
    except Exception:
        pass
    return None, None


def get_battery() -> tuple[str, float | None]:
    batt = psutil.sensors_battery()
    if batt is None:
        return "Desktop (no battery)", None
    status = "Plugged in" if batt.power_plugged else "On battery"
    return status, batt.percent


def get_top_processes(n: int = 8) -> list[dict]:
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
        try:
            info = p.info
            # Skip System Idle Process (PID 0) — always shows inflated CPU%
            if info["pid"] == 0:
                continue
            mem_mb = (info["memory_info"].rss / (1024 ** 2)) if info.get("memory_info") else 0
            procs.append({
                "pid": info["pid"],
                "name": info["name"] or "?",
                "cpu": info.get("cpu_percent", 0) or 0,
                "mem_mb": mem_mb,
                "status": info.get("status", "?"),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda p: p["cpu"], reverse=True)
    return procs[:n]


def get_connected_bt_devices() -> list[dict]:
    """Get only actively connected Bluetooth devices using IsPresent property.
    This matches what Windows Settings > Bluetooth shows."""

    # PowerShell script that checks DEVPKEY_Device_IsPresent for each BT device
    # IsPresent=True means actively connected, False means paired-but-disconnected
    ps_script = (
        "$devices = Get-PnpDevice -Class Bluetooth | "
        "Where-Object { $_.FriendlyName -notmatch "
        "'adapter|radio|host|Microsoft|RFCOMM|Generic|Service|LE Generic|Device Information' }; "
        "$result = @(); "
        "foreach ($d in $devices) { "
        "$present = (Get-PnpDeviceProperty -InstanceId $d.InstanceId "
        "-KeyName '{83DA6326-97A6-4088-9453-A1923F573B29} 15' "
        "-ErrorAction SilentlyContinue).Data; "
        "if ($present -eq $true) { "
        "$result += [PSCustomObject]@{ Name = $d.FriendlyName } "
        "} }; "
        "$result | ConvertTo-Json"
    )

    devices = []
    try:
        result = subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, text=True, timeout=15,
            creationflags=_SUBPROCESS_FLAGS,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                name = item.get("Name", "")
                if not name:
                    continue
                # Skip Avrcp Transport / Hands-Free service entries
                if any(kw in name.lower() for kw in [
                    "avrcp", "hands-free", "handsfree", "a2dp",
                    "transport", "serial", "phonebook",
                ]):
                    continue
                devices.append({"name": name})
    except Exception:
        pass

    return devices


def detect_profile() -> tuple[str, str]:
    """Detect current profile using foreground window detection.
    Returns (profile_name, trigger_app)."""
    _profile_manager.detect_profile()
    profile_name = _profile_manager.active_profile.value.capitalize()
    trigger_app = _profile_manager.trigger_app
    return profile_name, trigger_app


def get_power_plan() -> str:
    """Get the active Windows power plan name."""
    try:
        result = subprocess.run(
            ["powercfg", "/getactivescheme"],
            capture_output=True, text=True, timeout=5,
            creationflags=_SUBPROCESS_FLAGS,
        )
        if result.returncode == 0:
            # Output format: "Power Scheme GUID: <guid>  (Plan Name)"
            match = re.search(r"\((.+)\)", result.stdout)
            if match:
                return match.group(1)
    except Exception:
        pass
    return "Unknown"


# ---------------------------------------------------------------------------
# UI builders
# ---------------------------------------------------------------------------

def _bar_color(val: float) -> str:
    if val >= 85:
        return "red"
    if val >= 60:
        return "yellow"
    return "green"


def build_metrics_panel() -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(width=6)
    table.add_column(width=30)
    table.add_column(width=8, justify="right")

    # CPU
    cpu_overall, cpu_cores = get_cpu()
    color = _bar_color(cpu_overall)
    bar = f"[{color}]{'█' * int(cpu_overall / 4)}{'░' * (25 - int(cpu_overall / 4))}[/]"
    table.add_row("CPU", bar, f"[{color}]{cpu_overall:.0f}%[/]")

    # RAM
    ram_pct, ram_used, ram_total = get_ram()
    color = _bar_color(ram_pct)
    bar = f"[{color}]{'█' * int(ram_pct / 4)}{'░' * (25 - int(ram_pct / 4))}[/]"
    table.add_row("RAM", bar, f"[{color}]{ram_pct:.0f}%[/]")
    table.add_row("", f"[dim]{ram_used:.1f} / {ram_total:.1f} GB[/]", "")

    # Disk
    disk_pct = get_disk()
    color = _bar_color(disk_pct)
    bar = f"[{color}]{'█' * int(disk_pct / 4)}{'░' * (25 - int(disk_pct / 4))}[/]"
    table.add_row("Disk", bar, f"[{color}]{disk_pct:.0f}%[/]")

    # GPU
    gpu_util, gpu_mem = get_gpu()
    if gpu_util is not None:
        color = _bar_color(gpu_util)
        bar = f"[{color}]{'█' * int(gpu_util / 4)}{'░' * (25 - int(gpu_util / 4))}[/]"
        table.add_row("GPU", bar, f"[{color}]{gpu_util:.0f}%[/]")
        if gpu_mem is not None:
            table.add_row("", f"[dim]VRAM: {gpu_mem:.0f} MB[/]", "")
    else:
        table.add_row("GPU", "[dim]N/A[/]", "")

    return Panel(table, title="[bold #88aaff]System Metrics[/]", border_style="bright_black")


def build_processes_panel() -> Panel:
    table = Table(show_header=True, header_style="dim", box=None, padding=(0, 1))
    table.add_column("Process", width=22, no_wrap=True)
    table.add_column("PID", width=7, justify="right")
    table.add_column("CPU%", width=6, justify="right")
    table.add_column("RAM MB", width=8, justify="right")

    for p in get_top_processes(8):
        name = p["name"]
        if len(name) > 20:
            name = name[:18] + ".."
        cpu_color = _bar_color(p["cpu"])
        table.add_row(
            name,
            str(p["pid"]),
            f"[{cpu_color}]{p['cpu']:.1f}[/]",
            f"{p['mem_mb']:.0f}",
        )

    return Panel(table, title="[bold #88aaff]Top Processes[/]", border_style="bright_black")


def build_status_panel() -> Panel:
    lines = []

    # Profile with trigger app
    profile, trigger = detect_profile()
    profile_colors = {
        "Gaming": "red", "Coding": "green", "Streaming": "magenta",
        "Browsing": "cyan", "Media": "yellow", "Comms": "blue", "Idle": "dim",
    }
    pc = profile_colors.get(profile, "white")
    lines.append(f"Profile:  [{pc}]● {profile}[/]")
    lines.append(f"          [dim]↳ {trigger}[/]")

    # Power plan
    power_plan = get_power_plan()
    plan_colors = {
        "High performance": "red",
        "Balanced": "yellow",
        "Power saver": "green",
    }
    plan_color = plan_colors.get(power_plan, "cyan")
    lines.append(f"Power:    [{plan_color}]⚡ {power_plan}[/]")
    lines.append("")

    # Agent task status
    agent_running = False
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = p.info.get("cmdline") or []
            if any("main.py" in str(c) for c in cmdline) and p.pid != os.getpid():
                agent_running = True
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if agent_running:
        lines.append("Agent:    [bold green]● Running[/]")
    else:
        lines.append("Agent:    [bold red]● Stopped[/]")

    # Battery
    batt_status, batt_pct = get_battery()
    if batt_pct is not None:
        color = "green" if batt_pct > 30 else ("yellow" if batt_pct > 15 else "red")
        lines.append(f"Battery:  [{color}]{batt_status} ({batt_pct:.0f}%)[/]")
    else:
        lines.append(f"Battery:  [dim]{batt_status}[/]")

    # Polling mode
    on_battery = batt_pct is not None and "On battery" in batt_status
    interval = 30 if on_battery else 5
    mode = "Battery (slow)" if on_battery else "AC (normal)"
    lines.append(f"Polling:  [cyan]{mode}[/] — every {interval}s")

    # CPU cores
    core_count = psutil.cpu_count(logical=True)
    lines.append(f"Cores:    [cyan]{core_count}[/] logical")

    return Panel("\n".join(lines), title="[bold #88aaff]Status[/]", border_style="bright_black")


def build_bt_panel() -> Panel:
    devices = get_connected_bt_devices()

    if devices:
        lines = [f"  [green]●[/] {d['name']}" for d in devices]
        content = "\n".join(lines)
    else:
        content = "[dim]  No connected devices[/]"

    return Panel(content, title="[bold #88aaff]Bluetooth[/]", border_style="bright_black")


def build_diagnosis_panel() -> Panel:
    entries = _diag_tracker.update()
    next_in = _diag_tracker.time_until_next()
    mins = next_in // 60
    secs = next_in % 60

    if not entries:
        content = "[dim]  No issues detected\n\n  Waiting for agent data...[/]"
        subtitle = f"[dim]next snapshot in {mins}m {secs}s[/]"
        return Panel(content, title="[bold #88aaff]Agent Diagnosis[/]",
                     subtitle=subtitle, border_style="bright_black")

    table = Table(show_header=True, header_style="dim", box=None, padding=(0, 1))
    table.add_column("Time", width=8, no_wrap=True)
    table.add_column("Issue", width=28, no_wrap=True)
    table.add_column("Fix", width=24, no_wrap=True)
    table.add_column("Conf", width=5, justify="right")
    table.add_column("Risk", width=6, justify="center")

    for e in entries[:6]:
        ts = e.timestamp.split(" ", 1)[1] if " " in e.timestamp else e.timestamp
        ts = ts[:8]  # HH:MM:SS

        issue = e.issue
        if len(issue) > 26:
            issue = issue[:24] + ".."

        fix = e.fix
        if len(fix) > 22:
            fix = fix[:20] + ".."

        risk_color = {"high": "red", "medium": "yellow", "low": "green"}.get(
            e.risk_level.lower(), "white"
        )

        table.add_row(
            f"[dim]{ts}[/]",
            issue,
            f"[dim]{fix}[/]",
            e.confidence,
            f"[{risk_color}]{e.risk_level}[/]",
        )

    subtitle = f"[dim]next snapshot in {mins}m {secs}s  |  high-risk = immediate[/]"
    return Panel(table, title="[bold #88aaff]Agent Diagnosis[/]",
                 subtitle=subtitle, border_style="bright_black")


def build_layout() -> Layout:
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="diagnosis", size=12),
    )

    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right", size=50),
    )

    layout["left"].split_column(
        Layout(name="metrics"),
        Layout(name="processes"),
    )

    layout["right"].split_column(
        Layout(name="status"),
        Layout(name="bluetooth", size=8),
    )

    return layout


def render(layout: Layout):
    # Header
    header_text = Text(" OS Agent — Live Dashboard", style="bold #88aaff")
    header_text.append("   Press Ctrl+C to exit", style="dim")
    layout["header"].update(Panel(header_text, border_style="bright_black"))

    # Panels
    layout["metrics"].update(build_metrics_panel())
    layout["processes"].update(build_processes_panel())
    layout["status"].update(build_status_panel())
    layout["bluetooth"].update(build_bt_panel())
    layout["diagnosis"].update(build_diagnosis_panel())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    console = Console()
    layout = build_layout()

    # Initial CPU reading to prime psutil (first call always returns 0)
    psutil.cpu_percent(interval=0.1, percpu=True)

    # Force first diagnosis snapshot immediately
    _diag_tracker.update(force=True)

    console.clear()
    try:
        with Live(layout, console=console, refresh_per_second=1, screen=True) as live:
            while True:
                render(layout)
                time.sleep(REFRESH_INTERVAL)
    except KeyboardInterrupt:
        console.clear()
        console.print("[dim]Dashboard closed.[/]")


if __name__ == "__main__":
    main()
