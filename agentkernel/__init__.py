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
