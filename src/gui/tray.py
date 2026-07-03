"""
System-tray icon + details window for OS Agent.

Runs the agent in a background QThread while PyQt owns the main event loop.
The tray icon surfaces the current profile via tooltip; right-click opens a
menu (show details / pause / dry-run / quit). Double-clicking shows the
details window with four tabs: Status, Actions, Crashes, Settings.

Status and actions update on a 2-second timer by reading the audit log and
cost ledger directly — no IPC, no shared state beyond the filesystem.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QAction, QIcon, QPixmap, QColor, QPainter, QBrush
from PyQt6.QtWidgets import (
    QApplication,
    QSystemTrayIcon,
    QMenu,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QPushButton,
    QHeaderView,
    QMessageBox,
)

from agentkernel.audit import ActionAuditLog
from agentkernel.costs import DailyCostLedger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Icon factory — avoids shipping binary assets
# ---------------------------------------------------------------------------

def _make_icon(color: str = "#44cc44") -> QIcon:
    """Build a 32x32 colored disc icon programmatically (no asset files)."""
    pix = QPixmap(32, 32)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(color)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 28, 28)
    painter.end()
    return QIcon(pix)


# ---------------------------------------------------------------------------
# Agent runner — keeps the OS Agent alive on a background thread
# ---------------------------------------------------------------------------

class AgentRunner(QThread):
    """Runs OS Agent's main() on a worker thread so PyQt owns the main loop."""

    status_changed = pyqtSignal(str)
    crashed = pyqtSignal(str)

    def __init__(self, dry_run: bool = False, parent: QObject | None = None):
        super().__init__(parent)
        self.dry_run = dry_run
        self._should_run = True

    def run(self) -> None:
        try:
            import main as agent_main  # noqa: WPS433 (runtime import)

            # Inject a lightweight sentinel: replace argv so main() picks up --dry-run
            prev_argv = sys.argv
            sys.argv = [prev_argv[0]] + (["--dry-run"] if self.dry_run else [])
            try:
                self.status_changed.emit("running")
                agent_main.main()
                self.status_changed.emit("stopped")
            finally:
                sys.argv = prev_argv
        except SystemExit:
            self.status_changed.emit("stopped")
        except Exception as e:
            logger.exception("Agent runner crashed")
            self.crashed.emit(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Details window
# ---------------------------------------------------------------------------

class DetailsWindow(QMainWindow):
    """Four-tab window: Status, Actions, Crashes, Settings."""

    def __init__(
        self,
        audit_log: ActionAuditLog,
        cost_ledger: DailyCostLedger,
        config_paths: dict[str, Path],
        crashes_dir: Path,
    ):
        super().__init__()
        self.setWindowTitle("OS Agent — Details")
        self.resize(820, 560)

        self.audit_log = audit_log
        self.cost_ledger = cost_ledger
        self.config_paths = config_paths
        self.crashes_dir = crashes_dir

        tabs = QTabWidget()
        self._status_tab = self._build_status_tab()
        self._actions_tab = self._build_actions_tab()
        self._crashes_tab = self._build_crashes_tab()
        self._settings_tab = self._build_settings_tab()

        tabs.addTab(self._status_tab, "Status")
        tabs.addTab(self._actions_tab, "Actions")
        tabs.addTab(self._crashes_tab, "Crashes")
        tabs.addTab(self._settings_tab, "Settings")
        self.setCentralWidget(tabs)

        # Refresh every 2s while visible
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()
        self.refresh()

    # ---- Tabs ----

    def _build_status_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self._cost_label = QLabel()
        self._cost_label.setStyleSheet("font-size: 13px; padding: 8px;")
        layout.addWidget(self._cost_label)

        self._history_label = QLabel()
        self._history_label.setStyleSheet("font-family: Consolas, monospace; padding: 8px;")
        layout.addWidget(self._history_label)

        layout.addStretch(1)
        return w

    def _build_actions_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self._actions_table = QTableWidget(0, 5)
        self._actions_table.setHorizontalHeaderLabels(
            ["Time", "Action", "Success", "Dry-run", "Message"]
        )
        self._actions_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._actions_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self._actions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._actions_table)

        refresh_btn = QPushButton("Refresh now")
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)

        return w

    def _build_crashes_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self._crashes_table = QTableWidget(0, 3)
        self._crashes_table.setHorizontalHeaderLabels(["Time", "Exception", "Message"])
        self._crashes_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._crashes_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._crashes_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._crashes_table)

        info = QLabel(f"Dumps live at: {self.crashes_dir}")
        info.setStyleSheet("color: #888; padding: 4px;")
        layout.addWidget(info)

        return w

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        layout.addWidget(QLabel("<b>Read-only config preview.</b> Edit the YAML files directly and restart."))

        self._settings_view = QTextEdit()
        self._settings_view.setReadOnly(True)
        self._settings_view.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(self._settings_view)

        btn_row = QHBoxLayout()
        for name, path in self.config_paths.items():
            b = QPushButton(f"Open {name}")
            b.clicked.connect(lambda _checked=False, p=path: self._open_path(p))
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        return w

    # ---- Refresh ----

    def refresh(self) -> None:
        if not self.isVisible():
            return
        self._refresh_status()
        self._refresh_actions()
        self._refresh_crashes()
        self._refresh_settings()

    def _refresh_status(self) -> None:
        today = self.cost_ledger.today_spend()
        cap = self.cost_ledger.daily_cap_usd
        remaining = self.cost_ledger.remaining()
        pct = (today / cap * 100) if cap else 0.0
        self._cost_label.setText(
            f"<b>Daily LLM spend:</b> ${today:.4f} / ${cap:.2f}  "
            f"(<b>{pct:.1f}%</b> of cap, ${remaining:.4f} remaining)"
        )
        history = self.cost_ledger.history(7)
        if history:
            lines = [f"{date}   ${amount:.4f}" for date, amount in history.items()]
            self._history_label.setText("Recent days:\n" + "\n".join(lines))
        else:
            self._history_label.setText("No cost history yet.")

    def _refresh_actions(self) -> None:
        records = self.audit_log.tail(100)
        self._actions_table.setRowCount(len(records))
        for row, rec in enumerate(reversed(records)):  # newest first
            self._actions_table.setItem(row, 0, QTableWidgetItem(rec.get("timestamp", "")[:19]))
            self._actions_table.setItem(row, 1, QTableWidgetItem(rec.get("action", "")))
            success_item = QTableWidgetItem("✓" if rec.get("success") else "✗")
            success_item.setForeground(QColor("#44cc44") if rec.get("success") else QColor("#ff4444"))
            self._actions_table.setItem(row, 2, success_item)
            self._actions_table.setItem(row, 3, QTableWidgetItem("DRY" if rec.get("dry_run") else ""))
            self._actions_table.setItem(row, 4, QTableWidgetItem(rec.get("message", "")))

    def _refresh_crashes(self) -> None:
        from src.reliability.crash_reporter import CrashReporter
        reporter = CrashReporter(crash_dir=self.crashes_dir)
        recent = reporter.list_recent(n=50)
        self._crashes_table.setRowCount(len(recent))
        for row, rec in enumerate(recent):
            self._crashes_table.setItem(row, 0, QTableWidgetItem(rec.get("timestamp", "")[:19]))
            self._crashes_table.setItem(row, 1, QTableWidgetItem(rec.get("exception_type", "")))
            self._crashes_table.setItem(row, 2, QTableWidgetItem(rec.get("exception_message", "")))

    def _refresh_settings(self) -> None:
        parts = []
        for name, path in self.config_paths.items():
            parts.append(f"# ==== {name} ({path}) ====")
            if path.exists():
                try:
                    parts.append(path.read_text(encoding="utf-8"))
                except OSError as e:
                    parts.append(f"<error reading: {e}>")
            else:
                parts.append("<file missing>")
            parts.append("")
        self._settings_view.setPlainText("\n".join(parts))

    def _open_path(self, path: Path) -> None:
        import os
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except (AttributeError, OSError) as e:
            QMessageBox.warning(self, "Open failed", f"Could not open {path}: {e}")


