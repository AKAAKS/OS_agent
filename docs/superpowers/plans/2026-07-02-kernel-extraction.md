# Kernel Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the OS Agent's trust/autonomy substrate (approval gate, risk classification, command sanitization, semantic fix memory, audit log, cost ledger) into a reusable, domain-agnostic `agentkernel` package, then rewire the OS Agent as its first consumer — with zero behavior change and all safety invariants preserved.

**Architecture:** A new top-level package `agentkernel/` (sibling of `src/`) holds the six kernel components with no imports from the app or platform libraries — enforced by an AST-based boundary test. The app keeps its domain policy (which actions exist, their canonical risks, the run_command allowlist) in `src/safety/policy.py` and `src/agent/tools.py`, and binds it to the kernel at construction time. The dead-code `HITLGate` and the inline `_hitl_gate` logic in the OODA graph are unified into one fail-closed `ApprovalGate`.

**Tech Stack:** Python 3.11+, pytest + pytest-mock, chromadb (optional dep of the kernel memory module only, imported lazily), LangGraph (app side only).

## Global Constraints

- **Repo:** `C:\Users\akshi\OneDrive\Desktop\OS_agent`. Run all commands from the repo root. Tests: `python -m pytest tests/ -q`. The suite currently passes 120 tests; it must never go red at a commit boundary.
- **Kernel boundary (hard rule):** no module under `agentkernel/` may import `src.*`, `psutil`, `win32*`, `wmi`, `pycaw`, `PyQt6`, `langgraph`, `openai`, `winotify`, or `comtypes`. `chromadb` is allowed only inside a `try/except ImportError`. Task 1's boundary test enforces this; it must pass at every commit from Task 1 onward.
- **Safety invariants (must remain true; existing tests verify them):** (a) structured actions only — never free-text-as-command; (b) the approval gate fails closed (no callback → deny, callback exception → deny) and never silently auto-approves `kill_process`/`restart_service`/`run_command`; (c) the run_command allowlist stays read-only-diagnostics (no shell interpreters, no `sc`/`net`/`wmic`/`taskkill`/`bcdedit`/`diskpart`); (d) nothing ever auto-disables Windows Defender.
- **Line endings:** repo files are LF. After every Edit/Write, check `git diff --stat` — if a file shows as fully rewritten, normalize with `tr -d '\r' < file > tmp && mv tmp file` before committing. Diffs must show only real changes.
- **Moves use `git mv`** so history follows the file.
- **Out of scope (future plans):** widening the anomaly surface, a second kernel consumer (personal assistant), publishing the kernel as a separate repo/PyPI package.

---

### Task 1: Kernel scaffold + boundary contract test

**Files:**
- Create: `agentkernel/__init__.py` (placeholder docstring; public API is filled in Task 12)
- Test: `tests/test_kernel_boundary.py`

**Interfaces:**
- Produces: importable package `agentkernel`; a parametrized test that every later task must keep green.

- [ ] **Step 1: Write the failing boundary test**

```python
"""The kernel must stay reusable: no app (src.*) or platform imports.

This test IS the architecture. If it fails, the kernel has grown a
dependency that would break extraction to its own repo later.
"""

import ast
from pathlib import Path

import pytest

KERNEL_DIR = Path(__file__).resolve().parent.parent / "agentkernel"

# Roots that must never be imported by kernel modules. chromadb is NOT
# listed: it is an optional dependency, guarded by try/except ImportError.
FORBIDDEN_ROOTS = {
    "src", "psutil", "wmi", "pycaw", "PyQt6",
    "langgraph", "openai", "winotify", "comtypes",
}


def _kernel_modules() -> list[Path]:
    if not KERNEL_DIR.is_dir():
        return []
    return sorted(KERNEL_DIR.glob("*.py"))


def test_kernel_package_exists():
    assert (KERNEL_DIR / "__init__.py").is_file(), (
        "agentkernel/ package missing — the kernel must live at repo root"
    )


@pytest.mark.parametrize("path", _kernel_modules(), ids=lambda p: p.name)
def test_no_forbidden_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            root = name.split(".")[0]
            if root in FORBIDDEN_ROOTS or root.startswith("win32"):
                violations.append(f"{path.name}: import of '{name}'")
    assert not violations, "Kernel boundary violated:\n" + "\n".join(violations)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_kernel_boundary.py -v`
Expected: FAIL — `test_kernel_package_exists` (agentkernel/ missing).

- [ ] **Step 3: Create the package**

Create `agentkernel/__init__.py` containing exactly:

```python
"""agentkernel — a reusable substrate for safe autonomous action.

Public API is assembled as components land; see tests/test_kernel_boundary.py
for the dependency contract this package must honor.
"""
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_kernel_boundary.py -v`
Expected: PASS (2 tests: package exists + `__init__.py` has no forbidden imports).

- [ ] **Step 5: Full suite, then commit**

Run: `python -m pytest tests/ -q` — expected: all pass (122).

```bash
git add agentkernel/__init__.py tests/test_kernel_boundary.py
git commit -m "kernel: scaffold agentkernel package with AST boundary contract test"
```

---

### Task 2: `agentkernel/risk.py` — risk levels and severity merge

**Files:**
- Create: `agentkernel/risk.py`
- Test: `tests/test_kernel_risk.py`

