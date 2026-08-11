# meta/rules/ 규칙 파일의 frontmatter 스키마와 실제 배포 상태를 검증하는 체커
"""규칙 배포 일관성 체커.

meta/rules/ 아래의 모든 규칙 파일에 대해 다음을 검증한다.

1. frontmatter 존재 및 필수 필드(id, tier, enforce, deployed-to)
2. tier 값이 허용된 등급(principle | convention)인지,
   enforce 값이 허용된 그릇(claude-md | skill | hook)인지
3. id가 파일명(stem)과 일치하는지
4. deployed-to가 저장소 내 상대 경로이고 대상 파일이 실제 존재하는지
5. 실배포 확인 — claude-md 그릇: deployed-to가 정확히 `CLAUDE.md`(루트 —
   유일한 claude-md vessel, raw 문자열 비교라 './CLAUDE.md' 같은 동치 표기도
   거부)이고, 그 파일이 `@meta/rules/<파일명>` import를 **활성 독립 줄**로
   포함하는지(#38 — 주석/펜스 속 import는 로드되지 않으므로 배포가 아니다,
   _active_lines 동결 스캐너 참조). hook 그릇(v2): deployed-to(settings JSON)의 hooks 구조
   안에서 규칙 id에서 도출한 harness 모듈(`harness.<id의 -를 _로>`)을 `-m`으로
   참조하는 커맨드가 1개 이상이고, 참조하는 모든 커맨드가 `blocking`
   frontmatter가 고르는 정본 래퍼 템플릿과 정확히 일치하며, 그 harness
   패키지가 실제 존재하고 `__init__.py`·`__main__.py`를 갖췄는지(#31 —
   uv 자체 오류의 exit 2가 차단으로 새지 않는 배선 강제, #38. 두 파일
   요구의 근거는 검사 지점 주석이 SSOT). skill 그릇:
   deployed-to가 `.claude/skills/` 아래의 SKILL.md이고 그 SKILL.md가
   `meta/rules/<파일명>`을 참조하는지 (규칙 본문의 SSOT는 meta/rules/,
   SKILL.md는 참조만 한다는 v1 규약).
   검증 로직이 없는 그릇은 통과가 아니라 **거부**한다(강화 사양).

규칙 단위 검사와 별개로 repo-level 검사를 셋 수행한다.

- 템플릿 동기화: root CLAUDE.md와 child 템플릿(meta/templates/CLAUDE.template.md)
  이 실존하고(부재는 위반 — 템플릿은 이 검사가 유일한 감시자다, #38) 두 파일의
  **활성** `@meta/rules/` import 집합이 동일한지 — 수동 동기화 지점의 침묵
  드리프트를 양방향으로 차단한다. skill 참조 검사도 같은 스캐너의 활성
  텍스트에서만 substring을 본다. 활성 import가 규칙 레지스트리(meta/rules/)의
  실제 규칙과 대응하는지도 파일별로 확인한다(#91 — 지워진 규칙의 고아
  import가 양쪽에 남는 침묵 채널. 파일 실존이 아니라 레지스트리 대조라
  `..` 관통·비규칙 파일 대상도 고아다).
- hook 배선 역방향 스윕: 프로젝트 설정 파일(.claude/settings*.json — hook
  규칙이 없어도 무조건)과 hook 규칙들의 deployed-to에 있는 모든 훅 커맨드 중
  `-m harness.*`를 참조하는 것이 두 정본 래퍼 템플릿 중 하나와 정확히
  일치하는지 — 규칙 파일 없이 추가된 구식 배선(#31의 exec 패턴)의 재발을 막는다.
- 인벤토리 커버리지: 오너용 인터페이스 인벤토리(meta/README.md)의 두 표가
  실체와 일치하는지 — `## Rules` 표는 meta/rules/의 규칙 집합과, `## Functional
  artifacts` 표는 규칙이 뒷받침하지 않는 스킬/하니스/인프라 집합과 양방향으로
  비교한다. 검증 없는 인벤토리는 검증 없는 규칙과 같은 실패이므로, 인벤토리
  파일 자체의 부재도 위반으로 본다. 다만 아티팩트 분류가 규칙 frontmatter에서
  파생되므로, 규칙 위반이 하나라도 있으면 이 검사는 통째로 미루고 미룬 사실을
  위반으로 남긴다 — 깨진 레지스트리 위의 분류는 틀린 지시를 낳기 때문이다.

경로는 실행 위치와 무관하게 이 파일의 고정 위치(meta/harness/rules_checker/)
로부터 역산한 저장소 루트 기준으로 해석하므로 로컬과 CI에서 결과가 동일하다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePosixPath

import yaml

# 허용되는 배포 그릇. 검증 미구현 그릇이 여기 추가되면 check_rule_file의
# else 분기가 통과 대신 거부한다 — 그 분기가 "검증 없는 vessel 금지"의
# 실제 가드다(#38에서 동일 내용의 죽은 상수 VERIFIABLE_ENFORCE 제거).
VALID_ENFORCE = {"claude-md", "skill", "hook"}

# 규칙 등급: principle(원칙 — 충돌 시 우선, 개정 문턱 높음) | convention(세칙).
VALID_TIER = {"principle", "convention"}

REQUIRED_FIELDS = ("id", "tier", "enforce", "deployed-to")
# `blocking`(hook 전용, bool)은 REQUIRED_FIELDS에 넣지 않는다 — 누락 검사가
# `not data.get(f)`라서 정당한 `blocking: false`를 누락으로 오탐한다.

# hook 커맨드 정본 템플릿(#31). settings JSON의 harness 훅 커맨드는 아래 두
# 형태 중 하나와 바이트 단위로 일치해야 한다. 차단형: uv run을 if 조건으로
# 감싸(훅 실행 셸이 set -e여도 동작 동일 — rc=$? 단독 패턴은 -e에서 uv 실패
# 시점에 셸이 uv 코드로 종료해 누출이 재발한다) 가드의 sentinel 42만 차단(2)
# 으로 되매핑하고, 그 외 nonzero(uv 자체 오류 2 포함)는 전부 1(비차단 경고)로
# 수렴한다. 비차단형: 어떤 실패도 1로 수렴해 구조적으로 차단이 불가능하다.
# {module} 치환은 str.format이 아니라 .replace를 쓴다 — 템플릿에 셸 ${VAR}
# 표기가 들어와도 안전하도록. 래퍼의 실동작은 셸 계약 테스트가 /bin/sh로 핀.
HOOK_COMMAND_BLOCKING = (
    'if command -v uv >/dev/null 2>&1; then '
    'if uv run --directory "$CLAUDE_PROJECT_DIR/meta" python -m {module}; '
    'then :; else rc=$?; if [ "$rc" -eq 42 ]; then exit 2; else exit 1; fi; fi; fi'
)
HOOK_COMMAND_NON_BLOCKING = (
    'if command -v uv >/dev/null 2>&1; then '
    'uv run --directory "$CLAUDE_PROJECT_DIR/meta" python -m {module} || exit 1; fi'
)

# 훅 커맨드에서 `-m` 플래그로 실행되는 harness 모듈을 뽑는 패턴. 독립 `-m`만
# 인정해(좌측 (?<!\S)) `--m`·`run-m` 같은 내부 매치를 배제하고, 인터프리터
# 표기(python/python3/uv run)와 무관하게 변형 배선을 잡는다. `\s*`는 붙여쓰기
# (`-mharness.x` — 유효한 인터프리터 호출)까지 커버. 점 포함 캡처로 하위모듈
# 진입점(harness.a.b)도 온전히 뽑는다. 한계: 따옴표로 감싼 모듈명
# (`-m "harness.x"`)은 미감지 — ruled hook이면 "not referenced" 위반으로
# 표면화되고, unruled는 bash -c 간접 실행과 같은 기존 잔여 클래스.
_HOOK_MODULE_RE = re.compile(r"(?<!\S)-m\s*(harness\.\w+(?:\.\w+)*)")

# 규칙 import 토큰의 형태. _import_lines가 활성 줄에 fullmatch로 적용한다 —
# findall substring이 아니라 줄 전체 일치라, 인라인 언급·주석/펜스 속 토큰은
# import로 인정되지 않는다(#38).
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


class _StrictLoader(yaml.SafeLoader):
    """중복 매핑 키를 오류로 거부하는 SafeLoader(#38).

    yaml.safe_load의 last-win 시맨틱은 `enforce: hook` 뒤의
    `enforce: claude-md`를 조용히 claude-md로 검증한다 — 파일을 읽는 사람이
    보는 선언과 checker의 판정이 어긋나는 침묵 통과. 중복 검사는
    construct_object 호출 없이 스칼라 key 노드의 raw 값 비교로(노드 이중
    평가 배제 — frontmatter 키는 항상 평문 스칼라), flatten_mapping이
    node.value를 변형하기 전인 super() 호출 앞에서 수행한다. merge key
    (`<<`)로 병합돼 들어온 키와 명시 키의 충돌은 미감지 — 문서화된 한계.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        seen: set[str] = set()
        for key_node, _value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                if key_node.value in seen:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"found duplicate key {key_node.value!r}",
                        key_node.start_mark,
                    )
                seen.add(key_node.value)
        return super().construct_mapping(node, deep)


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
        data = yaml.load(text[4:end], Loader=_StrictLoader)
    except yaml.YAMLError as exc:
        return None, f"invalid YAML in frontmatter: {exc}"
    if not isinstance(data, dict):
        return None, "frontmatter is not a mapping (key: value)"
    return data, None


