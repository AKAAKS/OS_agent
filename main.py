"""
OS Agent — Personal Windows 11 System Optimizer
Entry point: starts the observer daemon, agent loop, and UI components.
"""

import json
import logging
import logging.handlers
import os
import sys
import threading
import time as _time
from pathlib import Path

import yaml

# Force UTF-8 stdout/stderr to avoid cp1252 encoding errors on Windows
# (Skip when running under pytest — it manages its own capture)
import io
if "pytest" not in sys.modules:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Setup logging with rotation
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Rotating file handler — 10MB max, keep 3 backups
file_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "agent.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=3,
    encoding="utf-8",
)
file_handler.setFormatter(log_formatter)

# Console handler (stdout already set to UTF-8 above)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler],
)
logger = logging.getLogger("os_agent")


def load_config() -> tuple[dict, dict]:
    """Load settings and profiles from YAML config files."""
    config_dir = Path("config")

    settings_path = config_dir / "settings.yaml"
    profiles_path = config_dir / "profiles.yaml"

    if not settings_path.exists():
        print(f"FATAL: Config file not found: {settings_path.resolve()}")
        print("Create config/settings.yaml with the required configuration.")
        sys.exit(1)
    if not profiles_path.exists():
        print(f"FATAL: Config file not found: {profiles_path.resolve()}")
        print("Create config/profiles.yaml with the required configuration.")
        sys.exit(1)

    with open(settings_path) as f:
        settings = yaml.safe_load(f)

    with open(profiles_path) as f:
        profiles = yaml.safe_load(f)

    return settings, profiles


def validate_config(settings: dict, profiles: dict) -> list[str]:
    """Validate config files at startup. Returns a list of warnings (empty = OK)."""
    warnings = []

    # --- settings.yaml ---
    api = settings.get("api")
    if not api:
        warnings.append("settings.yaml: missing 'api' section")
    else:
        for key in ("triage_model", "complex_model"):
            if key not in api:
                warnings.append(f"settings.yaml: api.{key} is missing")
        if not api.get("api_key") and not os.environ.get("OPENAI_API_KEY"):
            warnings.append("settings.yaml: no API key configured and OPENAI_API_KEY env var is empty")
        cap = api.get("cost_cap_usd")
        if cap is not None and (not isinstance(cap, (int, float)) or cap <= 0):
            warnings.append(f"settings.yaml: api.cost_cap_usd must be a positive number, got {cap!r}")

    monitoring = settings.get("monitoring")
    if not monitoring:
        warnings.append("settings.yaml: missing 'monitoring' section")
    else:
        for key in ("poll_interval_seconds", "cpu_threshold_percent", "ram_threshold_percent"):
            val = monitoring.get(key)
            if val is None:
                warnings.append(f"settings.yaml: monitoring.{key} is missing")
            elif not isinstance(val, (int, float)):
                warnings.append(f"settings.yaml: monitoring.{key} must be a number, got {type(val).__name__}")

    safety = settings.get("safety")
    if not safety:
        warnings.append("settings.yaml: missing 'safety' section")

    # --- profiles.yaml ---
    prof_section = profiles.get("profiles")
    if not prof_section:
        warnings.append("profiles.yaml: missing 'profiles' section")
    else:
        valid_profiles = {"idle", "coding", "gaming", "streaming", "browsing", "media", "comms"}
        for name, cfg in prof_section.items():
            if name not in valid_profiles:
                warnings.append(f"profiles.yaml: unknown profile '{name}' (valid: {valid_profiles})")
            if not isinstance(cfg, dict):
                warnings.append(f"profiles.yaml: profile '{name}' must be a dict")
                continue
            for list_key in ("trigger_processes", "always_keep", "kill_candidates"):
                val = cfg.get(list_key)
                if val is not None and not isinstance(val, list):
                    warnings.append(f"profiles.yaml: {name}.{list_key} must be a list, got {type(val).__name__}")

    whitelist = profiles.get("global_whitelist")
    if whitelist is not None and not isinstance(whitelist, list):
        warnings.append("profiles.yaml: global_whitelist must be a list")

    return warnings


def _has_console() -> bool:
    """Check if we have an interactive console (False under pythonw / Task Scheduler)."""
    try:
        if sys.stdin is None or not hasattr(sys.stdin, "fileno"):
            return False
        sys.stdin.fileno()
        return True
    except (OSError, ValueError, AttributeError):
        return False