**Interfaces:**
- Produces: `RiskLevel` (Enum: LOW="low", MEDIUM="medium", HIGH="high"), `max_severity(a: RiskLevel, b: RiskLevel) -> RiskLevel`, `parse_risk(value: str, default: RiskLevel = RiskLevel.MEDIUM) -> RiskLevel`. Tasks 3, 8, 9 consume these.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for kernel risk levels and severity merging."""

from agentkernel.risk import RiskLevel, max_severity, parse_risk


def test_max_severity_takes_the_more_severe():
    assert max_severity(RiskLevel.LOW, RiskLevel.HIGH) is RiskLevel.HIGH
    assert max_severity(RiskLevel.HIGH, RiskLevel.LOW) is RiskLevel.HIGH
    assert max_severity(RiskLevel.MEDIUM, RiskLevel.LOW) is RiskLevel.MEDIUM
    assert max_severity(RiskLevel.MEDIUM, RiskLevel.MEDIUM) is RiskLevel.MEDIUM


def test_parse_risk_accepts_canonical_strings():
    assert parse_risk("low") is RiskLevel.LOW
    assert parse_risk("medium") is RiskLevel.MEDIUM
    assert parse_risk("high") is RiskLevel.HIGH


def test_parse_risk_falls_back_on_garbage():
    assert parse_risk("bogus") is RiskLevel.MEDIUM
    assert parse_risk("") is RiskLevel.MEDIUM
    assert parse_risk("bogus", default=RiskLevel.HIGH) is RiskLevel.HIGH
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_kernel_risk.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentkernel.risk'`.

- [ ] **Step 3: Implement `agentkernel/risk.py`**

```python
"""Risk levels and severity ordering for autonomous actions."""

from enum import Enum


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def max_severity(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    """The more severe of two risk levels.

    Used to merge an upstream risk assessment with an action's canonical
    risk so that neither a mislabelled upstream value nor a stale canonical
    entry can downgrade the other.
    """
    return a if _ORDER[a] >= _ORDER[b] else b


def parse_risk(value: str, default: RiskLevel = RiskLevel.MEDIUM) -> RiskLevel:
    """Parse a risk string, falling back to `default` on anything invalid."""
    try:
        return RiskLevel(value)
    except ValueError:
        return default
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_kernel_risk.py tests/test_kernel_boundary.py -v`
Expected: PASS (boundary test now also covers `risk.py`).

- [ ] **Step 5: Commit**

```bash
git add agentkernel/risk.py tests/test_kernel_risk.py
git commit -m "kernel: RiskLevel, max_severity, parse_risk"
```

---

### Task 3: `agentkernel/gate.py` — fail-closed ApprovalGate

This unifies the dead `HITLGate.check()` and the live inline logic in `OODAAgent._hitl_gate` into one tested component. Its test file must subsume every case in `tests/test_hitl.py` (deleted in Task 11).

**Files:**
- Create: `agentkernel/gate.py`
- Test: `tests/test_kernel_gate.py`

**Interfaces:**
- Consumes: `RiskLevel`, `max_severity` from `agentkernel.risk` (Task 2).
- Produces: `ApprovalRequest` (dataclass: action: str, target: str, risk_level: RiskLevel, reason: str, timestamp: datetime), `GateDecision` (dataclass: approved: bool, risk: RiskLevel, auto: bool, reason: str = ""), `ApprovalGate` with `__init__(risk_map: dict[str, RiskLevel], high_risk_actions, auto_approve_levels, default_risk=RiskLevel.HIGH)`, `set_approval_callback(cb: Callable[[ApprovalRequest], bool])`, `evaluate(action: str, target: str = "", reason: str = "", upstream_risk: RiskLevel | None = None) -> GateDecision`, `get_history() -> list[dict]`. Tasks 8, 9, 11 consume these.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the fail-closed ApprovalGate.

Subsumes tests/test_hitl.py (auto-approval, callback, history) and the
integration-level gate behavior (always-gated actions, severity merge,
fail-closed on missing/raising callback).
"""

import pytest

from agentkernel.gate import ApprovalGate, ApprovalRequest
from agentkernel.risk import RiskLevel

RISK_MAP = {
    "kill_process": RiskLevel.LOW,
    "restart_service": RiskLevel.MEDIUM,
    "run_command": RiskLevel.HIGH,
}


@pytest.fixture
def gate():
    return ApprovalGate(
        risk_map=RISK_MAP,
        high_risk_actions={"run_command", "modify_registry"},
        auto_approve_levels={RiskLevel.LOW},
    )


class TestAutoApproval:
    def test_low_risk_ungated_action_auto_approved(self, gate):
        decision = gate.evaluate("kill_process")
        assert decision.approved is True
        assert decision.auto is True
        assert decision.risk is RiskLevel.LOW

    def test_medium_risk_denied_without_callback(self, gate):
        decision = gate.evaluate("restart_service")
        assert decision.approved is False
        assert decision.auto is False

    def test_high_risk_denied_without_callback(self, gate):
        assert gate.evaluate("run_command").approved is False


class TestAlwaysGatedActions:
    def test_gated_action_never_auto_approved_even_at_low_upstream(self, gate):
        seen = []
        gate.set_approval_callback(lambda req: seen.append(req) or True)
        decision = gate.evaluate("run_command", upstream_risk=RiskLevel.LOW)
        assert len(seen) == 1, "callback must be consulted for gated actions"
        assert decision.auto is False

    def test_forced_low_upstream_cannot_downgrade_canonical_risk(self, gate):
        seen = []
        gate.set_approval_callback(lambda req: seen.append(req) or True)
        gate.evaluate("run_command", upstream_risk=RiskLevel.LOW)
        assert seen[0].risk_level is RiskLevel.HIGH

    def test_upstream_high_never_downgraded_by_canonical(self):
        gate = ApprovalGate(
            risk_map={"odd_action": RiskLevel.LOW},
            high_risk_actions={"odd_action"},
            auto_approve_levels={RiskLevel.LOW},
        )
        seen = []
        gate.set_approval_callback(lambda req: seen.append(req) or True)
        gate.evaluate("odd_action", upstream_risk=RiskLevel.HIGH)
        assert seen[0].risk_level is RiskLevel.HIGH

    def test_unknown_action_defaults_to_high_risk(self, gate):
        decision = gate.evaluate("never_heard_of_it")
        assert decision.approved is False
        assert decision.risk is RiskLevel.HIGH


class TestCallback:
    def test_callback_approves(self, gate):
        gate.set_approval_callback(lambda req: True)
        assert gate.evaluate("restart_service").approved is True

    def test_callback_denies(self, gate):
        gate.set_approval_callback(lambda req: False)
        assert gate.evaluate("restart_service").approved is False

    def test_callback_receives_request_object(self, gate):
        received = []

        def capture(req):
            received.append(req)
            return True

        gate.set_approval_callback(capture)
        gate.evaluate("run_command", target="systeminfo", reason="diagnostics")

        assert len(received) == 1
        assert isinstance(received[0], ApprovalRequest)
        assert received[0].action == "run_command"
        assert received[0].target == "systeminfo"
        assert received[0].risk_level is RiskLevel.HIGH

    def test_raising_callback_denies(self, gate):
        def explode(req):
            raise RuntimeError("UI crashed")

        gate.set_approval_callback(explode)
        assert gate.evaluate("run_command").approved is False