def _strip_comment_spans(line: str) -> tuple[str, bool]:
    """한 줄에서 같은 줄 개폐 HTML 주석 스팬을 공백 한 칸으로 치환한다.

    빈 문자열 치환은 스팬 앞뒤 토큰을 융합시켜(`@meta/rules/a<!-- -->b.md`)
    없던 import를 만들어내므로 금지 — 공백 치환은 위반 쪽으로 넘어지는
    fail-safe다. 닫히지 않은 `<!--`는 그 위치부터 줄 끝을 잘라낸다.

    Args:
        line: 처리할 한 줄(활성 문맥).

    Returns:
        (치환된 줄, 미종결 주석이 열렸는지) 튜플.
    """
    out = line
    while True:
        start = out.find("<!--")
        if start == -1:
            return out, False
        end = out.find("-->", start + 4)
        if end == -1:
            return out[:start], True
        out = out[:start] + " " + out[end + 3 :]


def _active_lines(text: str) -> list[str]:
    """checker가 배포로 인정하는 '활성' 줄만 남기는 동결 스캐너.

    정답 기준은 Claude Code 로더의 근사가 아니라 checker가 규정하는 유효
    배포 형태다(#38). 모델링하는 문법: ```/~~~ 코드 펜스(선행 공백·info
    string 허용, 같은 마커끼리만 닫힘)와 HTML 주석(여러 줄 가능). 상호작용
    규칙은 하나 — 먼저 열린 쪽이 자기 닫힘까지 소유한다(펜스 안 `<!--`는
    리터럴, 주석 안 펜스 마커는 비활성). 미종결 펜스·주석은 EOF까지
    비활성(fail-safe). 여러 줄 주석의 닫는 줄 잔여 텍스트는 활성이지만
    선행 공백을 붙여 독립 import 줄로는 인정되지 않게 한다.

    모델 밖(문서화된 한계 — #66/#75式 문법 확장 경쟁을 막기 위해 동결):
    인라인 코드 스팬, 인용구/리스트 속 펜스, 펜스 마커 길이 매칭. 들여쓰기
    코드 블록은 import 검사에서는 _import_lines의 선행 공백 불허가 함께
    닫지만, skill substring 검사에는 잔존한다.

    Args:
        text: 대상 파일 전체 내용.

    Returns:
        활성 줄 목록(주석 스팬은 공백 치환된 상태).
    """
    active: list[str] = []
    state: str | None = None  # None | "```" | "~~~" | "comment"
    for line in text.splitlines():
        if state in ("```", "~~~"):
            if line.lstrip().startswith(state):
                state = None
            continue
        if state == "comment":
            end = line.find("-->")
            if end == -1:
                continue
            state = None
            rest, opened = _strip_comment_spans(line[end + 3 :])
            if opened:
                state = "comment"
            active.append(" " + rest)
            continue
        marker = line.lstrip()[:3]
        if marker in ("```", "~~~"):
            state = marker
            continue
        processed, opened = _strip_comment_spans(line)
        if opened:
            state = "comment"
        active.append(processed)
    return active