# ---------------------------------------------------------------------------
# Tray controller
# ---------------------------------------------------------------------------

class TrayController(QObject):
    """Top-level controller — owns the tray icon, menu, agent, and window."""

    def __init__(self, app: QApplication, dry_run: bool = False):
        super().__init__()
        self.app = app
        self.dry_run = dry_run

        self.audit_log = ActionAuditLog()
        # Resolve daily cap from the real settings so the GUI matches the agent
        daily_cap = self._peek_daily_cap()
        self.cost_ledger = DailyCostLedger(daily_cap_usd=daily_cap)

        self.tray = QSystemTrayIcon(_make_icon("#44cc44"))
        self.tray.setToolTip("OS Agent — starting...")
        self._build_menu()
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

        self.window: DetailsWindow | None = None
        self.runner = AgentRunner(dry_run=dry_run)
        self.runner.status_changed.connect(self._on_runner_status)
        self.runner.crashed.connect(self._on_runner_crash)
        self.runner.start()

        # Periodic tooltip update with cost info
        self._tip_timer = QTimer(self)
        self._tip_timer.setInterval(5000)
        self._tip_timer.timeout.connect(self._update_tooltip)
        self._tip_timer.start()

    @staticmethod
    def _peek_daily_cap() -> float:
        try:
            from src.config import load_and_validate
            app, _ = load_and_validate()
            return app.api.daily_cost_cap_usd
        except Exception:
            return 5.00

    def _build_menu(self) -> None:
        menu = QMenu()

        show = QAction("Show details", menu)
        show.triggered.connect(self.show_window)
        menu.addAction(show)

        self._dry_run_action = QAction("Dry-run mode", menu)
        self._dry_run_action.setCheckable(True)
        self._dry_run_action.setChecked(self.dry_run)
        self._dry_run_action.setEnabled(False)  # read-only — change requires restart
        self._dry_run_action.setToolTip("Change dry-run state by restarting the agent")
        menu.addAction(self._dry_run_action)

        menu.addSeparator()

        quit_action = QAction("Quit OS Agent", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            self.show_window()

    def _on_runner_status(self, status: str) -> None:
        color = {"running": "#44cc44", "stopped": "#888888"}.get(status, "#ffaa00")
        self.tray.setIcon(_make_icon(color))
        self.tray.setToolTip(f"OS Agent — {status}" + (" (dry-run)" if self.dry_run else ""))

    def _on_runner_crash(self, message: str) -> None:
        self.tray.setIcon(_make_icon("#ff4444"))
        self.tray.setToolTip(f"OS Agent crashed: {message}")
        self.tray.showMessage(
            "OS Agent crashed",
            f"{message}\nCheck logs/crashes/ for the dump.",
            QSystemTrayIcon.MessageIcon.Critical,
            5000,
        )

    def _update_tooltip(self) -> None:
        spend = self.cost_ledger.today_spend()
        cap = self.cost_ledger.daily_cap_usd
        status = self.tray.toolTip().split(" — ", 1)[0]
        self.tray.setToolTip(
            f"{status} — today ${spend:.4f} / ${cap:.2f}"
            + (" (dry-run)" if self.dry_run else "")
        )

    def show_window(self) -> None:
        if self.window is None:
            self.window = DetailsWindow(
                audit_log=self.audit_log,
                cost_ledger=self.cost_ledger,
                config_paths={
                    "settings.yaml": Path("config/settings.yaml"),
                    "profiles.yaml": Path("config/profiles.yaml"),
                },
                crashes_dir=Path("logs/crashes"),
            )
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self) -> None:
        # Ask the agent to shut down via its module-level hook. Thread-safe.
        try:
            import main as agent_main  # noqa: WPS433
            agent_main.request_shutdown()
        except Exception as e:
            logger.warning(f"shutdown request failed: {e}")

        if self.runner.isRunning():
            self.runner.wait(10000)  # up to 10s for graceful shutdown
        self.tray.hide()
        self.app.quit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(dry_run: bool = False) -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # closing the window keeps the tray alive

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None, "OS Agent",
            "System tray is not available on this system.\nRun python main.py for console mode instead.",
        )
        return 1

    _controller = TrayController(app, dry_run=dry_run)
    return app.exec()
