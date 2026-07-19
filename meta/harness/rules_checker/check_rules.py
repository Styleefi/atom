# meta/rules/ 규칙 파일의 frontmatter 스키마와 실제 배포 상태를 검증하는 체커
"""규칙 배포 일관성 체커.

meta/rules/ 아래의 모든 규칙 파일에 대해 다음을 검증한다.

1. frontmatter 존재 및 필수 필드(id, enforce, deployed-to)
2. enforce 값이 허용된 그릇(claude-md | skill | hook)인지
3. id가 파일명(stem)과 일치하는지
4. deployed-to 대상 파일이 저장소에 실제 존재하는지
5. 실배포 확인 — claude-md 그릇: 대상 파일이 `@meta/rules/<파일명>` import를
   실제로 포함하는지. hook 그릇: deployed-to(settings JSON)가 규칙 id에서
   도출한 harness 모듈(`harness.<id의 -를 _로>`)을 참조하고 그 harness
   패키지가 실제 존재하는지. skill 그릇: 검증이 미구현이므로 **거부**한다
   (선언만 있고 검증 불가능한 배포는 통과시키지 않는다는 강화 사양).

경로는 실행 위치와 무관하게 이 파일의 고정 위치(meta/harness/rules_checker/)
로부터 역산한 저장소 루트 기준으로 해석하므로 로컬과 CI에서 결과가 동일하다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

# 허용되는 배포 그릇. 검증 로직이 구현된 그릇만 통과 대상이다.
VALID_ENFORCE = {"claude-md", "skill", "hook"}
VERIFIABLE_ENFORCE = {"claude-md", "hook"}

REQUIRED_FIELDS = ("id", "enforce", "deployed-to")


def find_repo_root() -> Path:
    """이 파일의 고정 위치로부터 저장소 루트를 역산한다.

    체커는 항상 <루트>/meta/harness/rules_checker/에 위치하므로,
    마커 파일 탐색 없이 상위 3단계가 곧 저장소 루트다. meta/가 자기완결
    uv 프로젝트(자체 pyproject.toml 보유)라서 pyproject 탐색 방식은
    meta/를 루트로 오인할 수 있어 쓰지 않는다.

    Returns:
        저장소 루트 디렉토리.
    """
    return Path(__file__).resolve().parents[3]


def parse_frontmatter(text: str) -> tuple[dict | None, str | None]:
    """마크다운 본문에서 YAML frontmatter를 파싱한다.

    Args:
        text: 규칙 파일 전체 내용.

    Returns:
        (frontmatter dict, 오류 메시지) 튜플. 성공 시 오류는 None,
        실패 시 dict는 None. 깨진 YAML은 예외가 아니라 오류 메시지로 보고한다.
    """
    if not text.startswith("---\n"):
        return None, "missing frontmatter (file does not start with '---')"
    end = text.find("\n---", 4)
    if end == -1:
        return None, "unterminated frontmatter (closing '---' not found)"
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        return None, f"invalid YAML in frontmatter: {exc}"
    if not isinstance(data, dict):
        return None, "frontmatter is not a mapping (key: value)"
    return data, None


def check_rule_file(rule_path: Path, root: Path) -> list[str]:
    """규칙 파일 하나를 검증하고 위반 목록을 돌려준다.

    Args:
        rule_path: 검증할 규칙 파일 경로.
        root: 저장소 루트 (deployed-to 해석 기준).

    Returns:
        위반 메시지 목록. 비어 있으면 통과.
    """
    rel = rule_path.relative_to(root)
    violations: list[str] = []

    data, error = parse_frontmatter(rule_path.read_text(encoding="utf-8"))
    if error:
        return [f"{rel}: {error}"]
    assert data is not None

    missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
    if missing:
        return [f"{rel}: missing required field(s): {', '.join(missing)}"]

    if data["id"] != rule_path.stem:
        violations.append(
            f"{rel}: id '{data['id']}' does not match filename stem '{rule_path.stem}'"
        )

    enforce = data["enforce"]
    if enforce not in VALID_ENFORCE:
        violations.append(
            f"{rel}: invalid enforce value '{enforce}' "
            f"(allowed: {', '.join(sorted(VALID_ENFORCE))})"
        )
        return violations

    target = root / str(data["deployed-to"])
    if not target.is_file():
        violations.append(
            f"{rel}: deployed-to target '{data['deployed-to']}' does not exist"
        )
        return violations

    if enforce == "claude-md":
        # claude-md 그릇: @import 줄의 존재가 곧 실배포다 (매 세션 자동 로드).
        import_line = f"@meta/rules/{rule_path.name}"
        if import_line not in target.read_text(encoding="utf-8"):
            violations.append(
                f"{rel}: '{data['deployed-to']}' does not contain the "
                f"'{import_line}' import — declared but not actually deployed"
            )
    elif enforce == "hook":
        # hook 그릇 규약(v1): 규칙 id에서 harness 모듈명을 도출해
        # (1) 대상 settings JSON이 그 모듈을 command로 참조하고
        # (2) meta/harness/ 아래에 해당 패키지가 실존해야 실배포로 본다.
        # 모듈 참조는 substring 검사이며 PreToolUse 위치까지는 보지 않는다.
        module_name = "harness." + rule_path.stem.replace("-", "_")
        target_text = target.read_text(encoding="utf-8")
        try:
            json.loads(target_text)
        except ValueError:
            violations.append(
                f"{rel}: deployed-to target '{data['deployed-to']}' is not valid JSON"
            )
            return violations
        if module_name not in target_text:
            violations.append(
                f"{rel}: '{data['deployed-to']}' does not reference the "
                f"'{module_name}' hook module — declared but not actually deployed"
            )
        package_dir = root / "meta" / "harness" / rule_path.stem.replace("-", "_")
        if not package_dir.is_dir():
            violations.append(
                f"{rel}: hook harness package "
                f"'meta/harness/{rule_path.stem.replace('-', '_')}/' does not exist"
            )
    else:
        # 검증 미구현 그릇은 통과가 아니라 거부 — 검증 없는 배포 선언 금지.
        violations.append(
            f"{rel}: deployment verification for enforce '{enforce}' is not "
            "implemented — implement it before using this vessel"
        )

    return violations


def check_rules(root: Path) -> list[str]:
    """meta/rules/ 전체를 검증하고 위반 목록을 돌려준다.

    Args:
        root: 저장소 루트.

    Returns:
        전 규칙 파일의 위반 메시지 목록. README.md는 규칙이 아니므로 제외한다.
    """
    rules_dir = root / "meta" / "rules"
    if not rules_dir.is_dir():
        return [f"rules directory not found: {rules_dir.relative_to(root)}"]

    violations: list[str] = []
    for rule_path in sorted(rules_dir.glob("*.md")):
        if rule_path.name == "README.md":
            continue
        violations.extend(check_rule_file(rule_path, root))
    return violations


def main() -> int:
    """체커를 실행하고 결과를 출력한다.

    Returns:
        위반이 없으면 0, 있으면 1.
    """
    root = find_repo_root()
    violations = check_rules(root)
    if violations:
        print(f"rules_checker: {len(violations)} violation(s) found")
        for violation in violations:
            print(f"  - {violation}")
        return 1
    print("rules_checker: all rules are deployed as declared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