def _import_lines(text: str) -> set[str]:
    """활성 줄 중 독립 `@meta/rules/<file>.md` import 줄의 집합을 뽑는다.

    선행 공백 불허(rstrip만 허용) — 들여쓰기 코드 블록 속 import가 인정되는
    fail-open을 스캐너 확장 없이 닫는다. 무공백 연접(`...a.md@meta/...b.md`)
    은 IMPORT_RE fullmatch를 통과하지만 융합 토큰이라 어떤 정확 import와도
    불일치 — 검사가 위반 쪽으로 수렴한다.

    Args:
        text: 대상 파일 전체 내용.

    Returns:
        활성 독립 import 줄의 집합.
    """
    imports: set[str] = set()
    for line in _active_lines(text):
        candidate = line.rstrip()
        if IMPORT_RE.fullmatch(candidate):
            imports.add(candidate)
    return imports


def _load_settings(path: Path) -> tuple[dict | None, str | None]:
    """settings JSON 파일을 읽어 (최상위 객체, 문제 구절)로 돌려준다.

    per-rule 검사와 역방향 스윕이 같은 실패 분류를 쓰게 하는 단일 로더다
    (#42 — 같은 깨진 파일에 두 경로가 서로 다른 정밀도의 진단을 내던 중복).
    실패 구절은 위반 메시지에 그대로 끼워 넣는 형태(주어 없는 서술)다.
    UnicodeDecodeError는 ValueError의 하위라 반드시 먼저 잡는다 — JSON
    문법이 멀쩡한 UTF-16 파일에 "is not valid JSON"이라고 답하지 않기 위함.
    비객체(배열·null 등)는 파싱 성공이어도 문제로 분류한다 — 파싱 결과의
    None을 실패 sentinel로 겸용하면 JSON 리터럴 null이 무위반 통과한다
    (최종 게이트 리뷰: 침묵 통과 회귀).

    Args:
        path: settings JSON 파일 경로.

    Returns:
        (파싱된 dict, None) 또는 (None, 문제 구절) 튜플.
    """
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, "cannot be read"
    except UnicodeDecodeError:
        return None, "is not valid UTF-8"
    except ValueError:
        return None, "is not valid JSON"
    if not isinstance(parsed, dict):
        return None, "is not a JSON object"
    return parsed, None


def _hook_commands(settings: dict) -> list[str]:
    """settings JSON의 hooks 구조에서 커맨드 문자열을 전부 뽑는다.

    구조가 어긋난 노드는 조용히 건너뛴다 — 이 함수는 수집기일 뿐이고,
    그 결과 빠진 참조는 상위 검사가 "not referenced" 위반으로 표면화한다.

    Args:
        settings: 파싱된 settings JSON 최상위 객체.

    Returns:
        모든 이벤트에 걸친 훅 커맨드 문자열 목록.
    """
    commands: list[str] = []
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return commands
    for event_entries in hooks.values():
        if not isinstance(event_entries, list):
            continue
        for entry in event_entries:
            if not isinstance(entry, dict):
                continue
            entry_hooks = entry.get("hooks")
            if not isinstance(entry_hooks, list):
                continue
            for hook in entry_hooks:
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    commands.append(hook["command"])
    return commands


def _references_module(command: str, module: str) -> bool:
    """커맨드가 해당 harness 모듈을 `-m`으로 실행하는지 판정한다.

    `python -m` 리터럴이 아니라 독립 `-m` 플래그에 앵커해 python3·공백 변형·
    붙여쓰기(`-mharness.x`)·`uv run -m` 배선도 잡는다. 우측 lookahead로
    harness.foo가 harness.foo_v2나 하위모듈(harness.foo.cli)에 오매치되는
    것을 막는다.

    Args:
        command: 훅 커맨드 문자열.
        module: `harness.<이름>` 형태의 모듈명.

    Returns:
        참조하면 True.
    """
    pattern = r"(?<!\S)-m\s*" + re.escape(module) + r"(?![\w.])"
    return re.search(pattern, command) is not None


