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
import os
from pathlib import Path

import pytest

from harness.rules_checker import check_rules as check_rules_module
from harness.rules_checker.check_rules import (
    HOOK_COMMAND_BLOCKING,
    HOOK_COMMAND_NON_BLOCKING,
    INVENTORY_ROW_RE,
    _active_lines,
    _import_lines,
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
    """인벤토리 보류 안내·동기화 파일 부재 위반을 뺀 위반 목록을 돌려준다.

    규칙 위반이 있으면 인벤토리 검사가 미뤄지고 그 사실이 위반으로 따라붙고,
    baseline 파일을 지우는 픽스처는 부재 위반이 따라붙는다. 규칙 검사 자체를
    다루는 테스트가 매번 그것까지 세면 불필요한 결합이 생기므로 걸러낸다 —
    두 동작 모두 전용 테스트가 고정한다. 부재 필터는 부재 위반의 고유 문구로만
    거른다 — "does not exist" 류 범용 문구로 거르면 규칙 쪽 missing-target
    위반까지 걸러진다.
    """
    return [
        v
        for v in check_rules(root)
        if "coverage was not checked" not in v
        and "template sync target is missing" not in v
    ]


def make_repo(tmp_path: Path) -> Path:
    """meta/rules/ 골격·빈 인벤토리·동기화 baseline을 가진 가짜 저장소를 만든다.

    동기화 파일(루트 CLAUDE.md·child 템플릿) 부재가 위반이므로(#38) import
    없는 동기화된 baseline 쌍을 기본 제공한다 — 부재 자체를 다루는 테스트는
    baseline을 명시적으로 지우고, import를 쓰는 테스트는 deploy_claude_md로
    양쪽을 함께 쓴다.

    Args:
        tmp_path: pytest가 제공하는 임시 디렉토리.

    Returns:
        가짜 저장소 루트 경로.
    """
    (tmp_path / "meta" / "rules").mkdir(parents=True)
    (tmp_path / "meta" / "README.md").write_text(INVENTORY_SKELETON, encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# fixture claude\n", encoding="utf-8")
    template = tmp_path / "meta" / "templates" / "CLAUDE.template.md"
    template.parent.mkdir(parents=True)
    template.write_text("# fixture template\n", encoding="utf-8")
    return tmp_path


def deploy_claude_md(root: Path, *imports: str) -> None:
    """루트 CLAUDE.md와 child 템플릿에 같은 import 목록을 함께 쓴다.

    한쪽에만 쓰면 템플릿 동기화 드리프트 위반이 따라붙으므로, import를 쓰는
    green-path 테스트는 이 헬퍼로 두 파일을 동기 상태로 유지한다.
    """
    text = "".join(f"{line}\n" for line in imports)
    (root / "CLAUDE.md").write_text(text, encoding="utf-8")
    (root / "meta" / "templates" / "CLAUDE.template.md").write_text(
        text, encoding="utf-8"
    )


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
    """import·`python -m` 실행이 가능한 하니스 패키지 디렉토리를 만든다."""
    package = root / "meta" / "harness" / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text("", encoding="utf-8")


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
    deploy_claude_md(root, "@meta/rules/my-rule.md")
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


def test_duplicate_frontmatter_key_is_rejected(tmp_path: Path) -> None:
    # #38: safe_load의 last-win은 사람이 보는 선언과 checker 판정을 어긋나게
    # 한다(enforce: hook 뒤 enforce: claude-md가 claude-md로 침묵 통과).
    root = make_repo(tmp_path)
    write_rule(
        root,
        "my-rule.md",
        "---\nid: my-rule\ntier: convention\nenforce: hook\n"
        "enforce: claude-md\ndeployed-to: CLAUDE.md\n---\n\nbody\n",
    )
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "invalid YAML in frontmatter" in violations[0]
    assert "enforce" in violations[0]


def test_anchor_alias_frontmatter_still_parses(tmp_path: Path) -> None:
    # 커스텀 로더가 표준 YAML 기능(앵커/별칭)을 깨지 않는지 방어 핀.
    root = make_repo(tmp_path)
    write_rule(
        root,
        "my-rule.md",
        "---\nid: &a my-rule\ntier: convention\nenforce: claude-md\n"
        "deployed-to: CLAUDE.md\nnote: *a\n---\n\nbody\n",
    )
    deploy_claude_md(root, "@meta/rules/my-rule.md")
    assert check_rules(root) == []


def test_id_filename_mismatch(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "actual-name.md", valid_rule("other-name"))
    deploy_claude_md(root, "@meta/rules/actual-name.md")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "does not match filename stem" in violations[0]


def test_missing_deploy_target(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").unlink()  # baseline 제거 — 대상 부재 시나리오
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


def claude_md_rule(rule_id: str, deployed_to: str) -> str:
    """deployed-to를 지정한 claude-md 규칙 본문을 만든다(#38 pin 테스트용)."""
    return (
        f"---\nid: {rule_id}\ntier: principle\nenforce: claude-md\n"
        f"deployed-to: {deployed_to}\n---\n\nbody\n"
    )


def test_claude_md_arbitrary_file_is_rejected(tmp_path: Path) -> None:
    # #38: import가 실재해도 루트 CLAUDE.md가 아니면 배포가 아니다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", claude_md_rule("my-rule", "docs/notes.md"))
    (root / "docs").mkdir()
    (root / "docs" / "notes.md").write_text("@meta/rules/my-rule.md\n", encoding="utf-8")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "exactly 'CLAUDE.md'" in violations[0]


def test_claude_md_pin_does_not_mask_missing_target(tmp_path: Path) -> None:
    # pin 위반이 존재 검사를 가리지 않는다(skill 형태 검사와 대칭).
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", claude_md_rule("my-rule", "docs/missing.md"))
    violations = rule_violations(root)
    assert len(violations) == 2
    assert any("exactly 'CLAUDE.md'" in v for v in violations)
    assert any("does not exist" in v for v in violations)


def test_claude_md_dot_slash_variant_is_rejected(tmp_path: Path) -> None:
    # raw 문자열 비교라 논리적 동치 표기도 거부한다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", claude_md_rule("my-rule", "./CLAUDE.md"))
    (root / "CLAUDE.md").write_text("# base\n", encoding="utf-8")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "exactly 'CLAUDE.md'" in violations[0]


def test_claude_md_absolute_variant_is_rejected(tmp_path: Path) -> None:
    # 절대경로 표기는 pin과 bad_path 둘 다 걸린다 — 누적 보고 확인.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", claude_md_rule("my-rule", "/CLAUDE.md"))
    violations = rule_violations(root)
    assert len(violations) == 2
    assert any("exactly 'CLAUDE.md'" in v for v in violations)
    assert any("repo-root-relative" in v for v in violations)


# 활성 줄 스캐너의 동결 문법 코퍼스(#38, PR #75의 _SEGMENT_CORPUS 패턴).
# 각 항목: (텍스트, imp가 활성 독립 import로 인정되는가, 라벨). 라벨은 실패
# 메시지용이자 케이스의 계약 설명이다 — 모델 밖 구문 확장 요구는 결함이
# 아니라 문서화된 한계로 triage한다(스캐너 docstring 참조).
_SCANNER_IMP = "@meta/rules/my-rule.md"
_SCANNER_CORPUS = [
    (f"{_SCANNER_IMP}\n", True, "독립 활성 줄은 인정"),
    (f"# head\n\n{_SCANNER_IMP}\n", True, "다른 활성 줄과 공존해도 인정"),
    (f"<!-- {_SCANNER_IMP} -->\n", False, "단일 줄 주석 속은 불인정"),
    ("@meta/rules/my<!-- x -->-rule.md\n", False, "스팬 공백 치환이 토큰 융합 차단"),
    (f"<!--\n{_SCANNER_IMP}\n-->\n", False, "여러 줄 주석 속은 불인정"),
    (f"<!-- start\n{_SCANNER_IMP}\nend -->\n{_SCANNER_IMP}\n", True, "주석 종료 후 독립 줄은 활성"),
    (f"<!--\nc\n-->{_SCANNER_IMP}\n", False, "닫는 줄 잔여는 활성이나 독립 줄은 아님"),
    (f"```\n{_SCANNER_IMP}\n```\n", False, "``` 펜스 속은 불인정"),
    (f"```python\n{_SCANNER_IMP}\n```\n", False, "info string 펜스도 동일"),
    (f"~~~\n{_SCANNER_IMP}\n~~~\n", False, "~~~ 펜스 속은 불인정"),
    (f"```\nx\n~~~\n{_SCANNER_IMP}\n```\n", False, "~~~는 ``` 펜스를 닫지 못함"),
    (f"```\n{_SCANNER_IMP}\n", False, "미종결 펜스는 EOF까지 비활성(fail-safe)"),
    (f"<!--\n```\n-->\n{_SCANNER_IMP}\n", True, "주석 안 펜스 마커는 펜스를 열지 않음"),
    (f"```\n<!--\n```\n{_SCANNER_IMP}\n", True, "펜스 안 <!--는 주석으로 전이하지 않음"),
    (f"    {_SCANNER_IMP}\n", False, "들여쓰기 줄(코드 블록)은 불인정"),
    (f"see {_SCANNER_IMP} in\n", False, "인라인 언급은 불인정"),
    (f"{_SCANNER_IMP}@meta/rules/other.md\n", False, "무공백 연접은 불일치로 수렴"),
]


def test_active_import_scanner_corpus() -> None:
    for text, expected, label in _SCANNER_CORPUS:
        assert (_SCANNER_IMP in _import_lines(text)) == expected, label


def test_scanner_same_line_comment_keeps_remainder_active() -> None:
    # 같은 줄 개폐 주석은 스팬만 죽고 앞뒤 텍스트는 활성으로 남는다.
    joined = "\n".join(_active_lines("a <!-- x --> b"))
    assert "a" in joined and "b" in joined
    assert "x" not in joined


def test_claude_md_fenced_import_is_not_deployed(tmp_path: Path) -> None:
    # #38 지점 배선(probe p2b 승격): 펜스 속 import는 배포가 아니다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text(
        "```\n@meta/rules/my-rule.md\n```\n", encoding="utf-8"
    )
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "declared but not actually deployed" in violations[0]


def test_claude_md_commented_import_is_not_deployed(tmp_path: Path) -> None:
    # #38 지점 배선(probe p2a 승격): 주석 처리된 import는 배포가 아니다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text(
        "<!-- @meta/rules/my-rule.md -->\n", encoding="utf-8"
    )
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
    """hook 규칙의 배포 대상(settings + 실행 가능한 harness 패키지)을 만든다."""
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(settings_text, encoding="utf-8")
    make_harness_package(root, rule_id.replace("-", "_"))


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
    # per-rule의 정밀 메시지 + 스윕의 검증 불가 보고(면제 없음 — 최종 게이트 리뷰).
    assert len(violations) == 2
    assert any("is not valid JSON" in v for v in violations)
    assert any("cannot verify hook wiring" in v for v in violations)


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


def test_hook_package_without_init_is_rejected(tmp_path: Path) -> None:
    # #38: __init__.py는 메타층 패키지 규약 — 근거 서술의 SSOT는 검사 지점 주석.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    make_hook_deployment(root, "my-guard", hook_settings(canonical_command("harness.my_guard")))
    (root / "meta" / "harness" / "my_guard" / "__init__.py").unlink()
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "__init__.py" in violations[0]


def test_hook_package_without_main_is_rejected(tmp_path: Path) -> None:
    # __main__.py는 python -m 진입점 — 근거 서술의 SSOT는 검사 지점 주석.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    make_hook_deployment(root, "my-guard", hook_settings(canonical_command("harness.my_guard")))
    (root / "meta" / "harness" / "my_guard" / "__main__.py").unlink()
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "__main__.py" in violations[0]


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
    deploy_claude_md(root, "@meta/rules/my-rule.md")
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


def test_hook_rule_referenced_twice_all_canonical_passes(tmp_path: Path) -> None:
    # 복수 matcher/이벤트 배선은 정당하다 — 전부 정본이면 통과(리뷰 완화).
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    command = canonical_command("harness.my_guard")
    make_hook_deployment(root, "my-guard", hook_settings(command, command))
    assert check_rules(root) == []


def test_hook_rule_referenced_twice_one_legacy_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    legacy = (
        "if command -v uv >/dev/null 2>&1; then exec uv run --directory "
        '"$CLAUDE_PROJECT_DIR/meta" python -m harness.my_guard; fi'
    )
    settings = hook_settings(canonical_command("harness.my_guard"), legacy)
    make_hook_deployment(root, "my-guard", settings)
    violations = rule_violations(root)
    # 정본 커맨드는 통과하고, 구식 커맨드만 per-rule 형태 검사와 스윕이 각각 잡는다.
    assert len(violations) == 2
    assert any("canonical blocking wrapper" in v for v in violations)


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
    # 위반 메시지만으로 복붙 수정이 가능해야 한다 — 렌더링된 정본을 담는다.
    assert canonical_command("harness.rogue") in violations[0]


def test_sweep_runs_without_any_hook_rule(tmp_path: Path) -> None:
    # settings.json은 hook 규칙이 하나도 없어도 무조건 스윕 대상이다(리뷰
    # 발견: 자식이 규칙을 지우면 구식 배선이 무검증으로 남던 구멍).
    root = make_repo(tmp_path)
    legacy = (
        "if command -v uv >/dev/null 2>&1; then exec uv run --directory "
        '"$CLAUDE_PROJECT_DIR/meta" python -m harness.rogue; fi'
    )
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        hook_settings(legacy), encoding="utf-8"
    )
    violations = check_rules(root)
    assert len(violations) == 1
    assert "harness.rogue" in violations[0]
    assert "matches neither canonical wrapper" in violations[0]


