# Architecture

OS Agent is structured around an **OODA loop** (Observe → Orient → Decide →
Act → Verify) implemented with LangGraph. Each stage is a node in a state
graph; edges are deterministic except for two conditional routers (skip vs.
act, HITL approved vs. denied).

---

## Module map

```
src/
├── observer/           # Observe stage
│   ├── daemon.py           SystemObserver — samples psutil every N seconds,
│   │                       emits ObservationEvent on threshold breach
│   └── process_monitor.py  Tracks per-process memory growth for leak detection
│
├── agent/              # Orient → Decide → Act
│   ├── graph.py            OODAAgent — LangGraph state machine
│   ├── llm.py              LLMClient — tiered OpenAI (triage → complex)
│   │                       with circuit breaker and cost caps
│   ├── tools.py            AgentTools — kill, priority, restart, powercfg,
│   │                       run_command (allowlisted + sanitized)
│   ├── audit.py            ActionAuditLog — JSONL record of every action
│   └── cost_ledger.py      Persistent per-day USD spend tracker
│
├── decisions/          # 4-layer decision engine
│   ├── engine.py           Whitelist → blacklist → learned → AI
│   └── learning.py         BehavioralLearner — records user kill/keep votes
│
├── profiles/
│   └── manager.py          Detects active profile from foreground process
│                           + running processes, with asymmetric debouncing
│
├── memory/
│   └── store.py            SemanticMemory — ChromaDB for fix reuse
│
├── safety/
│   └── hitl.py             HITLGate — risk level → approval callback
│
├── audio/                  Active app audio detection + suggestions
├── bluetooth/              Event-log scraper + diagnostic engine
├── notifications/          Windows toasts via winotify
├── startup/                One-time startup program analyzer
├── config/                 Pydantic schemas (settings.yaml + profiles.yaml)
└── reliability/
    └── crash_reporter.py   sys.excepthook + threading.excepthook →
                            logs/crashes/<timestamp>.json
```

---

## OODA loop in detail

### 1. Observe — `SystemObserver.run()`

- Polls CPU/RAM/disk/GPU/thermal every `poll_interval_seconds` (5s AC, 30s battery)
- Compares against thresholds → emits `ObservationEvent(anomaly_type, severity, summary, details)`
- Cooldown prevents duplicate alerts for the same anomaly within `cooldown_minutes`
- Throttle check requires `cpu_percent ≥ 50` to avoid false alarms from speed-stepping

### 2. Orient — `OODAAgent._orient`

- Builds a text context block from the event: active profile, top processes,
  memory consumers
- This context is what the LLM sees — keep it dense and factual

### 3. Decide — `OODAAgent._decide`

Order of precedence:

1. **Decision engine** (for `MEMORY_LEAK`, `UNKNOWN_HOG`): whitelist → blacklist
   → learned from user votes → returns KILL/KEEP or ASK
2. **Semantic memory**: if ChromaDB finds a past event with similarity ≥
   `similarity_threshold`, reuse that fix
3. **LLM triage** (`gpt-5-mini`) for `CPU_SPIKE`/`RAM_HIGH`; escalates to
   `gpt-5` if confidence < `confidence_threshold`
4. **Rule-based** for `DISK_PRESSURE`, `THERMAL_THROTTLE`, `GPU_HIGH`

Produces `planned_action`, `action_args`, `risk_level`, `diagnosis`. Advisory
fixes with no actionable command route to `skip` instead of failing downstream.

### 4. HITL gate — `OODAAgent._hitl_gate`

- Auto-approves if `risk_level` in `safety.auto_approve_risk_levels`
- Otherwise calls registered callback (console prompt, GUI dialog, or headless
  default)
- Headless mode auto-approves low/medium, auto-denies high

### 5. Act — `OODAAgent._act`

- Dispatches to `AgentTools.<action>()`
- `--dry-run` mode short-circuits execution, still audits the intent
- Every action (dry-run or live) appends a record to `data/actions.jsonl`

### 6. Verify — `OODAAgent._verify`

- Success → write outcome to `SemanticMemory` (future reuse)
- Failure → log + notify via toast

---

## Threading model

The process has one main thread plus four daemon workers:

| Thread | Owner | Purpose | Period |
|---|---|---|---|
| main | observer | System snapshots + agent loop | 5–30s |
| `profile-detect` | `main.py::profile_loop` | Active profile + power plan switch | 10s |
| `bt-monitor` | `main.py::bt_loop` | Bluetooth diagnostic sweeps | 60s |
| `audio-suggest` | `main.py::audio_loop` | Volume/output suggestions | 15s |
| `watchdog` | `main.py::watchdog_loop` | Alerts if any above thread dies | 30s |

All workers check `shutdown_event` (a `threading.Event`) on every iteration so
`SIGTERM`/`SIGBREAK`/`KeyboardInterrupt` propagates cleanly — no orphan
threads, no Windows "program stopped responding" dialog on shutdown.

---

## Data locations

- `data/decisions.db` — SQLite: user kill/keep votes
- `data/chroma/` — vector store for semantic memory
- `data/actions.jsonl` — append-only action audit log
- `data/cost_ledger.json` — daily LLM spend by date
- `data/sessions.jsonl` — one row per process run (duration, tokens, cost)
- `logs/agent.log` — rotating 10MB × 3 backups
- `logs/crashes/<timestamp>.json` — dumps from unhandled exceptions

---

## Config validation

`src/config/schema.py` defines Pydantic v2 models for `settings.yaml` and
`profiles.yaml`. On startup:

1. YAML is parsed (syntax errors fail fast)
2. Dict is validated against the schema (`extra="forbid"` — typos fail fast)
3. Field-level errors are printed with JSON path + message
4. Process exits with code 1 on any failure

This replaces the previous "collect warnings and continue" approach, which
let bad config through until something downstream crashed.

---

## Cost control

Two independent gates:

- **Session cap** (`cost_cap_usd`): in-memory; resets on restart. Soft
  boundary for runaway loops.
- **Daily cap** (`daily_cost_cap_usd`): persisted to `data/cost_ledger.json`,
  keyed by `YYYY-MM-DD`. Survives restarts. Hard boundary.

The daily ledger uses model-aware pricing (triage vs. complex); the session
stat is a lower-bound approximation using triage rates.

Entries older than 30 days are garbage-collected on each write.

---

## Crash reporting

`install_crash_reporter()` installs:

- `sys.excepthook` for unhandled exceptions on the main thread
- `threading.excepthook` for worker threads (Python 3.8+)

Each crash is dumped as a JSON file containing:

- Traceback (including chained causes)
- Last 200 log lines from a ring-buffer handler attached to root
- Runtime snapshot: Python version, OS, PID, CWD, argv, thread count
- Exception type and message

`KeyboardInterrupt` is explicitly excluded — clean Ctrl-C is not a crash.

Zero network egress. Retention: 50 dumps (older ones pruned).
