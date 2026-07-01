# OS Agent

A personal Windows 11 system optimizer. Watches your computer, detects
anomalies (CPU/RAM/disk/GPU/thermal/Bluetooth), diagnoses them with an LLM, and
applies fixes — with your approval for anything risky.

Runs as a background daemon with a console dashboard and Windows toast
notifications. Safe by default: every high-risk action goes through a
human-in-the-loop gate.

---

## Quick start

**Requirements**

- Windows 10/11
- Python 3.11+
- An OpenAI API key (set `OPENAI_API_KEY` as an environment variable)

**Install**

```bash
git clone https://github.com/AKAAKS/OS_agent.git
cd OS_agent
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Run**

Console mode (default):

```bash
python main.py
```

System-tray + details window:

```bash
python gui.py
```

The GUI provides a status icon, a right-click menu, and a four-tab details
window (Status, Actions, Crashes, Settings). Double-click the tray icon to
open it.

To validate the agent without it touching your system, run in dry-run mode:

```bash
python main.py --dry-run
# or
python gui.py --dry-run
```

In dry-run mode the agent still observes, diagnoses, and logs every action it
*would* take — but nothing is executed. Inspect `data/actions.jsonl` to see
what it planned.

**Autostart on login (optional)**

```bash
install_autostart.bat
```

To remove:

```bash
uninstall_autostart.bat
```

---

## What it does

| Anomaly | Response |
|---|---|
| CPU spike (>95% sustained) | Identify top process → kill or lower priority (with HITL for user apps) |
| RAM high (>85%) | Trim working sets, identify leaker, kill if decision engine agrees |
| Disk pressure (>90%) | Find large files; >95% → clean old temp files |
| GPU/thermal | Advisory-only (no auto-fix) |
| Memory leak | Track growth over 30 min, offer kill/keep decision |
| Unknown high-CPU process | LLM classification → 4-layer decision engine |
| Bluetooth issues | Monitor event log, diagnose recurring errors |

---

## Safety guarantees

- **Protected PIDs/names**: `csrss.exe`, `wininit.exe`, `winlogon.exe`, `smss.exe`,
  `services.exe`, `lsass.exe`, `svchost.exe`, `dwm.exe`, `explorer.exe`, `System`
- **User apps never auto-killed**: all major browsers, Office, OneNote, Outlook,
  editors/IDEs, Claude desktop
- **HITL gate**: `kill_process`, `restart_service`, `run_command`,
  `modify_registry` all require explicit approval (or auto-deny in headless
  mode)
- **Command sanitizer**: allowlisted executables only; blocks pipes,
  redirects, `Invoke-WebRequest`, `IEX`, download cradles, `rm -rf`, disk format
- **Daily + per-session LLM cost caps** (persisted to `data/cost_ledger.json`)
- **Circuit breaker** on repeated LLM errors
- **Audit log** of every action, dry-run or live, at `data/actions.jsonl`
- **Crash reporter** writes a self-contained JSON dump (traceback + log tail +
  environment) to `logs/crashes/<timestamp>.json` — never leaves your machine

---

## Configuration

Two YAML files, both validated on startup with Pydantic schemas (invalid
config → the process exits with a field-level error report):

- `config/settings.yaml` — thresholds, LLM model, cost caps, logging
- `config/profiles.yaml` — per-profile process lists (gaming/coding/streaming/idle)

Edit and restart. No reload required.

---

## Architecture overview

```
 ┌─ Observer daemon (psutil + WMI) ────── SystemSnapshots
 │     │
 │     ↓
 │  Anomaly detector ──────────────────── ObservationEvents
 │     │
 │     ↓
 │  OODA agent loop (LangGraph)
 │     │  Orient → Decide → HITL gate → Act → Verify
 │     │            │            │        │
 │     │            ↓            ↓        ↓
 │     │         LLM triage   Console   AgentTools
 │     │         + complex    prompt    (psutil/powercfg/
 │     │         escalation                powershell)
 │     │            │
 │     │            ↓
 │     │     Semantic memory (ChromaDB)
 │     │            │
 │     │            ↓
 │     │        Audit log (data/actions.jsonl)
 │     ↓
 │  Toast notifier + Rich dashboard
 │
 └─ Background: profile detection, BT monitor, audio suggestions, watchdog
```

Full module walkthrough: [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Testing

```bash
python -m pytest tests/ -v
```

Currently 111 tests covering: anomaly detection, decision engine, process
monitor, LLM client, profile manager, config schema, cost ledger, action
audit, crash reporter, and full OODA integration flows.

---

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for known issues: headless mode
behavior, toast notifications not appearing, admin-required actions, ChromaDB
init failures, cost cap behavior.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

---

## License

Personal project. No warranty. Use at your own risk — this program has the
ability to kill processes, restart services, and run PowerShell commands on
your machine.
