from agent.final_response_policy import (
    apply_final_response_policy,
    resolve_final_response_policy_config,
    sync_final_response_into_messages,
)


def test_policy_disabled_keeps_response_unchanged():
    text = "This is a long enough answer that should remain unchanged when the policy is disabled."

    result = apply_final_response_policy(
        response_text=text,
        original_user_message="Explain this gradually.",
        config={"enabled": False},
    )

    assert result.changed is False
    assert result.response_text == text
    assert result.reason == "disabled"


LONG_ORDINARY_RESPONSE = " ".join(
    [
        "This is a long ordinary answer with no code fences or structured payloads."
        " It keeps explaining details sentence after sentence so that it grows well past"
        " the progressive disclosure limit.",
    ]
    * 18
)


def test_resolve_final_response_policy_config_prefers_profile_override_over_shared_preset():
    resolved = resolve_final_response_policy_config(
        {
            "agent": {
                "final_response_policy": {
                    "preset": "aas_default",
                    "progressive_disclosure": {"max_words": 250},
                }
            }
        }
    )

    assert resolved["preset"] == "aas_default"
    assert resolved["progressive_disclosure"]["max_words"] == 250


def test_resolve_final_response_policy_config_merges_custom_preset_from_loaded_config():
    resolved = resolve_final_response_policy_config(
        {
            "agent": {
                "final_response_policy": {"preset": "custom_short"},
                "final_response_policy_presets": {
                    "custom_short": {
                        "enabled": True,
                        "mode": "validate_and_trim",
                        "policies": ["progressive_disclosure"],
                        "progressive_disclosure": {"max_words": 42},
                    }
                },
            }
        }
    )

    assert resolved["preset"] == "custom_short"
    assert resolved["enabled"] is True
    assert resolved["mode"] == "validate_and_trim"
    assert resolved["policies"] == ["progressive_disclosure"]
    assert resolved["progressive_disclosure"]["max_words"] == 42


def test_policy_enabled_trims_long_ordinary_response():
    result = apply_final_response_policy(
        response_text=LONG_ORDINARY_RESPONSE,
        original_user_message="Give me the answer.",
        config={
            "enabled": True,
            "mode": "validate_and_trim",
            "policies": ["progressive_disclosure"],
            "progressive_disclosure": {"max_words": 100},
        },
    )

    assert result.changed is True
    assert len(result.response_text.split()) <= 100
    assert result.reason == "trimmed"


def test_policy_trim_syncs_latest_assistant_message():
    messages = [
        {"role": "user", "content": "Give me the answer."},
        {"role": "assistant", "content": LONG_ORDINARY_RESPONSE},
    ]

    result = apply_final_response_policy(
        response_text=LONG_ORDINARY_RESPONSE,
        original_user_message="Give me the answer.",
        config={
            "enabled": True,
            "mode": "validate_and_trim",
            "policies": ["progressive_disclosure"],
            "progressive_disclosure": {"max_words": 100},
        },
    )
    changed = sync_final_response_into_messages(
        messages=messages,
        old_response_text=LONG_ORDINARY_RESPONSE,
        new_response_text=result.response_text,
    )

    assert result.changed is True
    assert changed is True
    assert result.response_text == messages[-1]["content"]
    assert len(messages[-1]["content"].split()) <= 100


def test_explicit_full_report_request_is_allowed():
    text = LONG_ORDINARY_RESPONSE

    result = apply_final_response_policy(
        response_text=text,
        original_user_message="Give me the full report in one pass.",
        config={
            "enabled": True,
            "mode": "validate_and_trim",
            "policies": ["progressive_disclosure"],
            "progressive_disclosure": {"max_words": 60, "allow_explicit_full_answer": True},
        },
    )

    assert result.changed is False
    assert result.response_text == text
    assert result.reason == "explicit_full_answer_allowed"


def test_default_final_response_policy_points_to_shared_preset_and_resolves_to_100_words():
    from hermes_cli.config import DEFAULT_CONFIG

    policy = DEFAULT_CONFIG["agent"]["final_response_policy"]
    presets = DEFAULT_CONFIG["agent"]["final_response_policy_presets"]

    assert policy["enabled"] is True
    assert policy["preset"] == "aas_default"
    assert presets["aas_default"]["enabled"] is True
    assert presets["aas_default"]["mode"] == "validate_and_trim"
    assert presets["aas_default"]["policies"] == ["progressive_disclosure"]
    assert presets["aas_default"]["progressive_disclosure"]["max_words"] == 100
    assert presets["aas_default"]["progressive_disclosure"]["allow_explicit_full_answer"] is True

    resolved = resolve_final_response_policy_config({"agent": {"final_response_policy": {"preset": "aas_default"}}})
    assert resolved["preset"] == "aas_default"
    assert resolved["progressive_disclosure"]["max_words"] == 100


def test_structured_code_output_is_allowed():
    text = "```python\nprint('hello')\n```\n\n" + LONG_ORDINARY_RESPONSE

    result = apply_final_response_policy(
        response_text=text,
        original_user_message="Show me the code.",
        config={
            "enabled": True,
            "mode": "validate_and_trim",
            "policies": ["progressive_disclosure"],
            "progressive_disclosure": {"max_words": 60},
        },
    )

    assert result.changed is False
    assert result.response_text == text
    assert result.reason == "structured_or_code_output"


def test_trim_mode_does_not_expand_short_response():
    text = "Short direct answer. Brief orientation. Offer next step."

    result = apply_final_response_policy(
        response_text=text,
        original_user_message="Help me.",
        config={
            "enabled": True,
            "mode": "validate_and_trim",
            "policies": ["progressive_disclosure"],
            "progressive_disclosure": {"max_words": 60},
        },
    )

    assert result.changed is False
    assert result.response_text == text
    assert result.reason == "ok"
