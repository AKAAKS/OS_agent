"""
System Dashboard widget — PyQt6 always-on-top, frameless, draggable overlay
showing live system metrics, profile, processes, events, agent activity,
bluetooth, audio, battery, and polling mode.
"""

import logging
import sys
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QProgressBar, QPushButton, QFrame, QScrollArea, QSizePolicy,
    )
    from PyQt6.QtCore import Qt, QTimer, QSize
    from PyQt6.QtGui import QFont, QColor
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False
    logger.warning("PyQt6 not installed — system dashboard widget disabled")

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not installed — battery info unavailable")


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _bar_colour(value: float) -> str:
    """Return a hex colour for a progress bar based on utilisation."""
    if value >= 85:
        return "#ff4444"
    if value >= 60:
        return "#ffaa00"
    return "#44cc44"


def _profile_colour(profile_name: str) -> str:
    """Return an accent colour per profile."""
    mapping = {
        "gaming": "#ff4444",
        "coding": "#44cc44",
        "streaming": "#aa44ff",
        "idle": "#888888",
    }
    return mapping.get(profile_name.lower(), "#88aaff")


# ---------------------------------------------------------------------------
# Stylesheet (matches audio widget dark theme)
# ---------------------------------------------------------------------------

_STYLESHEET = """
QFrame#container {
    background-color: rgba(30, 30, 30, 230);
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 30);
}
QLabel {
    color: #ffffff;
    font-size: 11px;
}
QLabel#header {
    font-size: 14px;
    font-weight: bold;
    color: #88aaff;
}
QLabel#sectionTitle {
    font-size: 12px;
    font-weight: bold;
    color: #88aaff;
}
QLabel#muted {
    font-size: 11px;
    color: #777777;
}
QLabel#profileIndicator {
    font-size: 12px;
    font-weight: bold;
}
QProgressBar {
    border: none;
    background-color: rgba(255, 255, 255, 20);
    border-radius: 4px;
    height: 8px;
    text-align: center;
    font-size: 9px;
    color: transparent;
}
QProgressBar::chunk {
    border-radius: 4px;
}
QPushButton {
    background: transparent;
    color: #aaaaaa;
    border: none;
    font-size: 14px;
    padding: 2px;
}
QPushButton:hover {
    color: #ffffff;
}
QScrollArea {
    border: none;
    background: transparent;
}
QScrollBar:vertical {
    background: rgba(255,255,255,10);
    width: 6px;
    border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: rgba(255,255,255,40);
    border-radius: 3px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
"""


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

