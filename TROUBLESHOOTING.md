# Troubleshooting

## Startup

### "Configuration failed validation"

The Pydantic schema rejected something in `config/settings.yaml` or
`config/profiles.yaml`. The error message lists every bad field by path.
Common causes:

- Typo in a key (schemas use `extra="forbid"` — unknown keys fail)
- Wrong type (e.g. `cpu_threshold_percent: "95"` as a string)
- Out-of-range value (e.g. `confidence_threshold: 1.5`)
- Unknown profile name (must be one of: `idle`, `coding`, `gaming`,
  `streaming`, `browsing`, `media`, `comms`)

Fix the config and restart.

### "No OPENAI_API_KEY configured"

The LLM client will return stubbed error responses. Set the env var:

```cmd
setx OPENAI_API_KEY "sk-..."
```

Restart the shell so the variable takes effect.

---

## Runtime

### Toasts not appearing

The agent registers a Windows `AppUserModelID` (`Python.OS_Agent.SystemOptimizer`)
on startup. If toasts still don't show:

1. Check Windows Settings → System → Notifications → verify "Focus Assist" is
   off
2. Check that the "Python.OS_Agent.SystemOptimizer" app is not blocked in
   Notifications & actions
3. Test with: `python -c "from src.notifications.toasts import ToastNotifier; \
   t = ToastNotifier(); t.notify('Test', 'Hello from OS Agent')"`

### Actions fail with "Access denied"

`set_process_priority`, `restart_service`, `defer_windows_update`, and
`modify_registry` typically need admin. Either:

- Run the agent from an elevated terminal, or
- Accept that these actions will fail gracefully and be logged as such

The agent never escalates on its own.

### Power plan doesn't switch

`powercfg` requires admin for `/setactive` on some Windows builds. Run
elevated, or disable the feature by mapping all profiles to the same plan in
`main.py::_PROFILE_POWER_MAP`.

### ChromaDB init failed

```
ERROR src.memory.store: Semantic memory failed to init
```

This is non-fatal — the agent continues without fix reuse. To recover:

1. Stop the agent
2. Delete `data/chromadb/`
3. Restart — a fresh collection is created

### "Session/daily cost cap reached"

The agent is blocking LLM calls to stay within budget. Check:

- `data/cost_ledger.json` for today's spend
- `config/settings.yaml`: `cost_cap_usd` (per session) and `daily_cost_cap_usd`

Raise the cap, restart, and re-check. The daily cap is authoritative across
restarts.

### Circuit breaker tripped

After 3 consecutive identical LLM errors, the client stops calling. It resets
on the next successful call, or manually:

```python
from src.agent.llm import LLMClient
llm.reset_circuit_breaker()
```

Most common causes: invalid API key, expired key, 429 rate limit, network
outage.

---

## Shutdown / crashes

### "OS Agent stopped responding" dialog on PC shutdown

This means the signal handlers didn't catch the shutdown signal. Check
`logs/agent.log` for entries near the time of shutdown. If missing entirely,
the process was SIGKILL'd before graceful shutdown could run.

The agent handles `SIGTERM` and `SIGBREAK` (Windows) and `KeyboardInterrupt`.
If shutdown dialogs persist, the scheduled task config may be sending an
immediate kill instead of a graceful term.

### Reading crash dumps

```bash
ls logs/crashes/
```

Each file is self-contained JSON. Useful fields:

- `exception_type` / `exception_message`
- `traceback` (full, with chained causes)
- `recent_logs` (last 200 lines before the crash)
- `context.thread_count`, `context.pid`

No network egress — dumps stay local. Delete freely; retention is 50 dumps.

### Background thread died

The watchdog thread logs `CRITICAL` and toasts "Thread Crash" if any worker
dies. The agent does not auto-restart the thread — restart the whole process:

```bash
# Kill existing
tasklist /fi "imagename eq python.exe"
taskkill /pid <PID> /f
python main.py
```

---

## Development

### Tests fail: "ImportError: cannot import name 'DecisionResult'"

Out-of-date test file. Rebase onto current main or re-run `git pull`.

### Adding a new action

1. Add the method to `src/agent/tools.py::AgentTools`
2. Add a branch in `src/agent/graph.py::OODAAgent._act`
3. If risky, add the name to `safety.high_risk_actions` in `settings.yaml`
4. Add unit tests in `tests/test_tools.py` and a branch test in
   `tests/test_integration.py`

### Adding a new anomaly type

1. Add to `AnomalyType` enum in `src/observer/daemon.py`
2. Wire the detection in `SystemObserver.run()`
3. Add a handler branch in `OODAAgent._decide`
4. Add fixture + test in `tests/test_integration.py`