class TestHistory:
    def test_history_records_auto_approval(self, gate):
        gate.evaluate("kill_process", target="test.exe")
        history = gate.get_history()
        assert len(history) == 1
        assert history[0]["approved"] is True
        assert history[0]["auto"] is True
        assert history[0]["action"] == "kill_process"

    def test_history_records_denials(self, gate):
        gate.evaluate("run_command", target="cmd")
        history = gate.get_history()
        assert len(history) == 1
        assert history[0]["approved"] is False
        assert history[0]["risk"] == "high"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_kernel_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentkernel.gate'`.

- [ ] **Step 3: Implement `agentkernel/gate.py`**

```python
"""Fail-closed approval gate for autonomous actions.

Domain-agnostic: the caller supplies the action vocabulary (risk_map), the
set of actions that must always face a human (high_risk_actions), and which
risk levels may auto-approve.

Two safety properties:

1. Actions in ``high_risk_actions`` ALWAYS go to the approval callback.
   An upstream risk label (which may be wrong, stale, or forced low by a
   caching layer) can never silently auto-approve them, and the risk shown
   to the human is the MORE severe of the upstream label and the action's
   canonical risk.
2. Fail closed: no callback wired means deny; a callback that raises means
   deny.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from agentkernel.risk import RiskLevel, max_severity

logger = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    """What the human sees when asked to approve an action."""

    action: str
    target: str
    risk_level: RiskLevel
    reason: str
    timestamp: datetime


@dataclass
class GateDecision:
    approved: bool
    risk: RiskLevel  # effective risk after upstream/canonical merge
    auto: bool  # True = approved without consulting the callback
    reason: str = ""


ApprovalCallback = Callable[[ApprovalRequest], bool]


class ApprovalGate:
    """Fail-closed approval gate. See module docstring for the contract."""

    def __init__(
        self,
        risk_map: dict[str, RiskLevel],
        high_risk_actions: set[str] | frozenset[str] = frozenset(),
        auto_approve_levels: set[RiskLevel] | frozenset[RiskLevel] = frozenset({RiskLevel.LOW}),
        default_risk: RiskLevel = RiskLevel.HIGH,
    ):
        self.risk_map = dict(risk_map)
        self.high_risk_actions = set(high_risk_actions)
        self.auto_approve_levels = set(auto_approve_levels)
        self.default_risk = default_risk
        self._callback: ApprovalCallback | None = None
        self._history: list[dict] = []

    def set_approval_callback(self, callback: ApprovalCallback) -> None:
        """callback(request: ApprovalRequest) -> bool (True = approved)."""
        self._callback = callback

    def evaluate(
        self,
        action: str,
        target: str = "",
        reason: str = "",
        upstream_risk: RiskLevel | None = None,
    ) -> GateDecision:
        """Decide whether `action` may proceed. Never raises."""
        canonical = self.risk_map.get(action, self.default_risk)
        resolved = upstream_risk if upstream_risk is not None else canonical
        is_high = action in self.high_risk_actions

        if not is_high and resolved in self.auto_approve_levels:
            return self._record(
                action, target, GateDecision(True, resolved, auto=True, reason="auto-approved")
            )

        # Always-gated actions show the human the MORE severe of upstream
        # and canonical risk, so a forced-low label can't understate them.
        risk = max_severity(resolved, canonical) if is_high else resolved

        if self._callback is None:
            logger.warning(f"Approval needed but no callback set — denying '{action}'")
            return self._record(
                action, target,
                GateDecision(False, risk, auto=False, reason="no approval callback (fail closed)"),
            )

        request = ApprovalRequest(
            action=action or "unknown",
            target=target,
            risk_level=risk,
            reason=reason,
            timestamp=datetime.now(),
        )
        try:
            approved = bool(self._callback(request))
        except Exception as e:
            logger.error(f"Approval callback failed: {e} — denying '{action}'")
            return self._record(
                action, target,
                GateDecision(False, risk, auto=False, reason=f"callback error: {e}"),
            )

        return self._record(action, target, GateDecision(approved, risk, auto=False))

    def _record(self, action: str, target: str, decision: GateDecision) -> GateDecision:
        self._history.append({
            "action": action,
            "target": target,
            "risk": decision.risk.value,
            "approved": decision.approved,
            "auto": decision.auto,
            "reason": decision.reason,
            "timestamp": datetime.now().isoformat(),
        })
        return decision

    def get_history(self) -> list[dict]:
        return list(self._history)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_kernel_gate.py tests/test_kernel_boundary.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentkernel/gate.py tests/test_kernel_gate.py
git commit -m "kernel: fail-closed ApprovalGate unifying HITL semantics"
```

---

### Task 4: `agentkernel/sanitize.py` — CommandSanitizer

**Files:**
- Create: `agentkernel/sanitize.py`
- Test: `tests/test_kernel_sanitize.py`

**Interfaces:**
- Produces: `DEFAULT_DANGEROUS_PATTERNS: tuple[re.Pattern, ...]`, `CommandSanitizer` with `__init__(allowed_executables: Iterable[str], dangerous_patterns: Sequence[re.Pattern] | None = None)` and `sanitize(command: str) -> str | None`. Task 10 consumes these. The pattern list and sanitize body are moved from `src/agent/tools.py:53-100` — same regexes, same semantics.

- [ ] **Step 1: Write the failing tests** (domain-neutral — the OS-specific allowlist cases stay in `tests/test_tools.py`)

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_kernel_sanitize.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `agentkernel/sanitize.py`** (patterns and body moved verbatim from `src/agent/tools.py` — do not change any regex)

```python
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
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_kernel_sanitize.py tests/test_kernel_boundary.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agentkernel/sanitize.py tests/test_kernel_sanitize.py
git commit -m "kernel: CommandSanitizer with default dangerous-pattern set"
```

---

### Task 5: Move the audit log into the kernel

Pure move — `src/agent/audit.py` is already domain-agnostic (its `event_type`/`event_summary`/`profile` fields are plain optional strings).