if PYQT_AVAILABLE:

    class SystemDashboard(QWidget):
        """Always-on-top system dashboard overlay."""

        def __init__(
            self,
            observer,
            profile_manager,
            event_log: list,
            activity_log: list,
            bt_monitor,
            audio_monitor,
            settings: dict,
        ):
            super().__init__()
            self.observer = observer
            self.profile_manager = profile_manager
            self.event_log = event_log
            self.activity_log = activity_log
            self.bt_monitor = bt_monitor
            self.audio_monitor = audio_monitor
            self.settings = settings

            self._drag_pos = None
            self._setup_ui()
            self._start_refresh()

        # ------------------------------------------------------------------ UI
        def _setup_ui(self):
            self.setWindowTitle("System Dashboard")
            self.setWindowFlags(
                Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setFixedWidth(380)

            # Main layout (transparent wrapper)
            main_layout = QVBoxLayout(self)
            main_layout.setContentsMargins(0, 0, 0, 0)

            # Scroll area so the whole widget is scrollable
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

            # Container with dark background
            self.container = QFrame()
            self.container.setObjectName("container")
            self.container.setStyleSheet(_STYLESHEET)

            self.c_layout = QVBoxLayout(self.container)
            self.c_layout.setContentsMargins(14, 10, 14, 10)
            self.c_layout.setSpacing(4)

            # --- Header row ---
            header_row = QHBoxLayout()
            header_label = QLabel("System Dashboard")
            header_label.setObjectName("header")
            header_row.addWidget(header_label)
            header_row.addStretch()

            minimize_btn = QPushButton("\u2013")  # en-dash
            minimize_btn.setFixedSize(22, 22)
            minimize_btn.setToolTip("Minimize")
            minimize_btn.clicked.connect(self.showMinimized)
            header_row.addWidget(minimize_btn)

            close_btn = QPushButton("\u00d7")  # multiplication sign
            close_btn.setFixedSize(22, 22)
            close_btn.setToolTip("Close")
            close_btn.clicked.connect(self.hide)
            header_row.addWidget(close_btn)

            self.c_layout.addLayout(header_row)

            # --- 1. System Metrics ---
            self._add_separator()
            self._add_section_title("System Metrics")

            self.cpu_bar = self._add_metric_row("CPU")
            self.ram_bar = self._add_metric_row("RAM")
            self.ram_detail = self._add_detail_label()
            self.disk_bar = self._add_metric_row("Disk")
            self.gpu_bar = self._add_metric_row("GPU")

            # --- 2. Current Profile ---
            self._add_separator()
            self._add_section_title("Current Profile")
            profile_row = QHBoxLayout()
            self.profile_dot = QLabel("\u25cf")  # filled circle
            self.profile_dot.setObjectName("profileIndicator")
            self.profile_dot.setFixedWidth(16)
            profile_row.addWidget(self.profile_dot)
            self.profile_label = QLabel("—")
            profile_row.addWidget(self.profile_label)
            profile_row.addStretch()
            self.c_layout.addLayout(profile_row)

            # --- 3. Top 5 Processes ---
            self._add_separator()
            self._add_section_title("Top Processes")

            # Table header
            th = QHBoxLayout()
            for text, width in [("Name", 140), ("CPU%", 55), ("RAM MB", 65)]:
                lbl = QLabel(text)
                lbl.setObjectName("muted")
                lbl.setFixedWidth(width)
                th.addWidget(lbl)
            th.addStretch()
            self.c_layout.addLayout(th)

            self.proc_rows: list[tuple[QLabel, QLabel, QLabel]] = []
            for _ in range(5):
                row = QHBoxLayout()
                row.setSpacing(4)
                name_l = QLabel("—")
                name_l.setFixedWidth(140)
                name_l.setStyleSheet("font-size: 11px;")
                cpu_l = QLabel("—")
                cpu_l.setFixedWidth(55)
                cpu_l.setStyleSheet("font-size: 11px;")
                mem_l = QLabel("—")
                mem_l.setFixedWidth(65)
                mem_l.setStyleSheet("font-size: 11px;")
                row.addWidget(name_l)
                row.addWidget(cpu_l)
                row.addWidget(mem_l)
                row.addStretch()
                self.c_layout.addLayout(row)
                self.proc_rows.append((name_l, cpu_l, mem_l))

            # --- 4. Recent Events ---
            self._add_separator()
            self._add_section_title("Recent Events")
            self.event_labels: list[QLabel] = []
            for _ in range(5):
                lbl = QLabel("—")
                lbl.setWordWrap(True)
                lbl.setStyleSheet("font-size: 11px; color: #cccccc;")
                self.c_layout.addWidget(lbl)
                self.event_labels.append(lbl)

            # --- 5. Agent Activity ---
            self._add_separator()
            self._add_section_title("Agent Activity")
            self.activity_labels: list[QLabel] = []
            for _ in range(5):
                lbl = QLabel("—")
                lbl.setWordWrap(True)
                lbl.setStyleSheet("font-size: 11px; color: #cccccc;")
                self.c_layout.addWidget(lbl)
                self.activity_labels.append(lbl)

            # --- 6. Bluetooth Status ---
            self._add_separator()
            self._add_section_title("Bluetooth")
            self.bt_label = QLabel("—")
            self.bt_label.setStyleSheet("font-size: 11px;")
            self.c_layout.addWidget(self.bt_label)

            # --- 7. Audio ---
            self._add_separator()
            self._add_section_title("Audio")
            self.audio_label = QLabel("—")
            self.audio_label.setStyleSheet("font-size: 11px;")
            self.c_layout.addWidget(self.audio_label)

            # --- 8. Battery ---
            self._add_separator()
            self._add_section_title("Battery")
            self.battery_label = QLabel("—")
            self.battery_label.setStyleSheet("font-size: 11px;")
            self.c_layout.addWidget(self.battery_label)

            # --- 9. Polling Mode ---
            self._add_separator()
            self._add_section_title("Polling Mode")
            self.polling_label = QLabel("—")
            self.polling_label.setStyleSheet("font-size: 11px;")
            self.c_layout.addWidget(self.polling_label)

            # Bottom spacer
            self.c_layout.addStretch()

            scroll.setWidget(self.container)
            main_layout.addWidget(scroll)

            # Cap the widget height to screen and allow scrolling
            screen = QApplication.primaryScreen()
            if screen:
                avail = screen.availableGeometry()
                self.setMaximumHeight(avail.height() - 40)

            self._position_window()

        # -------------------------------------------------------- helpers
        def _add_separator(self):
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet(
                "background-color: rgba(255,255,255,20); max-height: 1px;"
            )
            self.c_layout.addWidget(sep)

        def _add_section_title(self, text: str):
            lbl = QLabel(text)
            lbl.setObjectName("sectionTitle")
            self.c_layout.addWidget(lbl)

        def _add_metric_row(self, name: str) -> QProgressBar:
            row = QHBoxLayout()
            row.setSpacing(6)
            label = QLabel(name)
            label.setFixedWidth(36)
            label.setStyleSheet("font-size: 11px;")
            row.addWidget(label)

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            row.addWidget(bar)

            val = QLabel("0%")
            val.setFixedWidth(38)
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            val.setStyleSheet("font-size: 11px;")
            row.addWidget(val)

            self.c_layout.addLayout(row)
            # Store the value label on the bar for easy access
            bar._val_label = val  # type: ignore[attr-defined]
            return bar

        def _add_detail_label(self) -> QLabel:
            lbl = QLabel("")
            lbl.setObjectName("muted")
            lbl.setStyleSheet("font-size: 10px; color: #777777; padding-left: 42px;")
            self.c_layout.addWidget(lbl)
            return lbl

        def _set_bar(self, bar: QProgressBar, value: float):
            val = max(0, min(100, int(value)))
            bar.setValue(val)
            colour = _bar_colour(value)
            bar.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {colour}; border-radius: 4px; }}"
            )
            bar._val_label.setText(f"{val}%")  # type: ignore[attr-defined]

        # -------------------------------------------------------- position
        def _position_window(self):
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                x = geo.right() - self.width() - 20
                y = geo.bottom() - self.sizeHint().height() - 20
                # Clamp y to be at least at the top of the screen
                y = max(geo.top() + 10, y)
                self.move(x, y)

        # -------------------------------------------------------- refresh
        def _start_refresh(self):
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._refresh)
            self.timer.start(3000)
            self._refresh()

        def _refresh(self):
            if not self.isVisible():
                return

            # 1. System metrics
            try:
                snap = self.observer.take_snapshot()
                self._set_bar(self.cpu_bar, snap.cpu_percent)
                self._set_bar(self.ram_bar, snap.ram_percent)
                self.ram_detail.setText(
                    f"{snap.ram_used_gb:.1f} / {snap.ram_total_gb:.1f} GB"
                )
                self._set_bar(self.disk_bar, snap.disk_percent)

                gpu_val = snap.gpu_percent
                if gpu_val is not None:
                    self._set_bar(self.gpu_bar, gpu_val)
                    self.gpu_bar.parentWidget()  # just to keep reference alive
                else:
                    self.gpu_bar.setValue(0)
                    self.gpu_bar._val_label.setText("N/A")  # type: ignore[attr-defined]

                # 3. Top 5 processes
                procs = (snap.top_processes or [])[:5]
                for i, (name_l, cpu_l, mem_l) in enumerate(self.proc_rows):
                    if i < len(procs):
                        p = procs[i]
                        pname = p.get("name", "?")
                        if len(pname) > 20:
                            pname = pname[:18] + ".."
                        name_l.setText(pname)
                        cpu_l.setText(f"{p.get('cpu_percent', 0):.1f}")
                        mem_l.setText(f"{p.get('memory_mb', 0):.0f}")
                    else:
                        name_l.setText("—")
                        cpu_l.setText("—")
                        mem_l.setText("—")
            except Exception as exc:
                logger.debug("snapshot refresh error: %s", exc)

            # 2. Current profile
            try:
                profile_val = self.profile_manager.active_profile.value
                self.profile_label.setText(profile_val)
                colour = _profile_colour(profile_val)
                self.profile_dot.setStyleSheet(
                    f"font-size: 12px; font-weight: bold; color: {colour};"
                )
            except Exception:
                self.profile_label.setText("Unknown")

            # 4. Recent events
            try:
                recent = list(self.event_log)[-5:]
                for i, lbl in enumerate(self.event_labels):
                    if i < len(recent):
                        evt = recent[-(i + 1)]  # newest first
                        ts = ""
                        if hasattr(evt, "timestamp"):
                            ts = evt.timestamp.strftime("%H:%M:%S") + " "
                        summary = getattr(evt, "summary", str(evt))
                        lbl.setText(f"{ts}{summary}")
                    else:
                        lbl.setText("—")
            except Exception:
                pass

            # 5. Agent activity
            try:
                recent_acts = list(self.activity_log)[-5:]
                for i, lbl in enumerate(self.activity_labels):
                    if i < len(recent_acts):
                        lbl.setText(str(recent_acts[-(i + 1)]))
                    else:
                        lbl.setText("—")
            except Exception:
                pass

            # 6. Bluetooth
            try:
                devices = self.bt_monitor.get_paired_devices()
                count = len(devices) if devices else 0
                names = ", ".join(d.name for d in (devices or [])[:3])
                text = f"Connected: {count}"
                if names:
                    text += f"  ({names})"
                self.bt_label.setText(text)
            except Exception:
                self.bt_label.setText("Unavailable")

            # 7. Audio
            try:
                a_snap = self.audio_monitor.take_snapshot()
                active = [s for s in a_snap.sessions if s.state == "active"]
                out = a_snap.default_output or "Unknown"
                self.audio_label.setText(
                    f"Sessions: {len(active)}  |  Output: {out}"
                )
            except Exception:
                self.audio_label.setText("Unavailable")

            # 8. Battery
            try:
                if PSUTIL_AVAILABLE:
                    batt = psutil.sensors_battery()
                    if batt is not None:
                        status = "Plugged in" if batt.power_plugged else "On battery"
                        self.battery_label.setText(
                            f"{status}  |  {batt.percent:.0f}%"
                        )
                    else:
                        self.battery_label.setText("No battery detected")
                else:
                    self.battery_label.setText("psutil not available")
            except Exception:
                self.battery_label.setText("Unavailable")

            # 9. Polling mode
            try:
                if PSUTIL_AVAILABLE:
                    batt = psutil.sensors_battery()
                    on_ac = batt.power_plugged if batt else True
                else:
                    on_ac = True
                mon = self.settings.get("monitoring", self.settings)
                ac_interval = mon.get("poll_interval_seconds", 5)
                batt_interval = mon.get("poll_interval_battery_seconds", 30)
                current = ac_interval if on_ac else batt_interval
                mode = "AC" if on_ac else "Battery"
                self.polling_label.setText(
                    f"Mode: {mode}  |  Interval: {current}s"
                )
            except Exception:
                self.polling_label.setText("—")

        # -------------------------------------------------------- dragging
        def mousePressEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = event.globalPosition().toPoint() - self.pos()

        def mouseMoveEvent(self, event):
            if self._drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_pos)

        def mouseReleaseEvent(self, event):
            self._drag_pos = None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_dashboard(
    observer,
    profile_manager,
    event_log: list,
    activity_log: list,
    bt_monitor,
    audio_monitor,
    settings: dict,
) -> Optional["SystemDashboard"]:
    """Create and return a SystemDashboard widget.

    Returns ``None`` if PyQt6 is not installed.
    Must be called from the main / GUI thread.
    """
    if not PYQT_AVAILABLE:
        logger.error("PyQt6 not available — cannot create system dashboard")
        return None

    return SystemDashboard(
        observer=observer,
        profile_manager=profile_manager,
        event_log=event_log,
        activity_log=activity_log,
        bt_monitor=bt_monitor,
        audio_monitor=audio_monitor,
        settings=settings,
    )
