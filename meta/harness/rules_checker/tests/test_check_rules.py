# rules_checker의 정상 경로와 실패 경로를 픽스처로 검증하는 테스트
"""rules_checker 테스트.

가짜 저장소(tmp_path)를 만들어 정상 규칙과 각 위반 유형(필드 누락, 잘못된
enum, 깨진 YAML, 없는 배포 대상, 미배포 선언, 미검증 그릇 거부)을 검증하고,
마지막에 실제 저장소의 규칙이 전부 통과하는지 통합 확인한다.
"""

from __future__ import annotations

from pathlib import Path

from harness.rules_checker.check_rules import check_rules, find_repo_root


def make_repo(tmp_path: Path) -> Path:
    """meta/rules/ 골격을 가진 가짜 저장소를 만든다.

    Args:
        tmp_path: pytest가 제공하는 임시 디렉토리.

    Returns:
        가짜 저장소 루트 경로.
    """
    (tmp_path / "meta" / "rules").mkdir(parents=True)
    return tmp_path


def write_rule(root: Path, name: str, body: str) -> Path:
    """meta/rules/ 아래에 규칙 파일을 만든다."""
    path = root / "meta" / "rules" / name
    path.write_text(body, encoding="utf-8")
    return path


def valid_rule(rule_id: str) -> str:
    """유효한 claude-md 규칙 본문을 만든다."""
    return (
        f"---\nid: {rule_id}\nenforce: claude-md\ndeployed-to: CLAUDE.md\n---\n\nbody\n"
    )


def test_valid_rule_passes(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    assert check_rules(root) == []


def test_readme_is_excluded(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    # frontmatter 없는 README가 있어도 위반이 아니어야 한다.
    write_rule(root, "README.md", "# not a rule\n")
    assert check_rules(root) == []


def test_missing_required_field(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "no-target.md", "---\nid: no-target\nenforce: claude-md\n---\n")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "missing required field" in violations[0]
    assert "deployed-to" in violations[0]


def test_invalid_enforce_enum(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(
        root,
        "bad-enum.md",
        "---\nid: bad-enum\nenforce: cron\ndeployed-to: CLAUDE.md\n---\n",
    )
    violations = check_rules(root)
    assert len(violations) == 1
    assert "invalid enforce value 'cron'" in violations[0]


def test_broken_yaml_reported_not_raised(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "broken.md", "---\nid: [unclosed\n---\n")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "invalid YAML" in violations[0]


def test_id_filename_mismatch(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "actual-name.md", valid_rule("other-name"))
    (root / "CLAUDE.md").write_text("@meta/rules/actual-name.md\n", encoding="utf-8")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "does not match filename stem" in violations[0]


def test_missing_deploy_target(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))  # CLAUDE.md 미생성
    violations = check_rules(root)
    assert len(violations) == 1
    assert "does not exist" in violations[0]


def test_declared_but_not_deployed(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("# no import here\n", encoding="utf-8")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "declared but not actually deployed" in violations[0]


def test_unverifiable_vessels_are_rejected(tmp_path: Path) -> None:
    # 강화 사양: 검증 미구현 그릇(현재 skill)은 통과가 아니라 거부.
    root = make_repo(tmp_path)
    (root / "CLAUDE.md").write_text("anything\n", encoding="utf-8")
    write_rule(
        root,
        "skill-rule.md",
        "---\nid: skill-rule\nenforce: skill\ndeployed-to: CLAUDE.md\n---\n",
    )
    violations = check_rules(root)
    assert len(violations) == 1
    assert "is not implemented" in violations[0]


def hook_rule(rule_id: str) -> str:
    """유효한 hook 규칙 본문을 만든다."""
    return (
        f"---\nid: {rule_id}\nenforce: hook\n"
        "deployed-to: .claude/settings.json\n---\n\nbody\n"
    )


def make_hook_deployment(root: Path, rule_id: str, settings_text: str) -> None:
    """hook 규칙의 배포 대상(settings + harness 패키지)을 만든다."""
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(settings_text, encoding="utf-8")
    (root / "meta" / "harness" / rule_id.replace("-", "_")).mkdir(parents=True)


def test_valid_hook_rule_passes(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    settings = '{"hooks": {"PreToolUse": [{"hooks": [{"command": "uv run python -m harness.my_guard"}]}]}}'
    make_hook_deployment(root, "my-guard", settings)
    assert check_rules(root) == []


def test_hook_rule_with_broken_settings_json(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    make_hook_deployment(root, "my-guard", "{not json")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "is not valid JSON" in violations[0]


def test_hook_rule_without_module_reference(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    make_hook_deployment(root, "my-guard", '{"hooks": {}}')
    violations = check_rules(root)
    assert len(violations) == 1
    assert "harness.my_guard" in violations[0]
    assert "declared but not actually deployed" in violations[0]


def test_hook_rule_without_harness_package(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        '{"hooks": {"PreToolUse": [{"hooks": [{"command": "python -m harness.my_guard"}]}]}}',
        encoding="utf-8",
    )
    violations = check_rules(root)
    assert len(violations) == 1
    assert "does not exist" in violations[0]
    assert "meta/harness/my_guard/" in violations[0]


def test_real_repo_rules_all_pass() -> None:
    # 통합 확인: 실제 저장소의 규칙이 전부 선언대로 배포되어 있어야 한다.
    assert check_rules(find_repo_root()) == []