def test_sweep_covers_settings_local_json(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    make_hook_deployment(
        root, "my-guard", hook_settings(canonical_command("harness.my_guard"))
    )
    legacy = (
        "if command -v uv >/dev/null 2>&1; then exec uv run --directory "
        '"$CLAUDE_PROJECT_DIR/meta" python -m harness.local_rogue; fi'
    )
    (root / ".claude" / "settings.local.json").write_text(
        hook_settings(legacy), encoding="utf-8"
    )
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "settings.local.json" in violations[0]
    assert "harness.local_rogue" in violations[0]


def test_sweep_catches_python3_variant(tmp_path: Path) -> None:
    # `python -m` 리터럴이 아닌 변형 표기도 -m 앵커로 잡는다(리뷰 발견).
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    variant = (
        "if command -v uv >/dev/null 2>&1; then exec uv run --directory "
        '"$CLAUDE_PROJECT_DIR/meta" python3 -m harness.rogue; fi'
    )
    settings = hook_settings(canonical_command("harness.my_guard"), variant)
    make_hook_deployment(root, "my-guard", settings)
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "harness.rogue" in violations[0]


def test_sweep_allows_canonical_submodule_entrypoint(tmp_path: Path) -> None:
    # 하위모듈 진입점은 스윕에서 허용 — 정본 래핑이면 통과(리뷰 발견:
    # 캡처 잘림으로 검사 불충족이던 케이스).
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    settings = hook_settings(
        canonical_command("harness.my_guard"),
        canonical_command("harness.my_tool.cli"),
    )
    make_hook_deployment(root, "my-guard", settings)
    assert check_rules(root) == []


def test_sweep_ignores_harness_like_paths(tmp_path: Path) -> None:
    # `-m` 호출이 아닌 경로성 문자열(meta/harness.txt)은 모듈 참조가 아니다.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    settings = hook_settings(
        canonical_command("harness.my_guard"), "cat meta/harness.txt"
    )
    make_hook_deployment(root, "my-guard", settings)
    assert check_rules(root) == []