**Files:**
- Move: `src/agent/audit.py` → `agentkernel/audit.py` (via `git mv`)
- Modify: `src/agent/graph.py:15`, `src/gui/tray.py:41`, `tests/test_audit.py:9`, `tests/test_gui.py:20`, `tests/test_integration.py:17`

**Interfaces:**
- Produces: `agentkernel.audit.ActionRecord`, `agentkernel.audit.ActionAuditLog` — identical classes, new import path. Task 12 re-exports them.

- [ ] **Step 1: Move the file**

```bash
git mv src/agent/audit.py agentkernel/audit.py
```

- [ ] **Step 2: Run suite to see the expected failures**

Run: `python -m pytest tests/ -q`
Expected: collection errors — `ModuleNotFoundError: No module named 'src.agent.audit'` (from graph.py and the three test files).

- [ ] **Step 3: Update every import site**

In each file below, replace the line shown (exact old → new):

- `src/agent/graph.py:15`: `from src.agent.audit import ActionAuditLog, ActionRecord` → `from agentkernel.audit import ActionAuditLog, ActionRecord`
- `src/gui/tray.py:41`: `from src.agent.audit import ActionAuditLog` → `from agentkernel.audit import ActionAuditLog`
- `tests/test_audit.py:9`: `from src.agent.audit import ActionAuditLog, ActionRecord` → `from agentkernel.audit import ActionAuditLog, ActionRecord`
- `tests/test_gui.py:20`: `from src.agent.audit import ActionAuditLog, ActionRecord  # noqa: E402` → `from agentkernel.audit import ActionAuditLog, ActionRecord  # noqa: E402`
- `tests/test_integration.py:17`: `from src.agent.audit import ActionAuditLog` → `from agentkernel.audit import ActionAuditLog`

Then verify no stragglers: `grep -rn "src.agent.audit" src/ tests/ main.py gui.py dashboard.py` — expected: no output.

- [ ] **Step 4: Run full suite + boundary test**

Run: `python -m pytest tests/ -q`
Expected: all pass (audit.py imports only stdlib, so the boundary test stays green).

- [ ] **Step 5: Commit**

```bash
git add agentkernel/audit.py src/agent/graph.py src/gui/tray.py tests/test_audit.py tests/test_gui.py tests/test_integration.py
git commit -m "kernel: move action audit log into agentkernel"
```

(Never use `git add -A` in this repo — `docs/` contains an intentionally uncommitted file.)

---

### Task 6: Move the cost ledger into the kernel

Pure move — `DailyCostLedger` is already generic (stdlib only).

**Files:**
- Move: `src/agent/cost_ledger.py` → `agentkernel/costs.py` (via `git mv`)
- Modify: `src/agent/llm.py:14`, `src/gui/tray.py:42`, `tests/test_cost_ledger.py:8`, `tests/test_gui.py:21`

**Interfaces:**
- Produces: `agentkernel.costs.DailyCostLedger` — identical class, new import path.

- [ ] **Step 1: Move the file**

```bash
git mv src/agent/cost_ledger.py agentkernel/costs.py
```

- [ ] **Step 2: Run suite to see the expected failures**

Run: `python -m pytest tests/ -q`
Expected: `ModuleNotFoundError: No module named 'src.agent.cost_ledger'`.

- [ ] **Step 3: Update every import site**

- `src/agent/llm.py:14`: `from src.agent.cost_ledger import DailyCostLedger` → `from agentkernel.costs import DailyCostLedger`
- `src/gui/tray.py:42`: `from src.agent.cost_ledger import DailyCostLedger` → `from agentkernel.costs import DailyCostLedger`
- `tests/test_cost_ledger.py:8`: `from src.agent.cost_ledger import DailyCostLedger` → `from agentkernel.costs import DailyCostLedger`
- `tests/test_gui.py:21`: `from src.agent.cost_ledger import DailyCostLedger  # noqa: E402` → `from agentkernel.costs import DailyCostLedger  # noqa: E402`

Verify: `grep -rn "cost_ledger" src/ tests/ main.py gui.py dashboard.py` — expected: only hits on `agentkernel.costs` imports, the ledger's own data-file path string, and variable names like `cost_ledger=`.

- [ ] **Step 4: Run full suite**

Run: `python -m pytest tests/ -q` — expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agentkernel/costs.py src/agent/llm.py src/gui/tray.py tests/test_cost_ledger.py tests/test_gui.py
git commit -m "kernel: move daily cost ledger into agentkernel"
```

---

### Task 7: Move semantic fix memory into the kernel as `FixMemory`

Move `src/memory/store.py` → `agentkernel/memory.py`, rename the class `SemanticMemory` → `FixMemory`, and change the constructor to take an explicit threshold instead of the app's settings dict (kernel components never read app config schemas). Everything else — chromadb lazy import, cosine space, `store_fix` metadata flattening, `find_similar_fix` returning the structured `"action"` key, stale-handle re-init — stays byte-identical.

**Files:**
- Move: `src/memory/store.py` → `agentkernel/memory.py` (via `git mv`)
- Modify: `agentkernel/memory.py` (rename + constructor), `src/agent/graph.py:19,60`, `main.py:207,231`
- Test: `tests/test_kernel_memory.py` (new — there is currently NO test file for this module)

**Interfaces:**
- Produces: `agentkernel.memory.FixMemory` with `__init__(similarity_threshold: float = 0.78, db_path: str = "data/chromadb")`, `available: bool` property, `store_fix(anomaly_summary: str, anomaly_type: str, fix_applied: str, success: bool, details: dict | None = None)`, `find_similar_fix(anomaly_summary: str) -> dict | None` (dict keys: similarity, original_anomaly, fix_applied, action, anomaly_type, timestamp), `get_stats() -> dict`.

- [ ] **Step 1: Move the file**

```bash
git mv src/memory/store.py agentkernel/memory.py
```

- [ ] **Step 2: Rename class and change constructor**

In `agentkernel/memory.py`:

Replace the module docstring:

```python
"""Semantic fix memory — stores past anomaly events and their fixes in
ChromaDB so similar future anomalies can reuse a structured action that
already succeeded, instead of re-diagnosing (or worse, parsing prose into
a command). chromadb is an optional dependency: without it the store is
inert (`available` is False) and all operations are safe no-ops.
"""
```

Replace:

```python
class SemanticMemory:
    """Vector store for past anomaly/fix pairs using ChromaDB."""

    def __init__(self, settings: dict, db_path: str = "data/chromadb"):
        self.similarity_threshold = settings.get("decisions", {}).get(
            "similarity_threshold", 0.78
        )
