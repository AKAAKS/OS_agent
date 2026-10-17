"""Render the live dashboard with representative demo data and export an SVG.

This reuses the real dashboard panel builders (same layout, colours, bars) but
feeds them sanitized sample data, so the showcase image contains no personal
device names, processes, or paths. Output: docs/dashboard-demo.svg.

Run from the repo root:
    python scripts/demo_dashboard.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402  (path set above)
from rich.console import Console  # noqa: E402


# --- sanitized sample data ------------------------------------------------

def _sample_processes(_n: int = 8):
    return [
        {"name": "chrome.exe", "pid": 7421, "cpu": 38.4, "mem_mb": 2860},
        {"name": "Code.exe", "pid": 10233, "cpu": 21.7, "mem_mb": 1740},
        {"name": "python.exe", "pid": 15880, "cpu": 12.1, "mem_mb": 410},
        {"name": "Discord.exe", "pid": 9044, "cpu": 6.3, "mem_mb": 690},
        {"name": "Spotify.exe", "pid": 8120, "cpu": 3.9, "mem_mb": 520},
        {"name": "dwm.exe", "pid": 1356, "cpu": 2.4, "mem_mb": 240},
        {"name": "pythonw.exe", "pid": 15764, "cpu": 1.8, "mem_mb": 180},
        {"name": "explorer.exe", "pid": 6012, "cpu": 0.7, "mem_mb": 330},
    ]


class _FakeTracker:
    def __init__(self, entries):
        self._entries = entries

    def update(self, force: bool = False):
        return self._entries

    def time_until_next(self) -> int:
        return 423  # 7m 3s


def _sample_diagnoses():
    E = dashboard.DiagnosisEntry
    return [
        E("2026-10-17 14:32:07", "High CPU on chrome.exe", "Runaway render process",
          "Lower priority to free the core", "Set process priority low", "0.92", "low"),
        E("2026-10-17 14:21:50", "RAM pressure at 86%", "Fragmented browser memory",
          "Trim working sets", "Trim working sets", "0.88", "low"),
        E("2026-10-17 13:58:12", "Frequent Bluetooth disconnects", "Adapter power saving",
          "Disable selective suspend", "Disable BT power mgmt", "0.80", "medium"),
        E("2026-10-17 11:04:33", "Unknown high-CPU process", "Unclassified binary",
          "Escalate to human approval", "Ask user (HITL gate)", "0.61", "high"),
    ]


class _FakeProc:
    def __init__(self, pid, cmdline):
        self.pid = pid
        self.info = {"pid": pid, "name": "pythonw.exe", "cmdline": cmdline}


def _install_demo_data() -> None:
    dashboard.get_cpu = lambda: (96.0, [])
    dashboard.get_ram = lambda: (82.0, 13.1, 16.0)
    dashboard.get_disk = lambda: 74.0
    dashboard.get_gpu = lambda: (45.0, 1536.0)
    dashboard.get_top_processes = _sample_processes
    dashboard.detect_profile = lambda: ("Coding", "Code.exe")
    dashboard.get_power_plan = lambda: "Balanced"
    dashboard.get_battery = lambda: ("On AC", None)
    dashboard.get_connected_bt_devices = lambda: [
        {"name": "Headphones"}, {"name": "Wireless Mouse"}, {"name": "Phone"},
    ]
    dashboard._diag_tracker = _FakeTracker(_sample_diagnoses())
    # Status panel probes psutil directly — keep it deterministic + clean.
    dashboard.psutil.process_iter = lambda *a, **k: [_FakeProc(15880, ["python", "main.py"])]
    dashboard.psutil.cpu_count = lambda logical=True: 16


def main() -> None:
    _install_demo_data()

    # Record into an in-memory buffer so we never write the block-bar glyphs to
    # a legacy Windows console (cp1252 can't encode them); save_svg still works.
    console = Console(record=True, width=118, height=40,
                      file=io.StringIO(), legacy_windows=False)
    layout = dashboard.build_layout()
    dashboard.render(layout)
    console.print(layout)

    out = ROOT / "docs" / "dashboard-demo.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(out), title="OS Agent — Live Dashboard (demo data)")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
