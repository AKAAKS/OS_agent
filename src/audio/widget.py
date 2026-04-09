"""
Audio dashboard widget — PyQt6 always-on-top overlay showing
active audio sessions, device routing, and volume levels.
"""

import logging
import sys
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QProgressBar, QPushButton, QFrame, QScrollArea,
    )
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QFont, QColor
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False
    logger.warning("PyQt6 not installed — audio widget disabled")

from src.audio.monitor import AudioMonitor


def create_audio_widget(monitor: AudioMonitor):
    """Create and return the audio dashboard widget. Must be called from the main thread."""
    if not PYQT_AVAILABLE:
        logger.error("PyQt6 not available")
        return None

    class AudioDashboard(QWidget):
        def __init__(self):
            super().__init__()
            self.monitor = monitor
            self._setup_ui()
            self._start_refresh()

        def _setup_ui(self):
            self.setWindowTitle("Audio Dashboard")
            self.setWindowFlags(
                Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setFixedWidth(320)
            self.setMaximumHeight(400)

            # Main layout
            main_layout = QVBoxLayout(self)
            main_layout.setContentsMargins(0, 0, 0, 0)

            # Container with background
            container = QFrame()
            container.setObjectName("container")
            container.setStyleSheet("""
                QFrame#container {
                    background-color: rgba(30, 30, 30, 230);
                    border-radius: 12px;
                    border: 1px solid rgba(255, 255, 255, 30);
                }
                QLabel {
                    color: #ffffff;
                    font-size: 12px;
                }
                QLabel#header {
                    font-size: 14px;
                    font-weight: bold;
                    color: #88aaff;
                }
                QLabel#device {
                    font-size: 11px;
                    color: #aaaaaa;
                }
                QProgressBar {
                    border: none;
                    background-color: rgba(255, 255, 255, 20);
                    border-radius: 4px;
                    height: 8px;
                }
                QProgressBar::chunk {
                    background-color: #4488ff;
                    border-radius: 4px;
                }
                QPushButton {
                    background: transparent;
                    color: #aaaaaa;
                    border: none;
                    font-size: 16px;
                    padding: 4px;
                }
                QPushButton:hover {
                    color: #ffffff;
                }
            """)

            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(16, 12, 16, 12)

            # Header row
            header_row = QHBoxLayout()
            self.header_label = QLabel("Audio")
            self.header_label.setObjectName("header")
            header_row.addWidget(self.header_label)
            header_row.addStretch()

            close_btn = QPushButton("x")
            close_btn.setFixedSize(24, 24)
            close_btn.clicked.connect(self.hide)
            header_row.addWidget(close_btn)
            container_layout.addLayout(header_row)

            # Device info
            self.device_label = QLabel("")
            self.device_label.setObjectName("device")
            container_layout.addWidget(self.device_label)

            # Separator
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("background-color: rgba(255,255,255,20); max-height: 1px;")
            container_layout.addWidget(sep)

            # Scroll area for sessions
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

            self.sessions_widget = QWidget()
            self.sessions_layout = QVBoxLayout(self.sessions_widget)
            self.sessions_layout.setContentsMargins(0, 4, 0, 4)
            self.sessions_layout.setSpacing(8)
            scroll.setWidget(self.sessions_widget)

            container_layout.addWidget(scroll)

            # No audio label
            self.no_audio_label = QLabel("No audio playing")
            self.no_audio_label.setStyleSheet("color: #666666; font-style: italic;")
            self.no_audio_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            container_layout.addWidget(self.no_audio_label)

            main_layout.addWidget(container)

            # Position in bottom-right
            self._position_window()

            # Drag support
            self._drag_pos = None

        def _position_window(self):
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                self.move(geo.right() - self.width() - 20, geo.bottom() - 420)

        def _start_refresh(self):
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._refresh)
            self.timer.start(2000)  # Refresh every 2 seconds
            self._refresh()

        def _refresh(self):
            if not self.isVisible():
                return

            snapshot = self.monitor.take_snapshot()

            # Update device info
            self.device_label.setText(
                f"Output: {snapshot.default_output}  |  Input: {snapshot.default_input}"
            )

            # Clear old session entries
            while self.sessions_layout.count():
                item = self.sessions_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            active_sessions = [s for s in snapshot.sessions if s.state == "active"]

            self.no_audio_label.setVisible(len(active_sessions) == 0)

            for session in active_sessions:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(8)

                # App name
                name_label = QLabel(session.process_name)
                name_label.setFixedWidth(120)
                row_layout.addWidget(name_label)

                # Volume bar
                vol_bar = QProgressBar()
                vol_bar.setRange(0, 100)
                vol_bar.setValue(int(session.volume * 100))
                vol_bar.setTextVisible(False)
                vol_bar.setFixedHeight(8)
                row_layout.addWidget(vol_bar)

                # Volume %
                vol_label = QLabel(f"{session.volume * 100:.0f}%")
                vol_label.setFixedWidth(35)
                vol_label.setAlignment(Qt.AlignmentFlag.AlignRight)
                row_layout.addWidget(vol_label)

                # Mute indicator
                mute_label = QLabel("M" if session.is_muted else "")
                mute_label.setFixedWidth(15)
                mute_label.setStyleSheet("color: #ff4444;" if session.is_muted else "")
                row_layout.addWidget(mute_label)

                self.sessions_layout.addWidget(row)

        def mousePressEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = event.globalPosition().toPoint() - self.pos()

        def mouseMoveEvent(self, event):
            if self._drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_pos)

        def mouseReleaseEvent(self, event):
            self._drag_pos = None

    return AudioDashboard()