```

with:

```python
class FixMemory:
    """Vector store for past anomaly/fix pairs using ChromaDB."""

    def __init__(self, similarity_threshold: float = 0.78, db_path: str = "data/chromadb"):
        self.similarity_threshold = similarity_threshold
```

No other changes to the file.

- [ ] **Step 3: Update consumers**

- `src/agent/graph.py:19`: `from src.memory.store import SemanticMemory` → `from agentkernel.memory import FixMemory`
- `src/agent/graph.py:60`: `memory: SemanticMemory | None = None,` → `memory: FixMemory | None = None,`
- `main.py:207`: `from src.memory.store import SemanticMemory` → `from agentkernel.memory import FixMemory`
- `main.py:231`: `memory = SemanticMemory(settings)` →

```python
        memory = FixMemory(
            similarity_threshold=settings.get("decisions", {}).get("similarity_threshold", 0.78)
        )
```

Verify: `grep -rn "SemanticMemory\|memory.store" src/ tests/ main.py gui.py dashboard.py` — expected: no output.

- [ ] **Step 4: Write kernel memory tests**

Create `tests/test_kernel_memory.py`. The fakes bypass `__init__` (via `__new__`) so tests never touch chromadb or download embedding models:

```python
"""Tests for kernel FixMemory (fake collection — no real chromadb)."""

from agentkernel.memory import FixMemory


class FakeCollection:
    def __init__(self, query_result=None):
        self.added = []
        self.query_result = query_result or {
            "ids": [[]], "distances": [[]], "metadatas": [[]], "documents": [[]],
        }

    def add(self, documents, metadatas, ids):
        self.added.append({"documents": documents, "metadatas": metadatas, "ids": ids})

    def query(self, **kwargs):
        return self.query_result

    def count(self):
        return 1


def _memory(fake: FakeCollection, threshold: float = 0.9) -> FixMemory:
    # Bypass __init__ so tests never construct a real chromadb client.
    mem = FixMemory.__new__(FixMemory)
    mem.similarity_threshold = threshold
    mem.db_path = "unused"
    mem._available = True
    mem._collection = fake
    mem.client = None
    return mem


def _hit(distance: float, metadata: dict) -> dict:
    return {
        "ids": [["fix_1"]],
        "distances": [[distance]],
        "metadatas": [[metadata]],
        "documents": [["chrome eating 95% CPU"]],
    }


def test_unavailable_memory_is_inert():
    mem = FixMemory.__new__(FixMemory)
    mem._available = False
    mem._collection = None
    assert mem.available is False
    assert mem.find_similar_fix("anything") is None
    mem.store_fix("a", "cpu_spike", "killed it", True)  # must not raise


def test_below_threshold_returns_none():
    fake = FakeCollection(_hit(0.5, {"success": "True", "action": "kill_process"}))
    assert _memory(fake, threshold=0.9).find_similar_fix("cpu spike") is None


def test_match_returns_structured_action():
    fake = FakeCollection(_hit(0.05, {
        "success": "True",
        "action": "kill_process",
        "fix_applied": "Killed chrome",
        "anomaly_type": "cpu_spike",
        "timestamp": "2026-07-01T00:00:00",
    }))
    result = _memory(fake, threshold=0.9).find_similar_fix("cpu spike")
    assert result is not None
    assert result["action"] == "kill_process"
    assert result["fix_applied"] == "Killed chrome"
    assert result["similarity"] == 0.95


def test_failed_past_fix_is_not_reused():
    fake = FakeCollection(_hit(0.05, {"success": "False", "action": "kill_process"}))
    assert _memory(fake, threshold=0.9).find_similar_fix("cpu spike") is None


def test_store_fix_flattens_details_into_metadata():
    fake = FakeCollection()
    mem = _memory(fake)
    mem.store_fix(
        anomaly_summary="chrome eating CPU",
        anomaly_type="cpu_spike",
        fix_applied="Killed chrome",
        success=True,
        details={"action": "kill_process", "risk": "low"},
    )
    assert len(fake.added) == 1
    metadata = fake.added[0]["metadatas"][0]
    assert metadata["action"] == "kill_process"
    assert metadata["success"] == "True"
```

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass. (`test_integration.py`'s `TestMemoryReuse` builds its own memory stub, so it is unaffected by the rename — verify it still passes.)

- [ ] **Step 6: Commit**

```bash
git add agentkernel/memory.py src/agent/graph.py main.py tests/test_kernel_memory.py
git commit -m "kernel: move semantic fix memory into agentkernel as FixMemory"
```

---

### Task 8: App policy module — `src/safety/policy.py`

The kernel gate is generic; this module is where the OS agent's action vocabulary binds to it. `RISK_MAP` moves here from `HITLGate.RISK_MAP` (which Task 11 deletes), extended with the two emitted actions it was missing (`cleanup_temp_files`, `find_large_files`).

**Files:**
- Create: `src/safety/policy.py`
- Test: `tests/test_policy.py`

**Interfaces:**
- Consumes: `ApprovalGate` (Task 3), `RiskLevel` (Task 2).
- Produces: `RISK_MAP: dict[str, RiskLevel]`, `build_approval_gate(settings: dict) -> ApprovalGate`. Tasks 9 and 11 consume these.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the OS-domain safety policy binding."""

from agentkernel.risk import RiskLevel
from src.safety.policy import RISK_MAP, build_approval_gate

# Every action name the OODA agent's decide/act stages can emit, plus the
# profile-triggered actions. If you add an action to graph.py or main.py,
# it MUST get a canonical risk here.
EMITTED_ACTIONS = {
    "kill_process", "set_priority", "restart_service", "run_command",
    "cleanup_temp_files", "find_large_files", "trim_working_sets",
    "defer_windows_update",
}


def test_every_emitted_action_has_a_canonical_risk():
    missing = EMITTED_ACTIONS - set(RISK_MAP)
    assert not missing, f"actions without canonical risk: {missing}"


def test_run_command_is_canonically_high():
    assert RISK_MAP["run_command"] is RiskLevel.HIGH


def test_gate_from_settings_fails_closed_on_gated_action(sample_settings):
    gate = build_approval_gate(sample_settings)
    # run_command is in sample_settings' high_risk_actions; no callback wired.
    decision = gate.evaluate("run_command", upstream_risk=RiskLevel.LOW)
    assert decision.approved is False


def test_gate_from_settings_auto_approves_ungated_low(sample_settings):
    gate = build_approval_gate(sample_settings)
    decision = gate.evaluate("kill_process", upstream_risk=RiskLevel.LOW)
    assert decision.approved is True
    assert decision.auto is True


def test_unknown_action_defaults_high(sample_settings):
    gate = build_approval_gate(sample_settings)
    assert gate.evaluate("brand_new_action").approved is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.safety.policy'`.

