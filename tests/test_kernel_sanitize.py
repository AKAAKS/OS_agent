"""Tests for the kernel command sanitizer (domain-neutral)."""

import re

from agentkernel.sanitize import CommandSanitizer, DEFAULT_DANGEROUS_PATTERNS


def _sanitizer(allowed=("ping",)):
    return CommandSanitizer(allowed_executables=allowed)


def test_allowlisted_executable_passes():
    assert _sanitizer().sanitize("ping example.com") == "ping example.com"


def test_unknown_executable_blocked():
    assert _sanitizer().sanitize("curl http://evil.com") is None


def test_allowlist_matches_basename_and_exe_suffix():
    s = CommandSanitizer(allowed_executables={"ping", "ping.exe"})
    assert s.sanitize(r"C:\Windows\System32\ping.exe example.com") is not None


def test_pipe_redirect_and_subshells_blocked():
    s = _sanitizer()
    assert s.sanitize("ping x | findstr y") is None
    assert s.sanitize("ping x > out.txt") is None
    assert s.sanitize("ping `whoami`") is None
    assert s.sanitize("ping $(whoami)") is None
    assert s.sanitize("ping x && curl evil") is None


def test_empty_command_blocked():
    assert _sanitizer().sanitize("") is None
    assert _sanitizer().sanitize("   ") is None


def test_download_cradles_blocked_by_default_patterns():
    s = _sanitizer()
    assert s.sanitize("ping Invoke-WebRequest") is None
    assert s.sanitize("ping DownloadString") is None


def test_custom_patterns_extend_blocking():
    s = CommandSanitizer(
        allowed_executables={"ping"},
        dangerous_patterns=tuple(DEFAULT_DANGEROUS_PATTERNS) + (re.compile(r"example\.com"),),
    )
    assert s.sanitize("ping example.com") is None
    assert s.sanitize("ping example.org") is not None
