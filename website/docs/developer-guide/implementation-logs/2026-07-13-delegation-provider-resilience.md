# Delegation Provider Resilience Implementation Log

- Timestamp: 2026-07-13T21:19:52-07:00
- Owner: Solomon
- Branch: `solomon/delegation-resilience`
- Worktree: `/home/xli24/.hermes/profiles/solomon/workspace/hermes-delegation-resilience`
- Status: verified; ready for isolated-branch integration review

## Objective

Prevent delegated work from losing completed tool evidence or repeating the same request three times when a non-streaming provider remains silent and the watchdog abort produces a secondary `ReadError`/`Broken pipe`.

## Isolation and Runtime Safety

The shared Hermes checkout had extensive unrelated uncommitted changes and multiple active Hermes processes. Implementation was therefore performed in a dedicated Git worktree. No gateway, CLI, profile, or other Hermes process was stopped, restarted, reconfigured, or sent signals.

## Files Changed

- `agent/provider_failure_policy.py`
- `agent/chat_completion_helpers.py`
- `agent/error_classifier.py`
- `agent/conversation_loop.py`
- `run_agent.py`
- `tools/delegate_tool.py`
- `tests/agent/test_provider_stale_recovery.py`
- `tests/agent/test_non_stream_stale_timeout.py`
- `tests/agent/test_error_classifier.py`
- `tests/tools/test_delegate_partial_salvage.py`
- `tests/tools/test_browser_hardening.py`
- `tests/tools/test_browser_homebrew_paths.py`
- `tests/tools/test_file_staleness.py`
- `tests/tools/test_terminal_tool.py`
- `tests/agent/test_vision_routing_31179.py`
- `tools/voice_mode.py`
- `website/docs/user-guide/features/delegation.md`
- `docs/plans/2026-07-13-delegation-provider-resilience.md`

## Implementation Summary

- Added typed `ProviderStaleError` preserving provider, model, timeout, context estimate, phase, and abort-side transport cause.
- All stale watchdog paths replace secondary socket errors with the causal provider-stale failure.
- Added adaptive implicit timeout policy for synthesis after tool results, large evidence payloads, and high reasoning effort.
- Explicit provider/model/environment stale timeout configuration remains authoritative.
- Added `provider_stale` error taxonomy with immediate-fallback guidance.
- Conversation recovery now prefers fallback immediately, otherwise performs one expanded same-route retry, then terminates with a structured partial failure.
- Delegation returns `status=partial`, failure class, tool trace, and evidence counts when a failed child has successful tool results.
- Added map/reduce guidance for complete audits and multi-source reconciliation.
- Force-redacted partial evidence previews before truncation, including URL query secrets, URL userinfo, Authorization header variants, non-string content, and UTF-8 byte accounting.
- Hardened browser discovery unit tests so “not found” tests do not invoke the real lazy dependency installer during broad regression.

## TDD Evidence

RED command:

```bash
PYTHONPATH=. /home/xli24/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent/test_provider_stale_recovery.py tests/agent/test_non_stream_stale_timeout.py tests/tools/test_delegate_partial_salvage.py -q -o 'addopts='
```

Result: `7 failed, 13 passed`; failures matched the missing typed policy, adaptive timeout, partial evidence, and decomposition guidance.

Initial GREEN command:

```bash
PYTHONPATH=. /home/xli24/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent/test_provider_stale_recovery.py tests/agent/test_non_stream_stale_timeout.py tests/agent/test_error_classifier.py tests/tools/test_delegate_partial_salvage.py tests/tools/test_delegate.py tests/run_agent/test_provider_fallback.py -q -o 'addopts='
```

Result: `332 passed, 1 warning in 47.30s`. Warning is the pre-existing Python `audioop` deprecation from `discord/player.py`.

## Final Verification Evidence

Focused reviewer regressions:

```bash
/home/xli24/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent/test_provider_stale_recovery.py::test_stale_stream_raises_typed_failure_without_internal_identical_retries tests/run_agent/test_openai_client_lifecycle.py::test_stale_non_stream_close_is_single_owner tests/tools/test_delegate_partial_salvage.py::test_structured_success_false_is_not_salvaged_as_successful_evidence tests/tools/test_delegate_partial_salvage.py::test_partial_evidence_tail_is_force_redacted_and_bounded
```

Result: `4 passed, 1 warning in 19.79s`.

Directly affected suites:

