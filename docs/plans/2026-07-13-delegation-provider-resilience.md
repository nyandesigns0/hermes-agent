# Delegation Provider Resilience Implementation Plan

> **For Hermes:** Implement with strict TDD and preserve existing provider, prompt-caching, profile-isolation, and fallback behavior.

**Goal:** Make delegated tasks recover safely from silent provider calls without identical retry loops or loss of completed tool evidence, across non-streaming and streaming provider paths.

**Architecture:** Add a typed provider-stale exception and small policy helpers under `agent/`. The existing conversation retry loop will immediately route typed stale failures to an available fallback, or perform at most one same-route retry with a larger stale allowance when no fallback exists. Delegation will expose structured partial evidence when a child still fails after runtime recovery. Adaptive timeout calculation remains profile-configurable and explicit provider settings remain authoritative.

**Tech Stack:** Python 3.11, pytest, existing Hermes OpenAI/Codex transports and delegation runtime.

---

### Task 1: Reproduce stale transport misclassification

**Files:**
- Create: `tests/agent/test_provider_stale_recovery.py`
- Modify: `tests/agent/test_non_stream_stale_timeout.py`

1. Test that abort-induced `BrokenPipeError` is surfaced as a typed provider-stale failure.
2. Test typed failure classification requests fallback rather than ordinary retry.
3. Test moderate synthesis payloads receive adaptive silence allowance.
4. Run focused tests and verify RED.

### Task 2: Implement provider stale policy

**Files:**
- Create: `agent/provider_failure_policy.py`
- Modify: `agent/chat_completion_helpers.py`
- Modify: `agent/error_classifier.py`
- Modify: `run_agent.py`

1. Add `ProviderStaleError` with timeout, provider, model, context, phase, and cause metadata.
2. Replace abort-side transport errors with the typed causal error.
3. Add adaptive timeout calculation for non-streaming synthesis and reasoning effort while preserving explicit overrides.
4. Classify provider stale separately with eager fallback guidance.
5. Run focused tests and verify GREEN.

### Task 3: Prevent identical stale retries

**Files:**
- Modify: `agent/conversation_loop.py`
- Test: `tests/agent/test_provider_stale_recovery.py`

1. Eagerly activate configured fallback on typed stale failure.
2. Without fallback, allow only one same-route stale retry and apply a per-agent timeout multiplier.
3. On a second stale failure, terminate with structured failure rather than consuming the generic retry budget.
4. Reset stale recovery state after a successful model response or route switch.

### Task 4: Preserve delegated evidence

**Files:**
- Modify: `tools/delegate_tool.py`
- Create: `tests/tools/test_delegate_partial_salvage.py`

1. Detect a failed child that still has successful tool results.
2. Return `status=partial`, `failure_class`, tool trace, evidence byte count, and bounded evidence tail.
3. Preserve the original error and do not claim task completion.
4. Add parent-facing decomposition guidance for broad multi-source audits.

### Task 5: Configuration and documentation

**Files:**
- Modify: `website/docs/user-guide/features/delegation.md`
- Create: `website/docs/developer-guide/implementation-logs/2026-07-13-delegation-provider-resilience.md`

Document adaptive timeout behavior, explicit provider override precedence, fallback recommendation, partial results, restart requirements, and operator diagnostics.

### Task 6: Verification and review

Run:

```bash
PYTHONPATH=. /home/xli24/.hermes/hermes-agent/venv/bin/python -m pytest \
  tests/agent/test_provider_stale_recovery.py \
  tests/agent/test_non_stream_stale_timeout.py \
  tests/agent/test_error_classifier.py \
  tests/tools/test_delegate_partial_salvage.py \
  tests/tools/test_delegate.py \
  tests/run_agent/test_provider_fallback.py \
  -q -o 'addopts='
```

Then run broader agent/delegation regression tests, `git diff --check`, syntax compilation, and an independent code review. Commit only the isolated feature branch. Do not restart running Hermes processes or modify profile configs.