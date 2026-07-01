# Changelog

## Unreleased — Production-quality release

### Added

- **Pydantic config schema** (`src/config/schema.py`). YAML files are now
  validated on startup with field-level error messages; invalid config exits
  the process instead of limping along.
- **Persistent daily cost ledger** (`src/agent/cost_ledger.py`). Tracks LLM
  spend by date in `data/cost_ledger.json`, survives restarts, auto-prunes
  entries older than 30 days. New `daily_cost_cap_usd` setting.
- **Action audit log** (`src/agent/audit.py`). Every executed or dry-run
  action is appended to `data/actions.jsonl` with before/after context, risk
  level, profile, and timing.
- **`--dry-run` mode**. Full OODA loop runs and audits, but actions are never
  executed. Use to validate behavior before going live.
- **Local crash reporter** (`src/reliability/crash_reporter.py`). Installs
  `sys.excepthook` + `threading.excepthook`. Dumps unhandled exceptions to
  `logs/crashes/<timestamp>.json` with traceback, log tail (200 lines), and
  runtime context. Zero network egress.
- **Integration tests** covering full OODA flow: CPU spike, HITL gate, dry
  run, disk pressure, thermal/GPU advisory.
- Documentation: `README.md`, `ARCHITECTURE.md`, `TROUBLESHOOTING.md`,
  `CHANGELOG.md`.
- **System-tray GUI** (`gui.py`, `src/gui/tray.py`). PyQt6 tray icon with
  status indicator + tooltip (shows today's LLM cost), right-click menu, and
  a 4-tab details window: Status (cost history), Actions (audit log),
  Crashes (dump viewer), Settings (config preview).

### Changed

- Model-aware cost estimation. Previously used `gpt-4o-mini` rates for all
  calls; now distinguishes triage (mini) vs. complex ($5/$15 per 1M).
- `LLMClient` session cost cap message updated from "Cost cap reached" to
  "Session cost cap reached" to distinguish from daily cap.
- Config pydantic dependency added explicitly to `requirements.txt` (was
  transitive via langgraph).

### Removed

- Manual `validate_config()` function in `main.py` — superseded by Pydantic
  schema.

## Previous session — Reliability fixes

### Fixed

- Thermal throttle false alarms at idle (speed-stepping misread as throttling).
  `_check_thermal_throttle` now requires `cpu_percent ≥ 50`.
- Profile flip-flopping on alt-tab. Longer `EXIT_DEBOUNCE_SECONDS` (browsing
  20→45, media 20→30); `None` foreground returns no longer force idle switch.
- Bluetooth LLM spam (897 wasted calls). Stricter event classification + 2+
  error events required before escalation + cooldown raised from 10 min to
  30 min.
- Actions failing with missing PID/service_name. Routing now requires valid
  args; falls through to `skip` with advisory log otherwise.
- ChromaDB `NoneType` errors. Auto-reinit on write failure with fallback to
  disable.
- Browser being killed unprompted (firefox.exe, 8+ occurrences). New
  `_NEVER_KILL_USER_APPS` blocklist covering all major browsers, Office,
  editors, IDEs.
- Toasts silently dropped. Now registers proper `AppUserModelID`
  (`Python.OS_Agent.SystemOptimizer`) via `SetCurrentProcessExplicitAppUserModelID`.
- "Program stopped responding" dialog on Windows shutdown. Added
  `SIGTERM`/`SIGBREAK` handlers in `main.py` for graceful shutdown.

### Added

- Power plan display on dashboard (below active profile).
- `complex_model: gpt-5` (upgraded from `gpt-5-mini`).
- Session stats persistence to `data/sessions.jsonl`.
