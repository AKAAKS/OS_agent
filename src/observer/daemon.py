"""
System observer daemon — monitors CPU, RAM, disk, GPU, and processes.
Emits ObservationEvent objects when anomalies are detected.
"""

import subprocess
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable

import psutil

# Hide console windows from subprocess calls on Windows
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

logger = logging.getLogger(__name__)


class AnomalyType(Enum):
    CPU_SPIKE = "cpu_spike"
    RAM_HIGH = "ram_high"
    DISK_PRESSURE = "disk_pressure"
    GPU_HIGH = "gpu_high"
    PROCESS_CRASH = "process_crash"
    MEMORY_LEAK = "memory_leak"
    UNKNOWN_HOG = "unknown_hog"
    THERMAL_THROTTLE = "thermal_throttle"


@dataclass
class ObservationEvent:
    timestamp: datetime
    anomaly_type: AnomalyType
    severity: str  # "low", "medium", "high"
    summary: str
    details: dict = field(default_factory=dict)


@dataclass
class SystemSnapshot:
    timestamp: datetime
    cpu_percent: float
    cpu_per_core: list[float]
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disk_percent: float
    disk_read_bytes: int
    disk_write_bytes: int
    gpu_percent: float | None
    gpu_memory_percent: float | None
    top_processes: list[dict]


