"""Tests for local crash reporter."""

import json
import logging
import sys
import threading

import pytest

from src.reliability.crash_reporter import CrashReporter, install_crash_reporter


@pytest.fixture(autouse=True)
def _isolate_excepthooks():
    """Keep crash-reporter tests from leaking global hooks into the session.

    install_crash_reporter() replaces sys.excepthook / threading.excepthook and
    chains to whatever was installed before. Without restoring them, each test
    stacks another hook and a later exception fans out across all of them —
    which is how 'hook test' dumps ended up in the real logs/crashes. Start
    every test from the pristine interpreter defaults and restore afterward.
    """
    saved_sys, saved_thread = sys.excepthook, threading.excepthook
    sys.excepthook = sys.__excepthook__
    threading.excepthook = threading.__excepthook__
    try:
        yield
    finally:
        sys.excepthook = saved_sys
        threading.excepthook = saved_thread


def test_importing_main_does_not_install_a_crash_hook_at_import():
    """Importing main must not mutate global excepthooks (the install belongs
    inside main(), so a test that imports main doesn't register a logs/crashes
    hook for the whole session)."""
    import main

    assert not hasattr(main, "_crash_reporter"), \
        "crash reporter must be installed inside main(), not at module import"


@pytest.fixture
def reporter(tmp_path):
    return CrashReporter(crash_dir=tmp_path / "crashes", retention=5)


class TestCrashReporter:

    def test_capture_writes_json_dump(self, reporter, tmp_path):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            path = reporter.capture(*sys.exc_info())

        assert path is not None
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["exception_type"] == "RuntimeError"
        assert "boom" in data["exception_message"]
        assert "traceback" in data
        assert "context" in data
        assert data["context"]["pid"] > 0

    def test_captures_log_tail(self, reporter):
        reporter.attach_to_root_logger()
        root = logging.getLogger()
        prev_level = root.level
        root.setLevel(logging.INFO)
        try:
            logging.getLogger("test").info("breadcrumb_before_crash")
            try:
                raise ValueError("xyz")
            except ValueError:
                path = reporter.capture(*sys.exc_info())
        finally:
            root.setLevel(prev_level)

        data = json.loads(path.read_text(encoding="utf-8"))
        tail_text = "\n".join(data["recent_logs"])
        assert "breadcrumb_before_crash" in tail_text

    def test_retention_prunes_old_dumps(self, tmp_path):
        rep = CrashReporter(crash_dir=tmp_path / "c", retention=3)
        for i in range(6):
            try:
                raise RuntimeError(f"err{i}")
            except RuntimeError:
                rep.capture(*sys.exc_info())

        dumps = list((tmp_path / "c").glob("crash_*.json"))
        assert len(dumps) <= 3

    def test_list_recent_returns_summaries(self, reporter):
        for i in range(3):
            try:
                raise RuntimeError(f"err{i}")
            except RuntimeError:
                reporter.capture(*sys.exc_info())

        recent = reporter.list_recent(n=10)
        assert len(recent) == 3
        assert all("exception_type" in r for r in recent)

    def test_rapid_captures_in_same_instant_do_not_overwrite(self, reporter, monkeypatch):
        """Three crashes within one clock tick must yield three distinct dumps.

        The filename was timestamp+PID only; on Windows the clock is coarse
        enough that rapid captures collided and overwrote each other.
        """
        import datetime as _dt

        import src.reliability.crash_reporter as cr

        frozen = _dt.datetime(2026, 1, 1, 12, 0, 0, 0)

        class _FrozenDateTime:
            @staticmethod
            def now(*args, **kwargs):
                return frozen

        monkeypatch.setattr(cr, "datetime", _FrozenDateTime)

        for i in range(3):
            try:
                raise RuntimeError(f"err{i}")
            except RuntimeError:
                reporter.capture(*sys.exc_info())

        dumps = list(reporter.crash_dir.glob("crash_*.json"))
        assert len(dumps) == 3, "rapid captures must not overwrite one another"

    def test_list_recent_on_missing_dir_is_empty(self, tmp_path):
        rep = CrashReporter(crash_dir=tmp_path / "does-not-exist")
        assert rep.list_recent() == []


class TestInstallCrashReporter:

    def test_keyboard_interrupt_is_not_dumped(self, tmp_path, monkeypatch):
        # Redirect the crash dir
        from src.reliability import crash_reporter as cr
        rep = install_crash_reporter(crash_dir=tmp_path / "crashes")

        # Simulate a KeyboardInterrupt going through the hook
        try:
            raise KeyboardInterrupt()
        except KeyboardInterrupt:
            exc = sys.exc_info()

        # Call our installed hook directly — it should not write a dump
        # Save and restore original to avoid test pollution
        hook = sys.excepthook
        try:
            hook(*exc)
        except KeyboardInterrupt:
            pass  # default excepthook may re-raise; we only care about no dump

        dumps = list((tmp_path / "crashes").glob("crash_*.json"))
        assert dumps == []

    def test_hook_dumps_regular_exception(self, tmp_path):
        install_crash_reporter(crash_dir=tmp_path / "crashes")
        try:
            raise RuntimeError("hook test")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())

        dumps = list((tmp_path / "crashes").glob("crash_*.json"))
        assert len(dumps) == 1
        data = json.loads(dumps[0].read_text(encoding="utf-8"))
        assert data["exception_type"] == "RuntimeError"
