# OS Agent — Complete Project Guide

A personal Windows 11 system optimizer that continuously monitors your computer, detects problems (CPU spikes, memory leaks, Bluetooth issues, etc.), diagnoses them using AI, and fixes them automatically — with your approval for risky actions.

---

## Table of Contents

1. [How It Works — The Big Picture](#1-how-it-works--the-big-picture)
2. [Project Structure](#2-project-structure)
3. [The OODA Loop (Core Brain)](#3-the-ooda-loop-core-brain)
4. [System Observer (The Eyes)](#4-system-observer-the-eyes)
5. [Profile System (Context Awareness)](#5-profile-system-context-awareness)
6. [Decision Engine (The 4-Layer Brain)](#6-decision-engine-the-4-layer-brain)
7. [LLM Integration (AI Diagnostics)](#7-llm-integration-ai-diagnostics)
8. [Agent Tools (The Hands)](#8-agent-tools-the-hands)
9. [Safety System (The Guardrails)](#9-safety-system-the-guardrails)
10. [Semantic Memory (Learning From Past)](#10-semantic-memory-learning-from-past)
11. [Behavioral Learning](#11-behavioral-learning)
12. [Bluetooth Subsystem](#12-bluetooth-subsystem)
13. [Audio Subsystem](#13-audio-subsystem)
14. [Notification System](#14-notification-system)
15. [Dashboard & UI](#15-dashboard--ui)
16. [Threading Architecture](#16-threading-architecture)
17. [Configuration](#17-configuration)
18. [Data Storage](#18-data-storage)
19. [Testing](#19-testing)
20. [Dependencies](#20-dependencies)
21. [How to Run](#21-how-to-run)

---

## 1. How It Works — The Big Picture

The agent runs a continuous loop inspired by military decision-making called **OODA** (Observe → Orient → Decide → Act). Here's the flow in plain English:

```
1. OBSERVE  →  Monitor CPU, RAM, disk, GPU, processes every 5 seconds
2. ORIENT   →  "Is this normal?" — compare against thresholds
3. DECIDE   →  If something's wrong, figure out what to do about it
4. ACT      →  Execute the fix (kill a process, restart a service, etc.)
5. VERIFY   →  Did it work? Store the outcome for next time
```

**Example scenario:**
- Observer sees RAM at 92% for 2 minutes
- It creates an anomaly event: "RAM_HIGH"
- The OODA agent gathers context (which processes are using the most memory)
- Decision engine checks: is the top process on the whitelist? Kill list? Unknown?
- If unknown, the LLM is asked: "Should we kill `mystery.exe` using 4GB of RAM?"
- LLM says "kill it, it's a background updater, low risk"
- Safety gate checks: is this low risk? → Auto-approve
- Agent kills the process
- Result stored in memory: "mystery.exe hogging RAM → killed → success"
- Next time mystery.exe acts up, the agent remembers and skips the LLM call

---

## 2. Project Structure

```
OS_agent/
├── main.py                         # Entry point — wires everything together
├── dashboard.py                    # Standalone terminal dashboard (Rich library)
├── config/
│   ├── settings.yaml               # Thresholds, API keys, safety rules
│   └── profiles.yaml               # Gaming/Coding/Streaming profiles
├── src/
│   ├── agent/
│   │   ├── graph.py                # OODA loop via LangGraph state machine
│   │   ├── llm.py                  # OpenAI API wrapper with tiered models
│   │   └── tools.py                # System actions (kill process, set priority, etc.)
│   ├── observer/
│   │   ├── daemon.py               # System monitor (CPU, RAM, disk, GPU)
│   │   └── process_monitor.py      # Per-process memory leak detector
│   ├── profiles/
│   │   └── manager.py              # Auto-detects Gaming/Coding/Streaming/Idle mode
│   ├── decisions/
│   │   ├── engine.py               # 4-layer keep/kill decision engine
│   │   └── learning.py             # Tracks user decisions with full context
│   ├── safety/
│   │   ├── hitl.py                 # Human-in-the-loop approval gate
│   │   └── sandbox.py              # Docker sandbox for testing risky commands
│   ├── memory/
│   │   ├── store.py                # ChromaDB vector store for fix memory
│   │   └── embeddings.py           # Local sentence-transformers embeddings
│   ├── notifications/
│   │   └── toasts.py               # Windows toast notifications (winotify)
│   ├── bluetooth/
│   │   ├── monitor.py              # BT device & event monitoring via PowerShell
│   │   ├── diagnostics.py          # Pattern-based BT issue diagnosis
│   │   └── actions.py              # BT fix actions (restart adapter, etc.)
│   ├── audio/
│   │   ├── monitor.py              # Audio session & device monitoring (pycaw)
│   │   ├── suggestions.py          # Smart audio routing suggestions
│   │   └── widget.py               # PyQt6 audio overlay widget
│   └── dashboard/
│       └── widget.py               # PyQt6 system dashboard overlay widget
├── data/
│   ├── decisions.db                # SQLite — learned user decisions
│   └── chromadb/                   # ChromaDB — semantic fix memory
├── logs/
│   └── agent.log                   # Rotating log (10MB × 3 backups)
├── tests/
│   ├── conftest.py                 # Shared test fixtures
│   ├── test_decisions.py           # Decision engine tests
│   ├── test_hitl.py                # HITL safety gate tests
│   ├── test_llm.py                 # LLM client tests
│   ├── test_profiles.py            # Profile detection tests
│   ├── test_tools.py               # Agent tools tests
│   └── test_config.py              # Config validation tests
├── requirements.txt                # Python dependencies
├── install_autostart.bat           # Register as Windows startup task
└── uninstall_autostart.bat         # Remove startup task
```

---

## 3. The OODA Loop (Core Brain)

**File:** `src/agent/graph.py`

The OODA loop is implemented as a **LangGraph state machine** — a directed graph where each node is a step, and edges define the flow between steps.

### State

Every event flowing through the loop carries this state:

```python
class AgentState:
    event: ObservationEvent      # What happened (CPU spike, memory leak, etc.)
    context: str                 # Gathered info about the situation
    diagnosis: dict              # LLM's analysis (root cause, fix, risk)
    planned_action: str          # What to do ("kill_process", "restart_service", etc.)
    action_args: dict            # Arguments for the action (PID, service name, etc.)
    risk_level: str              # "low", "medium", "high"
    action_result: ActionResult  # Did the action succeed?
    needs_hitl: bool             # Does this need human approval?
    hitl_approved: bool          # Did the human approve?
```

### Graph Flow

```
START → orient → decide → [should_act?]
                              ├── "skip" → END (nothing to do)
                              └── "act"  → hitl_gate → [approved?]
                                                          ├── "denied"   → END
                                                          └── "approved" → act → verify → END
```

### Step-by-Step Breakdown

1. **Orient (`_orient`):** Gathers context — active profile, top processes, memory consumers. Builds a text summary for the LLM.

2. **Decide (`_decide`):** The decision-making hub. Tries multiple strategies in order:
   - **Decision engine first** (for process-specific anomalies): checks whitelist → profile → history
   - **Semantic memory** (ChromaDB): "Have we seen a similar problem before?" If yes, reuse the fix
   - **LLM** (OpenAI API): For CPU/RAM anomalies, asks the LLM for a diagnosis. If confidence is low, escalates to the complex model
   - If nothing matched, sets `planned_action = "skip"`

3. **HITL Gate (`_hitl_gate`):** Checks if the action needs human approval based on risk level. Low-risk actions are auto-approved. High-risk actions require explicit user confirmation (or are auto-denied in headless mode).

4. **Act (`_act`):** Executes the planned action using `AgentTools` — kill process, set priority, restart service, or run a command.

5. **Verify (`_verify`):** Checks if the action succeeded. Stores the outcome (anomaly + fix + success/fail) in semantic memory for future reuse.

---

## 4. System Observer (The Eyes)

**File:** `src/observer/daemon.py`

Continuously monitors system health and raises anomaly events.

### What It Monitors

| Metric | How | Threshold (default) |
|--------|-----|-------------------|
| CPU | `psutil.cpu_percent()` | >95% sustained for 120s |
| RAM | `psutil.virtual_memory()` | >85% |
| Disk | `psutil.disk_usage("C:\\")` | >90% |
| GPU | `nvidia-smi` subprocess | >95% |
| Process crashes | Tracking top PIDs between snapshots | PID vanishes from top 10 |

### Anomaly Types

```python
class AnomalyType(Enum):
    CPU_SPIKE = "cpu_spike"          # CPU pegged high for too long
    RAM_HIGH = "ram_high"            # RAM usage above threshold
    DISK_PRESSURE = "disk_pressure"  # Disk almost full
    GPU_HIGH = "gpu_high"            # GPU maxed out
    PROCESS_CRASH = "process_crash"  # Important process disappeared
    MEMORY_LEAK = "memory_leak"      # Process memory growing steadily
    UNKNOWN_HOG = "unknown_hog"      # Unknown process eating resources
```

### Key Behaviors

- **Polling interval**: 5 seconds on AC power, 30 seconds on battery
- **Cooldown**: After detecting an anomaly, suppresses the same type for 10 minutes (prevents spam)
- **Battery awareness**: Can pause entirely on battery if configured
- **Error recovery**: Exponential backoff on repeated failures (2s → 4s → 8s → ... max 60s). Logs CRITICAL after 5+ consecutive failures
- **Disk error handling**: If `disk_usage("C:\\")` throws an OSError (e.g., permissions), uses a stub with 0% so the snapshot still works

### SystemSnapshot

Each poll produces a snapshot containing:
```python
class SystemSnapshot:
    cpu_percent: float
    cpu_per_core: list[float]
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disk_percent: float
    gpu_percent: float | None      # None if no NVIDIA GPU / nvidia-smi not found
    gpu_memory_percent: float | None
    top_processes: list[dict]       # Top 10 by CPU + memory
```

### Process Memory Leak Detector

**File:** `src/observer/process_monitor.py`

Tracks per-process memory over time. If a process's memory grows by **500MB+ in 30 minutes**, it's flagged as a memory leak.

- Stores up to 360 samples per process (~30 min at 5s intervals)
- Cleans up processes that leave the top list after 5 minutes
- Only alerts once per PID (tracks `_alerted_pids`)

---

## 5. Profile System (Context Awareness)

**File:** `src/profiles/manager.py`
**Config:** `config/profiles.yaml`

The agent behaves differently based on what you're doing. It auto-detects your activity and switches profiles.

### Profiles

| Profile | Detected By | Effect |
|---------|-------------|--------|
| **Gaming** | Steam, Epic, Battle.net running (background scan, needs 2+ matches) | Keeps game + Discord, kills OneDrive/Teams/Widgets |
| **Coding** | VS Code, Visual Studio, PyCharm in foreground | Keeps IDE + Terminal + Docker, kills Widgets |
| **Streaming** | OBS, Streamlabs running (background scan) | Keeps browsers + Discord, kills OneDrive |
| **Browsing** | Chrome, Edge, Firefox in foreground | Detected by foreground window |
| **Media** | Spotify, VLC in foreground | Detected by foreground window |
| **Comms** | Discord, Teams, Slack, Zoom in foreground | Detected by foreground window |
| **Idle** | None of the above | Default fallback |

### Detection Strategy

1. **Foreground window** (highest priority): Uses Win32 `GetForegroundWindow()` API via ctypes to find what app you're actively using. Maps exe name to profile.
2. **Background scan** (for Gaming/Streaming only): Scans all running processes. Gaming needs 2+ matching processes to activate (prevents false positives from having Steam open but not playing).
3. **Idle fallback**: If nothing matches, defaults to Idle.

### Debouncing

To prevent rapid profile switching when alt-tabbing (e.g., VS Code ↔ Chrome), a new profile must be **consistently detected for 10 seconds** before the switch happens.

```python
DEBOUNCE_SECONDS = 10
```

How it works:
- New profile detected → start debounce timer
- Same profile detected again → check if 10s have passed
- Different profile detected → reset timer
- Timer expires → commit the switch

### Process Classification

Each profile defines three lists:
- **`always_keep`**: Never kill these (e.g., `code.exe` in coding mode)
- **`kill_candidates`**: Kill these to free resources (e.g., `Widgets.exe`)
- **`trigger_processes`**: Processes that activate this profile

The global whitelist (critical Windows processes like `csrss.exe`, `svchost.exe`, `explorer.exe`) overrides everything — these are never killed regardless of profile.

---

## 6. Decision Engine (The 4-Layer Brain)

**File:** `src/decisions/engine.py`

When the agent encounters a process and needs to decide keep/kill, it goes through 4 layers:

```
Layer 1: Config Rules          → Instant, 100% confidence
   ├── Global whitelist (csrss, svchost, etc.)    → KEEP
   └── Global blacklist (known bloatware)          → KILL

Layer 2: Profile Rules         → Instant, 90-95% confidence
   ├── Profile always_keep (code.exe in coding)   → KEEP
   └── Profile kill_candidates (widgets.exe)       → KILL

Layer 3: Behavioral History    → Instant (SQLite), 85% confidence
   └── If user chose KILL 3+ times with >75% consistency → auto-KILL

Layer 4: Unknown               → Needs LLM or user input
   └── Returns ASK → caller prompts user or queries LLM
```

### Key Details

- **Profile-scoped history**: Decisions learned in gaming mode don't apply in coding mode. You might want `teams.exe` killed during gaming but kept during coding.
- **Null safety**: If process name is None/empty, returns KEEP with layer="safety" (prevents crashes)
- **SQLite storage**: Decisions stored in `data/decisions.db` with indexed queries on `(process_name, profile)`
- **All DB operations** wrapped in `try/except` with `timeout=5` to prevent hangs on locked databases

---

## 7. LLM Integration (AI Diagnostics)

**File:** `src/agent/llm.py`

Uses OpenAI's API with a **tiered model strategy**:

### Model Tiers

| Tier | Model (configured) | When Used |
|------|-------------------|-----------|
| Triage | `gpt-5-mini` | First attempt at diagnosis |
| Complex | `gpt-5-mini` | Escalated when triage confidence < 70% |

### How It Works

1. **Prompt engineering**: Each call includes structured context (process info, system state, profile) and asks for JSON output with `root_cause`, `fix`, `risk_level`, and `confidence`
2. **Confidence-based escalation**: If the triage model returns confidence < threshold (0.7), the same prompt is sent to the complex model with additional context
3. **Cost cap**: Tracks total tokens used. Once estimated cost exceeds `$1.00` (configurable), all LLM calls are blocked for the session
4. **Thread safety**: Token counters protected by `threading.Lock`

### Safety Features

- **Empty choices guard**: If the API returns an empty `choices` list, returns an error response instead of crashing
- **Rate limit detection**: Detects 429 errors and logs a clear warning
- **Timeout detection**: Logs timeout errors separately from other failures
- **No API key**: Gracefully returns error JSON instead of crashing

### Token Cost Tracking

```python
# Approximate pricing (configured for gpt-5-mini)
input_cost  = tokens × $0.15 / 1M
output_cost = tokens × $0.60 / 1M
```

---

## 8. Agent Tools (The Hands)

**File:** `src/agent/tools.py`

These are the actions the agent can take on the system.

### Available Actions

| Action | What It Does | Risk |
|--------|-------------|------|
| `kill_process(pid)` | Terminates a process | Low |
| `set_process_priority(pid, priority)` | Changes CPU priority (low/normal/high/realtime) | Low |
| `restart_service(name)` | Stops and starts a Windows service | Medium |
| `defer_windows_update()` | Pauses Windows Update for 7 days | Medium |
| `trim_working_sets()` | Frees idle process memory via `EmptyWorkingSet` | Low |
| `run_command(cmd)` | Runs a sanitized shell command | High |

### Safety Guards

**Protected PIDs**: PIDs 0 (System Idle) and 4 (System) are hardcoded as unkillable.

**Protected process names**: `csrss.exe`, `wininit.exe`, `winlogon.exe`, `smss.exe`, `services.exe`, `lsass.exe`, `svchost.exe`, `dwm.exe`, `explorer.exe`, `system` — these are blocked even if the decision engine says kill.

**Command sanitization** (for `run_command`):
1. **Executable allowlist**: Only these commands can run: `powershell`, `cmd`, `net`, `sc`, `tasklist`, `taskkill`, `wmic`, `systeminfo`, `ipconfig`, `netstat`, `sfc`, `dism`, `chkdsk`, `nvidia-smi`, `ping`, `tracert`, `nslookup`
2. **Dangerous pattern blocking**: Blocks `&&`, `||`, pipes `|`, redirects `>/<`, backtick subshells, `Invoke-WebRequest`, `Invoke-Expression`, `IEX(`, `Start-Process`, `DownloadString`, `rm -rf`, `format C:`, `del /s`
3. **No shell**: Commands run with `shell=False` and explicit token splitting
4. **Output cap**: Captured output truncated to 1MB to prevent OOM
5. **Timeout**: Default 30s timeout on all subprocesses
6. **Hidden windows**: All subprocesses use `CREATE_NO_WINDOW` flag

### Process Kill Flow

```
kill_process(pid)
  → Is PID 0 or 4? → REFUSE
  → Get process name
  → Is name in _PROTECTED_NAMES? → REFUSE
  → Try terminate() + wait(5s)
  → If TimeoutExpired → Retry with kill() (force=True)
  → If AccessDenied → Return error ("may need admin")
```

---

## 9. Safety System (The Guardrails)

### HITL (Human-in-the-Loop)

**File:** `src/safety/hitl.py`

Every action goes through a risk assessment before execution.

**Risk classification:**

| Action | Default Risk |
|--------|-------------|
| kill_process | Low |
| set_priority | Low |
| trim_working_sets | Low |
| restart_service | Medium |
| defer_windows_update | Medium |
| restart_bt_adapter | Medium |
| run_command | High |
| modify_registry | High |
| modify_driver | High |

**Flow:**
```
Action requested
  → Is risk level in auto_approve_levels? (default: ["low"])
      → YES → Execute immediately
      → NO  → Is action in high_risk_actions list?
          → YES → Prompt user for approval
          → NO  → Execute
```

**Headless mode** (no console, e.g., running as a Windows service):
- Low/Medium risk → auto-approve
- High risk → auto-deny (and log the denial)

**Console mode**: Shows a formatted prompt:
```
==================================================
  ACTION APPROVAL REQUIRED
  Action: kill_process
  Target: {'pid': 12345}
  Risk:   high
  Reason: Unknown process using 4GB RAM
==================================================
  Approve? (y/n):
```

### Docker Sandbox

**File:** `src/safety/sandbox.py`

Can test risky commands in a disposable Docker container before running them on the real system. Uses `mcr.microsoft.com/windows/nanoserver:ltsc2022` with resource limits (512MB RAM, 0.5 CPU). Falls back gracefully if Docker isn't available.

---

## 10. Semantic Memory (Learning From Past)

**File:** `src/memory/store.py`

Uses **ChromaDB** (a vector database) to remember past anomalies and their fixes.

### How It Works

1. **Storing a fix**: After the agent successfully fixes an anomaly, it stores the anomaly description + fix + outcome as a vector embedding in ChromaDB
2. **Finding similar fixes**: When a new anomaly occurs, the agent searches ChromaDB for similar past anomalies using cosine similarity
3. **Similarity threshold**: Default 0.92 — only reuse a fix if it's a very close match
4. **Only successful fixes**: Only returns fixes that previously succeeded (`success == "True"`)

### Example Flow

```
First time:  "chrome.exe using 3.2GB RAM" → LLM diagnoses → kill → success → stored
Second time: "chrome.exe using 3.5GB RAM" → similarity=0.96 → reuse "kill" → skip LLM call ($0 cost)
```

### Local Embeddings

**File:** `src/memory/embeddings.py`

Provides local embeddings via `sentence-transformers` (model: `all-MiniLM-L6-v2`). Runs on CPU, no API calls. ChromaDB can use these automatically, but this module also provides direct access for custom embedding needs.

**Graceful degradation**: If `sentence-transformers` fails to load (DLL error on some Windows setups), falls back to ChromaDB's built-in embeddings.

---

## 11. Behavioral Learning

**File:** `src/decisions/learning.py`

Extends the decision engine with richer context tracking.

### What It Stores

For each user decision, records:
- Process name, PID
- CPU %, memory MB at the time
- Parent process name
- Active profile
- User's decision (keep/kill)
- Timestamp

### LLM Context Building

When the LLM needs to decide about a process, the behavioral learner provides history:
```
History for 'mystery.exe' (5 past encounters):
  - kill | Profile: gaming | CPU: 12.3% | RAM: 450MB | Parent: explorer.exe
  - kill | Profile: gaming | CPU: 8.1% | RAM: 380MB | Parent: explorer.exe
  - keep | Profile: coding | CPU: 2.0% | RAM: 120MB | Parent: code.exe
```

This gives the LLM much better context than just the process name.

---

## 12. Bluetooth Subsystem

### BT Monitor

**File:** `src/bluetooth/monitor.py`

Monitors Bluetooth devices and events using PowerShell commands.

**Device discovery** (two sources):
1. `Get-PnpDevice -Class Bluetooth` — classic BT devices
2. `Get-PnpDevice -Class AudioEndpoint` filtered for BT — catches A2DP audio devices

**Event monitoring** (two sources):
1. `Microsoft-Windows-Bluetooth-BthLEEnum/Operational` event log — BLE events
2. `System` log filtered for `BTHUSB`/`bthport` providers — classic BT errors

**Disconnect tracking**: Counts disconnects per device to detect frequent disconnection patterns.

**Safety**: All JSON parsing wrapped in try/except. Timestamps parsed with fallback to `datetime.now()`. Messages truncated to 200 chars.

### BT Diagnostics

**File:** `src/bluetooth/diagnostics.py`

Pattern-based diagnosis that handles common BT issues **without API calls**:

| Pattern | Trigger | Fix |
|---------|---------|-----|
| Frequent disconnects | 3+ disconnects from same device | Disable BT adapter power management |
| Pairing failure | Any `pair_fail` event | Remove and re-pair device |
| No connections | No `connect` events and errors exist | Restart BT adapter |

If no pattern matches but errors exist → flags for LLM diagnosis.

### BT Actions

**File:** `src/bluetooth/actions.py`

- `restart_adapter()` — Disable/re-enable BT adapter via PowerShell
- `disable_power_saving()` — Turn off selective suspend to prevent random disconnects
- `reconnect_device(instance_id)` — Force reconnect a specific device
- `switch_audio_profile()` — Provides guidance for A2DP vs HFP switching
- `run_bt_troubleshooter()` — Runs Windows built-in BT troubleshooter

---

## 13. Audio Subsystem

### Audio Monitor

**File:** `src/audio/monitor.py`

Uses **pycaw** (Python Core Audio Windows) to monitor audio sessions and devices.

**What it provides:**
- List of active audio sessions (which apps are playing audio, their volume, mute state)
- Output/input device enumeration with default device identification
- Complete audio snapshots

**Device detection**: Uses `AudioUtilities.GetAllDevices()` for proper `FriendlyName` resolution. Identifies render vs. capture devices by ID prefix (`{0.0.0` = render, `{0.0.1` = capture).

### Audio Suggestions

**File:** `src/audio/suggestions.py`

Smart audio routing suggestions (no API calls):

| Check | Trigger | Suggestion |
|-------|---------|------------|
| Muted active | App playing audio but muted | "Spotify is playing audio but is muted" |
| No output | Audio playing, no output device | "Audio is playing but no output device detected" |
| BT available | BT headphones connected but audio goes to speakers | "BT device 'AirPods' connected but audio goes to 'Speakers'" |

### Audio Widget

**File:** `src/audio/widget.py`

PyQt6 always-on-top overlay showing active audio sessions with volume bars. Refreshes every 2 seconds. Draggable, frameless, dark-themed.

---

## 14. Notification System

**File:** `src/notifications/toasts.py`

Windows toast notifications via **winotify**.

### Features

- **Rate limiting**: Same notification suppressed for 30 seconds (prevents spam)
- **Dedup keys**: Each notification type has a unique key for rate limiting
- **Audio cues**: INFO = silent, WARNING = default sound, CRITICAL = alarm
- **Message truncation**: Title capped at 128 chars, body at 256 chars
- **Non-blocking**: Notifications sent in background threads
- **Graceful degradation**: If winotify not installed, all notification calls are silently ignored

### Notification Types

| Method | When | Level |
|--------|------|-------|
| `anomaly_detected()` | System anomaly found | Varies by severity |
| `action_taken()` | Agent performed an action | INFO/WARNING |
| `profile_changed()` | Profile switched | INFO |
| `audio_suggestion()` | Audio routing issue | INFO |
| `diagnosis_report()` | Detailed fix result (root cause + action) | INFO/CRITICAL |
| `bt_issue()` | Bluetooth problem | WARNING |

---

## 15. Dashboard & UI

### Terminal Dashboard

**File:** `dashboard.py`

A **standalone** Rich-based terminal dashboard. Runs independently from the agent — doesn't interfere with the background service.

**Panels:**
- System Metrics (CPU, RAM, Disk, GPU with colored bars)
- Top 8 Processes (sorted by CPU)
- Status (current profile with trigger app, agent running status, battery, polling mode)
- Bluetooth (connected devices via `DEVPKEY_Device_IsPresent`)
- Agent Diagnosis (parsed from agent log file, snapshots every 10 minutes, high-risk = immediate)

**Run:** `python dashboard.py`

### PyQt6 System Dashboard Widget

**File:** `src/dashboard/widget.py`

Always-on-top, frameless, draggable overlay showing live metrics, profile, processes, events, agent activity, BT, audio, battery, and polling mode. Dark-themed to match the audio widget.

### PyQt6 Audio Widget

**File:** `src/audio/widget.py`

Smaller overlay focused on audio sessions and volume levels.

---

## 16. Threading Architecture

The agent uses multiple daemon threads coordinated by a shared `threading.Event`:

```
Main Thread
  └── observer.run()          # Blocking main loop (polls every 5s)

Daemon Threads:
  ├── profile-detect          # Profile detection loop (every 10s)
  ├── bt-monitor              # Bluetooth monitoring loop (every 60s)
  ├── audio-suggest           # Audio suggestion loop (every 15s)
  └── watchdog                # Thread health monitor (every 30s)
```

### Shutdown Flow

```
Ctrl+C pressed
  → shutdown_event.set()           # Signal all threads
  → observer.stop()                # Stop main loop
  → Join all daemon threads (5s timeout each)
  → Print session stats (API cost, events processed, memory entries)
```

### Thread Watchdog

A dedicated watchdog thread polls every 30 seconds. If any daemon thread dies unexpectedly:
- Logs CRITICAL
- Sends a toast notification ("Thread Crash: thread_name stopped unexpectedly")

### Thread Safety

- **LLM token counters**: Protected by `threading.Lock`
- **Toast rate limiter**: Protected by `threading.Lock`
- **Shutdown coordination**: All threads use `shutdown_event.wait(timeout=N)` instead of `time.sleep()` — they wake up immediately on shutdown

---

## 17. Configuration

### settings.yaml

```yaml
api:
  provider: openai
  api_key: "${OPENAI_API_KEY}"       # From environment variable
  triage_model: gpt-5-mini           # Fast model for initial diagnosis
  complex_model: gpt-5-mini          # Powerful model for escalation
  confidence_threshold: 0.7          # Below this → escalate to complex model
  cost_cap_usd: 1.00                 # Max spend per session

monitoring:
  poll_interval_seconds: 5           # How often to check system (AC power)
  poll_interval_battery_seconds: 30  # How often on battery
  cpu_threshold_percent: 95          # CPU spike threshold
  cpu_sustained_seconds: 120         # How long CPU must stay high
  ram_threshold_percent: 85          # RAM pressure threshold
  disk_threshold_percent: 90         # Disk fullness threshold
  memory_leak_growth_mb: 500         # MB growth to flag as leak
  memory_leak_window_minutes: 30     # Time window for leak detection
  gpu_threshold_percent: 95          # GPU spike threshold
  cooldown_minutes: 10               # Suppress duplicate alerts

decisions:
  auto_learn_after_n: 3              # Auto-decide after N consistent user choices
  similarity_threshold: 0.92         # ChromaDB match threshold

safety:
  high_risk_actions:                 # These need HITL approval
    - kill_system_process
    - modify_power_settings
    - modify_driver
    - modify_registry
  auto_approve_risk_levels: [low]    # Auto-approve these risk levels
```

### profiles.yaml

Defines per-activity behavior. Each profile has:
- `trigger_processes`: What activates this profile
- `always_keep`: Never kill these while in this profile
- `kill_candidates`: Kill these to free resources
- `actions`: Profile-specific actions (defer updates, trim working sets, etc.)

Plus global whitelist (critical Windows processes) and global blacklist (always kill).

### Config Validation

At startup, `validate_config()` checks:
- Required sections exist (`api`, `monitoring`, `safety`, `profiles`)
- Values are correct types (numbers are numbers, lists are lists)
- Profile names are valid
- API key is set (via config or environment variable)
- Cost cap is a positive number

Returns warnings (doesn't crash) — the agent can still run with partial config.

---

## 18. Data Storage

### SQLite: `data/decisions.db`

Two tables:

**`process_decisions`** (Decision Engine):
```sql
CREATE TABLE process_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_name TEXT NOT NULL,
    profile TEXT NOT NULL,         -- e.g., "gaming", "coding"
    decision TEXT NOT NULL,        -- "keep" or "kill"
    timestamp TEXT NOT NULL
);
-- Index on (process_name, profile) for fast lookups
```

**`process_context`** (Behavioral Learner):
```sql
CREATE TABLE process_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_name TEXT NOT NULL,
    pid INTEGER,
    cpu_percent REAL,
    memory_mb REAL,
    parent_process TEXT,
    profile TEXT,
    user_decision TEXT,
    timestamp TEXT NOT NULL
);
```

### ChromaDB: `data/chromadb/`

Vector database storing anomaly/fix pairs:
- **Collection**: `fix_memory` (cosine similarity space)
- **Documents**: Anomaly summaries (e.g., "chrome.exe using 3.2GB RAM")
- **Metadata**: anomaly_type, fix_applied, success, timestamp
- **Embeddings**: Generated by ChromaDB's built-in model (or sentence-transformers if available)

### Log Files: `logs/agent.log`

- Rotating file handler: 10MB max, keeps 3 backups
- Format: `2024-01-15 14:30:22 [INFO] module_name: message`
- UTF-8 encoded

---

## 19. Testing

**73 tests** across 6 test files, all passing.

### Test Files

| File | Tests | What It Covers |
|------|-------|---------------|
| `test_profiles.py` | Profile detection, classification, debouncing |
| `test_decisions.py` | 4-layer decision engine, history learning, null safety |
| `test_tools.py` | Process killing, command sanitization, protected PIDs |
| `test_hitl.py` | Risk classification, auto-approve, callbacks |
| `test_llm.py` | LLM client, cost cap, error handling |
| `test_config.py` | Config validation warnings |

### Shared Fixtures (`conftest.py`)

- `sample_settings`: Minimal settings matching `settings.yaml` structure
- `sample_profiles_config`: Minimal profiles matching `profiles.yaml`
- `profile_manager`: Pre-built ProfileManager instance

### Running Tests

```bash
cd OS_agent
pytest                    # Run all tests
pytest -v                 # Verbose output
pytest tests/test_tools.py  # Run specific test file
```

---

## 20. Dependencies

### Core
| Package | Purpose |
|---------|---------|
| `langgraph` | State machine for OODA loop |
| `openai` | LLM API client |
| `psutil` | System monitoring (CPU, RAM, processes) |
| `pyyaml` | Config file parsing |

### System Integration (Windows-specific)
| Package | Purpose |
|---------|---------|
| `pywin32` | Windows API access |
| `wmi` | Windows Management Instrumentation |
| `pycaw` + `comtypes` | Audio session monitoring |
| `winotify` | Toast notifications |

### AI / Memory
| Package | Purpose |
|---------|---------|
| `chromadb` | Vector database for semantic memory |
| `sentence-transformers` | Local text embeddings |

### UI
| Package | Purpose |
|---------|---------|
| `rich` | Terminal dashboard |
| `PyQt6` | GUI overlay widgets |

### Testing
| Package | Purpose |
|---------|---------|
| `pytest` | Test framework |
| `pytest-mock` | Mocking utilities |

---

## 21. How to Run

### Prerequisites
1. Python 3.11+
2. Windows 11
3. OpenAI API key set as `OPENAI_API_KEY` environment variable
4. (Optional) NVIDIA GPU with `nvidia-smi` for GPU monitoring
5. (Optional) Docker for command sandboxing

### Install

```bash
cd OS_agent
pip install -r requirements.txt
```

### Run the Agent

```bash
python main.py
```

This starts the background agent that monitors your system, detects issues, and fixes them.

### Run the Dashboard

```bash
python dashboard.py
```

This starts a separate terminal dashboard showing live system metrics. Runs independently — doesn't interfere with the agent.

### Auto-Start with Windows

```bash
install_autostart.bat     # Register as startup task
uninstall_autostart.bat   # Remove startup task
```

### Startup Output

When the agent starts, it prints a summary:
```
╔══════════════════════════════════════════════╗
║           OS Agent — System Optimizer         ║
╠══════════════════════════════════════════════╣
║  Profile:    coding                          ║
║  Observer:   5s (AC) / 30s (battery)         ║
║  CPU alert:  >95% for 120s                   ║
║  RAM alert:  >85%                            ║
║  Cooldown:   10 min between alerts           ║
║  Battery:    Plugged in (100%)               ║
║  LLM:        gpt-5-mini                      ║
║  Memory:     Ready                           ║
║  Audio:      Ready                           ║
║  Bluetooth:  Ready                           ║
║  Toasts:     Ready                           ║
║  Log rotate: 10MB x 3 backups               ║
╚══════════════════════════════════════════════╝
```

### Shutdown

Press `Ctrl+C`. The agent:
1. Signals all threads to stop
2. Waits up to 5s for each thread
3. Prints session stats (API cost, events processed, memory entries)
4. Exits cleanly
