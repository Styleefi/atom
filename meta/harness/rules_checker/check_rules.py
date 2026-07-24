# meta/rules/ 규칙 파일의 frontmatter 스키마와 실제 배포 상태를 검증하는 체커
"""규칙 배포 일관성 체커.

meta/rules/ 아래의 모든 규칙 파일에 대해 다음을 검증한다.

1. frontmatter 존재 및 필수 필드(id, tier, enforce, deployed-to)
2. tier 값이 허용된 등급(principle | convention)인지,
   enforce 값이 허용된 그릇(claude-md | skill | hook)인지
3. id가 파일명(stem)과 일치하는지
4. deployed-to 대상 파일이 저장소에 실제 존재하는지
5. 실배포 확인 — claude-md 그릇: 대상 파일이 `@meta/rules/<파일명>` import를
   실제로 포함하는지. hook 그릇: deployed-to(settings JSON)가 규칙 id에서
   도출한 harness 모듈(`harness.<id의 -를 _로>`)을 참조하고 그 harness
   패키지가 실제 존재하는지. skill 그릇: deployed-to가 `.claude/skills/`
   아래의 SKILL.md이고 그 SKILL.md가 `meta/rules/<파일명>`을 참조하는지
   (규칙 본문의 SSOT는 meta/rules/, SKILL.md는 참조만 한다는 v1 규약).
   검증 로직이 없는 그릇은 통과가 아니라 **거부**한다(강화 사양).

규칙 단위 검사와 별개로 repo-level 검사를 둘 수행한다.

- 템플릿 동기화: root CLAUDE.md와 child 템플릿(meta/templates/CLAUDE.template.md)
  의 `@meta/rules/` import 집합이 동일한지 — 수동 동기화 지점의 침묵 드리프트를
  양방향으로 차단한다.
- 인벤토리 커버리지: 오너용 인터페이스 인벤토리(meta/README.md)의 두 표가
  실체와 일치하는지 — `## Rules` 표는 meta/rules/의 규칙 집합과, `## Functional
  artifacts` 표는 규칙이 뒷받침하지 않는 스킬/하니스/인프라 집합과 양방향으로
  비교한다. 검증 없는 인벤토리는 검증 없는 규칙과 같은 실패이므로, 인벤토리
  파일 자체의 부재도 위반으로 본다. 스킬 소유권만은 규칙의 deployed-to를 읽어야
  알 수 있어서, 그 값을 확보하지 못한 규칙이 있으면 스킬 판정만 보류하고 보류
  사실을 위반으로 남긴다.

경로는 실행 위치와 무관하게 이 파일의 고정 위치(meta/harness/rules_checker/)
로부터 역산한 저장소 루트 기준으로 해석하므로 로컬과 CI에서 결과가 동일하다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

# 허용되는 배포 그릇. 검증 로직이 구현된 그릇만 통과 대상이다.
VALID_ENFORCE = {"claude-md", "skill", "hook"}
VERIFIABLE_ENFORCE = {"claude-md", "skill", "hook"}

# 규칙 등급: principle(원칙 — 충돌 시 우선, 개정 문턱 높음) | convention(세칙).
VALID_TIER = {"principle", "convention"}

REQUIRED_FIELDS = ("id", "tier", "enforce", "deployed-to")

# CLAUDE.md/템플릿에서 규칙 import 줄을 뽑는 패턴.
IMPORT_RE = re.compile(r"@meta/rules/\S+\.md")

TEMPLATE_PATH = Path("meta") / "templates" / "CLAUDE.template.md"

# 오너용 인터페이스 인벤토리와 그 두 표의 헤딩(원문 그대로 매치한다).
INVENTORY_PATH = Path("meta") / "README.md"
RULES_HEADING = "## Rules"
ARTIFACTS_HEADING = "## Functional artifacts"

# 인벤토리 표에서 항목 이름을 뽑는 패턴. 첫 셀의 백틱 토큰만 잡도록 줄 시작에
# 앵커하며(뒤 컬럼의 `ATOM_*=1` 같은 토큰 배제), 표 정렬 패딩을 흡수한다.
INVENTORY_ROW_RE = re.compile(r"^\|\s*`([a-z0-9_-]+)`\s*\|", re.MULTILINE)

# 규칙이 아닌 아티팩트의 열거 루트. 각각 직계 자식만 본다.
SKILLS_DIR = Path(".claude") / "skills"
HARNESS_DIR = Path("meta") / "harness"
INFRA_DIR = Path("meta") / "infra"


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


def rule_files(root: Path) -> list[Path]:
    """meta/rules/ 아래의 규칙 파일을 정렬해 돌려준다.

    규칙이 아닌 README.md 제외 규칙을 여기 한 곳에 두어, 규칙 순회와 인벤토리
    커버리지 검사가 서로 다른 집합을 보게 되는 드리프트를 막는다.

    Args:
        root: 저장소 루트.

    Returns:
        규칙 파일 경로 목록. 디렉토리가 없으면 빈 목록.
    """
    rules_dir = root / "meta" / "rules"
    if not rules_dir.is_dir():
        return []
    return [path for path in sorted(rules_dir.glob("*.md")) if path.name != "README.md"]


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

    tier = data["tier"]
    if tier not in VALID_TIER:
        violations.append(
            f"{rel}: invalid tier value '{tier}' "
            f"(allowed: {', '.join(sorted(VALID_TIER))})"
        )
        return violations

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
    elif enforce == "skill":
        # skill 그릇 규약(v1): deployed-to는 .claude/skills/ 아래의 SKILL.md여야
        # 하고, 그 SKILL.md가 규칙 파일을 참조해야 실배포로 본다. 규칙 본문의
        # SSOT는 meta/rules/이고 SKILL.md는 참조만 한다(내용 드리프트 방지).
        deployed = Path(str(data["deployed-to"]))
        if deployed.parts[:2] != (".claude", "skills") or deployed.name != "SKILL.md":
            violations.append(
                f"{rel}: skill deployed-to '{data['deployed-to']}' must be a "
                "SKILL.md under .claude/skills/"
            )
            return violations
        reference = f"meta/rules/{rule_path.name}"
        if reference not in target.read_text(encoding="utf-8"):
            violations.append(
                f"{rel}: '{data['deployed-to']}' does not reference "
                f"'{reference}' — declared but not actually deployed"
            )
    else:
        # 검증 미구현 그릇은 통과가 아니라 거부 — 검증 없는 배포 선언 금지.
        violations.append(
            f"{rel}: deployment verification for enforce '{enforce}' is not "
            "implemented — implement it before using this vessel"
        )

    return violations


def check_template_sync(root: Path) -> list[str]:
    """root CLAUDE.md와 child 템플릿의 규칙 import 집합 동등성을 검증한다.

    템플릿의 INHERITED 블록은 root CLAUDE.md Rules 섹션의 수동 복제본이라
    체커 없이는 드리프트가 조용히 누적된다. 추가 누락(root에만 있는 import)과
    제거 잔류(템플릿에만 남은 낡은 import)를 양방향으로 잡는다.

    Args:
        root: 저장소 루트.

    Returns:
        위반 메시지 목록. 두 파일 중 하나라도 없으면 검사할 동기화 지점이
        없는 것이므로 빈 목록(개별 규칙의 deployed-to 검사가 부재를 잡는다).
    """
    claude_md = root / "CLAUDE.md"
    template = root / TEMPLATE_PATH
    if not claude_md.is_file() or not template.is_file():
        return []

    root_imports = set(IMPORT_RE.findall(claude_md.read_text(encoding="utf-8")))
    template_imports = set(IMPORT_RE.findall(template.read_text(encoding="utf-8")))

    violations: list[str] = []
    for missing in sorted(root_imports - template_imports):
        violations.append(
            f"{TEMPLATE_PATH}: missing '{missing}' present in root CLAUDE.md "
            "— sync the INHERITED FROM ATOM block"
        )
    for stale in sorted(template_imports - root_imports):
        violations.append(
            f"{TEMPLATE_PATH}: contains '{stale}' absent from root CLAUDE.md "
            "— remove the stale import from the INHERITED FROM ATOM block"
        )
    return violations


def _section_body(text: str, heading: str) -> str:
    """인벤토리 본문에서 헤딩 하나가 지배하는 구간을 잘라낸다.

    헤딩 판정은 부분 문자열이 아니라 줄 단위 정확 일치다. CRLF는 read_text의
    개행 정규화가 이미 걷어내므로, rstrip이 맡는 것은 헤딩 뒤 트레일링 공백뿐이다.
    구간은 헤딩 다음 줄부터 다음 `## ` 헤딩 직전 또는 파일 끝까지이며, 같은
    헤딩이 여러 번 나오면 첫 번째 것만 쓴다.

    Args:
        text: 인벤토리 파일 전체 내용.
        heading: 찾을 헤딩 줄 (예: "## Rules").

    Returns:
        구간 본문. 헤딩이 없으면 빈 문자열.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.rstrip() == heading:
            start = index + 1
            break
    if start is None:
        return ""

    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].rstrip().startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end])


