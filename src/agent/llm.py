"""
OpenAI API wrapper with tiered model selection.
Uses gpt-4o-mini for triage, escalates to gpt-4o for complex cases.
"""

import json
import logging
import os
import threading
from dataclasses import dataclass

from openai import OpenAI

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    model_used: str
    input_tokens: int
    output_tokens: int
    confidence: float  # Self-assessed confidence from the model


class LLMClient:
    """Tiered OpenAI client — triage with mini, escalate to full for complex cases."""

    def __init__(self, settings: dict):
        api_config = settings.get("api", {})
        api_key = api_config.get("api_key", "").replace(
            "${OPENAI_API_KEY}", os.environ.get("OPENAI_API_KEY", "")
        )

        if not api_key:
            logger.warning("No OpenAI API key configured. LLM calls will fail.")

        self.client = OpenAI(api_key=api_key) if api_key else None
        self.triage_model = api_config.get("triage_model", "gpt-4o-mini")
        self.complex_model = api_config.get("complex_model", "gpt-4o")
        self.confidence_threshold = api_config.get("confidence_threshold", 0.7)

        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._cost_cap = api_config.get("cost_cap_usd", 1.00)
        self._lock = threading.Lock()  # Protects token counters and cost cap check

        # Circuit breaker: stop calling after N consecutive identical errors
        self._consecutive_errors = 0
        self._last_error_msg = ""
        self._CIRCUIT_BREAKER_THRESHOLD = 3  # trip after 3 identical errors in a row

    def analyze_process(self, process_info: dict, system_context: str) -> LLMResponse:
        """Ask the LLM whether a process should be kept or killed."""
        prompt = f"""You are a Windows 11 system optimizer. Analyze this unknown process and recommend whether to KEEP or KILL it.

Process info:
- Name: {process_info.get('name', 'unknown')}
- PID: {process_info.get('pid', 'unknown')}
- CPU: {process_info.get('cpu_percent', 0):.1f}%
- Memory: {process_info.get('memory_mb', 0):.0f} MB
- Parent: {process_info.get('parent', 'unknown')}
- Signed: {process_info.get('signed', 'unknown')}

System context:
{system_context}

Respond in JSON format:
{{"decision": "keep" or "kill", "confidence": 0.0-1.0, "reason": "brief explanation"}}"""

        return self._call(prompt, use_complex=False)

    def diagnose_anomaly(self, event_summary: str, context: str) -> LLMResponse:
        """Diagnose a system anomaly and suggest a fix."""
        prompt = f"""You are a Windows 11 system diagnostics agent. Analyze this anomaly and suggest a fix.

Anomaly: {event_summary}

System context:
{context}

Respond in JSON format:
{{"root_cause": "brief diagnosis", "fix": "specific action to take", "risk_level": "low/medium/high", "confidence": 0.0-1.0}}"""

        return self._call(prompt, use_complex=False)

    def diagnose_complex(self, event_summary: str, context: str, prior_attempt: str = "") -> LLMResponse:
        """Escalate to the complex model for difficult diagnoses."""
        prompt = f"""You are an expert Windows 11 system diagnostics agent. A simpler model couldn't resolve this issue.

Anomaly: {event_summary}

System context:
{context}

{"Prior attempt that failed: " + prior_attempt if prior_attempt else ""}

Provide a thorough diagnosis and specific fix. Respond in JSON format:
{{"root_cause": "detailed diagnosis", "fix": "specific commands or actions", "risk_level": "low/medium/high", "confidence": 0.0-1.0, "alternative_fixes": ["backup fix 1", "backup fix 2"]}}"""

        return self._call(prompt, use_complex=True)

    def diagnose_bluetooth(self, bt_events: list[dict], device_info: str) -> LLMResponse:
        """Diagnose Bluetooth connectivity issues."""
        events_str = "\n".join(
            f"  [{e.get('timestamp', '')}] {e.get('message', '')}" for e in bt_events[:10]
        )
        prompt = f"""You are a Windows 11 Bluetooth diagnostics expert. Analyze these Bluetooth issues.

Device: {device_info}

Recent BT event log entries:
{events_str}

Diagnose the issue and suggest a fix. Respond in JSON format:
{{"root_cause": "diagnosis", "fix": "specific action", "risk_level": "low/medium/high", "confidence": 0.0-1.0}}"""

        return self._call(prompt, use_complex=False)

    def _call(self, prompt: str, use_complex: bool = False) -> LLMResponse:
        """Make an API call with the appropriate model."""
        if not self.client:
            return LLMResponse(
                content='{"error": "No API key configured"}',
                model_used="none",
                input_tokens=0,
                output_tokens=0,
                confidence=0.0,
            )

        # Circuit breaker — stop hammering the API with the same failing request
        with self._lock:
            if self._consecutive_errors >= self._CIRCUIT_BREAKER_THRESHOLD:
                logger.warning(
                    f"Circuit breaker open ({self._consecutive_errors} identical errors) — skipping LLM call. "
                    f"Last error: {self._last_error_msg[:100]}"
                )
                return LLMResponse(
                    content='{"error": "Circuit breaker open — too many identical errors"}',
                    model_used="none",
                    input_tokens=0,
                    output_tokens=0,
                    confidence=0.0,
                )

        # Thread-safe cost cap check
        with self._lock:
            current_cost = self.get_usage_stats()["estimated_cost_usd"]
            if current_cost >= self._cost_cap:
                logger.warning(f"Cost cap reached (${current_cost:.4f} >= ${self._cost_cap:.2f}) — skipping LLM call")
                return LLMResponse(
                    content='{"error": "Cost cap reached"}',
                    model_used="none",
                    input_tokens=0,
                    output_tokens=0,
                    confidence=0.0,
                )

        model = self.complex_model if use_complex else self.triage_model
        logger.info(f"LLM call → {model}")

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a Windows system diagnostics agent. Always respond in valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                max_completion_tokens=2048,
            )

            if not response.choices:
                logger.warning("LLM returned empty choices list")
                return LLMResponse(
                    content='{"error": "Empty response from model"}',
                    model_used=model, input_tokens=0, output_tokens=0, confidence=0.0,
                )

            content = response.choices[0].message.content or ""
            usage = response.usage
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0

            with self._lock:
                self._total_input_tokens += input_tokens
                self._total_output_tokens += output_tokens
                # Reset circuit breaker on success
                self._consecutive_errors = 0
                self._last_error_msg = ""

            # Try to extract confidence from the response
            confidence = 0.5
            try:
                parsed = json.loads(content)
                confidence = float(parsed.get("confidence", 0.5))
            except (json.JSONDecodeError, ValueError):
                pass

            logger.info(f"LLM response: {input_tokens} in / {output_tokens} out tokens")

            return LLMResponse(
                content=content,
                model_used=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                confidence=confidence,
            )

        except Exception as e:
            error_type = type(e).__name__
            # Detect rate limit / timeout for clearer logging
            e_str = str(e)
            if "429" in e_str or "rate" in e_str.lower():
                logger.warning(f"LLM rate-limited (429) — back off before retrying: {e}")
            elif "timeout" in e_str.lower():
                logger.warning(f"LLM call timed out: {e}")
            else:
                logger.error(f"LLM call failed ({error_type}): {e}")

            # Circuit breaker: track consecutive identical errors
            error_sig = f"{error_type}:{e_str[:200]}"
            with self._lock:
                if error_sig == self._last_error_msg:
                    self._consecutive_errors += 1
                else:
                    self._consecutive_errors = 1
                    self._last_error_msg = error_sig
                if self._consecutive_errors >= self._CIRCUIT_BREAKER_THRESHOLD:
                    logger.warning(
                        f"Circuit breaker tripped after {self._consecutive_errors} identical errors — "
                        f"future LLM calls will be blocked until a successful call resets it"
                    )

            return LLMResponse(
                content=f'{{"error": "{error_type}: {str(e)}"}}',
                model_used=model,
                input_tokens=0,
                output_tokens=0,
                confidence=0.0,
            )

    def reset_circuit_breaker(self):
        """Reset the circuit breaker (e.g., after fixing config or API key)."""
        with self._lock:
            self._consecutive_errors = 0
            self._last_error_msg = ""
        logger.info("LLM circuit breaker reset")

    def get_usage_stats(self) -> dict:
        """Get cumulative token usage stats."""
        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "estimated_cost_usd": round(
                (self._total_input_tokens * 0.15 / 1_000_000)
                + (self._total_output_tokens * 0.60 / 1_000_000),
                4,
            ),
            "circuit_breaker_tripped": self._consecutive_errors >= self._CIRCUIT_BREAKER_THRESHOLD,
        }
