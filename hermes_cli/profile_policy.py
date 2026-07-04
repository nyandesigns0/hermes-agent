from __future__ import annotations

from dataclasses import dataclass
import copy
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml

from hermes_cli.config import DEFAULT_CONFIG
from hermes_cli.profiles import _get_default_hermes_home

_DEFAULT_PRESET = "aas_default"
_LEGACY_PROGRESSIVE_DISLOSURE_MAX_WORDS = 300


@dataclass(frozen=True)
class ProfilePolicyStatus:
    name: str
    path: Path
    has_config: bool
    has_final_response_policy: bool
    uses_preset: Optional[str]
    max_words: Optional[int]
    action: str
    notes: str = ""


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _shared_preset_template(preset: str = _DEFAULT_PRESET) -> Dict[str, Any]:
    agent_defaults = (DEFAULT_CONFIG.get("agent") or {})
    presets = agent_defaults.get("final_response_policy_presets") or {}
    preset_cfg = presets.get(preset)
    return copy.deepcopy(preset_cfg) if isinstance(preset_cfg, dict) else {}


def _legacy_preset_template() -> Dict[str, Any]:
    return {
        "enabled": True,
        "mode": "validate_and_trim",
        "policies": ["progressive_disclosure"],
        "progressive_disclosure": {
            "max_words": _LEGACY_PROGRESSIVE_DISLOSURE_MAX_WORDS,
            "allow_explicit_full_answer": True,
        },
    }


def _profile_config_path(profile_dir: Path) -> Path:
    return profile_dir / "config.yaml"


