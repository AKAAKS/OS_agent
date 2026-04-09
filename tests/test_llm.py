"""Tests for LLM client — cost cap, token tracking, threading safety."""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.agent.llm import LLMClient, LLMResponse


@pytest.fixture
def llm_no_key(sample_settings):
    """LLM client with no API key — all calls return error."""
    settings = {**sample_settings, "api": {**sample_settings["api"], "api_key": ""}}
    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
        return LLMClient(settings)


@pytest.fixture
def llm_with_mock(sample_settings):
    """LLM client with a mocked OpenAI client."""
    client = LLMClient(sample_settings)
    client.client = MagicMock()
    return client


class TestNoApiKey:

    def test_returns_error_content(self, llm_no_key):
        result = llm_no_key.analyze_process({"name": "test.exe"}, "context")
        assert "error" in result.content
        assert result.model_used == "none"
        assert result.confidence == 0.0

    def test_no_tokens_used(self, llm_no_key):
        llm_no_key.analyze_process({"name": "test.exe"}, "context")
        stats = llm_no_key.get_usage_stats()
        assert stats["total_input_tokens"] == 0
        assert stats["total_output_tokens"] == 0


class TestCostCap:

    def test_cost_cap_blocks_calls(self, llm_with_mock):
        # Simulate hitting cost cap by inflating token counters
        llm_with_mock._total_input_tokens = 10_000_000  # way over cap
        llm_with_mock._total_output_tokens = 10_000_000

        result = llm_with_mock.diagnose_anomaly("test", "context")
        assert "Cost cap reached" in result.content
        # Should NOT have called the OpenAI API
        llm_with_mock.client.chat.completions.create.assert_not_called()

    def test_under_cap_makes_call(self, llm_with_mock):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"root_cause": "test", "confidence": 0.9}'
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        llm_with_mock.client.chat.completions.create.return_value = mock_response

        result = llm_with_mock.diagnose_anomaly("test", "context")
        assert result.model_used == "gpt-4o-mini"
        llm_with_mock.client.chat.completions.create.assert_called_once()


class TestTokenTracking:

    def test_tokens_accumulate(self, llm_with_mock):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"confidence": 0.5}'
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        llm_with_mock.client.chat.completions.create.return_value = mock_response

        llm_with_mock.diagnose_anomaly("event1", "ctx")
        llm_with_mock.diagnose_anomaly("event2", "ctx")

        stats = llm_with_mock.get_usage_stats()
        assert stats["total_input_tokens"] == 200
        assert stats["total_output_tokens"] == 100

    def test_thread_safety(self, llm_with_mock):
        """Concurrent calls should not corrupt token counters."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"confidence": 0.5}'
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        llm_with_mock.client.chat.completions.create.return_value = mock_response

        threads = []
        for _ in range(20):
            t = threading.Thread(target=llm_with_mock.diagnose_anomaly, args=("evt", "ctx"))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        stats = llm_with_mock.get_usage_stats()
        assert stats["total_input_tokens"] == 200  # 20 * 10
        assert stats["total_output_tokens"] == 100  # 20 * 5


class TestConfidenceExtraction:

    def test_parses_confidence_from_json(self, llm_with_mock):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"confidence": 0.85, "root_cause": "test"}'
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        llm_with_mock.client.chat.completions.create.return_value = mock_response

        result = llm_with_mock.diagnose_anomaly("test", "ctx")
        assert result.confidence == 0.85

    def test_invalid_json_defaults_to_half(self, llm_with_mock):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not valid json"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        llm_with_mock.client.chat.completions.create.return_value = mock_response

        result = llm_with_mock.diagnose_anomaly("test", "ctx")
        assert result.confidence == 0.5
