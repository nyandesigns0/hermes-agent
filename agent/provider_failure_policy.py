"""Policy primitives for silent provider-call recovery.

The provider transport and the conversation retry loop both need to agree on
one causal failure.  Closing a stalled socket commonly makes the worker raise
``ReadError``/``BrokenPipeError``; those are effects of the watchdog abort, not
the reason the request failed.  ``ProviderStaleError`` preserves that reason
and carries bounded metadata used by retry, fallback, delegation, and logs.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Optional


_MAX_ROUTE_HEALTH_KEYS = 256
_MAX_STALE_OBSERVATIONS_PER_ROUTE = 8

class ProviderStaleError(TimeoutError):
    """A provider emitted no usable response before the stale watchdog fired."""

    failure_class = "provider_stale"

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: str,
        stale_timeout_seconds: float,
        estimated_context_tokens: int,
        phase: str,
        transport_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.stale_timeout_seconds = float(stale_timeout_seconds)
        self.estimated_context_tokens = int(estimated_context_tokens)
        self.phase = phase
        self.transport_error = transport_error
        if transport_error is not None:
            self.__cause__ = transport_error


class AuthoritativeFailureState:
    """Thread-safe request failure slot where watchdog causality wins."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failure: Optional[BaseException] = None

    @staticmethod
    def _attach_transport(stale: ProviderStaleError, error: BaseException) -> None:
        if error is stale:
            return
        if getattr(stale, "__cause__", None) is None:
            stale.transport_error = error
            stale.__cause__ = error

    def set_worker_error(self, error: BaseException) -> BaseException:
        with self._lock:
            if isinstance(self._failure, ProviderStaleError):
                self._attach_transport(self._failure, error)
                return self._failure
            self._failure = error
            return error

    def set_watchdog_failure(self, error: ProviderStaleError) -> ProviderStaleError:
        with self._lock:
            if self._failure is not None and not isinstance(self._failure, ProviderStaleError):
                self._attach_transport(error, self._failure)
            self._failure = error
            return error

    def get(self) -> Optional[BaseException]:
        with self._lock:
            return self._failure


@dataclass(frozen=True)
class PayloadWorkload:
    """Cheap, provider-neutral request characteristics for timeout policy."""

    tool_result_bytes: int = 0
    tool_result_count: int = 0
    has_prior_tool_call: bool = False

    @property
    def is_synthesis(self) -> bool:
        return self.tool_result_count > 0 or self.has_prior_tool_call


def inspect_payload_workload(api_payload: Any) -> PayloadWorkload:
    """Inspect Chat Completions or Responses payloads without retaining data."""

    if not isinstance(api_payload, dict):
        return PayloadWorkload()
    items = api_payload.get("input")
    if not isinstance(items, list):
        items = api_payload.get("messages")
    if not isinstance(items, list):
        return PayloadWorkload()

    result_bytes = 0
    result_count = 0
    has_call = False
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        role = str(item.get("role") or "")
        if item_type in {"function_call", "tool_call"} or item.get("tool_calls"):
            has_call = True
        if item_type in {"function_call_output", "tool_result"} or role == "tool":
            value = item.get("output", item.get("content", ""))
            result_bytes += len(value.encode("utf-8", errors="replace")) if isinstance(value, str) else len(str(value).encode("utf-8", errors="replace"))
            result_count += 1
    return PayloadWorkload(result_bytes, result_count, has_call)


def adaptive_stale_timeout(
    *,
    base_seconds: float,
    explicit: bool,
    api_payload: Any,
    reasoning_config: Any,
    retry_multiplier: float = 1.0,
) -> float:
    """Return a silence allowance scaled for synthesis and reasoning work.

    Explicit provider/model/environment stale timeouts are authoritative.  The
    adaptive policy only improves Hermes's implicit default.
    """

    if explicit:
        return float(base_seconds)

    timeout = float(base_seconds)
    workload = inspect_payload_workload(api_payload)
    effort = ""
    if isinstance(reasoning_config, dict):
        effort = str(reasoning_config.get("effort") or reasoning_config.get("reasoning_effort") or "").lower()
    elif reasoning_config is not None:
        effort = str(reasoning_config).lower()

    # Final synthesis after tools is slower than an ordinary short prompt even
    # when total input remains below the old 50k-token tier.
    if workload.is_synthesis:
        timeout = max(timeout, 150.0)
    if workload.tool_result_bytes >= 32_000:
        timeout = max(timeout, 180.0)

    if effort in {"high", "xhigh"}:
        timeout = max(timeout, 180.0 if effort == "high" else 240.0)

    multiplier = max(1.0, min(float(retry_multiplier or 1.0), 3.0))
    return min(timeout * multiplier, 600.0)