def test_ruled_hook_variant_spelling_gets_shape_violation(tmp_path: Path) -> None:
    # ruled hook의 python3 변형은 오해 소지 있는 "not referenced"가 아니라
    # 정확한 형태 위반으로 잡힌다(감지 확대의 개선점 핀).
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    variant = canonical_command("harness.my_guard").replace("python -m", "python3 -m")
    make_hook_deployment(root, "my-guard", hook_settings(variant))
    violations = rule_violations(root)
    assert len(violations) == 2  # per-rule 형태 위반 + 스윕
    assert any("does not match the canonical blocking wrapper" in v for v in violations)


def test_absolute_deployed_to_is_rejected_not_crash(tmp_path: Path) -> None:
    # 절대경로 deployed-to는 위반으로 보고되고 체커는 죽지 않는다(리뷰 발견:
    # relative_to의 ValueError로 전체 체커 사망). 경로 위반이 경로 무관 검사
    # (패키지 실존)를 가리지도 않는다(리뷰 2R).
    root = make_repo(tmp_path)
    outside = tmp_path / "outside-settings.json"
    outside.write_text(
        hook_settings(canonical_command("harness.my_guard")), encoding="utf-8"
    )
    body = (
        "---\nid: my-guard\ntier: convention\nenforce: hook\n"
        f"deployed-to: {outside}\nblocking: true\n---\n\nbody\n"
    )
    write_rule(root, "my-guard.md", body)
    violations = rule_violations(root)
    assert len(violations) == 2
    assert any("repo-root-relative" in v for v in violations)
    assert any("does not exist" in v for v in violations)