- [ ] **Step 3: Implement `src/safety/policy.py`**

```python
"""OS-domain safety policy: canonical action risks + gate construction.

The kernel's ApprovalGate is domain-agnostic; this module binds it to the
OS agent's action vocabulary and the settings.yaml safety schema.
"""

from agentkernel.gate import ApprovalGate
from agentkernel.risk import RiskLevel

# Canonical risk for every action the agent can emit or a profile can
# trigger. tests/test_policy.py enforces completeness.
RISK_MAP: dict[str, RiskLevel] = {
    "kill_process": RiskLevel.LOW,
    "set_priority": RiskLevel.LOW,
    "trim_working_sets": RiskLevel.LOW,
    "cleanup_temp_files": RiskLevel.LOW,
    "find_large_files": RiskLevel.LOW,
    "restart_service": RiskLevel.MEDIUM,
    "defer_windows_update": RiskLevel.MEDIUM,
    "restart_bt_adapter": RiskLevel.MEDIUM,
    "disable_bt_power_saving": RiskLevel.MEDIUM,
    "run_command": RiskLevel.HIGH,
    "modify_power_settings": RiskLevel.HIGH,
    "modify_registry": RiskLevel.HIGH,
    "modify_driver": RiskLevel.HIGH,
}


def build_approval_gate(settings: dict) -> ApprovalGate:
    """Map settings.yaml's `safety:` section onto a kernel ApprovalGate.

    Unknown actions default to HIGH (stricter than the old HITLGate's
    MEDIUM): anything we haven't classified must face a human. Invalid
    risk-level strings in config raise ValueError — fail fast at startup,
    not silently at approval time.
    """
    safety = settings.get("safety", {})
    return ApprovalGate(
        risk_map=RISK_MAP,
        high_risk_actions=set(safety.get("high_risk_actions", [])),
        auto_approve_levels={
            RiskLevel(level) for level in safety.get("auto_approve_risk_levels", ["low"])
        },
        default_risk=RiskLevel.HIGH,
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_policy.py -v` — expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/safety/policy.py tests/test_policy.py
git commit -m "app: safety policy module binding settings to the kernel gate"
```

---

### Task 9: Rewire the OODA graph onto ApprovalGate

Replace `OODAAgent._hitl_gate`'s inline logic with `self.gate.evaluate(...)`. Behavior must be bit-identical for the existing integration tests: gated actions always hit the callback, forced-low can't downgrade, upstream high is never weakened, fail-closed without callback, `needs_hitl`/`hitl_approved`/`risk_level` state keys still written.

**Files:**
- Modify: `src/agent/graph.py` (imports at 15-28, `__init__` at 53-81, memory-reuse branch at ~218, `_hitl_gate` at 343-396)

**Interfaces:**
- Consumes: `build_approval_gate`, `RISK_MAP` (Task 8); `RiskLevel`, `parse_risk` (Task 2); `GateDecision` fields approved/risk/auto (Task 3).
- Produces: unchanged public surface — `OODAAgent(llm, tools, decision_engine, profile_manager, settings, memory=None, dry_run=False, audit_log=None)` and `set_hitl_callback(callback)`; callback now receives `agentkernel.gate.ApprovalRequest` (same field names as the old `HITLRequest`: action, target, risk_level, reason, timestamp — existing callbacks work unmodified).

- [ ] **Step 1: Update imports**

In `src/agent/graph.py`, replace:

```python
from src.safety.hitl import HITLGate, HITLRequest, RiskLevel
```

with:

```python
from agentkernel.risk import RiskLevel, parse_risk
from src.safety.policy import RISK_MAP, build_approval_gate
```

and delete the now-unused module constant (lines 25-28):

```python
# Severity ordering used to take the MORE severe of an upstream-assigned risk
# and an action's canonical risk — so a mislabelled/forced-low risk can never
# downgrade a consequential action below what its action type warrants.
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
```

- [ ] **Step 2: Build the gate in `__init__` and delegate the callback setter**

Replace (in `__init__`):

```python
        self._hitl_callback = None
```

with:

```python
        self.gate = build_approval_gate(settings)
```

Replace the whole `set_hitl_callback` method:

```python
    def set_hitl_callback(self, callback):
        """Set a callback for human-in-the-loop approval.
        callback(request: ApprovalRequest) -> bool (True = approved)
        """
        self.gate.set_approval_callback(callback)
```

- [ ] **Step 3: Point the memory-reuse branch at the policy RISK_MAP**

In `_decide`, replace:

```python
                    state["risk_level"] = HITLGate.RISK_MAP.get(
                        cached_action, RiskLevel.MEDIUM
                    ).value
```

with:

```python
                    state["risk_level"] = RISK_MAP.get(
                        cached_action, RiskLevel.MEDIUM
                    ).value
```

- [ ] **Step 4: Replace `_hitl_gate` with the gate call**

Replace the entire `_hitl_gate` method (currently ~54 lines) with:

```python
    def _hitl_gate(self, state: dict) -> dict:
        """Route the planned action through the fail-closed approval gate.

        The gate enforces the two safety properties this node used to
        implement inline: actions in settings.safety.high_risk_actions
        always face the approval callback (an upstream risk label can never
        silently auto-approve them), and no/raising callback means deny.
        """
        decision = self.gate.evaluate(
            action=state.get("planned_action", ""),
            target=str(state.get("action_args", {})),
            reason=state.get("diagnosis", {}).get("root_cause", ""),
            upstream_risk=parse_risk(state.get("risk_level", "medium")),
        )
        state["needs_hitl"] = not decision.auto
        state["hitl_approved"] = decision.approved
        state["risk_level"] = decision.risk.value
        return state