def stale_recovery_action(*, reason: Any, stale_attempts: int, has_fallback: bool) -> str:
    """Choose fallback, one expanded retry, or terminal failure."""

    reason_value = getattr(reason, "value", reason)
    if reason_value != "provider_stale":
        return "normal"
    if has_fallback:
        return "fallback"
    if int(stale_attempts or 0) < 1:
        return "retry_expanded"
    return "fail"


@dataclass(frozen=True)
class ProviderRouteHealth:
    """Short-lived process-local health for one provider/model endpoint."""

    degraded: bool
    stale_count: int
    degraded_until: float = 0.0


_health_lock = threading.Lock()
_health_state: dict[tuple[str, str, str], dict[str, Any]] = {}


def _route_key(*, provider: str, model: str, base_url: str) -> tuple[str, str, str]:
    return (
        str(provider or "").strip().lower(),
        str(model or "").strip().lower(),
        str(base_url or "").strip().rstrip("/").lower(),
    )


def _prune_health_state_locked(current: float, window_seconds: float) -> None:
    """Prune expired observations and cap global route-health memory."""

    for key in list(_health_state.keys()):
        state = _health_state.get(key) or {}
        recent = [
            ts for ts in state.get("stale_times", [])[-_MAX_STALE_OBSERVATIONS_PER_ROUTE:]
            if current - float(ts) <= window_seconds
        ]
        degraded_until = float(state.get("degraded_until", 0.0) or 0.0)
        if not recent and degraded_until <= current:
            _health_state.pop(key, None)
            continue
        state["stale_times"] = recent
        state["last_seen"] = max(recent, default=float(state.get("last_seen", 0.0) or 0.0))

    while len(_health_state) > _MAX_ROUTE_HEALTH_KEYS:
        oldest_key = min(
            _health_state,
            key=lambda k: float(_health_state[k].get("last_seen", 0.0) or 0.0),
        )
        _health_state.pop(oldest_key, None)


def provider_route_health(
    *, provider: str, model: str, base_url: str, now: Optional[float] = None,
    window_seconds: float = 600.0,
) -> ProviderRouteHealth:
    """Return current route health and prune expired stale observations."""

    current = time.monotonic() if now is None else float(now)
    key = _route_key(provider=provider, model=model, base_url=base_url)
    with _health_lock:
        _prune_health_state_locked(current, window_seconds)
        state = _health_state.get(key)
        if state is None:
            return ProviderRouteHealth(False, 0, 0.0)
        recent = list(state.get("stale_times", []))
        degraded_until = float(state.get("degraded_until", 0.0) or 0.0)
        degraded = degraded_until > current
        if not recent and not degraded:
            _health_state.pop(key, None)
            return ProviderRouteHealth(False, 0, 0.0)
        state["stale_times"] = recent
        return ProviderRouteHealth(degraded, len(recent), degraded_until)


def record_provider_stale(
    *, provider: str, model: str, base_url: str, now: Optional[float] = None,
    threshold: int = 2, window_seconds: float = 600.0,
    degraded_seconds: float = 600.0,
) -> ProviderRouteHealth:
    """Record provider silence and open a bounded circuit after repetition."""

    current = time.monotonic() if now is None else float(now)
    key = _route_key(provider=provider, model=model, base_url=base_url)
    with _health_lock:
        _prune_health_state_locked(current, window_seconds)
        state = _health_state.setdefault(key, {"stale_times": [], "degraded_until": 0.0, "last_seen": current})
        recent = [
            ts for ts in state.get("stale_times", [])
            if current - float(ts) <= window_seconds
        ]
        recent.append(current)
        state["stale_times"] = recent[-_MAX_STALE_OBSERVATIONS_PER_ROUTE:]
        state["last_seen"] = current
        if len(recent) >= max(1, int(threshold)):
            state["degraded_until"] = max(
                float(state.get("degraded_until", 0.0) or 0.0),
                current + max(1.0, float(degraded_seconds)),
            )
        degraded_until = float(state.get("degraded_until", 0.0) or 0.0)
        _prune_health_state_locked(current, window_seconds)
        state = _health_state.get(key, state)
        degraded_until = float(state.get("degraded_until", degraded_until) or 0.0)
        return ProviderRouteHealth(degraded_until > current, len(state.get("stale_times", recent)), degraded_until)


def record_provider_success(*, provider: str, model: str, base_url: str) -> None:
    """A successful response closes the route's stale circuit immediately."""

    key = _route_key(provider=provider, model=model, base_url=base_url)
    with _health_lock:
        _health_state.pop(key, None)


def reset_provider_health_for_tests() -> None:
    """Clear process-local health state; public only for deterministic tests."""

    with _health_lock:
        _health_state.clear()
