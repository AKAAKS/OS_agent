"""Allowlist-based sanitizer for LLM-generated commands.

The caller supplies the executable allowlist (domain policy); the kernel
supplies the structural checks: dangerous shell patterns (chaining, pipes,
redirects, subshells, download cradles) and first-token allowlist matching.
A denylist alone is never sufficient — the allowlist is the real control.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
from collections.abc import Iterable, Sequence

logger = logging.getLogger(__name__)

# Patterns that should never appear in a command (chain operators, redirects,
# subshells, download cradles, destructive idioms).
DEFAULT_DANGEROUS_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"[;&|]{2,}"),           # chained commands via && || ;;
    re.compile(r"\|"),                   # pipe operator
    re.compile(r"[><]"),                 # redirects
    re.compile(r"`"),                    # backtick subshell
    re.compile(r"\$\("),                 # $() subshell
    re.compile(r"Invoke-WebRequest", re.IGNORECASE),
    re.compile(r"Invoke-Expression", re.IGNORECASE),
    re.compile(r"IEX\s*\(", re.IGNORECASE),
    re.compile(r"Start-Process", re.IGNORECASE),
    re.compile(r"DownloadString", re.IGNORECASE),
    re.compile(r"DownloadFile", re.IGNORECASE),
    re.compile(r"rm\s+-rf", re.IGNORECASE),
    re.compile(r"format\s+[a-z]:", re.IGNORECASE),
    re.compile(r"del\s+/[sfq]", re.IGNORECASE),
)


class CommandSanitizer:
    """Validates command strings before execution."""

    def __init__(
        self,
        allowed_executables: Iterable[str],
        dangerous_patterns: Sequence[re.Pattern] | None = None,
    ):
        self.allowed_executables = frozenset(e.lower() for e in allowed_executables)
        self.dangerous_patterns = (
            tuple(dangerous_patterns)
            if dangerous_patterns is not None
            else DEFAULT_DANGEROUS_PATTERNS
        )

    def sanitize(self, command: str) -> str | None:
        """Return the command unchanged if it passes all checks, else None."""
        stripped = command.strip()
        if not stripped:
            return None

        for pattern in self.dangerous_patterns:
            if pattern.search(stripped):
                logger.warning(
                    f"Command blocked (dangerous pattern '{pattern.pattern}'): {stripped[:80]}"
                )
                return None

        try:
            tokens = shlex.split(stripped, posix=False)
        except ValueError:
            tokens = stripped.split()

        if not tokens:
            return None

        exe = os.path.basename(tokens[0]).lower()
        if exe not in self.allowed_executables:
            logger.warning(
                f"Command blocked (executable not allowlisted '{exe}'): {stripped[:80]}"
            )
            return None

        return stripped