```

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass — especially `tests/test_integration.py::TestHITLGate` (or equivalently-named classes covering `test_low_risk_non_gated_action_auto_approved`, `test_high_risk_action_not_auto_approved_even_when_low_risk`) and `TestMemoryReuse`. If any gate test fails, the rewire changed semantics — fix the rewire, not the test.

- [ ] **Step 6: Verify line endings, then commit**

Run: `git diff --stat` — graph.py should show a focused diff, not a full rewrite. Normalize with `tr -d '\r'` if needed.

```bash
git add src/agent/graph.py
git commit -m "app: OODA graph delegates HITL to kernel ApprovalGate"
```

---

### Task 10: Rewire tools onto CommandSanitizer

`_ALLOWED_EXECUTABLES` (domain policy, with its rationale comment) stays in `tools.py`; the mechanism moves to the kernel. The module-level `_sanitize_command` function is kept as a thin binding so `tests/test_tools.py` passes unchanged.

**Files:**
- Modify: `src/agent/tools.py` (imports, delete `_DANGEROUS_PATTERNS` at 51-68, replace `_sanitize_command` body at 71-100)

**Interfaces:**
- Consumes: `CommandSanitizer` (Task 4).
- Produces: unchanged — `_sanitize_command(command: str) -> str | None`, `AgentTools`, `ActionResult`.

- [ ] **Step 1: Update imports**

In `src/agent/tools.py`, add after the `psutil` import:

```python
from agentkernel.sanitize import CommandSanitizer
```

and delete the now-unused `import re` and `import shlex` **only if** nothing else in the file uses them — check first: `grep -n "re\.\|shlex\." src/agent/tools.py`. (`shlex.split` is still used inside `run_command` at ~line 302 — keep `shlex`. `re` is only used by `_DANGEROUS_PATTERNS` — delete `import re` along with the patterns.)

- [ ] **Step 2: Delete `_DANGEROUS_PATTERNS` and replace `_sanitize_command`**

Delete the whole `_DANGEROUS_PATTERNS = [...]` block (lines 51-68 with its comment) and replace the `_sanitize_command` function with:

```python
_SANITIZER = CommandSanitizer(_ALLOWED_EXECUTABLES)


def _sanitize_command(command: str) -> str | None:
    """Validate a command string before execution.

    Kernel sanitizer bound to this module's read-only diagnostics allowlist.
    Returns the command unchanged if it passes all checks, or None if blocked.
    """
    return _SANITIZER.sanitize(command)
```

Keep the `_ALLOWED_EXECUTABLES` frozenset and its full rationale comment exactly as they are.

- [ ] **Step 3: Run the tools tests, then the full suite**

Run: `python -m pytest tests/test_tools.py -v` — expected: PASS, unchanged (shell interpreters blocked, mutating admin tools blocked, pipes/redirects/subshells blocked, restore-point ordering intact).
Run: `python -m pytest tests/ -q` — expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/agent/tools.py
git commit -m "app: tools bind kernel CommandSanitizer to the diagnostics allowlist"
```

---

### Task 11: Rewire main.py's profile gate; delete `src/safety/hitl.py` and `tests/test_hitl.py`

