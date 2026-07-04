from pathlib import Path

import pytest
import yaml

from types import SimpleNamespace

from hermes_cli.profile_policy import (
    ProfilePolicyStatus,
    apply_profile_policy_preset,
    audit_profile_policies,
)


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    default_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    return default_home


@pytest.fixture()
def named_profile_env(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root_home = tmp_path / ".hermes"
    root_home.mkdir(exist_ok=True)
    (root_home / "profiles").mkdir(exist_ok=True)
    named_home = root_home / "profiles" / "esther"
    named_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(named_home))
    return root_home, named_home


def _write_config(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _status_map(statuses):
    return {status.name: status for status in statuses}


def test_audit_profile_policies_classifies_shared_default_and_overrides(profile_env):
    default_home = profile_env

    _write_config(
        default_home / "config.yaml",
        "model:\n  default: gpt-5.5\n",
    )

    _write_config(
        default_home / "profiles" / "preset" / "config.yaml",
        "agent:\n  final_response_policy:\n    enabled: true\n    preset: aas_default\n",
    )

    _write_config(
        default_home / "profiles" / "legacy" / "config.yaml",
        "agent:\n  final_response_policy:\n    enabled: true\n    mode: validate_and_trim\n    policies:\n    - progressive_disclosure\n    progressive_disclosure:\n      max_words: 300\n      allow_explicit_full_answer: true\n",
    )

    _write_config(
        default_home / "profiles" / "override" / "config.yaml",
        "agent:\n  final_response_policy:\n    enabled: true\n    preset: aas_default\n    progressive_disclosure:\n      max_words: 250\n",
    )

    _write_config(
        default_home / "profiles" / "broken" / "config.yaml",
        "agent: [not valid yaml",
    )

    statuses = _status_map(audit_profile_policies())

    assert statuses["default"].action == "needs_apply"
    assert statuses["default"].has_config is True
    assert statuses["preset"].action == "ok"
    assert statuses["preset"].uses_preset == "aas_default"
    assert statuses["legacy"].action == "needs_apply"
    assert statuses["legacy"].max_words == 300
    assert statuses["override"].action == "explicit_override"
    assert statuses["override"].max_words == 250
    assert statuses["broken"].action == "invalid_config"


def test_audit_profile_policies_from_named_profile_still_scans_real_root(named_profile_env):
    root_home, named_home = named_profile_env

    _write_config(root_home / "config.yaml", "model:\n  default: gpt-5.5\n")
    _write_config(root_home / "profiles" / "esther" / "config.yaml", "agent:\n  final_response_policy:\n    enabled: true\n    preset: aas_default\n")
    _write_config(root_home / "profiles" / "solomon" / "config.yaml", "agent:\n  final_response_policy:\n    enabled: true\n    preset: aas_default\n")
    _write_config(root_home / "profiles" / "ezra" / "config.yaml", "agent:\n  final_response_policy:\n    enabled: true\n    preset: aas_default\n")
    _write_config(root_home / "profiles" / "habakkuk" / "config.yaml", "agent:\n  final_response_policy:\n    enabled: true\n    preset: aas_default\n")
    _write_config(root_home / "profiles" / "shared" / "config.yaml", "agent:\n  final_response_policy:\n    enabled: true\n    preset: aas_default\n")

    statuses = _status_map(audit_profile_policies())

    assert set(statuses) >= {"default", "esther", "ezra", "habakkuk", "shared", "solomon"}
    assert statuses["default"].path == root_home
    assert statuses["esther"].path == root_home / "profiles" / "esther"
    assert statuses["solomon"].path == root_home / "profiles" / "solomon"


def test_apply_profile_policy_preset_seeds_missing_config(profile_env):
    profile_dir = profile_env / "profiles" / "coder"
    profile_dir.mkdir(parents=True)

    status = apply_profile_policy_preset(profile_dir)
    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))

    assert status.action == "ok"
    assert status.notes == "applied preset aas_default"
    assert cfg["agent"]["final_response_policy"] == {
        "enabled": True,
        "preset": "aas_default",
    }


def test_apply_profile_policy_preset_preserves_explicit_override_without_force(profile_env):
    profile_dir = profile_env / "profiles" / "writer"
    profile_dir.mkdir(parents=True)
    _write_config(
        profile_dir / "config.yaml",
        "agent:\n  final_response_policy:\n    enabled: true\n    preset: aas_default\n    progressive_disclosure:\n      max_words: 250\n",
    )

    before = (profile_dir / "config.yaml").read_text(encoding="utf-8")
    status = apply_profile_policy_preset(profile_dir)
    after = (profile_dir / "config.yaml").read_text(encoding="utf-8")

    assert status.action == "explicit_override"
    assert status.notes == "preset=aas_default, max_words=250"
    assert before == after


def test_apply_profile_policy_preset_force_rewrites_override(profile_env):
    profile_dir = profile_env / "profiles" / "editor"
    profile_dir.mkdir(parents=True)
    _write_config(
        profile_dir / "config.yaml",
        "agent:\n  final_response_policy:\n    enabled: true\n    mode: validate_and_trim\n    policies:\n    - progressive_disclosure\n    progressive_disclosure:\n      max_words: 250\n      allow_explicit_full_answer: true\n",
    )

    status = apply_profile_policy_preset(profile_dir, force=True)
    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))

    assert status.action == "ok"
    assert status.notes == "applied preset aas_default"
    assert cfg["agent"]["final_response_policy"] == {
        "enabled": True,
        "preset": "aas_default",
    }


def test_profile_policy_apply_uses_audited_paths_from_named_profile(monkeypatch, named_profile_env):
    root_home, named_home = named_profile_env
    monkeypatch.setenv("HERMES_HOME", str(named_home))

    statuses = [
        ProfilePolicyStatus(
            name="default",
            path=root_home,
            has_config=True,
            has_final_response_policy=True,
            uses_preset="aas_default",
            max_words=None,
            action="ok",
            notes="using shared aas_default preset",
        ),
        ProfilePolicyStatus(
            name="esther",
            path=root_home / "profiles" / "esther",
            has_config=True,
            has_final_response_policy=True,
            uses_preset="aas_default",
            max_words=None,
            action="ok",
            notes="using shared aas_default preset",
        ),
    ]

    seen_paths = []

    def fake_audit_profile_policies():
        return statuses

    def fake_apply_profile_policy_preset(profile_dir, *, preset="aas_default", force=False):
        seen_paths.append(profile_dir)
        return ProfilePolicyStatus(
            name=profile_dir.name if profile_dir.name else "default",
            path=profile_dir,
            has_config=True,
            has_final_response_policy=True,
            uses_preset=preset,
            max_words=None,
            action="ok",
            notes=f"applied preset {preset}",
        )

    monkeypatch.setattr("hermes_cli.profile_policy.audit_profile_policies", fake_audit_profile_policies)
    monkeypatch.setattr("hermes_cli.profile_policy.apply_profile_policy_preset", fake_apply_profile_policy_preset)

    from hermes_cli.main import cmd_profile

    cmd_profile(
        SimpleNamespace(
            profile_action="policy",
            profile_policy_action="apply",
            preset="aas_default",
            force=False,
        )
    )

    assert seen_paths == [root_home, root_home / "profiles" / "esther"]