def _skill_path_shape_violations(rel: Path, deployed_to: str) -> list[str]:
    """skill 그릇 deployed-to의 경로 형태 위반을 돌려준다(문자열 검사).

    파일 존재·경로 유효성과 무관한 검사라 check_rule_file이 존재 검사 앞
    단일 지점에서 호출한다 — bad_path/missing-target 어느 조기 return도
    형태 위반을 가리지 못하게. 다른 곳에서 재호출하면 이중 보고가 된다.

    깊이는 정확히 4파트(.claude/skills/<이름>/SKILL.md) — Claude Code가
    skill로 인식하는 유일한 위치라 더 얕거나 깊은 SKILL.md는 죽은 배포다
    (#38). Path parts 비교라 './' 접두 같은 동치 표기는 정규화되어 통과한다
    — claude-md pin의 raw 문자열 비교와 의도적 비대칭(그쪽 표기는 여기서
    기존 테스트가 정규화 수용을 핀하고 있다).
    """
    deployed = Path(deployed_to)
    if (
        deployed.parts[:2] != (".claude", "skills")
        or deployed.name != "SKILL.md"
        or len(deployed.parts) != 4
    ):
        return [
            f"{rel}: skill deployed-to '{deployed_to}' must be a "
            "SKILL.md under .claude/skills/"
        ]
    return []