def test_bad_path_does_not_mask_blocking_and_package(tmp_path: Path) -> None:
    # 경로 위반 + blocking 부재 + 패키지 부재 → 셋 다 한 번에 보고(리뷰 2R:
    # 조기 return 가림 패턴을 hook 분기에서 구조적으로 제거했음을 핀).
    root = make_repo(tmp_path)
    body = (
        "---\nid: my-guard\ntier: convention\nenforce: hook\n"
        "deployed-to: /abs/settings.json\n---\n\nbody\n"
    )
    write_rule(root, "my-guard.md", body)
    violations = rule_violations(root)
    assert len(violations) == 3
    assert any("repo-root-relative" in v for v in violations)
    assert any("must declare 'blocking" in v for v in violations)
    assert any("does not exist" in v for v in violations)


def test_non_object_settings_does_not_mask_package(tmp_path: Path) -> None:
    # 비객체 settings 위반이 패키지 부재를 가리면 안 된다(리뷰 2R).
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text("[]", encoding="utf-8")
    violations = rule_violations(root)
    assert len(violations) == 3
    assert any("is not a JSON object" in v for v in violations)
    assert any("does not exist" in v for v in violations)
    assert any("cannot verify hook wiring" in v for v in violations)


def test_broken_rule_plus_corrupt_settings_both_reported(tmp_path: Path) -> None:
    # frontmatter가 깨진 hook 규칙 + 깨진 settings 조합 — 규칙별 검사가 조기
    # 종료해도 스윕이 검증 불가를 보고해야 한다(최종 게이트 리뷰: 면제 전제
    # 붕괴로 아무도 보고하지 않던 침묵 통과).
    root = make_repo(tmp_path)
    body = (
        "---\nid: my-guard\ntier: bogus\nenforce: hook\n"
        "deployed-to: .claude/settings.json\nblocking: true\n---\n\nbody\n"
    )
    write_rule(root, "my-guard.md", body)
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    violations = rule_violations(root)
    assert any("invalid tier" in v for v in violations)
    assert any("cannot verify hook wiring" in v for v in violations)


def test_broken_rule_file_does_not_kill_the_sweep(tmp_path: Path) -> None:
    # 규칙 파일 하나가 안 읽혀도(깨진 symlink) 스윕의 배선 위반은 살아야
    # 한다(최종 게이트 리뷰: 스윕 전체가 internal error 하나로 뭉개지던 가림).
    root = make_repo(tmp_path)
    (root / "meta" / "rules" / "broken.md").symlink_to(root / "nonexistent.md")
    legacy = (
        "if command -v uv >/dev/null 2>&1; then exec uv run --directory "
        '"$CLAUDE_PROJECT_DIR/meta" python -m harness.legacy; fi'
    )
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        hook_settings(legacy), encoding="utf-8"
    )
    violations = rule_violations(root)
    # per-rule 방어의 보고인지(스윕 붕괴 메시지가 아니라) 규칙 경로로 앵커한다.
    assert any("meta/rules/broken.md: internal checker error" in v for v in violations)
    assert any("harness.legacy" in v for v in violations)


