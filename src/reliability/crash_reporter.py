"""
Local crash reporter.

Installs a top-level exception hook (main thread + background threads) that
writes a self-contained JSON dump to ``logs/crashes/<timestamp>.json`` on
unhandled exceptions. Each dump contains:
  - traceback (full, with chained causes)
  - tail of the rotating log file
  - runtime snapshot (Python version, OS, working dir, PID)
  - config filenames (contents excluded — may contain secrets)

No network egress. No third-party dependencies. Zero PII beyond what's already
in the local log tail.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import threading
import traceback
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CRASH_DIR = Path("logs/crashes")
_MAX_LOG_TAIL_LINES = 200
_RETENTION = 50  # keep at most N crash dumps


class _RingBufferHandler(logging.Handler):
    """In-memory ring buffer of recent log records — cheap, thread-safe."""

    def __init__(self, capacity: int = _MAX_LOG_TAIL_LINES):
        super().__init__(level=logging.DEBUG)
        self._buffer: deque[str] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            return
        with self._lock:
            self._buffer.append(msg)

    def tail(self) -> list[str]:
        with self._lock:
            return list(self._buffer)


class CrashReporter:
    """Captures unhandled exceptions into structured local dumps."""

    def __init__(self, crash_dir: Path | str = _CRASH_DIR, retention: int = _RETENTION):
        self.crash_dir = Path(crash_dir)
        self.retention = retention
        self._ring = _RingBufferHandler()
        self._ring.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    def attach_to_root_logger(self) -> None:
        """Start capturing logs into the ring buffer."""
        logging.getLogger().addHandler(self._ring)

    def _gather_context(self) -> dict[str, Any]:
        return {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "machine": platform.machine(),
            "cwd": os.getcwd(),
            "pid": os.getpid(),
            "argv": sys.argv,
            "thread_count": threading.active_count(),
        }

    def capture(self, exc_type, exc_value, exc_traceback, thread_name: str = "MainThread") -> Path | None:
        """Write a crash dump and return its path. Safe to call from any thread."""
        try:
            self.crash_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # We can't write to disk — last resort is stderr
            print(f"CRASH REPORTER: failed to create {self.crash_dir}: {e}", file=sys.stderr)
            return None

        tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
        payload: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "thread": thread_name,
            "exception_type": getattr(exc_type, "__name__", str(exc_type)),
            "exception_message": str(exc_value),
            "traceback": "".join(tb_lines),
            "recent_logs": self._ring.tail(),
            "context": self._gather_context(),
        }

        # Timestamp + PID + a random suffix: rapid-fire crashes can land in the
        # same clock tick (Windows' datetime.now() is coarse), so the timestamp
        # alone is not unique — the suffix keeps dumps from overwriting each other.
        fname = f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{os.getpid()}_{uuid.uuid4().hex[:8]}.json"
        path = self.crash_dir / fname
        try:
            path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        except OSError as e:
            print(f"CRASH REPORTER: failed to write {path}: {e}", file=sys.stderr)
            return None

        self._prune()
        return path

    def _prune(self) -> None:
        """Drop oldest crash dumps past retention."""
        try:
            dumps = sorted(self.crash_dir.glob("crash_*.json"), key=lambda p: p.stat().st_mtime)
            for old in dumps[:-self.retention]:
                try:
                    old.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def list_recent(self, n: int = 10) -> list[dict]:
        """Read summaries of recent crash dumps for the GUI."""
        if not self.crash_dir.exists():
            return []
        try:
            dumps = sorted(
                self.crash_dir.glob("crash_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:n]
        except OSError:
            return []

        out = []
        for p in dumps:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "file": str(p),
                    "timestamp": data.get("timestamp", ""),
                    "thread": data.get("thread", ""),
                    "exception_type": data.get("exception_type", ""),
                    "exception_message": data.get("exception_message", "")[:200],
                })
            except (OSError, json.JSONDecodeError):
                continue
        return out


def install_crash_reporter(crash_dir: Path | str = _CRASH_DIR) -> CrashReporter:
    """Install global exception hooks for the main thread and worker threads."""
    reporter = CrashReporter(crash_dir=crash_dir)
    reporter.attach_to_root_logger()

    prev_excepthook = sys.excepthook

    def _main_hook(exc_type, exc_value, exc_traceback):
        # KeyboardInterrupt is not a crash — let it through to the normal handler
        if issubclass(exc_type, KeyboardInterrupt):
            prev_excepthook(exc_type, exc_value, exc_traceback)
            return
        try:
            path = reporter.capture(exc_type, exc_value, exc_traceback, thread_name="MainThread")
            logger.critical(
                "Unhandled exception — crash dump written to %s",
                path if path else "<unavailable>",
                exc_info=(exc_type, exc_value, exc_traceback),
            )
        except Exception as hook_err:
            print(f"CRASH REPORTER: hook itself failed: {hook_err}", file=sys.stderr)
        prev_excepthook(exc_type, exc_value, exc_traceback)

    sys.excepthook = _main_hook

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, KeyboardInterrupt):
            return
        try:
            thread_name = args.thread.name if args.thread else "unknown-thread"
            path = reporter.capture(args.exc_type, args.exc_value, args.exc_traceback, thread_name=thread_name)
            logger.critical(
                "Thread '%s' crashed — dump at %s",
                thread_name, path if path else "<unavailable>",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception as hook_err:
            print(f"CRASH REPORTER (thread): hook failed: {hook_err}", file=sys.stderr)

    threading.excepthook = _thread_hook
    return reporter