def _resolve_deploy_target(root: Path, rel: Path, data: dict) -> tuple[Path, str | None]:
    """deployed-to를 대상 경로로 해석하고 부재 여부를 판정한다.

    hook 분기와 일반 분기가 같은 해석·부재 메시지를 복붙하던 것을 단일화한다
    (#42). 부재 시의 제어 흐름(위반 누적 후 의존 검사만 skip vs 조기 return)은
    호출 지점마다 다르게 핀돼 있으므로 여기서는 값 도출만 맡는다.

    Args:
        root: 저장소 루트.
        rel: 위반 메시지에 앞세울 규칙 파일의 상대 경로.
        data: 파싱된 frontmatter.

    Returns:
        (대상 경로, 부재 위반 메시지 or None) 튜플.
    """
    target = root / str(data["deployed-to"])
    if not target.is_file():
        return target, (
            f"{rel}: deployed-to target '{data['deployed-to']}' does not exist"
        )
    return target, None


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

    # isinstance 선행 — 비문자열(비해시형 dict/list 포함)이 `in` 멤버십에서
    # TypeError로 새지 않고 기존 invalid-value 메시지로 보고되게 한다(#43).
    tier = data["tier"]
    if not isinstance(tier, str) or tier not in VALID_TIER:
        violations.append(
            f"{rel}: invalid tier value '{tier}' "
            f"(allowed: {', '.join(sorted(VALID_TIER))})"
        )
        return violations

    enforce = data["enforce"]
    if not isinstance(enforce, str) or enforce not in VALID_ENFORCE:
        violations.append(
            f"{rel}: invalid enforce value '{enforce}' "
            f"(allowed: {', '.join(sorted(VALID_ENFORCE))})"
        )
        return violations

    # deployed-to는 저장소 내 상대 경로여야 한다. 절대경로/..는 root와의 join을
    # 통째로 대체하거나 검사 대상을 저장소 밖으로 보낸다(#40 리뷰). 위반이어도
    # 진행한다 — 경로와 무관한 검사(blocking 스키마, hook 패키지 실존)는 여전히
    # 수행 가능하며, 조기 return은 같은 규칙의 다른 결함을 가린다.
    deployed = PurePosixPath(str(data["deployed-to"]))
    bad_path = deployed.is_absolute() or ".." in deployed.parts
    if bad_path:
        violations.append(
            f"{rel}: deployed-to '{data['deployed-to']}' must be a "
            "repo-root-relative path inside the repository"
        )

    # blocking 스키마(hook 전용). 위반이어도 계속 진행한다 — 존재·참조·패키지
    # 검사는 blocking과 무관하고, 템플릿 비교만 유효한 bool을 요구하므로 그
    # 비교를 건너뛰면 된다(조기 return은 같은 규칙의 다른 결함을 가린다).
    if enforce == "hook":
        if "blocking" not in data:
            violations.append(
                f"{rel}: hook rule must declare 'blocking: true | false' "
                "(selects the canonical wrapper template)"
            )
        elif not isinstance(data["blocking"], bool):
            violations.append(
                f"{rel}: 'blocking' must be a boolean, got {data['blocking']!r}"
            )
    elif "blocking" in data:
        violations.append(
            f"{rel}: 'blocking' is only valid for hook rules (enforce: hook)"
        )

    if enforce == "hook":
        # hook 그릇 규약(v2): 규칙 id에서 harness 모듈명을 도출해
        # (1) 대상 settings JSON의 hooks 구조 안에 그 모듈을 `-m`으로 참조하는
        #     커맨드가 1개 이상 있고(복수 matcher/이벤트 배선 허용),
        # (2) 참조하는 모든 커맨드가 blocking 여부가 고르는 정본 래퍼 템플릿과
        #     정확히 일치하며(#31 — uv 자체 exit 2가 차단으로 새는 배선 차단),
        # (3) meta/harness/ 아래에 해당 패키지가 실존해야 실배포로 본다.
        # 이 분기는 조기 return 없이 위반을 끝까지 수집한다(#40 리뷰 2R: 새로
        # 추가한 조기 return들이 blocking에서 제거했던 가림 패턴을 재도입 —
        # 사례가 아니라 구조로 닫는다). 경로/대상/JSON 문제는 각자 보고하고
        # 의존 검사만 건너뛰며, 패키지 실존 검사는 항상 수행한다.
        # 한계: 이벤트/matcher 위치까지는 보지 않는다 — 차단형 템플릿이
        # UserPromptSubmit 아래에 있어도 통과한다(기계 검증은 범위 밖 결정).
        # id→패키지 매핑은 여기서 한 번만 도출한다 — 모듈 참조 검사와 패키지
        # 실존 검사가 독립 도출로 어긋날 수 있던 이중화 제거(#42).
        package_name = rule_path.stem.replace("-", "_")
        module_name = f"harness.{package_name}"
        settings: dict | None = None
        if not bad_path:
            target, missing = _resolve_deploy_target(root, rel, data)
            if missing is not None:
                violations.append(missing)
            else:
                settings, problem = _load_settings(target)
                if problem is not None:
                    violations.append(
                        f"{rel}: deployed-to target '{data['deployed-to']}' {problem}"
                    )
        if settings is not None:
            commands = _hook_commands(settings)
            matching = [c for c in commands if _references_module(c, module_name)]
            if not matching:
                violations.append(
                    f"{rel}: '{data['deployed-to']}' does not reference the "
                    f"'{module_name}' hook module — declared but not actually deployed"
                )
            elif isinstance(data.get("blocking"), bool):
                # blocking이 무효(부재/비bool)면 위에서 이미 위반 — 템플릿
                # 선택이 불가능하므로 형태 비교만 건너뛴다.
                template = (
                    HOOK_COMMAND_BLOCKING
                    if data["blocking"]
                    else HOOK_COMMAND_NON_BLOCKING
                )
                expected = template.replace("{module}", module_name)
                shape = "blocking" if data["blocking"] else "non-blocking"
                for command in matching:
                    if command != expected:
                        violations.append(
                            f"{rel}: hook command for '{module_name}' does not "
                            f"match the canonical {shape} wrapper (#31 fail-open "
                            f"wiring) — got: {command} — expected exactly: {expected}"
                        )
        package_dir = root / "meta" / "harness" / package_name
        if not package_dir.is_dir():
            violations.append(
                f"{rel}: hook harness package "
                f"'meta/harness/{package_name}/' does not exist"
            )
        else:
            # 두 파일 요구의 근거 서술은 여기가 SSOT다(#38; 정리 경위는
            # PR #92 리뷰 스레드). __main__.py 부재는 `python -m` 진입점
            # 부재 — 훅이 래퍼 아래에서 조용히 no-op이 된다(셸 실험으로
            # 검증). __init__.py는 실행 요건이 아니라(namespace 패키지도
            # -m으로 돌아가고 meta/harness 자체가 __init__.py 없이 돈다) 모든
            # meta/harness 패키지를 regular package로 통일하는 메타층
            # 규약이다 — 인벤토리 분류(_expected_artifacts)나 pytest prepend
            # 모드 모듈 네이밍 같은 소비자들이 이 규약 위에 서 있다. 파일
            # 실존만 본다(빈 파일 통과는 문서화된 한계 — 임포트 오류는
            # pytest 몫). 디렉토리 부재 시엔 위 위반 하나로 충분해 파일
            # 검사를 걸지 않는다.
            if not (package_dir / "__init__.py").is_file():
                violations.append(
                    f"{rel}: hook harness package "
                    f"'meta/harness/{package_name}/' is missing __init__.py "
                    "— required by the meta-layer package convention"
                )
            if not (package_dir / "__main__.py").is_file():
                violations.append(
                    f"{rel}: hook harness package "
                    f"'meta/harness/{package_name}/' is missing __main__.py "
                    "— the `python -m` entry point"
                )
        return violations

    # claude-md 그릇의 대상 고정(#38)은 raw 문자열 검사라 파일 존재·경로
    # 유효성과 무관하게 항상 여기서 수행한다 — 정규화 후 비교가 아니므로
    # './CLAUDE.md' 같은 동치 표기도 거부한다(skill 깊이 검사의 parts 비교와
    # 의도적 비대칭: 그쪽은 기존 테스트가 정규화 수용을 핀하고 있다).
    claude_md_pinned = True
    if enforce == "claude-md" and str(data["deployed-to"]) != "CLAUDE.md":
        claude_md_pinned = False
        violations.append(
            f"{rel}: claude-md deployed-to '{data['deployed-to']}' must be "
            "exactly 'CLAUDE.md' — the root CLAUDE.md is the only claude-md vessel"
        )

    # skill 그릇의 SKILL.md 경로 형태는 문자열 검사라 파일 존재·경로 유효성과
    # 무관하게 항상 여기서 수행한다 — bad_path든 missing-target이든 조기
    # return이 형태 위반을 가리지 않게(탈출 관찰 라운드: bad_path만 고치고
    # missing-target 경로에 같은 가림이 남아 있었다).
    skill_shape_violations: list[str] = []
    if enforce == "skill":
        skill_shape_violations = _skill_path_shape_violations(
            rel, str(data["deployed-to"])
        )
        violations.extend(skill_shape_violations)

    if bad_path:
        return violations

    target, missing = _resolve_deploy_target(root, rel, data)
    if missing is not None:
        violations.append(missing)
        return violations

    if enforce == "claude-md":
        # claude-md 그릇: @import 줄의 존재가 곧 실배포다 (매 세션 자동 로드).
        # pin 위반이면 대상이 vessel이 아니므로 import 검사는 무의미 — skill
        # 형태 위반의 참조 검사 억제와 대칭(존재 검사는 위에서 이미 수행됨).
        if not claude_md_pinned:
            return violations
        import_line = f"@meta/rules/{rule_path.name}"
        if import_line not in _import_lines(target.read_text(encoding="utf-8")):
            violations.append(
                f"{rel}: '{data['deployed-to']}' does not contain the "
                f"'{import_line}' import as an active standalone line "
                "— declared but not actually deployed"
            )
    elif enforce == "skill":
        # skill 그릇 규약(v1): deployed-to는 .claude/skills/ 아래의 SKILL.md여야
        # 하고, 그 SKILL.md가 규칙 파일을 참조해야 실배포로 본다. 규칙 본문의
        # SSOT는 meta/rules/이고 SKILL.md는 참조만 한다(내용 드리프트 방지).
        # 경로 형태 검사는 위(존재 검사 전)에서 수행됨 — 형태가 틀리면 참조
        # 검사는 무의미하므로 여기서 종료.
        if skill_shape_violations:
            return violations
        reference = f"meta/rules/{rule_path.name}"
        active_text = "\n".join(_active_lines(target.read_text(encoding="utf-8")))
        if reference not in active_text:
            violations.append(
                f"{rel}: '{data['deployed-to']}' does not reference "
                f"'{reference}' in active text — declared but not actually deployed"
            )
    else:
        # 검증 미구현 그릇은 통과가 아니라 거부 — 검증 없는 배포 선언 금지.
        violations.append(
            f"{rel}: deployment verification for enforce '{enforce}' is not "
            "implemented — implement it before using this vessel"
        )

    return violations