def test_non_utf8_rule_file_does_not_kill_the_sweep(tmp_path: Path) -> None:
    # 비UTF-8 규칙 파일(UnicodeDecodeError — OSError가 아님)도 스윕을 뭉개면
    # 안 된다(탈출 관찰 라운드: OSError만 잡던 가드가 같은 가림을 재발시킴).
    root = make_repo(tmp_path)
    (root / "meta" / "rules" / "badenc.md").write_bytes(b"\xbe\xbe\xbe")
    legacy = (
        "if command -v uv >/dev/null 2>&1; then exec uv run --directory "
        '"$CLAUDE_PROJECT_DIR/meta" python -m harness.legacy; fi'
    )
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        hook_settings(legacy), encoding="utf-8"
    )
    violations = rule_violations(root)
    assert any("meta/rules/badenc.md: internal checker error" in v for v in violations)
    assert any("harness.legacy" in v for v in violations)


def test_pathological_rule_yaml_does_not_kill_the_sweep(tmp_path: Path) -> None:
    # 파싱 중 예외(YAML 중첩의 RecursionError — OSError도 UnicodeDecodeError도
    # 아님)도 스윕을 뭉개면 안 된다(탈출 관찰 2R: 가드가 예외 타입만 넓히고
    # 영역을 안 넓혀 같은 가림이 세 번째 재발).
    root = make_repo(tmp_path)
    (root / "meta" / "rules" / "deep.md").write_text(
        "---\nx: " + "[" * 8000 + "\n---\n", encoding="utf-8"
    )
    legacy = (
        "if command -v uv >/dev/null 2>&1; then exec uv run --directory "
        '"$CLAUDE_PROJECT_DIR/meta" python -m harness.legacy; fi'
    )
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        hook_settings(legacy), encoding="utf-8"
    )
    violations = rule_violations(root)
    # 형제 테스트들과 동일하게 "run은 red" 절반도 핀한다 — 규칙이 조용히
    # 건너뛰어지는 회귀를 이 테스트만 놓치면 안 된다(최종 게이트 R6).
    assert any("meta/rules/deep.md: internal checker error" in v for v in violations)
    assert any("harness.legacy" in v for v in violations)


def test_skill_missing_target_does_not_mask_shape(tmp_path: Path) -> None:
    # 대상 파일 부재가 SKILL.md 형태 위반을 가리면 안 된다(탈출 관찰 라운드:
    # bad_path만 고치고 missing-target 경로에 같은 가림이 남아 있었다).
    root = make_repo(tmp_path)
    write_rule(
        root,
        "my-style.md",
        "---\nid: my-style\ntier: convention\nenforce: skill\n"
        "deployed-to: docs/my-style.md\n---\n",
    )
    violations = rule_violations(root)
    assert len(violations) == 2
    assert any("must be a SKILL.md under .claude/skills/" in v for v in violations)
    assert any("does not exist" in v for v in violations)


def test_skill_bad_path_does_not_mask_shape(tmp_path: Path) -> None:
    # skill 그릇의 경로 위반이 SKILL.md 형태 위반을 가리면 안 된다(최종 게이트
    # 리뷰: 가림 부류의 skill 잔재).
    root = make_repo(tmp_path)
    body = (
        "---\nid: my-style\ntier: convention\nenforce: skill\n"
        "deployed-to: /abs/not-a-skill.txt\n---\n\nbody\n"
    )
    write_rule(root, "my-style.md", body)
    violations = rule_violations(root)
    assert len(violations) == 2
    assert any("repo-root-relative" in v for v in violations)
    assert any("must be a SKILL.md under .claude/skills/" in v for v in violations)


@pytest.mark.skipif(os.geteuid() == 0, reason="root는 mode 000도 읽을 수 있다")
def test_unreadable_settings_is_a_violation_not_a_crash(tmp_path: Path) -> None:
    # 읽기 불가 settings는 traceback이 아니라 위반이다(리뷰 2R: per-rule
    # read_text가 OSError 미포착으로 체커 사망).
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    settings = hook_settings(canonical_command("harness.my_guard"))
    make_hook_deployment(root, "my-guard", settings)
    (root / ".claude" / "settings.json").chmod(0o000)
    violations = rule_violations(root)
    assert len(violations) == 2
    assert any("cannot be read" in v for v in violations)
    assert any("cannot verify hook wiring" in v for v in violations)


def test_sweep_reports_unruled_corrupt_settings(tmp_path: Path) -> None:
    # hook 규칙이 없는 무조건 대상의 깨진 settings는 조용히 통과하면 안 된다
    # (리뷰 2R: 아무도 대신 보고하지 않는 조합이 green으로 새던 구멍).
    root = make_repo(tmp_path)
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "cannot verify hook wiring" in violations[0]