```bash
/home/xli24/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent/test_provider_stale_recovery.py tests/agent/test_non_stream_stale_timeout.py tests/agent/test_error_classifier.py tests/tools/test_delegate_partial_salvage.py tests/tools/test_delegate.py tests/run_agent/test_openai_client_lifecycle.py tests/run_agent/test_streaming.py tests/run_agent/test_stream_interrupt_retry.py tests/run_agent/test_stream_drop_logging.py tests/run_agent/test_partial_stream_finish_reason.py tests/run_agent/test_provider_fallback.py
```

Result: `408 passed, 1 warning in 45.85s` before the final URL-userinfo redaction hardening. After the final hardening, the affected delegation evidence suites were rerun:

```bash
/home/xli24/.hermes/hermes-agent/venv/bin/python -m pytest tests/tools/test_delegate_partial_salvage.py tests/tools/test_delegate.py
```

Result: `142 passed, 1 warning in 16.21s`.

Syntax and whitespace checks:

```bash
/home/xli24/.hermes/hermes-agent/venv/bin/python -m compileall -q agent/chat_completion_helpers.py agent/conversation_loop.py agent/error_classifier.py agent/provider_failure_policy.py run_agent.py tools/delegate_tool.py tools/voice_mode.py tests/agent/test_error_classifier.py tests/agent/test_non_stream_stale_timeout.py tests/agent/test_provider_stale_recovery.py tests/agent/test_vision_routing_31179.py tests/run_agent/test_openai_client_lifecycle.py tests/tools/test_delegate_partial_salvage.py tests/tools/test_file_staleness.py tests/tools/test_terminal_tool.py tests/tools/test_browser_hardening.py tests/tools/test_browser_homebrew_paths.py
git diff --check
```

Result: both commands exited `0`. After the final evidence-tail hardening, `compileall -q tools/delegate_tool.py tests/tools/test_delegate_partial_salvage.py` and `git diff --check` also exited `0`.

Broad regression was attempted as one command:

```bash
/home/xli24/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent tests/tools tests/run_agent
```

That monolithic run collected `11190` tests and hit the 600-second foreground command timeout before completion. The same coverage was therefore verified in split runs:

```bash
/home/xli24/.hermes/hermes-agent/venv/bin/python -m pytest tests/run_agent
/home/xli24/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent
/home/xli24/.hermes/hermes-agent/venv/bin/python -m pytest tests/tools
```

Results:

- `tests/run_agent`: `1526 passed, 3 skipped, 1 warning in 354.08s`.
- `tests/agent`: `3701 passed, 1 warning in 371.56s`.
- `tests/tools`: `5909 passed, 51 skipped, 3 warnings in 774.54s`.

The first full `tests/tools` run exposed two browser discovery tests that attempted a real lazy `agent-browser` install when simulating “not found”. Those tests were hardened by mocking `hermes_cli.dep_ensure.ensure_dependency` to return `False`; exact failing nodes then passed, and the full tools suite passed as recorded above.

## Independent Review

The second independent read-only review initially returned `passed: false` with one blocking security finding: URL userinfo redaction masked only passwords and could leave usernames visible in partial evidence. That was fixed by replacing all URL userinfo with `***@host`.

A focused re-review then returned `passed: false` with one blocking truncation finding: evidence was truncated before redaction, so URL userinfo could leak when `@` appeared beyond the evidence bound. That was fixed by redacting the complete content first and then truncating the sanitized preview. Tests now cover long userinfo URLs and strict aggregate bounds after query redaction.

Final focused read-only review verdict: `passed: true`.

Final reviewer non-blocking note: a query redaction marker may itself be truncated, for example `access_token=%2A`, if the preview bound cuts through the encoded `***`; the sensitive value is removed before truncation and the aggregate bound remains enforced.

## Operator Instructions

The fix is in Hermes core and applies to every profile after that process next starts normally. Existing running processes retain already-imported Python code and are intentionally not restarted by this change.

For critical delegated work, configure a fallback route with `hermes fallback add`. Optional provider/model stale timeout overrides remain available under `providers.<provider>.models.<model>.stale_timeout_seconds`.

## Known Caveats

- A provider/model fallback cannot be used if no fallback route is configured; Hermes then performs one expanded same-route attempt before returning partial evidence.
- Running Hermes processes require a normal future restart to load changed core code.
- Integration into the dirty shared checkout must not overwrite unrelated work; final integration decision is recorded after verification and review.
- The monolithic broad regression command exceeds the foreground tool timeout in this environment; equivalent `tests/agent`, `tests/tools`, and `tests/run_agent` split runs passed.
