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