def test_sweep_catches_attached_m_spelling(tmp_path: Path) -> None:
    # `-mharness.x` 붙여쓰기는 유효한 인터프리터 호출 — 스윕이 잡아야 한다(리뷰 2R).
    root = make_repo(tmp_path)
    attached = (
        "if command -v uv >/dev/null 2>&1; then exec uv run --directory "
        '"$CLAUDE_PROJECT_DIR/meta" python -mharness.rogue; fi'
    )
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        hook_settings(attached), encoding="utf-8"
    )
    violations = check_rules(root)
    assert len(violations) == 1
    assert "harness.rogue" in violations[0]


def test_dotdot_deployed_to_is_not_swept(tmp_path: Path) -> None:
    # ..로 저장소를 탈출하는 deployed-to는 스윕 대상에서 배제된다(리뷰 2R:
    # relative_to는 어휘적이라 ..를 통과시켜 저장소 밖 파일을 검사하던 구멍).
    root = make_repo(tmp_path / "repo")
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    rogue = (
        "if command -v uv >/dev/null 2>&1; then exec uv run --directory "
        '"$CLAUDE_PROJECT_DIR/meta" python -m harness.outside_rogue; fi'
    )
    (sibling / "settings.json").write_text(hook_settings(rogue), encoding="utf-8")
    body = (
        "---\nid: my-guard\ntier: convention\nenforce: hook\n"
        "deployed-to: ../sibling/settings.json\nblocking: true\n---\n\nbody\n"
    )
    write_rule(root, "my-guard.md", body)
    violations = rule_violations(root)
    # 경로 위반 + 패키지 부재만 — 저장소 밖 파일의 내용은 절대 검사되지 않는다.
    assert len(violations) == 2
    assert not any("outside_rogue" in v for v in violations)


def test_sweep_flags_multi_module_command(tmp_path: Path) -> None:
    # 한 커맨드가 harness 모듈 두 개를 참조하면 첫 토큰 기준 오보고 대신
    # "모듈별 분리" 안내를 낸다(리뷰 2R).
    root = make_repo(tmp_path)
    compound = "python -m harness.a && python -m harness.b"
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        hook_settings(compound), encoding="utf-8"
    )
    violations = check_rules(root)
    assert len(violations) == 1
    assert "multiple harness modules" in violations[0]
    assert "harness.a" in violations[0] and "harness.b" in violations[0]


def test_checker_never_raises_on_malformed_inputs(tmp_path: Path) -> None:
    """사망 부류 방어: 어떤 깨진 입력에도 check_rules는 예외 대신 위반을 낸다.

    지점 단위 방어(ValueError → 고침 → PermissionError)가 반복 실패한 부류라
    전역 변환 + 이 퍼즈성 테스트로 구조적으로 닫는다(#40 리뷰 2R).
    """
    cases = [
        # (규칙 본문, settings 내용 — None이면 파일 없음)
        ("---\nid: my-guard\ntier: convention\nenforce: hook\n"
         "deployed-to: /abs/x\nblocking: true\n---\n\nbody\n", None),
        ("---\nid: my-guard\ntier: convention\nenforce: hook\n"
         "deployed-to: ../up/x\nblocking: true\n---\n\nbody\n", None),
        ("---\nid: my-guard\ntier: convention\nenforce: hook\n"
         "deployed-to: .claude/settings.json\nblocking: [list]\n---\n\nbody\n",
         "{not json"),
        ("---\nid: my-guard\ntier: convention\nenforce: hook\n"
         "deployed-to: .claude/settings.json\nblocking: true\n---\n\nbody\n",
         '{"hooks": 42}'),
        ("---\nid: my-guard\ntier: convention\nenforce: hook\n"
         "deployed-to: .claude/settings.json\nblocking: true\n---\n\nbody\n",
         '{"hooks": {"PreToolUse": [{"hooks": [{"command": 42}]}]}}'),
        ("---\nid: [1, 2]\ntier: {a: b}\nenforce: hook\n"
         "deployed-to: 5\nblocking: yes\n---\n\nbody\n", "[]"),
    ]
    for i, (rule_body, settings_text) in enumerate(cases):
        root = make_repo(tmp_path / str(i))
        write_rule(root, "my-guard.md", rule_body)
        if settings_text is not None:
            (root / ".claude").mkdir()
            (root / ".claude" / "settings.json").write_text(
                settings_text, encoding="utf-8"
            )
        # 예외 없이 위반 목록이 나오면 통과 — 내용은 각 시나리오 테스트가 핀.
        assert isinstance(check_rules(root), list)


def test_missing_blocking_does_not_mask_other_defects(tmp_path: Path) -> None:
    # blocking 부재가 같은 규칙의 다른 결함(패키지 부재)을 가리면 안 된다.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard", blocking=None))
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        hook_settings(canonical_command("harness.my_guard")), encoding="utf-8"
    )
    violations = rule_violations(root)
    assert len(violations) == 2
    assert any("must declare 'blocking" in v for v in violations)
    assert any("does not exist" in v for v in violations)