class SystemObserver:
    """Continuously monitors system metrics and emits anomaly events."""

    def __init__(self, settings: dict):
        self.settings = settings.get("monitoring", {})
        self.poll_interval = self.settings.get("poll_interval_seconds", 5)
        self.poll_interval_battery = self.settings.get("poll_interval_battery_seconds", 30)
        self.cpu_threshold = self.settings.get("cpu_threshold_percent", 95)
        self.cpu_sustained_secs = self.settings.get("cpu_sustained_seconds", 120)
        self.ram_threshold = self.settings.get("ram_threshold_percent", 85)
        self.disk_threshold = self.settings.get("disk_threshold_percent", 90)
        self.gpu_threshold = self.settings.get("gpu_threshold_percent", 95)
        self.cooldown_minutes = self.settings.get("cooldown_minutes", 10)
        self.pause_on_battery = self.settings.get("pause_on_battery", False)

        self._cpu_high_since: datetime | None = None
        self._running = False
        self._event_callbacks: list[Callable[[ObservationEvent], None]] = []
        self._known_pids: set[int] = set()
        self._cooldowns: dict[str, datetime] = {}  # anomaly_type -> last_emitted

    def on_event(self, callback: Callable[[ObservationEvent], None]):
        self._event_callbacks.append(callback)

    def _is_on_battery(self) -> bool:
        """Check if the system is running on battery power."""
        battery = psutil.sensors_battery()
        if battery is None:
            return False  # Desktop, no battery
        return not battery.power_plugged

    def _is_cooled_down(self, anomaly_type: AnomalyType) -> bool:
        """Check if enough time has passed since last alert of this type."""
        key = anomaly_type.value
        if key not in self._cooldowns:
            return True
        elapsed = (datetime.now() - self._cooldowns[key]).total_seconds()
        return elapsed >= self.cooldown_minutes * 60

    def _emit(self, event: ObservationEvent):
        # Enforce cooldown — suppress duplicate alerts
        if not self._is_cooled_down(event.anomaly_type):
            logger.debug(f"Suppressed (cooldown): {event.anomaly_type.value} — {event.summary}")
            return

        self._cooldowns[event.anomaly_type.value] = datetime.now()
        logger.info(f"Anomaly detected: {event.anomaly_type.value} — {event.summary}")
        for cb in self._event_callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}")

    def take_snapshot(self) -> SystemSnapshot:
        cpu = psutil.cpu_percent(interval=1)
        cpu_per_core = psutil.cpu_percent(interval=0, percpu=True)
        mem = psutil.virtual_memory()
        try:
            disk = psutil.disk_usage("C:\\")
        except OSError as e:
            logger.warning(f"Disk usage check failed: {e}")
            # Stub with safe defaults so snapshot still works
            class _DiskStub:
                percent = 0.0
            disk = _DiskStub()
        disk_io = psutil.disk_io_counters()

        gpu_percent, gpu_mem = self._get_gpu_stats()

        top_procs = self._get_top_processes(n=10)

        return SystemSnapshot(
            timestamp=datetime.now(),
            cpu_percent=cpu,
            cpu_per_core=cpu_per_core,
            ram_percent=mem.percent,
            ram_used_gb=round(mem.used / (1024**3), 2),
            ram_total_gb=round(mem.total / (1024**3), 2),
            disk_percent=disk.percent,
            disk_read_bytes=disk_io.read_bytes if disk_io else 0,
            disk_write_bytes=disk_io.write_bytes if disk_io else 0,
            gpu_percent=gpu_percent,
            gpu_memory_percent=gpu_mem,
            top_processes=top_procs,
        )

    def _get_gpu_stats(self) -> tuple[float | None, float | None]:
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,utilization.memory",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=_SUBPROCESS_FLAGS,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(",")
                return float(parts[0].strip()), float(parts[1].strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            pass
        return None, None

    def _get_top_processes(self, n: int = 10) -> list[dict]:
        procs = []
        for proc in psutil.process_iter(
            ["pid", "name", "cpu_percent", "memory_percent", "memory_info", "status"]
        ):
            try:
                info = proc.info
                procs.append(
                    {
                        "pid": info["pid"],
                        "name": info["name"],
                        "cpu_percent": info["cpu_percent"] or 0.0,
                        "memory_percent": round(info["memory_percent"] or 0.0, 2),
                        "memory_mb": round(
                            (info["memory_info"].rss / (1024**2))
                            if info["memory_info"]
                            else 0,
                            1,
                        ),
                        "status": info["status"],
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        procs.sort(key=lambda p: p["cpu_percent"] + p["memory_percent"], reverse=True)
        return procs[:n]

    def _check_thermal_throttle(self, now: datetime, cpu_percent: float = 0.0) -> ObservationEvent | None:
        """Detect CPU thermal throttling via WMI clock speed query.

        Only flags throttling when the CPU is under significant load (>50%).
        Low clock speeds at low CPU usage are normal power-saving behaviour
        (Intel SpeedStep / AMD Cool'n'Quiet), not thermal throttling.
        """
        # Skip check when CPU is idle — low frequency is just speed-stepping
        if cpu_percent < 50:
            return None

        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "CurrentClockSpeed,MaxClockSpeed", "/format:csv"],
                capture_output=True, text=True, timeout=5,
                creationflags=_SUBPROCESS_FLAGS,
            )
            if result.returncode != 0:
                return None

            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if not line or line.startswith("Node"):
                    continue
                parts = line.split(",")
                if len(parts) >= 3:
                    try:
                        current = int(parts[1])
                        maximum = int(parts[2])
                        if maximum > 0:
                            ratio = current / maximum
                            if ratio < 0.80:  # throttled by 20%+
                                return ObservationEvent(
                                    timestamp=now,
                                    anomaly_type=AnomalyType.THERMAL_THROTTLE,
                                    severity="high" if ratio < 0.60 else "medium",
                                    summary=f"CPU throttled: {current}MHz / {maximum}MHz ({ratio:.0%}) under {cpu_percent:.0f}% load",
                                    details={
                                        "current_mhz": current,
                                        "max_mhz": maximum,
                                        "ratio": round(ratio, 3),
                                        "cpu_percent": cpu_percent,
                                    },
                                )
                    except (ValueError, IndexError):
                        continue
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            pass
        return None

    def check_anomalies(self, snapshot: SystemSnapshot) -> list[ObservationEvent]:
        events = []
        now = snapshot.timestamp

        # CPU sustained spike
        if snapshot.cpu_percent >= self.cpu_threshold:
            if self._cpu_high_since is None:
                self._cpu_high_since = now
            elif (now - self._cpu_high_since).total_seconds() >= self.cpu_sustained_secs:
                top = snapshot.top_processes[:3]
                events.append(
                    ObservationEvent(
                        timestamp=now,
                        anomaly_type=AnomalyType.CPU_SPIKE,
                        severity="high",
                        summary=f"CPU at {snapshot.cpu_percent}% for {self.cpu_sustained_secs}s+",
                        details={
                            "cpu_percent": snapshot.cpu_percent,
                            "top_processes": top,
                            "duration_seconds": (now - self._cpu_high_since).total_seconds(),
                        },
                    )
                )
        else:
            self._cpu_high_since = None

        # RAM pressure
        if snapshot.ram_percent >= self.ram_threshold:
            top_mem = sorted(
                snapshot.top_processes, key=lambda p: p["memory_mb"], reverse=True
            )[:3]
            events.append(
                ObservationEvent(
                    timestamp=now,
                    anomaly_type=AnomalyType.RAM_HIGH,
                    severity="medium" if snapshot.ram_percent < 95 else "high",
                    summary=f"RAM at {snapshot.ram_percent}% ({snapshot.ram_used_gb}/{snapshot.ram_total_gb} GB)",
                    details={
                        "ram_percent": snapshot.ram_percent,
                        "top_memory_processes": top_mem,
                    },
                )
            )

        # Disk pressure
        if snapshot.disk_percent >= self.disk_threshold:
            events.append(
                ObservationEvent(
                    timestamp=now,
                    anomaly_type=AnomalyType.DISK_PRESSURE,
                    severity="medium" if snapshot.disk_percent < 95 else "high",
                    summary=f"Disk C:\\ at {snapshot.disk_percent}% full",
                    details={"disk_percent": snapshot.disk_percent},
                )
            )

        # GPU spike
        if snapshot.gpu_percent is not None and snapshot.gpu_percent >= self.gpu_threshold:
            events.append(
                ObservationEvent(
                    timestamp=now,
                    anomaly_type=AnomalyType.GPU_HIGH,
                    severity="medium",
                    summary=f"GPU at {snapshot.gpu_percent}%",
                    details={
                        "gpu_percent": snapshot.gpu_percent,
                        "gpu_memory_percent": snapshot.gpu_memory_percent,
                    },
                )
            )

        # Thermal throttling detection (only meaningful under load)
        thermal_event = self._check_thermal_throttle(now, cpu_percent=snapshot.cpu_percent)
        if thermal_event:
            events.append(thermal_event)

        # Track process exits (crashes)
        current_pids = {p["pid"] for p in snapshot.top_processes}
        if self._known_pids:
            vanished = self._known_pids - current_pids
            for pid in vanished:
                # Only flag if it was a significant process (was in top 10)
                logger.debug(f"Process {pid} vanished from top processes")
        self._known_pids = current_pids

        return events

    def run_once(self) -> tuple[SystemSnapshot, list[ObservationEvent]]:
        snapshot = self.take_snapshot()
        events = self.check_anomalies(snapshot)
        for event in events:
            self._emit(event)
        return snapshot, events

    def run(self):
        """Main monitoring loop with exponential backoff on repeated failures."""
        self._running = True
        self._consecutive_errors = 0
        _MAX_BACKOFF = 60  # seconds

        logger.info(
            f"Observer daemon started (poll every {self.poll_interval}s, "
            f"battery poll every {self.poll_interval_battery}s, "
            f"CPU>{self.cpu_threshold}%, RAM>{self.ram_threshold}%, "
            f"cooldown={self.cooldown_minutes}min)"
        )
        while self._running:
            on_battery = self._is_on_battery()

            if on_battery and self.pause_on_battery:
                logger.debug("On battery — paused")
                time.sleep(self.poll_interval_battery)
                continue

            try:
                self.run_once()
                self._consecutive_errors = 0  # reset on success
            except Exception as e:
                self._consecutive_errors += 1
                backoff = min(2 ** self._consecutive_errors, _MAX_BACKOFF)
                logger.error(f"Observer error ({self._consecutive_errors} in a row, backoff {backoff}s): {e}")
                if self._consecutive_errors >= 5:
                    logger.critical("Observer: 5+ consecutive failures — subsystem may be unhealthy")
                time.sleep(backoff)
                continue

            interval = self.poll_interval_battery if on_battery else self.poll_interval
            time.sleep(interval)

    def stop(self):
        self._running = False
        logger.info("Observer daemon stopped")
