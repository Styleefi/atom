# rules_checker의 정상 경로와 실패 경로를 픽스처로 검증하는 테스트
"""rules_checker 테스트.

가짜 저장소(tmp_path)를 만들어 정상 규칙과 각 위반 유형(필드 누락, 잘못된
enum, 깨진 YAML, 없는 배포 대상, 미배포 선언, 미검증 그릇 거부, 템플릿
드리프트, 인벤토리 커버리지)을 검증하고, 마지막에 실제 저장소의 규칙이
전부 통과하는지 통합 확인한다.

인벤토리 검사가 check_rules에 연결되어 있으므로 모든 픽스처는 정합한
meta/README.md를 가져야 한다. 이를 개별 테스트에 떠넘기지 않도록 make_repo가
골격을 만들고 write_rule이 규칙 행을 자동으로 채운다. 인벤토리 자체를 다루는
테스트는 write_inventory로 파일을 통째로 써서 원하는 상태(위반 상태든, 포맷
내성을 보는 정합 상태든)를 명시적으로 구성한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.rules_checker import check_rules as check_rules_module
from harness.rules_checker.check_rules import (
    HOOK_COMMAND_BLOCKING,
    HOOK_COMMAND_NON_BLOCKING,
    INVENTORY_ROW_RE,
    check_rules,
    find_repo_root,
)


# 인벤토리 골격. 표 헤더 셀에는 백틱을 쓰지 않는다(항목으로 오인되므로).
# 마커 주석은 헬퍼가 행을 끼워 넣는 위치이며 추출 대상이 아니다.
RULES_MARKER = "<!-- rules-end -->"
ARTIFACTS_MARKER = "<!-- artifacts-end -->"
INVENTORY_SKELETON = (
    "# fixture inventory\n\n"
    "## Rules\n\n"
    "| id | notes |\n| --- | --- |\n"
    f"{RULES_MARKER}\n\n"
    "## Functional artifacts\n\n"
    "| name | notes |\n| --- | --- |\n"
    f"{ARTIFACTS_MARKER}\n"
)


def rule_violations(root: Path) -> list[str]:
    """인벤토리 보류 안내를 뺀 위반 목록을 돌려준다.

    규칙 위반이 있으면 인벤토리 검사가 미뤄지고 그 사실이 위반으로 따라붙는다.
    규칙 검사 자체를 다루는 테스트가 매번 그것까지 세면 불필요한 결합이 생기므로
    걸러낸다 — 보류 동작은 전용 테스트가 고정한다.
    """
    return [v for v in check_rules(root) if "coverage was not checked" not in v]


def make_repo(tmp_path: Path) -> Path:
    """meta/rules/ 골격과 빈 인벤토리를 가진 가짜 저장소를 만든다.

    Args:
        tmp_path: pytest가 제공하는 임시 디렉토리.

    Returns:
        가짜 저장소 루트 경로.
    """
    (tmp_path / "meta" / "rules").mkdir(parents=True)
    (tmp_path / "meta" / "README.md").write_text(INVENTORY_SKELETON, encoding="utf-8")
    return tmp_path


def write_inventory(root: Path, text: str) -> None:
    """인벤토리를 통째로 덮어쓴다.

    자동 유지되는 골격 대신 원하는 상태를 직접 구성할 때 쓴다 — 위반 상태든,
    포맷 내성을 보는 정합 상태든.
    """
    (root / "meta" / "README.md").write_text(text, encoding="utf-8")


def _add_inventory_row(root: Path, marker: str, name: str) -> None:
    """마커 바로 앞에 항목 행 하나를 끼워 넣는다."""
    path = root / "meta" / "README.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(marker, f"| `{name}` | fixture |\n{marker}"), encoding="utf-8")


def list_artifact(root: Path, name: str) -> None:
    """인벤토리의 Functional artifacts 표에 아티팩트를 등재한다."""
    _add_inventory_row(root, ARTIFACTS_MARKER, name)


def write_rule(root: Path, name: str, body: str) -> Path:
    """meta/rules/ 아래에 규칙 파일을 만들고 인벤토리에 등재한다.

    체커와 동일하게 README.md는 규칙이 아니므로 등재하지 않는다.
    """
    path = root / "meta" / "rules" / name
    path.write_text(body, encoding="utf-8")
    if name != "README.md":
        _add_inventory_row(root, RULES_MARKER, path.stem)
    return path


def make_harness_package(root: Path, name: str) -> None:
    """import 가능한 하니스 패키지 디렉토리를 만든다."""
    package = root / "meta" / "harness" / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")


def make_infra_stack(root: Path, name: str) -> None:
    """인프라 스택 디렉토리를 만든다."""
    (root / "meta" / "infra" / name).mkdir(parents=True, exist_ok=True)


def valid_rule(rule_id: str) -> str:
    """유효한 claude-md 규칙 본문을 만든다."""
    return (
        f"---\nid: {rule_id}\ntier: principle\nenforce: claude-md\n"
        "deployed-to: CLAUDE.md\n---\n\nbody\n"
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
    write_rule(
        root,
        "no-target.md",
        "---\nid: no-target\ntier: principle\nenforce: claude-md\n---\n",
    )
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "missing required field" in violations[0]
    assert "deployed-to" in violations[0]


def test_missing_tier_field(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(
        root,
        "no-tier.md",
        "---\nid: no-tier\nenforce: claude-md\ndeployed-to: CLAUDE.md\n---\n",
    )
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "missing required field" in violations[0]
    assert "tier" in violations[0]


def test_invalid_tier_enum(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(
        root,
        "bad-tier.md",
        "---\nid: bad-tier\ntier: law\nenforce: claude-md\ndeployed-to: CLAUDE.md\n---\n",
    )
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "invalid tier value 'law'" in violations[0]


def test_invalid_enforce_enum(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(
        root,
        "bad-enum.md",
        "---\nid: bad-enum\ntier: convention\nenforce: cron\ndeployed-to: CLAUDE.md\n---\n",
    )
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "invalid enforce value 'cron'" in violations[0]


def test_broken_yaml_reported_not_raised(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "broken.md", "---\nid: [unclosed\n---\n")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "invalid YAML" in violations[0]


def test_id_filename_mismatch(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "actual-name.md", valid_rule("other-name"))
    (root / "CLAUDE.md").write_text("@meta/rules/actual-name.md\n", encoding="utf-8")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "does not match filename stem" in violations[0]


def test_missing_deploy_target(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))  # CLAUDE.md 미생성
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "does not exist" in violations[0]


def test_declared_but_not_deployed(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("# no import here\n", encoding="utf-8")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "declared but not actually deployed" in violations[0]


def test_unverifiable_vessels_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 강화 사양: 검증 미구현 그릇은 통과가 아니라 거부. 세 그릇 모두 검증이
    # 구현된 뒤에는 정상 경로로 도달할 수 없으므로, 가상 그릇을 허용 enum에
    # 주입해 방어 분기가 살아 있는지 확인한다.
    monkeypatch.setattr(
        check_rules_module, "VALID_ENFORCE", {*check_rules_module.VALID_ENFORCE, "webhook"}
    )
    root = make_repo(tmp_path)
    (root / "CLAUDE.md").write_text("anything\n", encoding="utf-8")
    write_rule(
        root,
        "future-rule.md",
        "---\nid: future-rule\ntier: convention\nenforce: webhook\n"
        "deployed-to: CLAUDE.md\n---\n",
    )
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "is not implemented" in violations[0]


def hook_rule(rule_id: str, blocking: str | None = "true") -> str:
    """유효한 hook 규칙 본문을 만든다. blocking=None이면 필드를 뺀다."""
    blocking_line = f"blocking: {blocking}\n" if blocking is not None else ""
    return (
        f"---\nid: {rule_id}\ntier: convention\nenforce: hook\n"
        f"deployed-to: .claude/settings.json\n{blocking_line}---\n\nbody\n"
    )


def hook_settings(*commands: str) -> str:
    """주어진 커맨드들로 훅 하나짜리 settings JSON을 만든다."""
    return json.dumps(
        {"hooks": {"PreToolUse": [{"hooks": [{"command": c} for c in commands]}]}}
    )


def canonical_command(module: str, blocking: bool = True) -> str:
    """정본 템플릿에서 훅 커맨드를 만든다 — 픽스처가 상수와 드리프트하지 않게."""
    template = HOOK_COMMAND_BLOCKING if blocking else HOOK_COMMAND_NON_BLOCKING
    return template.replace("{module}", module)


def make_hook_deployment(root: Path, rule_id: str, settings_text: str) -> None:
    """hook 규칙의 배포 대상(settings + harness 패키지)을 만든다."""
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(settings_text, encoding="utf-8")
    (root / "meta" / "harness" / rule_id.replace("-", "_")).mkdir(parents=True)


def test_valid_hook_rule_passes(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    settings = hook_settings(canonical_command("harness.my_guard"))
    make_hook_deployment(root, "my-guard", settings)
    assert check_rules(root) == []


def test_valid_non_blocking_hook_rule_passes(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard", blocking="false"))
    settings = hook_settings(canonical_command("harness.my_guard", blocking=False))
    make_hook_deployment(root, "my-guard", settings)
    assert check_rules(root) == []


def test_hook_rule_with_broken_settings_json(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    make_hook_deployment(root, "my-guard", "{not json")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "is not valid JSON" in violations[0]


def test_hook_rule_without_module_reference(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    make_hook_deployment(root, "my-guard", '{"hooks": {}}')
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "harness.my_guard" in violations[0]
    assert "declared but not actually deployed" in violations[0]


def test_hook_rule_without_harness_package(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        hook_settings(canonical_command("harness.my_guard")), encoding="utf-8"
    )
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "does not exist" in violations[0]
    assert "meta/harness/my_guard/" in violations[0]


def test_hook_rule_with_legacy_exec_command_fails(tmp_path: Path) -> None:
    # #31의 원형: exec 배선은 uv 자체 exit 2를 차단으로 흘린다 — 형태 위반.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    legacy = (
        "if command -v uv >/dev/null 2>&1; then exec uv run --directory "
        '"$CLAUDE_PROJECT_DIR/meta" python -m harness.my_guard; fi'
    )
    make_hook_deployment(root, "my-guard", hook_settings(legacy))
    violations = rule_violations(root)
    # 규칙별 형태 검사와 역방향 스윕이 독립적으로 각자 잡는다 — 위반 2건.
    assert len(violations) == 2
    assert any("canonical blocking wrapper" in v for v in violations)
    # 위반 메시지만으로 복붙 수정이 가능해야 한다 — 기대 정본을 그대로 담는다.
    assert any(canonical_command("harness.my_guard") in v for v in violations)


def test_hook_rule_shape_must_match_blocking_value(tmp_path: Path) -> None:
    # blocking: false 규칙에 차단형 래퍼가 배선되면 형태 위반이다.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard", blocking="false"))
    settings = hook_settings(canonical_command("harness.my_guard", blocking=True))
    make_hook_deployment(root, "my-guard", settings)
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "canonical non-blocking wrapper" in violations[0]


def test_hook_rule_without_blocking_field(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard", blocking=None))
    settings = hook_settings(canonical_command("harness.my_guard"))
    make_hook_deployment(root, "my-guard", settings)
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "must declare 'blocking: true | false'" in violations[0]


def test_hook_rule_with_non_bool_blocking(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard", blocking="maybe"))
    settings = hook_settings(canonical_command("harness.my_guard"))
    make_hook_deployment(root, "my-guard", settings)
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "'blocking' must be a boolean" in violations[0]


def test_blocking_on_non_hook_rule_is_rejected(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    body = (
        "---\nid: my-rule\ntier: principle\nenforce: claude-md\n"
        "deployed-to: CLAUDE.md\nblocking: true\n---\n\nbody\n"
    )
    write_rule(root, "my-rule.md", body)
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "'blocking' is only valid for hook rules" in violations[0]


def test_hook_rule_module_mention_outside_command_is_not_deployed(tmp_path: Path) -> None:
    # hooks 구조 밖의 언급은 배포가 아니다 — v1 substring 검사와의 차이.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    settings = '{"hooks": {}, "note": "python -m harness.my_guard"}'
    make_hook_deployment(root, "my-guard", settings)
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "declared but not actually deployed" in violations[0]


def test_hook_rule_prefix_module_is_not_a_reference(tmp_path: Path) -> None:
    # harness.my_guard는 harness.my_guard_v2 커맨드의 참조로 오인되면 안 된다.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    settings = hook_settings(canonical_command("harness.my_guard_v2"))
    make_hook_deployment(root, "my-guard", settings)
    violations = rule_violations(root)
    assert any("declared but not actually deployed" in v for v in violations)


def test_hook_rule_referenced_twice_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    command = canonical_command("harness.my_guard")
    make_hook_deployment(root, "my-guard", hook_settings(command, command))
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "expected exactly one" in violations[0]


def test_sweep_flags_unruled_harness_hook(tmp_path: Path) -> None:
    # 규칙 파일 없는 harness 훅도 정본 형태여야 한다 — 역방향 스윕.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    rogue = (
        "if command -v uv >/dev/null 2>&1; then exec uv run --directory "
        '"$CLAUDE_PROJECT_DIR/meta" python -m harness.rogue; fi'
    )
    settings = hook_settings(canonical_command("harness.my_guard"), rogue)
    make_hook_deployment(root, "my-guard", settings)
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "harness.rogue" in violations[0]
    assert "matches neither canonical wrapper" in violations[0]


def test_sweep_ignores_non_harness_commands(tmp_path: Path) -> None:
    # meta 소관 밖(자식 프로젝트의 자체 훅)은 스윕이 건드리지 않는다.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    settings = hook_settings(
        canonical_command("harness.my_guard"), "npx prettier --check ."
    )
    make_hook_deployment(root, "my-guard", settings)
    assert check_rules(root) == []


def skill_rule(rule_id: str, skill_name: str = "my-skill") -> str:
    """유효한 skill 규칙 본문을 만든다."""
    return (
        f"---\nid: {rule_id}\ntier: convention\nenforce: skill\n"
        f"deployed-to: .claude/skills/{skill_name}/SKILL.md\n---\n\nbody\n"
    )


def make_skill_deployment(root: Path, skill_name: str, skill_text: str) -> None:
    """skill 규칙의 배포 대상(SKILL.md)을 만든다."""
    skill_dir = root / ".claude" / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")


def test_valid_skill_rule_passes(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-style.md", skill_rule("my-style"))
    make_skill_deployment(root, "my-skill", "Apply meta/rules/my-style.md here.\n")
    assert check_rules(root) == []


def test_skill_rule_outside_skills_dir(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(
        root,
        "my-style.md",
        "---\nid: my-style\ntier: convention\nenforce: skill\n"
        "deployed-to: docs/SKILL.md\n---\n",
    )
    (root / "docs").mkdir()
    (root / "docs" / "SKILL.md").write_text("meta/rules/my-style.md\n", encoding="utf-8")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "must be a SKILL.md under .claude/skills/" in violations[0]


def test_skill_rule_target_not_skill_md(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(
        root,
        "my-style.md",
        "---\nid: my-style\ntier: convention\nenforce: skill\n"
        "deployed-to: .claude/skills/my-skill/readme.md\n---\n",
    )
    skill_dir = root / ".claude" / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "readme.md").write_text("meta/rules/my-style.md\n", encoding="utf-8")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "must be a SKILL.md under .claude/skills/" in violations[0]


def test_skill_rule_without_reference(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-style.md", skill_rule("my-style"))
    make_skill_deployment(root, "my-skill", "No reference here.\n")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "does not reference" in violations[0]
    assert "meta/rules/my-style.md" in violations[0]


def write_template(root: Path, text: str) -> None:
    """child 템플릿 파일을 만든다."""
    template = root / "meta" / "templates" / "CLAUDE.template.md"
    template.parent.mkdir(parents=True)
    template.write_text(text, encoding="utf-8")


def test_template_in_sync_passes(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    write_template(root, "@meta/rules/my-rule.md\n")
    assert check_rules(root) == []


def test_template_missing_import(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    write_template(root, "# no imports\n")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "missing '@meta/rules/my-rule.md'" in violations[0]


def test_template_stale_import(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    write_template(root, "@meta/rules/my-rule.md\n@meta/rules/removed-rule.md\n")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "'@meta/rules/removed-rule.md'" in violations[0]
    assert "stale" in violations[0]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # 항목으로 인정되는 형태: 행 첫 셀의 백틱 이름 하나.
        ("| `my-rule` | note |", ["my-rule"]),
        ("|   `my-rule`   | aligned with padding |", ["my-rule"]),
        ("| `rules_checker` | underscores are names too |", ["rules_checker"]),
        # 표 헤더와 구분선은 항목이 아니다 (헤더 셀에 백틱을 쓰지 않는 규약).
        ("| id | tier | vessel |", []),
        ("| --- | --- | --- |", []),
        # 뒤 컬럼의 백틱 토큰은 이름이 아니다 — 문자 집합을 통과하더라도.
        ("| `my-rule` | mentions `ghost-token` |", ["my-rule"]),
        ("| `my-rule` | note | `ghost-token` |", ["my-rule"]),
        ("| `commit-guard` | `ATOM_COMMIT_OVERRIDE=1` |", ["commit-guard"]),
        # 첫 셀이어도 문자 집합 밖이면 이름이 아니다.
        ("| `Not-A-Name` | uppercase |", []),
        ("| `run.sh` | dots are not allowed |", []),
        # 표 행이 아닌 산문 속 백틱도 이름이 아니다.
        ("see `ghost-token` in the prose", []),
    ],
)
def test_inventory_row_pattern(line: str, expected: list[str]) -> None:
    # 인벤토리의 포맷 계약(첫 셀만 이름, 소문자 케밥/스네이크)을 명세한다.
    assert INVENTORY_ROW_RE.findall(line) == expected


def test_inventory_full_fixture_passes(tmp_path: Path) -> None:
    # 다섯 종류를 모두 담아 통과 경로에서 backing 제외가 실제로 작동함을 본다.
    root = make_repo(tmp_path)
    # 규칙이 뒷받침하는 스킬/하니스는 규칙 표가 커버하므로 아티팩트가 아니다.
    write_rule(root, "my-style.md", skill_rule("my-style"))
    make_skill_deployment(root, "my-skill", "meta/rules/my-style.md\n")
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    (root / ".claude" / "settings.json").write_text(
        hook_settings(canonical_command("harness.my_guard")), encoding="utf-8"
    )
    make_harness_package(root, "my_guard")
    # 규칙이 없는 셋은 전부 아티팩트 표에 실려야 한다.
    make_skill_deployment(root, "helper-skill", "functional skill\n")
    make_harness_package(root, "toolbox")
    make_infra_stack(root, "sandbox")
    for name in ("helper-skill", "toolbox", "sandbox"):
        list_artifact(root, name)
    assert check_rules(root) == []


def test_inventory_file_missing(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "meta" / "README.md").unlink()
    violations = check_rules(root)
    assert len(violations) == 1
    assert "inventory is missing" in violations[0]


def test_inventory_missing_rule_row(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    write_inventory(root, INVENTORY_SKELETON)  # 자동 등재된 행을 지운다
    violations = check_rules(root)
    assert len(violations) == 1
    assert "'my-rule' is missing from the '## Rules' section" in violations[0]


def test_inventory_missing_rules_heading(tmp_path: Path) -> None:
    # 헤딩 자체가 없으면 추출이 빈 집합이 되어 같은 forward 위반으로 드러난다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    write_inventory(
        root, "# inventory\n\n## Functional artifacts\n\n| name | notes |\n| --- | --- |\n"
    )
    violations = check_rules(root)
    assert len(violations) == 1
    assert "'my-rule' is missing from the '## Rules' section" in violations[0]


def test_inventory_stale_rule_row(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    _add_inventory_row(root, RULES_MARKER, "removed-rule")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "'## Rules' section lists 'removed-rule'" in violations[0]
    assert "stale" in violations[0]


def test_inventory_missing_functional_skill(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    make_skill_deployment(root, "post-merge", "functional skill\n")
    violations = check_rules(root)
    assert len(violations) == 1
    assert (
        "'post-merge' is missing from the '## Functional artifacts' section"
        in violations[0]
    )


def test_inventory_missing_harness_and_infra(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    make_harness_package(root, "toolbox")
    make_infra_stack(root, "sandbox")
    violations = check_rules(root)
    assert len(violations) == 2
    assert "'sandbox' is missing" in violations[0]
    assert "'toolbox' is missing" in violations[1]


def test_inventory_stale_artifact_row(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    list_artifact(root, "ghost-stack")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "'## Functional artifacts' section lists 'ghost-stack'" in violations[0]


def test_inventory_rule_backed_skill_listed_as_artifact(tmp_path: Path) -> None:
    # 단일 분류 불변식: 규칙이 뒷받침하는 스킬은 아티팩트 표에 실리면 안 된다.
    root = make_repo(tmp_path)
    write_rule(root, "my-style.md", skill_rule("my-style"))
    make_skill_deployment(root, "my-skill", "meta/rules/my-style.md\n")
    list_artifact(root, "my-skill")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "'## Functional artifacts' section lists 'my-skill'" in violations[0]


@pytest.mark.parametrize(
    "deployed",
    [
        ".claude/skills/my-skill/SKILL.md",
        "./.claude/skills/my-skill/SKILL.md",
        ".claude//skills/my-skill/SKILL.md",
    ],
)
def test_inventory_matches_skill_owner_by_normalized_path(
    tmp_path: Path, deployed: str
) -> None:
    # check_rule_file이 deployed-to를 Path로 해석하므로, 문자열 그대로 비교하면
    # 규칙 검증은 통과하는데 소유권만 어긋나 스킬이 '규칙 없는 아티팩트'로
    # 오분류된다.
    root = make_repo(tmp_path)
    write_rule(
        root,
        "my-style.md",
        f"---\nid: my-style\ntier: convention\nenforce: skill\n"
        f"deployed-to: {deployed}\n---\n",
    )
    make_skill_deployment(root, "my-skill", "meta/rules/my-style.md\n")
    assert check_rules(root) == []


@pytest.mark.parametrize(
    "body",
    [
        # frontmatter 자체가 깨진 경우.
        "---\nid: my-guard\ntier: convention\n",
        # YAML은 정상이지만 필수 필드(enforce)가 빠진 경우.
        "---\nid: my-guard\ntier: convention\ndeployed-to: .claude/settings.json\n---\n",
    ],
)
def test_inventory_deferred_while_a_rule_is_violating(tmp_path: Path, body: str) -> None:
    # 깨진 레지스트리 위에서 분류하면 규칙이 뒷받침하던 하니스를 '규칙 없는
    # 아티팩트'로 오분류해, 고치는 순간 stale이 될 행을 추가하라고 지시하게
    # 된다. 그래서 검사를 미루고, 미룬 사실을 보고한다.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", body)
    make_harness_package(root, "my_guard")
    violations = check_rules(root)
    assert not [v for v in violations if "Functional artifacts" in v]
    assert [v for v in violations if "coverage was not checked" in v]


def test_inventory_deferral_also_holds_unrelated_drift(tmp_path: Path) -> None:
    # 보류의 대가: 규칙이 깨진 동안에는 무관한 아티팩트 드리프트도 보고되지
    # 않는다. 침묵이 아니라 보류 안내로 드러나고, 다음 실행에서 잡힌다.
    root = make_repo(tmp_path)
    write_rule(root, "broken.md", "---\nid: broken\n")
    make_infra_stack(root, "sandbox")
    violations = check_rules(root)
    assert not [v for v in violations if "'sandbox'" in v]
    assert [v for v in violations if "coverage was not checked" in v]


def test_inventory_runs_once_the_registry_is_clean(tmp_path: Path) -> None:
    # 보류는 한시적이다 — 규칙을 고치면 같은 드리프트가 바로 잡힌다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    make_infra_stack(root, "sandbox")
    violations = check_rules(root)
    assert not [v for v in violations if "coverage was not checked" in v]
    assert [v for v in violations if "'sandbox' is missing" in v]


def test_inventory_not_deferred_by_template_drift(tmp_path: Path) -> None:
    # 템플릿 동기화는 규칙 frontmatter와 무관하므로 보류 사유가 아니다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    write_template(root, "# no imports\n")
    make_infra_stack(root, "sandbox")
    violations = check_rules(root)
    assert not [v for v in violations if "coverage was not checked" in v]
    assert [v for v in violations if "'sandbox' is missing" in v]


def test_inventory_skill_dir_without_skill_md_is_not_an_artifact(
    tmp_path: Path,
) -> None:
    # SKILL.md가 없으면 스킬이 아니다 — 등재를 요구하지 않아야 한다.
    root = make_repo(tmp_path)
    (root / ".claude" / "skills" / "scratch").mkdir(parents=True)
    assert check_rules(root) == []


def test_inventory_harness_dir_without_init_is_not_an_artifact(tmp_path: Path) -> None:
    # __init__.py가 없으면 하니스 패키지가 아니다 (__pycache__ 등이 여기 걸린다).
    root = make_repo(tmp_path)
    (root / "meta" / "harness" / "scratch").mkdir(parents=True)
    assert check_rules(root) == []


def test_inventory_subheadings_do_not_end_a_section(tmp_path: Path) -> None:
    # 포맷 계약: ### 소제목은 표현일 뿐이며 구간을 끊지 않는다.
    root = make_repo(tmp_path)
    make_infra_stack(root, "sandbox")
    write_inventory(
        root,
        "# inventory\n\n## Rules\n\n| id | notes |\n| --- | --- |\n\n"
        "## Functional artifacts\n\n### Infrastructure\n\n"
        "| name | notes |\n| --- | --- |\n| `sandbox` | grouped under a subheading |\n",
    )
    assert check_rules(root) == []


def test_inventory_uses_the_first_of_duplicated_headings(tmp_path: Path) -> None:
    # 헤딩이 중복되면 첫 번째 구간만 읽는다 — 두 번째 블록은 추출되지 않는다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    write_inventory(
        root,
        "# inventory\n\n## Rules\n\n| id | notes |\n| --- | --- |\n"
        "| `my-rule` | the first block wins |\n\n"
        "## Rules\n\n| id | notes |\n| --- | --- |\n"
        "| `ghost-rule` | the second block is never read |\n\n"
        "## Functional artifacts\n\n| name | notes |\n| --- | --- |\n",
    )
    assert check_rules(root) == []


def test_inventory_path_as_directory_reports_missing(tmp_path: Path) -> None:
    # 병리 케이스: 같은 경로가 디렉토리면 crash가 아니라 위반으로 보고한다.
    root = make_repo(tmp_path)
    (root / "meta" / "README.md").unlink()
    (root / "meta" / "README.md").mkdir()
    violations = check_rules(root)
    assert len(violations) == 1
    assert "inventory is missing" in violations[0]


def test_inventory_nested_dirs_are_not_artifacts(tmp_path: Path) -> None:
    # 하니스마다 tests/ 하위 패키지가 있으므로 재귀 열거는 유령 항목을 만든다.
    root = make_repo(tmp_path)
    make_harness_package(root, "toolbox")
    make_harness_package(root, "toolbox/tests")
    list_artifact(root, "toolbox")
    assert check_rules(root) == []


def test_inventory_ignores_rows_outside_first_column_and_sections(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    write_inventory(
        root,
        "# inventory\n\n"
        "| `stray-row` | sits before any section |\n\n"
        "## Rules\n\n| id | notes |\n| --- | --- |\n"
        "| `my-rule` | override marker `ATOM_COMMIT_OVERRIDE=1` in a later cell |\n\n"
        "## Functional artifacts\n\n| name | notes |\n| --- | --- |\n\n"
        "## Notes\n\n| `trailing-row` | sits after the last table |\n",
    )
    assert check_rules(root) == []


def test_inventory_tolerates_table_padding(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    write_inventory(
        root,
        "# inventory\n\n"
        "## Rules\n\n| id | notes |\n| --- | --- |\n"
        "|   `my-rule`   | aligned with padding |\n\n"
        "## Functional artifacts\n\n| name | notes |\n| --- | --- |\n",
    )
    assert check_rules(root) == []


def test_inventory_tolerates_crlf_and_trailing_space(tmp_path: Path) -> None:
    # 헤딩 뒤 공백은 파이썬의 개행 정규화가 걷어내지 않으므로 rstrip이 필요하다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    body = (root / "meta" / "README.md").read_text(encoding="utf-8")
    body = body.replace("## Rules\n", "## Rules  \n").replace("\n", "\r\n")
    (root / "meta" / "README.md").write_bytes(body.encode("utf-8"))
    assert check_rules(root) == []


def test_real_repo_rules_all_pass() -> None:
    # 통합 확인: 실제 저장소의 규칙이 전부 선언대로 배포되어 있어야 한다.
    assert check_rules(find_repo_root()) == []