def test_hook_rule_with_non_object_settings(tmp_path: Path) -> None:
    # 배열 등 비객체 settings는 전용 메시지로 보고한다(리뷰 발견: "not
    # referenced"로 오도하던 케이스). 스윕도 예외 없이 함께 보고한다.
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    make_hook_deployment(root, "my-guard", "[]")
    violations = rule_violations(root)
    assert len(violations) == 2
    assert any("is not a JSON object" in v for v in violations)
    assert any("cannot verify hook wiring" in v for v in violations)


def test_null_settings_is_rejected(tmp_path: Path) -> None:
    # JSON 리터럴 null은 무위반 통과가 아니라 비객체 위반이다(최종 게이트
    # 리뷰: parsed의 None 겸용 sentinel이 만든 침묵 통과 회귀).
    root = make_repo(tmp_path)
    write_rule(root, "my-guard.md", hook_rule("my-guard"))
    make_hook_deployment(root, "my-guard", "null")
    violations = rule_violations(root)
    assert len(violations) == 2
    assert any("is not a JSON object" in v for v in violations)
    assert any("cannot verify hook wiring" in v for v in violations)


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


def test_skill_nested_path_is_rejected(tmp_path: Path) -> None:
    # #38: skill은 정확히 .claude/skills/<이름>/SKILL.md 깊이만 로드된다.
    root = make_repo(tmp_path)
    write_rule(
        root,
        "my-style.md",
        "---\nid: my-style\ntier: convention\nenforce: skill\n"
        "deployed-to: .claude/skills/nested/deep/SKILL.md\n---\n",
    )
    skill_dir = root / ".claude" / "skills" / "nested" / "deep"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("meta/rules/my-style.md\n", encoding="utf-8")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "must be a SKILL.md under .claude/skills/" in violations[0]


def test_skill_shallow_path_is_rejected(tmp_path: Path) -> None:
    # 얕은 쪽 경계(.claude/skills/SKILL.md, 3파트)도 스킬 위치가 아니다 —
    # parts[:2]+name 검사만으로는 지금 통과하는 실존 구멍(#38 4R 리뷰).
    root = make_repo(tmp_path)
    write_rule(
        root,
        "my-style.md",
        "---\nid: my-style\ntier: convention\nenforce: skill\n"
        "deployed-to: .claude/skills/SKILL.md\n---\n",
    )
    skills_dir = root / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("meta/rules/my-style.md\n", encoding="utf-8")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "must be a SKILL.md under .claude/skills/" in violations[0]


def test_skill_commented_reference_is_not_deployed(tmp_path: Path) -> None:
    # #38 지점 배선(probe p2d 승격): 주석 속 참조는 배포가 아니다.
    root = make_repo(tmp_path)
    write_rule(root, "my-style.md", skill_rule("my-style"))
    make_skill_deployment(root, "my-skill", "<!-- meta/rules/my-style.md -->\n")
    violations = rule_violations(root)
    assert len(violations) == 1
    assert "does not reference" in violations[0]


def write_template(root: Path, text: str) -> None:
    """child 템플릿 파일을 만들거나 baseline을 덮어쓴다."""
    template = root / "meta" / "templates" / "CLAUDE.template.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(text, encoding="utf-8")