def create_hitl_console_callback():
    """Console-based HITL callback, with headless fallback."""
    headless = not _has_console()

    def callback(request):
        # In headless mode: auto-approve low/medium, deny high
        if headless:
            risk = getattr(request, "risk_level", None)
            risk_val = risk.value if hasattr(risk, "value") else str(risk)
            if risk_val == "high":
                logger.warning(
                    f"HITL denied (headless): {request.action} on {request.target} (risk: {risk_val})"
                )
                return False
            logger.info(
                f"HITL auto-approved (headless): {request.action} on {request.target} (risk: {risk_val})"
            )
            return True

        print(f"\n{'='*50}")
        print(f"  ACTION APPROVAL REQUIRED")
        print(f"  Action: {request.action}")
        print(f"  Target: {request.target}")
        print(f"  Risk:   {request.risk_level.value}")
        print(f"  Reason: {request.reason}")
        print(f"{'='*50}")
        while True:
            answer = input("  Approve? (y/n): ").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
    return callback


def run_observer_loop(observer, process_monitor, agent, notifier=None):
    """Run the observer daemon and feed events to the agent."""
    def on_event(event):
        try:
            # Notify via toast
            if notifier:
                notifier.anomaly_detected(event.anomaly_type.value, event.summary, event.severity)

            # Also check process monitor for memory leaks
            if observer._known_pids:
                snapshot = observer.take_snapshot()
                leak_events = process_monitor.update(snapshot.top_processes)
                for leak_event in leak_events:
                    agent.process_event(leak_event)

            # Process the original event and notify on action
            result = agent.process_event(event)
            if notifier and result.get("planned_action") not in ("skip", ""):
                action_result = result.get("action_result")
                diagnosis = result.get("diagnosis", {})
                if action_result and diagnosis:
                    # Detailed diagnosis toast for high-severity events
                    notifier.diagnosis_report(
                        anomaly_type=event.anomaly_type.value,
                        root_cause=diagnosis.get("root_cause", "Unknown"),
                        fix=diagnosis.get("fix", result["planned_action"]),
                        success=action_result.success,
                    )
                elif action_result:
                    notifier.action_taken(
                        result["planned_action"],
                        str(result.get("action_args", {})),
                        action_result.success,
                    )
        except Exception as e:
            logger.error(f"Event processing failed for {event.anomaly_type.value}: {e}", exc_info=True)

    observer.on_event(on_event)
    observer.run()


