"""Delegation must preserve useful evidence when final synthesis fails."""

from __future__ import annotations

from unittest.mock import MagicMock


def _failed_child_result():
    return {
        "final_response": "",
        "completed": False,
        "failed": True,
        "error": "provider_stale: final synthesis timed out",
        "failure_class": "provider_stale",
        "api_calls": 3,
        "messages": [
            {"role": "user", "content": "audit two files"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"a.md"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "important chronology evidence",
            },
        ],
    }


def test_failed_child_with_successful_tool_evidence_returns_partial(monkeypatch):
    from tools import delegate_tool

    monkeypatch.setattr(delegate_tool, "_get_child_timeout", lambda: 10.0)
    child = MagicMock()
    child.run_conversation.return_value = _failed_child_result()
    child.get_activity_summary.return_value = {"api_call_count": 3}
    child.model = "gpt-test"
    child.tool_progress_callback = None
    child._credential_pool = None
    child._delegate_role = "leaf"
    child._subagent_id = "sa-0-test"
    child._delegate_depth = 1
    child.session_prompt_tokens = 100
    child.session_completion_tokens = 0
    child.session_estimated_cost_usd = 0.0
    child.session_reasoning_tokens = 0

    parent = MagicMock()
    parent._current_task_id = None

    result = delegate_tool._run_single_child(
        task_index=0,
        goal="audit",
        child=child,
        parent_agent=parent,
    )

    assert result["status"] == "partial"
    assert result["exit_reason"] == "provider_stale"
    assert result["failure_class"] == "provider_stale"
    assert result["partial"] is True
    assert result["completed"] is False
    assert result["evidence"]["successful_tool_results"] == 1
    assert result["evidence"]["result_bytes"] >= len("important chronology evidence")
    assert result["evidence_tail"][0]["preview"] == "important chronology evidence"
    assert result["tool_trace"][0]["status"] == "ok"
    assert "provider_stale" in result["error"]


def test_delegate_description_recommends_map_reduce_for_broad_audits():
    from tools.delegate_tool import _build_top_level_description

    description = _build_top_level_description().lower()

    assert "map/reduce" in description
    assert "multi-source" in description
    assert "complete audit" in description


def test_partial_evidence_tail_is_force_redacted_and_bounded():
    from tools.delegate_tool import _extract_output_tail

    secret = "sk-supersecretvalue123456789"
    result = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c", "function": {"name": "terminal", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "c", "content": f"token={secret}"},
        ]
    }

    raw_content = f"token={secret}"

    tail = _extract_output_tail(result, max_entries=8, max_chars=40)
    preview = tail[0]["preview"]

    assert secret not in preview
    assert preview != raw_content
    assert "..." in preview
    assert sum(len(item["preview"]) for item in tail) <= 40


def test_partial_evidence_tail_redacts_url_query_and_authorization_headers():
    from tools.delegate_tool import _extract_output_tail

    secret_url = "https://example.test/cb?access_token=abc123secret&state=ok&client_secret=hide-me"
    auth_header = "Authorization: Basic dXNlcjpzdXBlcnNlY3JldA=="
    curl_header = "> Authorization: Basic cHJlZml4ZWQtc2VjcmV0"
    inline_header = "curl -H \"Authorization: Basic aW5saW5lLXNlY3JldA==\" https://api.example.test"
    json_header = '{"Authorization":"Basic anNvbi1zZWNyZXQ="}'
    userinfo_url = "https://visible-user:visible-password@example.test/private"
    result = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c", "function": {"name": "terminal", "arguments": "{}"}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c",
                "content": (
                    f"callback={secret_url}\n{auth_header}\n{curl_header}\n"
                    f"{inline_header}\n{json_header}\n{userinfo_url}\n"
                ),
            },
        ]
    }

    preview = _extract_output_tail(result, max_entries=8, max_chars=400)[0]["preview"]

    assert "abc123secret" not in preview
    assert "hide-me" not in preview
    assert "access_token=%2A%2A%2A" in preview or "access_token=***" in preview
    assert "client_secret=%2A%2A%2A" in preview or "client_secret=***" in preview
    assert "dXNlcjpzdXBlcnNlY3JldA==" not in preview
    assert "cHJlZml4ZWQtc2VjcmV0" not in preview
    assert "aW5saW5lLXNlY3JldA==" not in preview
    assert "anNvbi1zZWNyZXQ=" not in preview
    assert "visible-user" not in preview
    assert "visible-password" not in preview
    assert "https://***@example.test/private" in preview
    assert "Authorization: ***" in preview
    assert '"Authorization":"***"' in preview


def test_partial_evidence_tail_redacts_before_truncating_url_userinfo():
    from tools.delegate_tool import _extract_output_tail

    long_userinfo_url = (
        "https://visible-user"
        + "A" * 1400
        + ":visible-password@example.test/private"
    )
    result = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c", "function": {"name": "terminal", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "c", "content": long_userinfo_url},
        ]
    }

    preview = _extract_output_tail(result, max_entries=8, max_chars=30)[0]["preview"]

    assert len(preview) <= 30
    assert "visible-user" not in preview
    assert "visible-password" not in preview
    assert preview == "https://***@example.test/priva"


def test_partial_evidence_tail_bound_is_strict_after_query_redaction():
    from tools.delegate_tool import _extract_output_tail

    result = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c", "function": {"name": "terminal", "arguments": "{}"}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c",
                "content": "https://example.test/cb?access_token=abc123secret&state=ok",
            },
        ]
    }

    tail = _extract_output_tail(result, max_entries=8, max_chars=40)

    assert sum(len(item["preview"]) for item in tail) <= 40
    assert "abc123secret" not in tail[0]["preview"]


def test_structured_success_false_is_not_salvaged_as_successful_evidence():
    from tools.delegate_tool import _looks_like_error_output

    assert _looks_like_error_output('{"success": false, "message": "denied"}') is True
    assert _looks_like_error_output({"ok": False, "message": "denied"}) is True