def check_template_sync(root: Path) -> list[str]:
    """root CLAUDE.md·child 템플릿의 실존과 규칙 import 집합 동등성을 검증한다.

    템플릿의 INHERITED 블록은 root CLAUDE.md Rules 섹션의 수동 복제본이라
    체커 없이는 드리프트가 조용히 누적된다. 추가 누락(root에만 있는 import)과
    제거 잔류(템플릿에만 남은 낡은 import)를 양방향으로 잡는다.

    두 파일 중 하나라도 없으면 그 자체가 위반이다(#38) — 템플릿은 이 검사가
    유일한 감시자고(어떤 규칙도 deployed-to로 선언하지 않는다), 루트
    CLAUDE.md의 per-rule backstop은 claude-md 규칙이 1개 이상일 때만 성립하는
    조건부 보장이라 여기서도 직접 보고한다(건강한 대상의 이중 보고 수용 패턴).

    범위 밖(오너 수용 트레이드오프, PR #92 R1): 비활성 텍스트(주석·펜스) 속
    import 잔해는 검사하지 않는다 — 주석 잔해는 배포 주장이 아니고, 비활성
    텍스트 스캔은 활성/비활성 시맨틱과 어긋나며 정당한 규칙 언급까지 위반으로
    만든다. 잔해 정리는 PR 리뷰의 몫.

    Args:
        root: 저장소 루트.

    Returns:
        위반 메시지 목록. 파일 부재 시 부재 위반만 담는다(비교 불가).
    """
    claude_md = root / "CLAUDE.md"
    template = root / TEMPLATE_PATH
    missing_files = [path for path in (claude_md, template) if not path.is_file()]
    if missing_files:
        return [
            f"{path.relative_to(root)}: template sync target is missing — "
            "restore it; the rule import lists cannot be compared"
            for path in missing_files
        ]

    root_imports = _import_lines(claude_md.read_text(encoding="utf-8"))
    template_imports = _import_lines(template.read_text(encoding="utf-8"))

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

    # 고아 import 역방향 스윕(#91): 규칙 파일이 지워졌는데 import가 양쪽에
    # 남으면 규칙 순회도 sync 비교도 침묵한다 — 각 import를 규칙 레지스트리
    # (rule_files)와 대조한다(check_hook_wiring과 같은 역방향 패턴). 파일
    # 실존 검사가 아니라 집합 대조인 이유(R1 리뷰): `..` 관통 경로나
    # README.md처럼 "실존하지만 규칙이 아닌" 대상이 통과하면 #91의 구멍이
    # 그대로 다시 열린다.
    valid_imports = {f"@meta/rules/{path.name}" for path in rule_files(root)}
    for source, imports in ((claude_md, root_imports), (template, template_imports)):
        source_rel = source.relative_to(root)
        for imported in sorted(imports - valid_imports):
            violations.append(
                f"{source_rel}: orphan rule import '{imported}' — no such rule "
                "in meta/rules/; remove the import or restore the rule"
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


def _expected_artifacts(root: Path) -> set[str]:
    """인벤토리에 실려야 하는 비규칙 아티팩트 이름을 모은다.

    스킬/하니스/인프라 세 루트를 직계 자식만 열거하고, 그중 규칙이 뒷받침하는
    것(규칙 표가 이미 커버하는 것)은 뺀다. 단일 분류 불변식이므로 규칙 기반
    아티팩트가 이 표에 실리면 stale로 잡힌다.

    규칙 위반이 하나도 없을 때만 호출되므로(check_rules 참조) 모든 규칙의
    frontmatter는 파싱되고 필수 필드가 갖춰져 있다고 전제한다.

    Args:
        root: 저장소 루트.

    Returns:
        기능성 아티팩트 이름 집합.
    """
    skill_targets: set[str] = set()
    hook_packages: set[str] = set()
    for rule_path in rule_files(root):
        data, _ = parse_frontmatter(rule_path.read_text(encoding="utf-8"))
        assert data is not None
        if data["enforce"] == "hook":
            # 훅 하니스 매핑은 frontmatter id가 아니라 파일명 stem 기준이다
            # (id == stem 검증은 check_rule_file의 몫).
            hook_packages.add(rule_path.stem.replace("-", "_"))
        # check_rule_file이 이 값을 Path로 해석하므로 여기서도 같은 정규화를
        # 거친다. 문자열 그대로 비교하면 './'나 중복 슬래시가 섞인 경로가
        # 규칙 검증은 통과하고 소유권 판정만 어긋나 오분류를 낳는다.
        skill_targets.add(PurePosixPath(str(data["deployed-to"]).strip()).as_posix())

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
        if harness_dir.name in hook_packages:
            continue
        artifacts.add(harness_dir.name)
    for infra_dir in _child_dirs(root / INFRA_DIR):
        artifacts.add(infra_dir.name)
    return artifacts


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
        아티팩트 분류가 규칙 frontmatter에서 파생되므로 이 함수는 규칙 위반이
        하나도 없을 때만 호출된다(호출 조건은 check_rules에 있다).
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
    violations.extend(
        _diff_section(ARTIFACTS_HEADING, _expected_artifacts(root), listed_artifacts)
    )
    return violations


def check_hook_wiring(root: Path) -> list[str]:
    """harness 훅 커맨드가 전부 정본 래퍼 템플릿인지 검사한다(repo-level).

    규칙별 검사는 "규칙 → 배선" 방향만 보므로, 규칙 파일 없이 추가된 훅
    커맨드는 아무도 형태를 검증하지 않는다 — 구식 exec 배선이 그 틈으로
    재발하면 uv 자체 오류(exit 2)가 차단으로 새는 #31이 되돌아온다. 그래서
    Claude Code가 읽는 프로젝트 설정 파일(.claude/settings.json,
    settings.local.json — hook 규칙이 없어도 무조건)과 hook 규칙들의
    deployed-to 집합을 대상으로, `-m harness.*`를 참조하는 모든 커맨드가
    두 정본 템플릿 중 하나와 정확히 일치하는지 역방향으로도 훑는다.
    하위모듈 진입점(harness.a.b)은 여기서만 허용된다 — ruled hook의 규칙별
    검사는 규칙 id 파생 단일 모듈을 계속 요구한다. harness 참조가 없는
    커맨드는 meta 소관 밖이므로 검사하지 않는다(자식 프로젝트의 자체 훅을
    과잉 규제하지 않기 위함).

    한계: settings.local.json은 커밋되지 않으므로 CI에서는 검증 불가(로컬
    실행에서만 잡힘). 읽기·파싱이 불가능한 규칙 파일이 선언한 비표준
    deployed-to는 대상을 알 수 없어 그 파일이 고쳐질 때까지 스윕할 수 없다 —
    해당 규칙은 per-rule 전역 방어가 internal error로 보고해 run이 red이므로
    침묵 통과는 아니며, 인벤토리 보류와 같은 공표된 지연 패턴이다. 읽기 실패(OSError)·JSON 파싱 실패·비객체 대상은 규칙
    유무와 무관하게 위반으로 소리낸다 — 규칙별 검사가 frontmatter 오류로
    조기 종료하면 아무도 보고하지 않는 조합이 생기기 때문이며(최종 게이트
    리뷰), 건강한 ruled 대상의 이중 보고는 수용된 패턴이다.
    따옴표 감싼 모듈명(`-m "harness.x"`)은 미감지 —
    ruled hook이면 "not referenced" 위반으로 표면화되고, unruled는
    bash -c 간접 실행과 같은 기존 잔여 클래스.

    Args:
        root: 저장소 루트.

    Returns:
        위반 메시지 목록. 비어 있으면 통과.
    """
    unconditional: set[Path] = {
        root / ".claude" / "settings.json",
        root / ".claude" / "settings.local.json",
    }
    ruled: set[Path] = set()
    for rule_path in rule_files(root):
        try:
            data, error = parse_frontmatter(rule_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 아래 참조
            # 규칙 파일 하나가 스윕 전체를 뭉개면 안 된다. 가드는 예외 "타입"이
            # 아니라 반복 "영역"을 감싼다 — OSError만(깨진 symlink), 다음엔
            # UnicodeDecodeError(비UTF-8), 다음엔 파싱 예외(YAML RecursionError)
            # 로 같은 가림이 세 번 재발한 교훈. 문제의 규칙 파일 자체는
            # per-rule 전역 방어가 internal error로 보고하고(run은 red), 그
            # 파일이 선언한 비표준 deployed-to는 파일이 고쳐질 때까지 스윕
            # 불가 — 인벤토리 보류와 같은 공표된 지연 패턴(docstring 참조).
            continue
        if error or data is None:
            continue
        if data.get("enforce") == "hook" and data.get("deployed-to"):
            deployed = PurePosixPath(str(data["deployed-to"]))
            if deployed.is_absolute() or ".." in deployed.parts:
                # 저장소 밖 경로 — 규칙별 검사가 위반 보고. relative_to는
                # 어휘적이라 ..를 못 거르므로(#40 리뷰 2R) 여기서 배제한다.
                continue
            ruled.add(root / str(data["deployed-to"]))

    violations: list[str] = []
    for target in sorted(unconditional | ruled):
        if not target.is_file():
            continue
        rel = target.relative_to(root)
        try:
            violations.extend(_target_wiring_violations(rel, target))
        except Exception as exc:  # noqa: BLE001 — 사망 부류 방어가 설계 요구사항
            # 대상 파일 하나의 파싱 사망(심중첩 JSON의 RecursionError 등 —
            # _load_settings 분류 밖 예외)이 스윕 전체를 뭉개면 다른 대상의
            # 배선 위반이 가려진다 — 규칙 순회의 per-rule 가드와 같은 영역
            # 가드로 사망을 대상 단위에 국지화한다(PR #93 R1).
            violations.append(
                f"{rel}: internal checker error — {type(exc).__name__}: {exc}"
            )
    return violations


def _target_wiring_violations(rel: Path, target: Path) -> list[str]:
    """settings 대상 파일 하나의 훅 배선 위반을 돌려준다(check_hook_wiring 전용).

    Args:
        rel: 위반 메시지에 앞세울 대상 파일의 상대 경로.
        target: 검사할 settings JSON 경로.

    Returns:
        위반 메시지 목록. 비어 있으면 통과.
    """
    violations: list[str] = []
    settings, problem = _load_settings(target)
    if problem is not None:
        # ruled 대상도 예외 없이 보고한다 — 규칙별 검사는 frontmatter 오류
        # 등으로 조기 종료하면 여기까지 못 오므로, "규칙이 대신 보고한다"는
        # 면제 전제는 성립하지 않는다(최종 게이트 리뷰). ruled 대상의 이중
        # 보고는 per-rule/스윕 이중 검사와 같은 수용된 패턴.
        violations.append(f"{rel}: cannot verify hook wiring — file {problem}")
        return violations
    assert settings is not None
    for command in _hook_commands(settings):
        tokens = _HOOK_MODULE_RE.findall(command)
        if not tokens:
            continue
        if len(set(tokens)) > 1:
            violations.append(
                f"{rel}: hook command references multiple harness modules "
                f"({', '.join(sorted(set(tokens)))}) — split into one "
                "canonical hook command per module"
            )
            continue
        module = tokens[0]
        expected_blocking = HOOK_COMMAND_BLOCKING.replace("{module}", module)
        expected_non_blocking = HOOK_COMMAND_NON_BLOCKING.replace(
            "{module}", module
        )
        if command not in (expected_blocking, expected_non_blocking):
            violations.append(
                f"{rel}: hook command referencing '{module}' matches neither "
                f"canonical wrapper (#31 fail-open wiring) — got: {command} "
                f"— expected exactly (blocking): {expected_blocking} — or "
                f"(non-blocking): {expected_non_blocking}"
            )
    return violations


def check_rules(root: Path) -> list[str]:
    """meta/rules/ 전체와 repo-level 동기화를 검증하고 위반 목록을 돌려준다.

    Args:
        root: 저장소 루트.

    Returns:
        전 규칙 파일 + 템플릿 동기화 + hook 배선 역방향 스윕 + 인벤토리
        커버리지의 위반 메시지 목록. README.md는 규칙이 아니므로 제외한다.
    """
    rules_dir = root / "meta" / "rules"
    if not rules_dir.is_dir():
        return [f"rules directory not found: {rules_dir.relative_to(root)}"]

    # 사망 부류 방어(#40 리뷰 2R: ValueError를 고치자 PermissionError가 나왔다 —
    # 지점 단위 방어는 이 부류를 못 닫는다). 어떤 예외든 일반 위반과 구별되는
    # "internal checker error" 위반으로 변환한다 — 검증기는 절대 traceback으로
    # 죽지 않고, run은 red로 유지되며, 다른 규칙의 판정은 보존된다.
    rule_violations: list[str] = []
    for rule_path in rule_files(root):
        try:
            rule_violations.extend(check_rule_file(rule_path, root))
        except Exception as exc:  # noqa: BLE001 — 사망 부류 방어가 설계 요구사항
            rule_violations.append(
                f"{rule_path.relative_to(root)}: internal checker error — "
                f"{type(exc).__name__}: {exc}"
            )

    violations = list(rule_violations)
    # 역방향 스윕은 인벤토리와 달리 규칙 위반이 있어도 미루지 않는다 —
    # frontmatter 분류에 의존하지 않고, 파싱된 hook 규칙만으로 동작한다.
    for repo_check in (check_template_sync, check_hook_wiring):
        try:
            violations.extend(repo_check(root))
        except Exception as exc:  # noqa: BLE001 — 사망 부류 방어가 설계 요구사항
            violations.append(
                f"internal checker error in {repo_check.__name__} — "
                f"{type(exc).__name__}: {exc}"
            )
    if rule_violations:
        # 인벤토리의 아티팩트 분류는 규칙 frontmatter에서 파생된다. 깨진
        # 레지스트리 위에서 분류하면 규칙이 뒷받침하던 하니스/스킬을 '규칙 없는
        # 아티팩트'로 오분류해, 규칙을 고치는 순간 stale이 될 행을 추가하라고
        # 지시하게 된다. 미루되 조용히 넘어가지 않고 미룬 사실을 보고한다.
        violations.append(
            f"{INVENTORY_PATH}: coverage was not checked — fix the "
            f"{len(rule_violations)} rule violation(s) above and re-run"
        )
    else:
        try:
            violations.extend(check_inventory(root))
        except Exception as exc:  # noqa: BLE001 — 사망 부류 방어가 설계 요구사항
            violations.append(
                f"internal checker error in check_inventory — "
                f"{type(exc).__name__}: {exc}"
            )
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
