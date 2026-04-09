"""
LangGraph OODA agent loop.
Observe → Orient → Decide → Act → Verify
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated

from langgraph.graph import StateGraph, START, END

from src.observer.daemon import ObservationEvent, AnomalyType
from src.agent.llm import LLMClient
from src.agent.tools import AgentTools, ActionResult
from src.decisions.engine import DecisionEngine, Decision
from src.memory.store import SemanticMemory
from src.profiles.manager import ProfileManager

logger = logging.getLogger(__name__)


@dataclass
class AgentState:
    """State passed through the OODA loop."""
    event: ObservationEvent | None = None
    context: str = ""
    diagnosis: dict = field(default_factory=dict)
    planned_action: str = ""
    action_args: dict = field(default_factory=dict)
    risk_level: str = "low"
    action_result: ActionResult | None = None
    needs_hitl: bool = False
    hitl_approved: bool = False
    completed: bool = False
    error: str = ""


class OODAAgent:
    """
    The core agent loop implementing Observe → Orient → Decide → Act → Verify.
    Uses LangGraph for structured state transitions.
    """

    def __init__(
        self,
        llm: LLMClient,
        tools: AgentTools,
        decision_engine: DecisionEngine,
        profile_manager: ProfileManager,
        settings: dict,
        memory: SemanticMemory | None = None,
    ):
        self.llm = llm
        self.tools = tools
        self.decision_engine = decision_engine
        self.profile_manager = profile_manager
        self.settings = settings
        self.memory = memory
        self._hitl_callback = None
        self._event_log: list[dict] = []

        self.graph = self._build_graph()

    def set_hitl_callback(self, callback):
        """Set a callback for human-in-the-loop approval.
        callback(state: AgentState) -> bool (True = approved)
        """
        self._hitl_callback = callback

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(dict)

        builder.add_node("orient", self._orient)
        builder.add_node("decide", self._decide)
        builder.add_node("hitl_gate", self._hitl_gate)
        builder.add_node("act", self._act)
        builder.add_node("verify", self._verify)

        builder.add_edge(START, "orient")
        builder.add_edge("orient", "decide")
        builder.add_conditional_edges(
            "decide",
            self._should_act,
            {"act": "hitl_gate", "skip": END},
        )
        builder.add_conditional_edges(
            "hitl_gate",
            self._hitl_approved,
            {"approved": "act", "denied": END},
        )
        builder.add_edge("act", "verify")
        builder.add_edge("verify", END)

        return builder.compile()

    def process_event(self, event: ObservationEvent) -> dict:
        """Process a single anomaly event through the OODA loop."""
        initial_state = {
            "event": event,
            "context": "",
            "diagnosis": {},
            "planned_action": "",
            "action_args": {},
            "risk_level": "low",
            "action_result": None,
            "needs_hitl": False,
            "hitl_approved": False,
            "completed": False,
            "error": "",
        }

        logger.info(f"Processing event: {event.anomaly_type.value} — {event.summary}")

        try:
            result = self.graph.invoke(initial_state)
            self._log_event(event, result)
            return result
        except Exception as e:
            logger.error(f"Agent loop error: {e}")
            return {**initial_state, "error": str(e)}

    def _orient(self, state: dict) -> dict:
        """Gather context about the anomaly."""
        event: ObservationEvent = state["event"]

        # Build context from event details
        context_parts = [
            f"Active profile: {self.profile_manager.active_profile.value}",
            f"Anomaly: {event.summary}",
            f"Severity: {event.severity}",
        ]

        if event.details.get("top_processes"):
            procs = event.details["top_processes"]
            context_parts.append("Top processes:")
            for p in procs[:5]:
                context_parts.append(
                    f"  - {p['name']} (PID {p['pid']}): CPU {p['cpu_percent']:.1f}%, RAM {p['memory_mb']:.0f}MB"
                )

        if event.details.get("top_memory_processes"):
            procs = event.details["top_memory_processes"]
            context_parts.append("Top memory consumers:")
            for p in procs[:5]:
                context_parts.append(
                    f"  - {p['name']} (PID {p['pid']}): {p['memory_mb']:.0f}MB"
                )

        state["context"] = "\n".join(context_parts)
        return state

    def _decide(self, state: dict) -> dict:
        """Analyze the anomaly and plan a fix."""
        event: ObservationEvent = state["event"]

        # For process-specific anomalies, check the decision engine first
        if event.anomaly_type in (AnomalyType.MEMORY_LEAK, AnomalyType.UNKNOWN_HOG):
            pid = event.details.get("pid")
            name = event.details.get("name", "unknown")
            if pid:
                decision = self.decision_engine.decide(name, pid)
                if decision.decision == Decision.KILL:
                    state["planned_action"] = "kill_process"
                    state["action_args"] = {"pid": pid}
                    state["risk_level"] = "low"
                    state["diagnosis"] = {
                        "root_cause": decision.reason,
                        "fix": f"Kill {name} (PID {pid})",
                    }
                    return state
                elif decision.decision == Decision.KEEP:
                    state["planned_action"] = "skip"
                    state["diagnosis"] = {"root_cause": "Protected process", "fix": "None"}
                    return state

        # Check semantic memory for a known fix before calling LLM
        if self.memory and self.memory.available:
            try:
                past_fix = self.memory.find_similar_fix(event.summary)
            except Exception as e:
                logger.warning(f"Semantic memory query failed: {e}")
                past_fix = None
            if past_fix:
                logger.info(f"Memory hit (similarity={past_fix['similarity']}): reusing fix '{past_fix['fix_applied']}'")
                state["diagnosis"] = {
                    "root_cause": f"Similar to past event: {past_fix['original_anomaly']}",
                    "fix": past_fix["fix_applied"],
                }
                state["risk_level"] = "low"
                fix = past_fix["fix_applied"]
                if "kill" in fix.lower():
                    state["planned_action"] = "kill_process"
                elif "restart" in fix.lower():
                    state["planned_action"] = "restart_service"
                elif "priority" in fix.lower():
                    state["planned_action"] = "set_priority"
                else:
                    state["planned_action"] = "run_command"
                state["action_args"] = {"command": fix}
                return state

        # For CPU/RAM anomalies, try the LLM
        if event.anomaly_type in (AnomalyType.CPU_SPIKE, AnomalyType.RAM_HIGH):
            try:
                response = self.llm.diagnose_anomaly(event.summary, state["context"])
                diagnosis = json.loads(response.content)
                state["diagnosis"] = diagnosis
                state["risk_level"] = diagnosis.get("risk_level", "medium")

                # If confidence is low, escalate
                if response.confidence < self.settings.get("api", {}).get("confidence_threshold", 0.7):
                    response2 = self.llm.diagnose_complex(
                        event.summary, state["context"], response.content
                    )
                    diagnosis = json.loads(response2.content)
                    state["diagnosis"] = diagnosis
                    state["risk_level"] = diagnosis.get("risk_level", "medium")

                # Extract the top offending process from event details
                # so actions have a concrete PID/name to operate on
                top_proc = self._extract_top_process(event)

                fix = diagnosis.get("fix", "")
                fix_lower = fix.lower()
                if ("kill" in fix_lower or "end task" in fix_lower or "terminate" in fix_lower) and top_proc and top_proc.get("pid"):
                    state["planned_action"] = "kill_process"
                    state["action_args"] = {"pid": top_proc["pid"]}
                elif "restart" in fix_lower and "service" in fix_lower:
                    svc = diagnosis.get("service_name", "")
                    if svc:
                        state["planned_action"] = "restart_service"
                        state["action_args"] = {"service_name": svc}
                    else:
                        state["planned_action"] = "skip"
                        logger.info(f"LLM suggested restart_service but no service_name extracted: {fix[:120]}")
                elif ("priority" in fix_lower or "lower" in fix_lower) and top_proc and top_proc.get("pid"):
                    state["planned_action"] = "set_priority"
                    state["action_args"] = {
                        "pid": top_proc["pid"],
                        "priority": "low",
                    }
                else:
                    # LLM returned general advice — skip rather than
                    # passing prose as a command (which always fails)
                    state["planned_action"] = "skip"
                    logger.info(f"LLM fix is advisory (no actionable command): {fix[:120]}")

            except json.JSONDecodeError:
                state["error"] = "Failed to parse LLM response"
                state["planned_action"] = "skip"
            except Exception as e:
                logger.error(f"LLM diagnosis failed: {e}")
                state["error"] = str(e)
                state["planned_action"] = "skip"
        elif event.anomaly_type == AnomalyType.DISK_PRESSURE:
            disk_pct = event.details.get("disk_percent", 0)
            if disk_pct >= 95:
                state["planned_action"] = "cleanup_temp_files"
                state["risk_level"] = "low"
                state["diagnosis"] = {
                    "root_cause": f"Disk C:\\ at {disk_pct}% — temp file bloat likely",
                    "fix": "Clean temporary files older than 24h",
                }
            else:
                state["planned_action"] = "find_large_files"
                state["risk_level"] = "low"
                state["diagnosis"] = {
                    "root_cause": f"Disk C:\\ at {disk_pct}%",
                    "fix": "Identify large files for user review",
                }
        elif event.anomaly_type == AnomalyType.GPU_HIGH:
            state["planned_action"] = "skip"
            state["diagnosis"] = {
                "root_cause": event.summary,
                "fix": "Advisory — GPU load is normal during gaming/rendering",
            }
        elif event.anomaly_type == AnomalyType.THERMAL_THROTTLE:
            state["planned_action"] = "skip"
            state["diagnosis"] = {
                "root_cause": event.summary,
                "fix": "Alert user — check cooling, reduce workload",
            }
        else:
            state["planned_action"] = "skip"

        return state

    @staticmethod
    def _extract_top_process(event: ObservationEvent) -> dict | None:
        """Extract the top offending process from event details.

        Returns a dict with 'pid' and 'name', or None if unavailable.
        Looks at top_processes (CPU events) and top_memory_processes (RAM events).
        """
        # Direct PID in event details (memory leak, unknown hog)
        if event.details.get("pid"):
            return {"pid": event.details["pid"], "name": event.details.get("name", "unknown")}

        # CPU spikes — top process by CPU
        top = event.details.get("top_processes", [])
        if top:
            return {"pid": top[0]["pid"], "name": top[0].get("name", "unknown")}

        # RAM pressure — top process by memory
        top_mem = event.details.get("top_memory_processes", [])
        if top_mem:
            return {"pid": top_mem[0]["pid"], "name": top_mem[0].get("name", "unknown")}

        return None

    def _should_act(self, state: dict) -> str:
        if state.get("planned_action") in ("skip", ""):
            return "skip"
        return "act"

    def _hitl_gate(self, state: dict) -> dict:
        """Check if action needs human approval."""
        high_risk = self.settings.get("safety", {}).get("high_risk_actions", [])
        auto_approve = self.settings.get("safety", {}).get("auto_approve_risk_levels", ["low"])

        if state["risk_level"] in auto_approve:
            state["hitl_approved"] = True
            return state

        if state["planned_action"] in high_risk:
            state["needs_hitl"] = True
            if self._hitl_callback:
                from src.safety.hitl import HITLRequest, RiskLevel
                try:
                    risk = RiskLevel(state.get("risk_level", "medium"))
                except ValueError:
                    risk = RiskLevel.MEDIUM
                request = HITLRequest(
                    action=state.get("planned_action", "unknown"),
                    target=str(state.get("action_args", {})),
                    risk_level=risk,
                    reason=state.get("diagnosis", {}).get("root_cause", ""),
                    timestamp=datetime.now(),
                )
                try:
                    state["hitl_approved"] = self._hitl_callback(request)
                except Exception as e:
                    logger.error(f"HITL callback failed: {e} — denying action")
                    state["hitl_approved"] = False
            else:
                logger.warning("HITL required but no callback set — denying action")
                state["hitl_approved"] = False
        else:
            state["hitl_approved"] = True

        return state

    def _hitl_approved(self, state: dict) -> str:
        return "approved" if state.get("hitl_approved") else "denied"

    def _act(self, state: dict) -> dict:
        """Execute the planned action."""
        action = state["planned_action"]
        args = state.get("action_args", {})

        if action == "kill_process":
            pid = args.get("pid")
            if pid:
                state["action_result"] = self.tools.kill_process(pid)
            else:
                logger.warning(f"kill_process requested but no PID in action_args: {args}")
                state["action_result"] = ActionResult(False, "No PID provided for kill_process")
        elif action == "set_priority":
            pid = args.get("pid")
            priority = args.get("priority", "normal")
            if pid:
                state["action_result"] = self.tools.set_process_priority(pid, priority)
            else:
                logger.warning(f"set_priority requested but no PID in action_args: {args}")
                state["action_result"] = ActionResult(False, "No PID provided for set_priority")
        elif action == "restart_service":
            service = args.get("service_name", "")
            if service:
                state["action_result"] = self.tools.restart_service(service)
            else:
                logger.warning(f"restart_service requested but no service name in action_args: {args}")
                state["action_result"] = ActionResult(False, "No service name provided")
        elif action == "run_command":
            cmd = args.get("command", "")
            if cmd:
                state["action_result"] = self.tools.run_command(cmd)
        elif action == "cleanup_temp_files":
            state["action_result"] = self.tools.cleanup_temp_files()
        elif action == "find_large_files":
            state["action_result"] = self.tools.find_large_files()
        else:
            state["action_result"] = ActionResult(False, f"Unknown action: {action}")

        return state

    def _verify(self, state: dict) -> dict:
        """Verify the action was successful and store outcome in memory."""
        result = state.get("action_result")
        event: ObservationEvent | None = state.get("event")
        if result and result.success:
            state["completed"] = True
            logger.info(f"Action successful: {result.message}")
        elif result:
            logger.warning(f"Action failed: {result.message}")
            state["completed"] = False

        # Store outcome in semantic memory for future reuse
        if self.memory and self.memory.available and event and result:
            diagnosis = state.get("diagnosis", {})
            self.memory.store_fix(
                anomaly_summary=event.summary,
                anomaly_type=event.anomaly_type.value,
                fix_applied=diagnosis.get("fix", state.get("planned_action", "")),
                success=result.success,
                details={"action": state.get("planned_action", ""), "risk": state.get("risk_level", "")},
            )

        return state

    def _log_event(self, event: ObservationEvent, result: dict):
        self._event_log.append({
            "timestamp": event.timestamp.isoformat(),
            "anomaly": event.anomaly_type.value,
            "summary": event.summary,
            "action": result.get("planned_action", "none"),
            "success": result.get("completed", False),
        })

    def get_event_log(self) -> list[dict]:
        return list(self._event_log)