def main():
    logger.info("OS Agent starting...")

    # Load and validate configuration
    settings, profiles_config = load_config()
    config_warnings = validate_config(settings, profiles_config)
    if config_warnings:
        for w in config_warnings:
            logger.warning(f"Config: {w}")
        print(f"\n⚠ {len(config_warnings)} config warning(s) — check logs for details.")
    logger.info("Configuration loaded")

    # Initialize components
    from src.profiles.manager import ProfileManager
    from src.observer.daemon import SystemObserver
    from src.observer.process_monitor import ProcessMonitor
    from src.decisions.engine import DecisionEngine
    from src.agent.llm import LLMClient
    from src.agent.tools import AgentTools
    from src.agent.graph import OODAAgent
    from src.memory.store import SemanticMemory
    from src.safety.hitl import HITLGate
    from src.audio.monitor import AudioMonitor
    from src.audio.suggestions import AudioSuggestionEngine
    from src.bluetooth.monitor import BluetoothMonitor
    from src.bluetooth.diagnostics import BTDiagnosticEngine
    from src.decisions.learning import BehavioralLearner
    from src.notifications.toasts import ToastNotifier

    # Core components
    profile_manager = ProfileManager(
        profiles_config,
        global_whitelist=profiles_config.get("global_whitelist", []),
        global_blacklist=profiles_config.get("global_blacklist", []),
    )

    observer = SystemObserver(settings)
    process_monitor = ProcessMonitor(settings)
    decision_engine = DecisionEngine(profile_manager, settings)
    llm = LLMClient(settings)
    tools = AgentTools()

    # Components that may fail independently
    memory = None
    try:
        memory = SemanticMemory(settings)
    except Exception as e:
        logger.error(f"Semantic memory failed to init: {e} — continuing without it")

    # Behavioral learner (shares DB with decision engine)
    learner = None
    try:
        learner = BehavioralLearner()
        logger.info("Behavioral learner initialized")
    except Exception as e:
        logger.error(f"Behavioral learner failed to init: {e} — continuing without it")

    hitl = HITLGate(settings)

    # Setup HITL console callback
    hitl.set_approval_callback(create_hitl_console_callback())

    # Agent (with semantic memory wired in)
    agent = OODAAgent(llm, tools, decision_engine, profile_manager, settings, memory=memory)
    agent.set_hitl_callback(create_hitl_console_callback())

    # Audio (optional — pycaw may not be available)
    audio_monitor = None
    audio_suggestions = None
    try:
        audio_monitor = AudioMonitor()
        audio_suggestions = AudioSuggestionEngine(audio_monitor)
    except Exception as e:
        logger.error(f"Audio subsystem failed to init: {e} — continuing without it")

    # Bluetooth (optional)
    bt_monitor = None
    bt_diagnostics = None
    try:
        bt_monitor = BluetoothMonitor()
        bt_diagnostics = BTDiagnosticEngine(bt_monitor)
    except Exception as e:
        logger.error(f"Bluetooth subsystem failed to init: {e} — continuing without it")

    # Toast notifications
    notifier = ToastNotifier(enabled=True)
    logger.info(f"Notifications: {'Ready' if notifier.available else 'Disabled'}")

    logger.info("All components initialized")

    # One-time startup optimization analysis
    try:
        from src.startup.optimizer import StartupOptimizer
        startup_opt = StartupOptimizer()
        startup_items = startup_opt.analyze()
        high_impact = [i for i in startup_items if i.impact_score >= 60]
        if high_impact:
            logger.info(f"Startup optimizer: {len(high_impact)} high-impact startup items found")
            for item in high_impact[:5]:
                logger.info(f"  Startup: {item.name} (score={item.impact_score:.0f}, {item.recommendation}) — {item.source}")
            from src.notifications.toasts import NotifyLevel
            notifier.notify(
                title="Startup Optimizer",
                message=f"{len(high_impact)} high-impact startup items found. Check logs for details.",
                level=NotifyLevel.INFO,
                dedup_key="startup_optimizer",
            )
        else:
            logger.info("Startup optimizer: no high-impact items found")
    except Exception as e:
        logger.error(f"Startup optimizer failed: {e}")

    # Detect initial profile
    active_profile = profile_manager.detect_profile()
    logger.info(f"Active profile: {active_profile.value}")

    # Check battery status
    import psutil as _psutil
    _battery = _psutil.sensors_battery()
    if _battery:
        _batt_str = f"{'On battery' if not _battery.power_plugged else 'Plugged in'} ({_battery.percent:.0f}%)"
    else:
        _batt_str = "Desktop (no battery)"

    _poll_ac = settings['monitoring']['poll_interval_seconds']
    _poll_batt = settings['monitoring'].get('poll_interval_battery_seconds', 30)
    _cooldown = settings['monitoring'].get('cooldown_minutes', 10)

    # Print startup summary
    print(f"""
╔══════════════════════════════════════════════╗
║           OS Agent — System Optimizer         ║
╠══════════════════════════════════════════════╣
║  Profile:    {active_profile.value:<32} ║
║  Observer:   {_poll_ac}s (AC) / {_poll_batt}s (battery){' ' * (20 - len(str(_poll_ac)) - len(str(_poll_batt)))}║
║  CPU alert:  >{settings['monitoring']['cpu_threshold_percent']}% for {settings['monitoring']['cpu_sustained_seconds']}s{' ' * 17}║
║  RAM alert:  >{settings['monitoring']['ram_threshold_percent']}%{' ' * 28}║
║  Cooldown:   {_cooldown} min between alerts{' ' * (19 - len(str(_cooldown)))}║
║  Battery:    {_batt_str:<32} ║
║  LLM:        {settings['api']['triage_model']:<32} ║
║  Memory:     {'Ready' if memory and memory.available else 'Disabled':<32} ║
║  Audio:      {'Ready' if audio_monitor and audio_monitor.available else 'Disabled':<32} ║
║  Bluetooth:  {'Ready' if bt_monitor else 'Disabled':<32} ║
║  Toasts:     {'Ready' if notifier.available else 'Disabled':<32} ║
║  Log rotate: 10MB x 3 backups{' ' * 16}║
╚══════════════════════════════════════════════╝
    """)

    # Shared shutdown event — all threads check this instead of `while True`
    shutdown_event = threading.Event()
    _watched_threads = []  # Thread watchdog list
    _session_start = _time.monotonic()
    _profile_switch_count = 0

    # Handle OS shutdown signals (SIGTERM, SIGBREAK) so Windows doesn't show
    # an error dialog when the PC shuts down or the scheduled task is stopped.
    import signal

    def _graceful_shutdown(signum, frame):
        logger.info(f"Received signal {signum} — shutting down gracefully")
        shutdown_event.set()
        observer.stop()

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _graceful_shutdown)

    # ---- Profile action execution helper ----

    def _execute_profile_actions(actions: dict):
        """Execute actions defined in profiles.yaml for the active profile."""
        for action_name, action_value in actions.items():
            try:
                if action_name == "defer_windows_update" and action_value:
                    result = tools.defer_windows_update()
                    logger.info(f"Profile action defer_windows_update: {result.message}")
                elif action_name == "trim_idle_working_sets" and action_value:
                    result = tools.trim_working_sets()
                    logger.info(f"Profile action trim_idle_working_sets: {result.message}")
                elif action_name == "defer_defender_scan" and action_value:
                    # Pause real-time scanning (requires admin — will fail gracefully)
                    result = tools.run_command(
                        'powershell -Command "Set-MpPreference -DisableRealtimeMonitoring $true"'
                    )
                    logger.info(f"Profile action defer_defender_scan: {result.message}")
                else:
                    logger.debug(f"Skipping unknown/disabled profile action: {action_name}")
            except Exception as e:
                logger.error(f"Profile action '{action_name}' failed: {e}")

    # ---- Power plan mapping per profile ----

    from src.profiles.manager import ProfileType

    _PROFILE_POWER_MAP = {
        ProfileType.GAMING: "high_performance",
        ProfileType.STREAMING: "high_performance",
        ProfileType.CODING: "balanced",
        ProfileType.BROWSING: "balanced",
        ProfileType.MEDIA: "balanced",
        ProfileType.COMMS: "balanced",
        ProfileType.IDLE: "power_saver",
    }
    _POWER_PLAN_STABILITY_SECONDS = 120  # 2 minutes stable before switching power plan

    # Start profile detection loop (with toast on profile change, power plan switching)
    _last_profile = profile_manager.active_profile

    def profile_loop():
        nonlocal _last_profile, _profile_switch_count
        _profile_stable_since = _time.monotonic()
        _current_power_plan = ""

        while not shutdown_event.is_set():
            try:
                profile_manager.detect_profile()
                current = profile_manager.active_profile
                now = _time.monotonic()

                if current != _last_profile:
                    notifier.profile_changed(
                        _last_profile.value, current.value, profile_manager.trigger_app
                    )
                    # Execute profile-specific actions (defer updates, trim working sets, etc.)
                    actions = profile_manager.get_profile_actions()
                    if actions:
                        _execute_profile_actions(actions)

                    _last_profile = current
                    _profile_switch_count += 1
                    _profile_stable_since = now  # reset power plan stability timer

                # Power plan switching — only after profile has been stable for 2+ minutes
                time_stable = now - _profile_stable_since
                target_plan = _PROFILE_POWER_MAP.get(current, "balanced")
                if time_stable >= _POWER_PLAN_STABILITY_SECONDS and target_plan != _current_power_plan:
                    result = tools.set_power_plan(target_plan)
                    if result.success:
                        _current_power_plan = target_plan
                        logger.info(f"Power plan → {target_plan} (profile {current.value} stable for {time_stable:.0f}s)")
                    else:
                        logger.warning(f"Power plan switch failed: {result.message}")

            except Exception as e:
                logger.debug(f"Profile detection error: {e}")
            shutdown_event.wait(timeout=10)

    profile_thread = threading.Thread(target=profile_loop, daemon=True, name="profile-detect")
    profile_thread.start()
    _watched_threads.append(profile_thread)

    # Start BT monitoring loop (if available)
    if bt_monitor and bt_diagnostics:
        _bt_seen_issues: dict[str, float] = {}  # issue_key -> timestamp of last LLM/notify
        _BT_COOLDOWN = 1800  # 30 minutes — don't re-diagnose the same BT issue

        def bt_loop():
            import time as _time
            while not shutdown_event.is_set():
                try:
                    diagnoses = bt_diagnostics.diagnose()
                    for d in diagnoses:
                        issue_key = d.issue[:60]
                        now = _time.monotonic()
                        last_seen = _bt_seen_issues.get(issue_key, 0)
                        if now - last_seen < _BT_COOLDOWN:
                            logger.debug(f"BT diagnosis suppressed (cooldown): {issue_key}")
                            continue
                        _bt_seen_issues[issue_key] = now

                        if d.needs_llm:
                            events = bt_monitor.get_recent_events()
                            devices = bt_monitor.get_paired_devices()
                            device_info = ", ".join(d.name for d in devices) or "No devices"
                            response = llm.diagnose_bluetooth(
                                [{"timestamp": str(e.timestamp), "message": e.message} for e in events],
                                device_info,
                            )
                            logger.info(f"BT LLM diagnosis: {response.content}")
                        else:
                            logger.info(f"BT issue: {d.issue} → Fix: {d.fix_description}")
                            notifier.bt_issue(d.issue, d.fix_description)
                except Exception as e:
                    logger.debug(f"BT monitor error: {e}")
                shutdown_event.wait(timeout=60)

        bt_thread = threading.Thread(target=bt_loop, daemon=True, name="bt-monitor")
        bt_thread.start()
        _watched_threads.append(bt_thread)

    # Start audio suggestion loop (if available)
    if audio_monitor and audio_suggestions:
        def audio_loop():
            while not shutdown_event.is_set():
                try:
                    suggestions = audio_suggestions.check()
                    for s in suggestions:
                        logger.info(f"Audio suggestion: {s.message}")
                        notifier.audio_suggestion(s.message)
                except Exception as e:
                    logger.debug(f"Audio suggestion error: {e}")
                shutdown_event.wait(timeout=15)

        audio_thread = threading.Thread(target=audio_loop, daemon=True, name="audio-suggest")
        audio_thread.start()
        _watched_threads.append(audio_thread)

    # Thread watchdog — log and notify if any daemon thread dies unexpectedly

    def watchdog_loop():
        from src.notifications.toasts import NotifyLevel
        while not shutdown_event.is_set():
            for t in _watched_threads:
                if not t.is_alive():
                    logger.critical(f"Thread '{t.name}' has died unexpectedly!")
                    notifier.notify(
                        title="Thread Crash",
                        message=f"Background thread '{t.name}' stopped unexpectedly.",
                        level=NotifyLevel.CRITICAL,
                    )
            shutdown_event.wait(timeout=30)

    watchdog_thread = threading.Thread(target=watchdog_loop, daemon=True, name="watchdog")
    watchdog_thread.start()

    # Run the main observer loop (blocking)
    logger.info("Starting observer daemon...")
    try:
        run_observer_loop(observer, process_monitor, agent, notifier=notifier)
    except (KeyboardInterrupt, SystemExit):
        pass  # Signal handler already set shutdown_event
    finally:
        logger.info("Shutting down gracefully...")
        shutdown_event.set()
        observer.stop()

        # Wait for threads to finish (up to 5s each)
        for t in threading.enumerate():
            if t is not threading.current_thread() and t.daemon:
                t.join(timeout=5)

        # Print usage stats
        usage = llm.get_usage_stats()
        event_log = agent.get_event_log()
        try:
            mem_count = memory.get_stats().get('count', 0) if memory and memory.available else 'N/A'
        except Exception:
            mem_count = 'Error'

        session_duration = _time.monotonic() - _session_start
        actions_taken = sum(1 for e in event_log if e.get("success"))

        print(f"\nSession stats:")
        print(f"  Duration: {session_duration / 60:.1f} minutes")
        print(f"  API tokens used: {usage['total_input_tokens']} in / {usage['total_output_tokens']} out")
        print(f"  Estimated cost: ${usage['estimated_cost_usd']:.4f}")
        print(f"  Events processed: {len(event_log)}")
        print(f"  Actions taken: {actions_taken}")
        print(f"  Profile switches: {_profile_switch_count}")
        print(f"  Memory entries: {mem_count}")

        # Persist session stats to JSONL
        from datetime import datetime
        session_record = {
            "timestamp": datetime.now().isoformat(),
            "duration_minutes": round(session_duration / 60, 1),
            "api_tokens_in": usage["total_input_tokens"],
            "api_tokens_out": usage["total_output_tokens"],
            "estimated_cost_usd": usage["estimated_cost_usd"],
            "events_processed": len(event_log),
            "actions_taken": actions_taken,
            "actions_succeeded": actions_taken,
            "profile_switches": _profile_switch_count,
            "memory_entries": mem_count if isinstance(mem_count, int) else 0,
            "circuit_breaker_tripped": usage.get("circuit_breaker_tripped", False),
        }

        sessions_path = Path("data/sessions.jsonl")
        sessions_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(sessions_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(session_record) + "\n")
            logger.info(f"Session stats saved to {sessions_path}")
        except Exception as e:
            logger.error(f"Failed to save session stats: {e}")

        logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
