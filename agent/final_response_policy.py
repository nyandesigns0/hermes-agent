from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")
_EXPLICIT_FULL_REQUEST_RE = re.compile(
    r"\b(full|complete|entire)\b.*\b(report|artifact|reference|details?|thing|answer|draft|write-?up)\b"
    r"|\bone[- ]shot\b"
    r"|\bin one pass\b"
    r"|\bfull thing\b",
    re.IGNORECASE,
)
_STRUCTURED_PREFIXES = (
    "diff --git ",
    "*** Begin Patch",
    "{",
    "[",
    "<svg",
    "<?xml",
)
_DEFAULT_PRESET_NAME = "aas_default"


@dataclass(frozen=True)
class FinalResponsePolicyResult:
    response_text: str
    changed: bool
    reason: str
    policy: Optional[str] = None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve_final_response_policy_config(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    session_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        from hermes_cli.config import DEFAULT_CONFIG, load_config

        source_cfg = load_config() if cfg is None else cfg
    except Exception:
        DEFAULT_CONFIG = {"agent": {"final_response_policy": {}}}
        source_cfg = cfg or {}

    if not isinstance(source_cfg, dict):
        source_cfg = {}

    agent_cfg = source_cfg.get("agent") if isinstance(source_cfg.get("agent"), dict) else {}
    default_agent_cfg = (DEFAULT_CONFIG.get("agent") or {}) if isinstance(DEFAULT_CONFIG, dict) else {}
    default_policy = copy.deepcopy(default_agent_cfg.get("final_response_policy") or {})
    default_presets = copy.deepcopy(default_agent_cfg.get("final_response_policy_presets") or {})
    config_presets = agent_cfg.get("final_response_policy_presets") if isinstance(agent_cfg, dict) else {}
    if not isinstance(config_presets, dict):
        config_presets = {}

    profile_policy = agent_cfg.get("final_response_policy") if isinstance(agent_cfg, dict) else None
    if not isinstance(profile_policy, dict):
        profile_policy = {}

    session_policy = session_override if isinstance(session_override, dict) else {}
    if not isinstance(session_policy, dict):
        session_policy = {}

    preset_name = None
    if isinstance(session_policy.get("preset"), str):
        preset_name = session_policy["preset"]
    elif isinstance(profile_policy.get("preset"), str):
        preset_name = profile_policy["preset"]
    elif isinstance(default_policy.get("preset"), str):
        preset_name = default_policy["preset"]

    preset_cfg: Dict[str, Any] = {}
    if preset_name:
        preset_cfg = {}
        if isinstance(default_presets.get(preset_name), dict):
            preset_cfg = _deep_merge(preset_cfg, default_presets[preset_name])
        if isinstance(config_presets.get(preset_name), dict):
            preset_cfg = _deep_merge(preset_cfg, config_presets[preset_name])

    resolved = copy.deepcopy(preset_cfg) if preset_cfg else copy.deepcopy(default_policy)
    if not resolved:
        resolved = {}

    if profile_policy:
        resolved = _deep_merge(resolved, {k: v for k, v in profile_policy.items() if k != "preset"})
    if session_policy:
        resolved = _deep_merge(resolved, {k: v for k, v in session_policy.items() if k != "preset"})

    if preset_name:
        resolved["preset"] = preset_name
    elif isinstance(default_policy.get("preset"), str):
        resolved["preset"] = default_policy["preset"]
    elif preset_cfg:
        resolved["preset"] = _DEFAULT_PRESET_NAME if "_DEFAULT_PRESET_NAME" in globals() else "aas_default"

    return resolved


def _load_policy_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
    except Exception:
        cfg = {}
    return resolve_final_response_policy_config(cfg)


def _normalize_user_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(p for p in parts if p)
    if value is None:
        return ""
    return str(value)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _looks_like_explicit_full_request(user_text: str) -> bool:
    return bool(_EXPLICIT_FULL_REQUEST_RE.search(user_text or ""))


def _looks_structured_or_code_output(text: str) -> bool:
    stripped = (text or "").lstrip()
    if "```" in stripped:
        return True
    if stripped.startswith(_STRUCTURED_PREFIXES):
        return True
    if stripped.startswith("LINE_NUM|"):
        return True
    if "\nLINE_NUM|" in stripped:
        return True
    return False


def _opening_is_too_long(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    first_block = stripped.split("\n\n", 1)[0].strip()
    first_sentences = _SENTENCE_SPLIT_RE.split(first_block)
    opening = " ".join(first_sentences[:5]).strip()
    return _word_count(opening) > 120


def _trim_to_word_budget(text: str, max_words: int) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return stripped

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(stripped) if s.strip()]
    kept: list[str] = []
    used = 0
    for sentence in sentences:
        count = _word_count(sentence)
        if kept and used + count > max_words:
            break
        if not kept and count > max_words:
            words = _WORD_RE.findall(sentence)
            return " ".join(words[:max_words]).rstrip() + " …"
        kept.append(sentence)
        used += count
        if used >= max_words:
            break

    if kept:
        trimmed = " ".join(kept).strip()
        if trimmed != stripped and not trimmed.endswith((".", "!", "?", "。", "！", "？")):
            trimmed += " …"
        return trimmed

    words = _WORD_RE.findall(stripped)
    if len(words) <= max_words:
        return stripped
    return " ".join(words[:max_words]).rstrip() + " …"


def sync_final_response_into_messages(
    *,
    messages: list[dict],
    old_response_text: str,
    new_response_text: str,
) -> bool:
    """Synchronize the final assistant message content with transformed output.

    Walk backwards to the latest assistant message in the current tail of the
    transcript and replace its content when it matches the original response
    text or is the last assistant text slot. Keeps ``result['messages']`` and
    persisted session history aligned with ``result['final_response']``.
    """
    if not messages or old_response_text == new_response_text:
        return False

    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user":
            break
        if role != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if content == old_response_text or content:
                msg["content"] = new_response_text
                return True
        elif content is None:
            msg["content"] = new_response_text
            return True
    return False


def apply_final_response_policy(
    *,
    response_text: str,
    original_user_message: Any,
    config: Optional[Dict[str, Any]] = None,
) -> FinalResponsePolicyResult:
    text = response_text or ""
    policy_cfg = config if isinstance(config, dict) else _load_policy_config()
    if not policy_cfg.get("enabled"):
        return FinalResponsePolicyResult(text, False, "disabled")

    policies = policy_cfg.get("policies") or []
    if isinstance(policies, str):
        policies = [policies]
    if "progressive_disclosure" not in policies:
        return FinalResponsePolicyResult(text, False, "no_matching_policy")

    progressive_cfg = policy_cfg.get("progressive_disclosure") or {}
    if not isinstance(progressive_cfg, dict):
        progressive_cfg = {}
    max_words = int(progressive_cfg.get("max_words", 100) or 100)
    allow_explicit = progressive_cfg.get("allow_explicit_full_answer", True)
    mode = str(policy_cfg.get("mode", "validate_and_trim") or "validate_and_trim").strip().lower()
    user_text = _normalize_user_text(original_user_message)

    if not text.strip():
        return FinalResponsePolicyResult(text, False, "empty", "progressive_disclosure")
    if allow_explicit and _looks_like_explicit_full_request(user_text):
        return FinalResponsePolicyResult(text, False, "explicit_full_answer_allowed", "progressive_disclosure")
    if _looks_structured_or_code_output(text):
        return FinalResponsePolicyResult(text, False, "structured_or_code_output", "progressive_disclosure")

    too_long = _word_count(text) > max_words
    opening_too_long = _opening_is_too_long(text)
    if not too_long and not opening_too_long:
        return FinalResponsePolicyResult(text, False, "ok", "progressive_disclosure")

    if mode == "warn_only":
        logger.warning(
            "Final-response policy violation: progressive_disclosure would trim response "
            "(words=%d max=%d)",
            _word_count(text),
            max_words,
        )
        return FinalResponsePolicyResult(text, False, "warn_only_violation", "progressive_disclosure")

    trimmed = _trim_to_word_budget(text, max_words=max_words)
    if trimmed and trimmed != text:
        return FinalResponsePolicyResult(trimmed, True, "trimmed", "progressive_disclosure")

    logger.warning(
        "Final-response policy could not trim response cleanly; returning best effort unchanged"
    )
    return FinalResponsePolicyResult(text, False, "trim_failed", "progressive_disclosure")
