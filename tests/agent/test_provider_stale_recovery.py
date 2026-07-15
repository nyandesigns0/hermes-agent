"""Regression tests for typed provider-stale recovery."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest


class _BlockingCompletions:
    def __init__(self, release: threading.Event):
        self._release = release

    def create(self, **_kwargs):
        self._release.wait(2.0)
        raise BrokenPipeError(32, "Broken pipe")


class _BlockingClient:
    def __init__(self, release: threading.Event):
        self.chat = SimpleNamespace(completions=_BlockingCompletions(release))


class _BlockingStream:
    response = None

    def __init__(self, release: threading.Event):
        self._release = release

    def __iter__(self):
        self._release.wait(3.0)
        raise BrokenPipeError(32, "Broken pipe")
        yield  # pragma: no cover - makes this a generator


class _StreamingCompletions:
    def __init__(self, release: threading.Event):
        self._release = release
        self.create_calls = 0

    def create(self, **_kwargs):
        self.create_calls += 1
        return _BlockingStream(self._release)


class _StreamingClient:
    def __init__(self, release: threading.Event):
        self.completions = _StreamingCompletions(release)
        self.chat = SimpleNamespace(completions=self.completions)


class _StaleAgent:
    api_mode = "chat_completions"
    provider = "test-provider"
    model = "slow-model"
    _interrupt_requested = False
    _base_url_lower = "https://example.test/v1"
    _base_url_hostname = "example.test"
    base_url = "https://example.test/v1"

    def __init__(self):
        self.release = threading.Event()
        self.status = []

    def _compute_non_stream_stale_timeout(self, _payload):
        return 0.01

    def _create_request_openai_client(self, **_kwargs):
        return _BlockingClient(self.release)

    def _abort_request_openai_client(self, _client, **_kwargs):
        self.release.set()

    def _close_request_openai_client(self, _client, **_kwargs):
        self.release.set()

    def _touch_activity(self, _description):
        return None

    def _buffer_status(self, text):
        self.status.append(text)

    def _codex_silent_hang_hint(self, model=None):
        return None


class _StreamingStaleAgent(_StaleAgent):
    stream_delta_callback = None
    reasoning_callback = None
    _stream_callback = None
    _disable_streaming = False

    def __init__(self):
        super().__init__()
        self.client = _StreamingClient(self.release)
        self.request_client = self.client

    def _create_request_openai_client(self, **_kwargs):
        return self.request_client

    def _stream_diag_init(self):
        return {}

    def _stream_diag_capture_response(self, *_args, **_kwargs):
        return None

    def _capture_rate_limits(self, *_args, **_kwargs):
        return None

    def _check_openrouter_cache_status(self, *_args, **_kwargs):
        return None

    def _is_provider_stream_parse_error(self, _error):
        return False

    def _has_stream_consumers(self):
        return False

    def _fire_stream_delta(self, _text):
        return None

    def _fire_reasoning_delta(self, _text):
        return None

    def _fire_tool_gen_started(self, _name):
        return None

    def _emit_stream_drop(self, **_kwargs):
        return None

    def _log_stream_retry(self, **_kwargs):
        return None

    def _replace_primary_openai_client(self, **_kwargs):
        return None

    def _safe_print(self, *_args, **_kwargs):
        return None


class _BlockingAnthropicStream:
    response = None

    def __init__(self, release: threading.Event):
        self._release = release

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        self._release.wait(3.0)
        raise BrokenPipeError(32, "Broken pipe")
        yield  # pragma: no cover - makes this a generator

    def get_final_message(self):  # pragma: no cover - iterator never completes
        return SimpleNamespace(content=[])


class _BlockingAnthropicClient:
    def __init__(self):
        self.release = threading.Event()
        self.close_calls = 0
        self.stream_calls = 0
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **_kwargs):
        self.stream_calls += 1
        return _BlockingAnthropicStream(self.release)

    def close(self):
        self.close_calls += 1
        self.release.set()


class _AnthropicStaleAgent(_StreamingStaleAgent):
    api_mode = "anthropic_messages"
    provider = "anthropic"
    model = "claude-test"

    def __init__(self):
        super().__init__()
        self._anthropic_client = _BlockingAnthropicClient()
        self.rebuilds = 0
        self.primary_replacements = 0

    def _try_refresh_anthropic_client_credentials(self):
        return None

    def _rebuild_anthropic_client(self):
        self.rebuilds += 1

    def _replace_primary_openai_client(self, **_kwargs):
        self.primary_replacements += 1


class _BlockingBedrockEventStream:
    def __init__(self):
        self.release = threading.Event()
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        self.release.set()


class _BlockingBedrockClient:
    def __init__(self, event_stream: _BlockingBedrockEventStream):
        self.event_stream = event_stream
        self.converse_stream_calls = 0

    def converse_stream(self, **_kwargs):
        self.converse_stream_calls += 1
        return {"stream": self.event_stream}


class _BedrockStaleAgent(_StreamingStaleAgent):
    api_mode = "bedrock_converse"
    provider = "bedrock"
    model = "claude-bedrock-test"
    reasoning_callback = None

    def __init__(self):
        super().__init__()
        self.primary_replacements = 0

    def _replace_primary_openai_client(self, **_kwargs):
        self.primary_replacements += 1


def test_stale_stream_raises_typed_failure_without_internal_identical_retries(monkeypatch):
    from agent.chat_completion_helpers import interruptible_streaming_api_call
    from agent.provider_failure_policy import ProviderStaleError

    monkeypatch.setenv("HERMES_STREAM_STALE_TIMEOUT", "0.01")
    monkeypatch.setenv("HERMES_STREAM_RETRIES", "2")
    agent = _StreamingStaleAgent()

    with __import__("pytest").raises(ProviderStaleError) as raised:
        interruptible_streaming_api_call(agent, {"model": "slow-model", "messages": []})

    assert isinstance(raised.value.__cause__, BrokenPipeError)
    assert agent.request_client.completions.create_calls == 1


def test_anthropic_stream_stale_uses_typed_policy_and_rebuilds_client(monkeypatch):
    from agent.chat_completion_helpers import interruptible_streaming_api_call
    from agent.provider_failure_policy import ProviderStaleError

    monkeypatch.setenv("HERMES_STREAM_STALE_TIMEOUT", "0.01")
    monkeypatch.setenv("HERMES_STREAM_RETRIES", "2")
    agent = _AnthropicStaleAgent()

    with pytest.raises(ProviderStaleError) as raised:
        interruptible_streaming_api_call(agent, {"model": "claude-test", "messages": []})

    assert isinstance(raised.value.__cause__, BrokenPipeError)
    assert agent._anthropic_client.stream_calls == 1
    assert agent._anthropic_client.close_calls == 1
    assert agent.rebuilds == 1
    assert agent.primary_replacements == 1


def test_bedrock_stream_stale_closes_stream_invalidates_client_and_raises_typed(monkeypatch):
    from agent.chat_completion_helpers import interruptible_streaming_api_call
    from agent.provider_failure_policy import ProviderStaleError
    import agent.bedrock_adapter as bedrock_adapter

    event_stream = _BlockingBedrockEventStream()
    client = _BlockingBedrockClient(event_stream)
    invalidated_regions = []

    monkeypatch.setattr(bedrock_adapter, "_get_bedrock_runtime_client", lambda region: client)
    monkeypatch.setattr(bedrock_adapter, "invalidate_runtime_client", invalidated_regions.append)
    monkeypatch.setattr(bedrock_adapter, "is_stale_connection_error", lambda _exc: False)

    def _blocked_stream_converse(*_args, **_kwargs):
        event_stream.release.wait(3.0)
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(bedrock_adapter, "stream_converse_with_callbacks", _blocked_stream_converse)
    agent = _BedrockStaleAgent()

    with pytest.raises(ProviderStaleError) as raised:
        interruptible_streaming_api_call(
            agent,
            {
                "modelId": "anthropic.claude-test",
                "messages": [],
                "__bedrock_region__": "us-west-2",
                "__bedrock_converse__": True,
            },
        )

    assert isinstance(raised.value.__cause__, BrokenPipeError)
    assert client.converse_stream_calls == 1
    assert event_stream.close_calls == 1
    assert invalidated_regions == ["us-west-2"]


def test_stale_detector_preserves_cause_when_abort_surfaces_broken_pipe():
    from agent.chat_completion_helpers import interruptible_api_call
    from agent.provider_failure_policy import ProviderStaleError

    agent = _StaleAgent()

    try:
        interruptible_api_call(agent, {"model": "slow-model", "messages": []})
    except Exception as exc:
        assert isinstance(exc, ProviderStaleError)
        assert exc.provider == "test-provider"
        assert exc.model == "slow-model"
        assert exc.stale_timeout_seconds == 0.01
        assert isinstance(exc.__cause__, BrokenPipeError)
    else:  # pragma: no cover - explicit assertion message is clearer
        raise AssertionError("expected ProviderStaleError")


def test_provider_stale_classification_requests_immediate_fallback():
    from agent.error_classifier import FailoverReason, classify_api_error
    from agent.provider_failure_policy import ProviderStaleError

    error = ProviderStaleError(
        "provider produced no response",
        provider="openai-codex",
        model="gpt-test",
        stale_timeout_seconds=90,
        estimated_context_tokens=17_000,
        phase="final_synthesis",
    )

    classified = classify_api_error(error, provider="openai-codex", model="gpt-test")

    assert classified.reason == FailoverReason.provider_stale
    assert classified.retryable is True
    assert classified.should_fallback is True


def test_stale_retry_policy_prefers_fallback_without_same_route_retry():
    from agent.error_classifier import FailoverReason
    from agent.provider_failure_policy import stale_recovery_action

    assert stale_recovery_action(
        reason=FailoverReason.provider_stale,
        stale_attempts=0,
        has_fallback=True,
    ) == "fallback"


def test_stale_retry_policy_allows_only_one_expanded_same_route_retry():
    from agent.error_classifier import FailoverReason
    from agent.provider_failure_policy import stale_recovery_action

    assert stale_recovery_action(
        reason=FailoverReason.provider_stale,
        stale_attempts=0,
        has_fallback=False,
    ) == "retry_expanded"
    assert stale_recovery_action(
        reason=FailoverReason.provider_stale,
        stale_attempts=1,
        has_fallback=False,
    ) == "fail"


def test_provider_health_circuit_opens_after_two_recent_stale_calls():
    from agent.provider_failure_policy import (
        provider_route_health,
        record_provider_stale,
        reset_provider_health_for_tests,
    )

    reset_provider_health_for_tests()
    route = dict(provider="openai-codex", model="gpt-test", base_url="https://example.test")

    first = record_provider_stale(**route, now=100.0)
    second = record_provider_stale(**route, now=120.0)

    assert first.degraded is False
    assert second.degraded is True
    assert second.stale_count == 2
    assert provider_route_health(**route, now=121.0).degraded is True
    assert provider_route_health(**route, now=721.0).degraded is False


def test_provider_health_state_is_globally_bounded():
    import agent.provider_failure_policy as policy
    from agent.provider_failure_policy import record_provider_stale, reset_provider_health_for_tests

    reset_provider_health_for_tests()
    for idx in range(policy._MAX_ROUTE_HEALTH_KEYS + 25):
        record_provider_stale(
            provider="p",
            model=f"m-{idx}",
            base_url=f"https://example-{idx}.test",
            now=float(idx),
        )

    assert len(policy._health_state) <= policy._MAX_ROUTE_HEALTH_KEYS


def test_provider_health_per_route_observations_are_bounded():
    import agent.provider_failure_policy as policy
    from agent.provider_failure_policy import record_provider_stale, reset_provider_health_for_tests

    reset_provider_health_for_tests()
    route = dict(provider="p", model="m", base_url="https://example.test")
    for idx in range(policy._MAX_STALE_OBSERVATIONS_PER_ROUTE + 10):
        record_provider_stale(**route, now=float(idx), threshold=99)

    state = next(iter(policy._health_state.values()))
    assert len(state["stale_times"]) <= policy._MAX_STALE_OBSERVATIONS_PER_ROUTE


def test_provider_success_closes_the_route_circuit():
    from agent.provider_failure_policy import (
        provider_route_health,
        record_provider_stale,
        record_provider_success,
        reset_provider_health_for_tests,
    )

    reset_provider_health_for_tests()
    route = dict(provider="openai-codex", model="gpt-test", base_url="https://example.test")
    record_provider_stale(**route, now=100.0)
    record_provider_stale(**route, now=110.0)
    assert provider_route_health(**route, now=111.0).degraded is True

    record_provider_success(**route)

    health = provider_route_health(**route, now=112.0)
    assert health.degraded is False
    assert health.stale_count == 0


def test_authoritative_watchdog_failure_cannot_be_overwritten_by_late_worker_error():
    from agent.provider_failure_policy import AuthoritativeFailureState, ProviderStaleError

    state = AuthoritativeFailureState()
    stale = ProviderStaleError(
        "silent",
        provider="p",
        model="m",
        stale_timeout_seconds=1,
        estimated_context_tokens=1,
        phase="provider_response",
    )
    state.set_watchdog_failure(stale)
    state.set_worker_error(BrokenPipeError(32, "Broken pipe"))

    assert state.get() is stale
    assert isinstance(stale.__cause__, BrokenPipeError)