The profile-action gate in `main.py` is the last consumer of `src.safety.hitl`. Swap it to kernel types, then delete the dead module and its test file (every case in `tests/test_hitl.py` has a named equivalent in `tests/test_kernel_gate.py` — see Task 3's docstring).

**Files:**
- Modify: `main.py:368-388` (`_profile_action_approved`)
- Delete: `src/safety/hitl.py`, `tests/test_hitl.py`

**Interfaces:**
- Consumes: `ApprovalRequest` (Task 3), `RiskLevel` (Task 2).
- Produces: nothing new; `src.safety.hitl` ceases to exist.

- [ ] **Step 1: Rewire `_profile_action_approved`**

In `main.py`, replace inside `_profile_action_approved`:

```python
        from datetime import datetime as _dt
        from src.safety.hitl import HITLRequest, RiskLevel
        try:
            request = HITLRequest(
```

with:

```python
        from datetime import datetime as _dt
        from agentkernel.gate import ApprovalRequest
        from agentkernel.risk import RiskLevel
        try:
            request = ApprovalRequest(
```

(The rest of the function — `RiskLevel(risk)`, the callback invocation, the deny-on-exception — is unchanged. The console callback in `create_hitl_console_callback` only reads `request.action/target/risk_level/reason`, so it works with `ApprovalRequest` as-is.)

- [ ] **Step 2: Verify no remaining consumers, then delete**

Run: `grep -rn "safety.hitl\|HITLGate\|HITLRequest" src/ tests/ main.py gui.py dashboard.py`
Expected: hits ONLY in `src/safety/hitl.py` and `tests/test_hitl.py` themselves. If anything else appears, fix it before deleting.

```bash
git rm src/safety/hitl.py tests/test_hitl.py
```

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass. Net test count vs. the pre-plan 120: minus 8 (test_hitl.py) plus the ~40 kernel/policy tests added by Tasks 1-8.

- [ ] **Step 4: Smoke-check main.py imports cleanly**

Run: `python -c "import ast; ast.parse(open('main.py', encoding='utf-8').read()); print('main.py parses')"`
Then: `python main.py --help` — expected: argparse help prints without import errors (no API key needed for `--help`).

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "app: profile gate uses kernel types; remove dead HITLGate module"
```

(The `git rm` in Step 2 already staged the two deletions.)

---

### Task 12: Kernel public API, docs, final verification

**Files:**
- Modify: `agentkernel/__init__.py` (public API), `ARCHITECTURE.md` (kernel section)
- Create: `agentkernel/README.md`

**Interfaces:**
- Produces: `from agentkernel import ApprovalGate, RiskLevel, ...` works for future consumers.

- [ ] **Step 1: Fill in the public API**

Replace `agentkernel/__init__.py` with:

```python
"""agentkernel — a reusable substrate for safe autonomous action.

Extracted from the OS Agent (2026-07). Provides the trust components an
LLM-driven agent needs before it may touch a live system:

  risk       RiskLevel, max_severity, parse_risk
  gate       ApprovalGate (fail-closed HITL), ApprovalRequest, GateDecision
  sanitize   CommandSanitizer, DEFAULT_DANGEROUS_PATTERNS
  audit      ActionAuditLog, ActionRecord (append-only JSONL)
  costs      DailyCostLedger (persistent per-day spend cap)
  memory     FixMemory (ChromaDB-backed fix reuse; structured-action replay)

Dependency contract (enforced by tests/test_kernel_boundary.py): stdlib
only, except chromadb as an optional lazy import in `memory`. Never imports
the host app (src.*) or platform libraries.
"""

from agentkernel.audit import ActionAuditLog, ActionRecord
from agentkernel.costs import DailyCostLedger
from agentkernel.gate import ApprovalGate, ApprovalRequest, GateDecision
from agentkernel.memory import FixMemory
from agentkernel.risk import RiskLevel, max_severity, parse_risk
from agentkernel.sanitize import CommandSanitizer, DEFAULT_DANGEROUS_PATTERNS

__all__ = [
    "ActionAuditLog", "ActionRecord",
    "DailyCostLedger",
    "ApprovalGate", "ApprovalRequest", "GateDecision",
    "FixMemory",
    "RiskLevel", "max_severity", "parse_risk",
    "CommandSanitizer", "DEFAULT_DANGEROUS_PATTERNS",
]
```

- [ ] **Step 2: Write `agentkernel/README.md`**

```markdown
# agentkernel

A reusable substrate for **safe autonomous action** — the trust components
an LLM-driven agent needs before it may touch a live system. Extracted
from the OS Agent; designed to be lifted into its own repo unchanged.

## Components

| Module | What it does |
|---|---|
| `risk` | `RiskLevel` enum, severity merge (`max_severity`), lenient parsing |
| `gate` | `ApprovalGate` — fail-closed human approval. Actions you name as high-risk **always** face the callback; forced-low labels can't downgrade them; no callback or a raising callback means DENY |
| `sanitize` | `CommandSanitizer` — executable allowlist + dangerous-pattern denylist for LLM-generated commands. You supply the allowlist; never allowlist a shell interpreter |
| `audit` | Append-only JSONL log of every action (args, risk, outcome, duration) |
| `costs` | `DailyCostLedger` — persistent per-day LLM spend cap across restarts |
| `memory` | `FixMemory` — ChromaDB-backed recall of past fixes. Returns the *structured action* that succeeded, so replay never parses prose into a command |

## Wiring a consumer

```python
from agentkernel import ApprovalGate, CommandSanitizer, RiskLevel

gate = ApprovalGate(
    risk_map={"delete_file": RiskLevel.HIGH, "read_file": RiskLevel.LOW},
    high_risk_actions={"delete_file"},
    auto_approve_levels={RiskLevel.LOW},
)
gate.set_approval_callback(ask_the_human)   # without this, everything gated is DENIED

decision = gate.evaluate("delete_file", target="C:/tmp/x", reason="cleanup")
if decision.approved:
    ...
```

## Dependency contract

Stdlib only, except `chromadb` (optional, lazy) in `memory`. No imports of
the host app or platform libraries — enforced by
`tests/test_kernel_boundary.py`. Keep it that way; the boundary test is the
architecture.
```

- [ ] **Step 3: Add a kernel section to `ARCHITECTURE.md`**

Append to `ARCHITECTURE.md`:

```markdown
## agentkernel (extracted 2026-07)

The trust/autonomy substrate lives in the top-level `agentkernel/` package:
risk model, fail-closed ApprovalGate, CommandSanitizer, audit log, cost
ledger, and semantic fix memory. It has **no dependency on `src/` or any
platform library** (AST-enforced by `tests/test_kernel_boundary.py`), so it
can be reused by other agents (e.g. the personal assistant) or extracted to
its own repo without change.

The OS agent binds its domain policy to the kernel in two places:

- `src/safety/policy.py` — canonical `RISK_MAP` for every emitted action +
  `build_approval_gate(settings)` mapping `settings.yaml`'s `safety:`
  section onto the kernel gate.
- `src/agent/tools.py` — the read-only diagnostics `_ALLOWED_EXECUTABLES`
  allowlist bound to the kernel's `CommandSanitizer`.
```

- [ ] **Step 4: Final verification**

Run, in order:

1. `python -m pytest tests/ -q` — all pass.
2. `python -m pytest tests/test_kernel_boundary.py -v` — every kernel module checked, PASS.
3. `grep -rn "from src\.\|import src" agentkernel/` — no output.
4. `grep -rn "src.safety.hitl\|src.agent.audit\|src.agent.cost_ledger\|src.memory.store" src/ tests/ main.py gui.py dashboard.py` — no output.
5. `python -c "import agentkernel; print(sorted(agentkernel.__all__))"` — prints the API list.

- [ ] **Step 5: Commit**

```bash
git add agentkernel/__init__.py agentkernel/README.md ARCHITECTURE.md
git commit -m "kernel: public API, README, architecture docs"
```

---

## Self-Review Notes

- **Spec coverage:** HITL gate → Tasks 3/8/9/11; risk classification → Tasks 2/8; command sanitization → Tasks 4/10; semantic memory → Task 7; audit → Task 5; cost controls → Task 6; "rebuild the OS agent as a consumer" → Tasks 9/10/11; reusability guarantee → Tasks 1/12.
- **Behavior deltas (intentional, both stricter):** (1) unknown actions now default to HIGH risk at the gate (old `HITLGate.check` used MEDIUM; the old graph path already used HIGH for gated actions); (2) `cleanup_temp_files`/`find_large_files` gain explicit LOW canonical risks (previously absent from any map).
- **Type consistency check:** `ApprovalRequest` field names match the old `HITLRequest` exactly (action/target/risk_level/reason/timestamp) so `create_hitl_console_callback` and test callbacks work unmodified. `GateDecision.risk.value` writes back the same strings ("low"/"medium"/"high") the state dict already used. `FixMemory` keeps every method signature of `SemanticMemory` except the constructor, whose only two call sites are updated in Task 7.