def _child_dirs(path: Path) -> list[Path]:
    """직계 자식 디렉토리만 정렬해 돌려준다.

    재귀하지 않는 것이 핵심이다 — 하니스마다 tests/ 하위 패키지가 있어
    재귀하면 유령 아티팩트가 잡힌다.

    Args:
        path: 열거할 디렉토리.

    Returns:
        직계 자식 디렉토리 목록. 대상이 없으면 빈 목록.
    """
    if not path.is_dir():
        return []
    return sorted(child for child in path.iterdir() if child.is_dir())


def _expected_artifacts(root: Path) -> tuple[set[str], int]:
    """인벤토리에 실려야 하는 비규칙 아티팩트 이름을 모은다.

    스킬/하니스/인프라 세 루트를 직계 자식만 열거하고, 그중 규칙이 뒷받침하는
    것(규칙 표가 이미 커버하는 것)은 뺀다. 단일 분류 불변식이므로 규칙 기반
    아티팩트가 이 표에 실리면 stale로 잡힌다.

    세 루트의 frontmatter 의존도는 서로 다르다. 인프라는 규칙 개념이 없어 전혀
    의존하지 않고, 하니스 소유권은 규칙 파일명에서 도출되므로 frontmatter 없이도
    판정할 수 있다. 스킬 소유권만은 deployed-to를 읽어야 알 수 있어서, 그 값을
    확보하지 못한 규칙이 있으면 스킬 판정만 보류한다(호출자가 보류 사실을
    출력에 남긴다). 그릇을 확정할 수 없는 규칙은 하니스를 방어적으로 규칙
    소유로 보는데, 그러지 않으면 그 하니스가 '규칙 없는 아티팩트'로 오분류되어
    행을 추가하라는 잘못된 지시가 나온다(추가하면 규칙을 고치는 순간 stale이
    된다).

    Args:
        root: 저장소 루트.

    Returns:
        (기능성 아티팩트 이름 집합, deployed-to를 읽지 못한 규칙 수) 튜플.
    """
    skill_targets: set[str] = set()
    harness_claims: set[str] = set()
    unreadable_rules = 0
    for rule_path in rule_files(root):
        data, _ = parse_frontmatter(rule_path.read_text(encoding="utf-8"))
        enforce = data.get("enforce") if data else None
        deployed = str(data.get("deployed-to") or "").strip() if data else ""

        if enforce == "hook" or enforce is None:
            harness_claims.add(rule_path.stem.replace("-", "_"))
        if deployed:
            skill_targets.add(deployed)
        else:
            unreadable_rules += 1

    artifacts: set[str] = set()
    for skill_dir in _child_dirs(root / SKILLS_DIR):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        if f".claude/skills/{skill_dir.name}/SKILL.md" in skill_targets:
            continue
        artifacts.add(skill_dir.name)
    for harness_dir in _child_dirs(root / HARNESS_DIR):
        if not (harness_dir / "__init__.py").is_file():
            continue
        if harness_dir.name in harness_claims:
            continue
        artifacts.add(harness_dir.name)
    for infra_dir in _child_dirs(root / INFRA_DIR):
        artifacts.add(infra_dir.name)
    return artifacts, unreadable_rules