def _read_yaml_dict(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_yaml_dict(path: Path, data: Dict[str, Any]) -> None:
    from utils import atomic_yaml_write

    atomic_yaml_write(path, data, sort_keys=False)


def _agent_policy(raw_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    agent_cfg = raw_config.get("agent") if isinstance(raw_config, dict) else None
    if not isinstance(agent_cfg, dict):
        return None
    policy = agent_cfg.get("final_response_policy")
    return policy if isinstance(policy, dict) else None


def _effective_policy(policy: Dict[str, Any], *, preset: str = _DEFAULT_PRESET) -> Dict[str, Any]:
    template = _shared_preset_template(preset)
    if not template:
        return {k: copy.deepcopy(v) for k, v in policy.items() if k != "preset"}

    effective = copy.deepcopy(template)
    for key, value in policy.items():
        if key == "preset":
            continue
        if isinstance(value, dict) and isinstance(effective.get(key), dict):
            effective[key] = _deep_merge(effective[key], value)
        else:
            effective[key] = copy.deepcopy(value)
    return effective


def _is_shared_default_policy(policy: Dict[str, Any], *, preset: str = _DEFAULT_PRESET) -> bool:
    template = _shared_preset_template(preset)
    if not template:
        return False
    if policy.get("preset") not in {None, preset}:
        return False
    return _effective_policy(policy, preset=preset) == template


def _is_legacy_default_policy(policy: Dict[str, Any]) -> bool:
    if policy.get("preset") not in {None, _DEFAULT_PRESET}:
        return False
    return _effective_policy(policy) == _legacy_preset_template()


def _policy_words(policy: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(policy, dict):
        return None
    progressive = policy.get("progressive_disclosure")
    if not isinstance(progressive, dict):
        return None
    words = progressive.get("max_words")
    return words if isinstance(words, int) else None


def _classify_profile_config(
    *,
    name: str,
    profile_dir: Path,
    raw_config: Optional[Dict[str, Any]],
) -> ProfilePolicyStatus:
    has_config = raw_config is not None
    policy = _agent_policy(raw_config or {})
    has_policy = isinstance(policy, dict) and bool(policy)
    uses_preset = policy.get("preset") if isinstance(policy, dict) and isinstance(policy.get("preset"), str) else None
    max_words = _policy_words(policy)

    if not has_config:
        return ProfilePolicyStatus(
            name=name,
            path=profile_dir,
            has_config=False,
            has_final_response_policy=False,
            uses_preset=None,
            max_words=None,
            action="needs_apply",
            notes="no config.yaml",
        )

    if not has_policy:
        return ProfilePolicyStatus(
            name=name,
            path=profile_dir,
            has_config=True,
            has_final_response_policy=False,
            uses_preset=None,
            max_words=None,
            action="needs_apply",
            notes="missing final_response_policy",
        )

    if _is_shared_default_policy(policy):
        notes = "using shared aas_default preset" if uses_preset == _DEFAULT_PRESET else "matches shared default policy"
        return ProfilePolicyStatus(
            name=name,
            path=profile_dir,
            has_config=True,
            has_final_response_policy=True,
            uses_preset=uses_preset,
            max_words=max_words,
            action="ok",
            notes=notes,
        )

    if _is_legacy_default_policy(policy):
        return ProfilePolicyStatus(
            name=name,
            path=profile_dir,
            has_config=True,
            has_final_response_policy=True,
            uses_preset=uses_preset,
            max_words=max_words,
            action="needs_apply",
            notes="legacy 300-word default policy",
        )

    note_bits = []
    if uses_preset:
        note_bits.append(f"preset={uses_preset}")
    if max_words is not None:
        note_bits.append(f"max_words={max_words}")
    return ProfilePolicyStatus(
        name=name,
        path=profile_dir,
        has_config=True,
        has_final_response_policy=True,
        uses_preset=uses_preset,
        max_words=max_words,
        action="explicit_override",
        notes=", ".join(note_bits) if note_bits else "explicit override",
    )


def _iter_profile_targets(include_default: bool = True) -> Iterable[tuple[str, Path]]:
    root_home = Path(_get_default_hermes_home())
    if include_default:
        yield "default", root_home

    profiles_root = root_home / "profiles"
    if not profiles_root.is_dir():
        return

    for entry in sorted(profiles_root.iterdir()):
        if entry.is_dir():
            yield entry.name, entry


def audit_profile_policies(*, include_default: bool = True) -> list[ProfilePolicyStatus]:
    statuses: list[ProfilePolicyStatus] = []
    for name, profile_dir in _iter_profile_targets(include_default=include_default):
        config_path = _profile_config_path(profile_dir)
        raw_config = _read_yaml_dict(config_path)
        if raw_config is None and config_path.exists():
            statuses.append(
                ProfilePolicyStatus(
                    name=name,
                    path=profile_dir,
                    has_config=True,
                    has_final_response_policy=False,
                    uses_preset=None,
                    max_words=None,
                    action="invalid_config",
                    notes="config.yaml could not be parsed",
                )
            )
            continue
        statuses.append(_classify_profile_config(name=name, profile_dir=profile_dir, raw_config=raw_config))
    return statuses


def _apply_policy_fragment(
    raw_config: Dict[str, Any],
    *,
    preset: str,
    force: bool,
) -> tuple[Dict[str, Any], bool, str]:
    policy = _agent_policy(raw_config) or {}

    if policy and _is_shared_default_policy(policy, preset=preset):
        return raw_config, False, "already compliant"

    if policy and not force and not (_is_legacy_default_policy(policy) or not policy):
        return raw_config, False, "explicit override preserved"

    updated = copy.deepcopy(raw_config)
    agent_cfg = dict(updated.get("agent") or {})
    agent_cfg["final_response_policy"] = {
        "enabled": True,
        "preset": preset,
    }
    updated["agent"] = agent_cfg
    return updated, True, f"applied preset {preset}"


def apply_profile_policy_preset(
    profile_dir: Path,
    *,
    preset: str = _DEFAULT_PRESET,
    force: bool = False,
) -> ProfilePolicyStatus:
    config_path = _profile_config_path(profile_dir)
    raw_config = _read_yaml_dict(config_path)

    if raw_config is None and config_path.exists():
        return ProfilePolicyStatus(
            name=profile_dir.name if profile_dir.name != "" else "default",
            path=profile_dir,
            has_config=True,
            has_final_response_policy=False,
            uses_preset=None,
            max_words=None,
            action="invalid_config",
            notes="config.yaml could not be parsed",
        )

    if raw_config is None:
        raw_config = {}

    name = profile_dir.name if profile_dir.name != "" else "default"
    current = _classify_profile_config(name=name, profile_dir=profile_dir, raw_config=raw_config if raw_config else None)
    if current.action == "ok" and not force:
        return current
    if current.action == "explicit_override" and not force:
        return current

    updated, changed, notes = _apply_policy_fragment(raw_config, preset=preset, force=force)
    if changed:
        _write_yaml_dict(config_path, updated)

    refreshed = _classify_profile_config(name=name, profile_dir=profile_dir, raw_config=updated)
    return ProfilePolicyStatus(
        name=refreshed.name,
        path=refreshed.path,
        has_config=True,
        has_final_response_policy=True,
        uses_preset=refreshed.uses_preset,
        max_words=refreshed.max_words,
        action="ok" if changed or refreshed.action == "ok" else refreshed.action,
        notes=notes if changed else refreshed.notes,
    )


def seed_profile_policy(profile_dir: Path, *, preset: str = _DEFAULT_PRESET) -> bool:
    config_path = _profile_config_path(profile_dir)
    raw_config = _read_yaml_dict(config_path)
    if raw_config is None:
        raw_config = {}

    policy = _agent_policy(raw_config)
    if policy and _is_shared_default_policy(policy, preset=preset):
        return False
    if policy and not (_is_legacy_default_policy(policy) or not policy):
        return False

    updated, changed, _ = _apply_policy_fragment(raw_config, preset=preset, force=False)
    if changed:
        _write_yaml_dict(config_path, updated)
    return changed