def test_template_in_sync_passes(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    deploy_claude_md(root, "@meta/rules/my-rule.md")
    write_template(root, "@meta/rules/my-rule.md\n")
    assert check_rules(root) == []


def test_template_missing_import(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    deploy_claude_md(root, "@meta/rules/my-rule.md")
    write_template(root, "# no imports\n")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "missing '@meta/rules/my-rule.md'" in violations[0]


def test_template_stale_import(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    deploy_claude_md(root, "@meta/rules/my-rule.md")
    write_template(root, "@meta/rules/my-rule.md\n@meta/rules/removed-rule.md\n")
    violations = check_rules(root)
    # 지워진 규칙의 잔존 import는 드리프트(stale)이자 고아(#91)다.
    assert len(violations) == 2
    assert any("stale" in v and "'@meta/rules/removed-rule.md'" in v for v in violations)
    assert any("orphan rule import" in v for v in violations)


def test_template_fenced_import_counts_as_missing(tmp_path: Path) -> None:
    # #38 지점 배선(probe p2c 승격): 템플릿 쪽 펜스 속 import는 집합에 없다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    deploy_claude_md(root, "@meta/rules/my-rule.md")
    write_template(root, "```\n@meta/rules/my-rule.md\n```\n")
    violations = check_rules(root)
    assert len(violations) == 1
    assert "missing '@meta/rules/my-rule.md'" in violations[0]


def test_root_commented_import_counts_as_stale(tmp_path: Path) -> None:
    # #38 지점 배선(반대 방향): 루트 쪽이 주석이면 규칙 미배포 + 템플릿 stale.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    (root / "CLAUDE.md").write_text(
        "<!-- @meta/rules/my-rule.md -->\n", encoding="utf-8"
    )
    write_template(root, "@meta/rules/my-rule.md\n")
    violations = rule_violations(root)
    assert len(violations) == 2
    assert any("declared but not actually deployed" in v for v in violations)
    assert any("absent from root" in v for v in violations)


def test_orphan_import_in_root_is_reported(tmp_path: Path) -> None:
    # #91: 실존 규칙이 없는 import는 고아다 — 드리프트와 별개로 보고.
    root = make_repo(tmp_path)
    (root / "CLAUDE.md").write_text("@meta/rules/ghost.md\n", encoding="utf-8")
    violations = check_rules(root)
    assert len(violations) == 2
    assert any("orphan rule import '@meta/rules/ghost.md'" in v for v in violations)
    assert any("missing" in v for v in violations)


def test_orphan_import_on_both_sides_is_reported(tmp_path: Path) -> None:
    # #91의 침묵 케이스: 양쪽에 남은 고아는 sync가 통과시킨다 — 고아 검사만이
    # 잡는다. 파일별 보고라 2건(복붙 수정 가능 메시지 관례).
    root = make_repo(tmp_path)
    deploy_claude_md(root, "@meta/rules/ghost.md")
    violations = check_rules(root)
    assert len(violations) == 2
    assert all("orphan rule import '@meta/rules/ghost.md'" in v for v in violations)


def test_traversal_import_is_orphan(tmp_path: Path) -> None:
    # 리뷰 R1: ..로 meta/rules/ 밖 실존 파일을 가리키는 import는 파일 존재
    # 검사를 통과했었다 — 레지스트리 대조는 통과시키지 않는다.
    root = make_repo(tmp_path)
    deploy_claude_md(root, "@meta/rules/../../CLAUDE.md")
    violations = check_rules(root)
    assert len(violations) == 2
    assert all("orphan rule import" in v for v in violations)


def test_non_rule_file_import_is_orphan(tmp_path: Path) -> None:
    # 리뷰 R1: meta/rules/README.md는 실존하지만 규칙이 아니다.
    root = make_repo(tmp_path)
    write_rule(root, "README.md", "# not a rule\n")
    deploy_claude_md(root, "@meta/rules/README.md")
    violations = check_rules(root)
    assert len(violations) == 2
    assert all("orphan rule import" in v for v in violations)


def test_missing_template_is_reported(tmp_path: Path) -> None:
    # #38: 템플릿 부재의 유일한 감시자가 이 검사다 — 초록 통과 금지.
    root = make_repo(tmp_path)
    (root / "meta" / "templates" / "CLAUDE.template.md").unlink()
    violations = check_rules(root)
    assert len(violations) == 1
    assert "template sync target is missing" in violations[0]
    assert "CLAUDE.template.md" in violations[0]


def test_missing_root_claude_md_is_reported(tmp_path: Path) -> None:
    # 루트 쪽 부재도 여기서 직접 보고한다 — claude-md 규칙이 하나도 없으면
    # per-rule 검사의 backstop이 성립하지 않으므로(이중 보고 수용 패턴).
    root = make_repo(tmp_path)
    (root / "CLAUDE.md").unlink()
    violations = check_rules(root)
    assert len(violations) == 1
    assert "template sync target is missing" in violations[0]
    assert "CLAUDE.md" in violations[0]


def test_both_sync_files_missing_are_reported(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "CLAUDE.md").unlink()
    (root / "meta" / "templates" / "CLAUDE.template.md").unlink()
    violations = check_rules(root)
    assert len(violations) == 2
    assert all("template sync target is missing" in v for v in violations)


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
    deploy_claude_md(root, "@meta/rules/my-rule.md")
    write_inventory(root, INVENTORY_SKELETON)  # 자동 등재된 행을 지운다
    violations = check_rules(root)
    assert len(violations) == 1
    assert "'my-rule' is missing from the '## Rules' section" in violations[0]


def test_inventory_missing_rules_heading(tmp_path: Path) -> None:
    # 헤딩 자체가 없으면 추출이 빈 집합이 되어 같은 forward 위반으로 드러난다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    deploy_claude_md(root, "@meta/rules/my-rule.md")
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
    deploy_claude_md(root, "@meta/rules/my-rule.md")
    make_infra_stack(root, "sandbox")
    violations = check_rules(root)
    assert not [v for v in violations if "coverage was not checked" in v]
    assert [v for v in violations if "'sandbox' is missing" in v]


def test_inventory_not_deferred_by_template_drift(tmp_path: Path) -> None:
    # 템플릿 동기화는 규칙 frontmatter와 무관하므로 보류 사유가 아니다.
    root = make_repo(tmp_path)
    write_rule(root, "my-rule.md", valid_rule("my-rule"))
    deploy_claude_md(root, "@meta/rules/my-rule.md")
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
    deploy_claude_md(root, "@meta/rules/my-rule.md")
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
    deploy_claude_md(root, "@meta/rules/my-rule.md")
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
    deploy_claude_md(root, "@meta/rules/my-rule.md")
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
    deploy_claude_md(root, "@meta/rules/my-rule.md")
    body = (root / "meta" / "README.md").read_text(encoding="utf-8")
    body = body.replace("## Rules\n", "## Rules  \n").replace("\n", "\r\n")
    (root / "meta" / "README.md").write_bytes(body.encode("utf-8"))
    assert check_rules(root) == []


def test_real_repo_rules_all_pass() -> None:
    # 통합 확인: 실제 저장소의 규칙이 전부 선언대로 배포되어 있어야 한다.
    assert check_rules(find_repo_root()) == []