def _diff_section(heading: str, expected: set[str], listed: set[str]) -> list[str]:
    """한 표의 기대 집합과 등재 집합을 양방향으로 비교한다.

    Args:
        heading: 대상 표의 헤딩 (위반 메시지에 그대로 실린다).
        expected: 실체에서 뽑은 기대 이름 집합.
        listed: 인벤토리 표에서 뽑은 등재 이름 집합.

    Returns:
        위반 메시지 목록. 출력 결정성을 위해 이름순으로 정렬한다.
    """
    violations: list[str] = []
    for missing in sorted(expected - listed):
        violations.append(
            f"{INVENTORY_PATH}: '{missing}' is missing from the '{heading}' "
            "section — add a row for it"
        )
    for stale in sorted(listed - expected):
        violations.append(
            f"{INVENTORY_PATH}: '{heading}' section lists '{stale}', which does "
            "not exist — remove the stale row"
        )
    return violations


def check_inventory(root: Path) -> list[str]:
    """오너용 인터페이스 인벤토리가 실체를 빠짐없이 반영하는지 검증한다.

    인벤토리(meta/README.md)의 두 표를 각각의 실체 집합과 양방향으로 비교한다.
    v1 시맨틱은 이름 존재 여부만이며, 나머지 컬럼(등급·관여 방식·인터페이스
    설명)은 사람이 유지한다.

    Args:
        root: 저장소 루트.

    Returns:
        위반 메시지 목록. 인벤토리 파일이 없으면 그 자체가 위반이다 —
        템플릿 동기화와 달리 부재를 잡아줄 다른 검사가 없기 때문이다.
        deployed-to를 읽지 못한 규칙이 있으면 스킬 항목의 판정만 보류하고 그
        사실을 위반 목록에 남긴다 — 규칙 표·하니스·인프라 판정은 frontmatter에
        의존하지 않으므로 그대로 수행한다.
    """
    inventory = root / INVENTORY_PATH
    if not inventory.is_file():
        return [
            f"{INVENTORY_PATH}: owner-facing interface inventory is missing "
            "— restore it; every meta-layer artifact must be listed there"
        ]

    text = inventory.read_text(encoding="utf-8")
    listed_rules = set(INVENTORY_ROW_RE.findall(_section_body(text, RULES_HEADING)))
    listed_artifacts = set(
        INVENTORY_ROW_RE.findall(_section_body(text, ARTIFACTS_HEADING))
    )

    violations = _diff_section(
        RULES_HEADING, {path.stem for path in rule_files(root)}, listed_rules
    )
    expected_artifacts, unreadable_rules = _expected_artifacts(root)
    on_disk_skills = {skill.name for skill in _child_dirs(root / SKILLS_DIR)}
    if unreadable_rules and on_disk_skills:
        # 스킬 소유권을 확정할 수 없으므로 디스크에 있는 스킬의 판정만 뺀다.
        # 실체가 없는 스킬 행(stale)은 frontmatter와 무관하므로 계속 잡는다.
        # 보류할 스킬이 실제로 있을 때만 알린다 — 보류한 것이 없는데 알리면
        # 이미 보고된 규칙 위반에 잡음만 얹는다.
        expected_artifacts -= on_disk_skills
        listed_artifacts -= on_disk_skills
        violations.append(
            f"{INVENTORY_PATH}: skill coverage of '{ARTIFACTS_HEADING}' was not "
            f"verified — {unreadable_rules} rule(s) have no readable "
            "'deployed-to'; fix those and re-run"
        )
    violations.extend(
        _diff_section(ARTIFACTS_HEADING, expected_artifacts, listed_artifacts)
    )
    return violations


def check_rules(root: Path) -> list[str]:
    """meta/rules/ 전체와 repo-level 동기화를 검증하고 위반 목록을 돌려준다.

    Args:
        root: 저장소 루트.

    Returns:
        전 규칙 파일 + 템플릿 동기화 + 인벤토리 커버리지의 위반 메시지 목록.
        README.md는 규칙이 아니므로 제외한다.
    """
    rules_dir = root / "meta" / "rules"
    if not rules_dir.is_dir():
        return [f"rules directory not found: {rules_dir.relative_to(root)}"]

    violations: list[str] = []
    for rule_path in rule_files(root):
        violations.extend(check_rule_file(rule_path, root))
    violations.extend(check_template_sync(root))
    violations.extend(check_inventory(root))
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
