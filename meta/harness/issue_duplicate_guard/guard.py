# gh/glab 이슈 생성 전 중복 검색을 강제하는 PreToolUse hook
"""이슈 중복 생성 방지 hook (issue-duplicate-guard 규칙의 배포체).

Claude Code의 PreToolUse(Bash) hook으로 실행되어, `gh issue create` 또는
`glab issue create` 명령을 감지하면 같은 CLI로 열림+닫힘 전체 이슈를 제목
검색하고, 유사 이슈가 있으면 차단(exit 42 — 래퍼가 2로 되매핑)하며 후보
목록을 제시한다.
모델이 후보를 검토한 뒤 진짜 신규라고 판단하면 gh/glab이 있는 **세그먼트
선두**에 `ATOM_DUP_REVIEWED=1`을 붙여 재실행해 통과한다(복합 명령이면 마커를
gh/glab 호출 바로 앞에 — 환경 변수의 셸 의미론과 동일) — 판단은 모델이,
검색이 반드시 일어났다는 사실은 기계가 보장한다.

설계 불변식:
- 차단 경로는 정확히 두 가지다. (1) "대상 명령 + 검색 성공 + 유사 이슈
  존재 + override 없음"의 교집합, (2) `--title` 없는 생성 명령(`--web`
  포함) — 제목이 없으면 검색 자체가 불가능하므로 검색 없이 차단한다.
  두 경로 모두 override로 복구된다. 그 외 모든 실패 경로는 fail-open
  (통과)이다 — 이 hook은 모든 Bash 호출에 실행되므로 절대 Bash 전체를
  막으면 안 된다.
- 오탐 방지가 최우선: 전체 명령을 shlex로 먼저 토큰화해 따옴표 문자열을
  단일 토큰으로 만든 뒤 연산자 위치에서 세그먼트를 나누므로, 커밋 메시지
  등 문자열 내부의 "gh issue create" 언급은 명령 위치에 올 수 없다.
- 검색 기준 디렉터리는 hook 페이로드의 `cwd`(Bash 도구의 작업 디렉터리는
  호출 간 유지되므로 프로세스 cwd만으로는 어긋날 수 있다). `--repo`/`-R`가
  없으면 gh/glab이 그 디렉터리로 대상 저장소를 해석하므로, 선행 세그먼트에
  `cd`가 있으면 대상이 불명 → 검색을 건너뛴다(fail-open). 이 표시는 한 번
  켜지면 유지된다. 서브셸에 갇힌 cd(`(cd x) && ...`)도 래치를 켜는 오판과
  `pushd`/`cd -` 미커버는 sibling commit_guard와 동일한 수용 한계다 —
  실행 의미론 판정은 범위 밖.
- 감지 못하는 형태(`bash -c` 내부, backtick 치환, `env` 프리픽스, 그리고
  유효 bash지만 shlex가 두 단계 모두 실패하는 계열 — 주석 뒤 불균형 따옴표,
  ANSI-C 인용 `$'...\''` 등)는 전부 통과 방향의 한계이며, claude-md 쪽
  issue-workflow 규칙의 관례가 커버한다.

종료 코드: 0 통과, 1 내부 오류(비차단 경고), 42 차단(sentinel — settings.json
래퍼가 2로 되매핑).
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass

# override 마커: 후보 검토를 마쳤다는 선언. 세그먼트 선두에 있으면 통과.
OVERRIDE_TOKEN = "ATOM_DUP_REVIEWED=1"

# 차단 sentinel 종료 코드. Claude Code의 차단 코드는 2지만 uv(자체 오류 2)와
# python(예외 1, CLI 오류 2)이 같은 코드를 낼 수 있어, exec 배선에서는 도구
# 실패가 차단으로 샜다(#31). 자연 발생 불가능한 42를 반환하고 settings.json의
# 셸 래퍼가 42→2로 되매핑하며, 그 외 nonzero는 전부 1(비차단 경고)로 수렴한다.
EXIT_BLOCK = 42

# shlex(punctuation_chars=True)가 별도 토큰으로 분리하는 셸 연산자.
OPERATORS = {"&&", "||", "|", ";", ";;", "&", "(", ")"}

FORGE_CLIS = {"gh", "glab"}

SEARCH_TIMEOUT_SECONDS = 15
MAX_CANDIDATES = 10

_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# glab 텍스트 출력에서 이슈로 확신할 수 있는 라인(#번호로 시작)만 채택.
_GLAB_ISSUE_LINE_RE = re.compile(r"^#\d+\s+\S.*$")

# heredoc 시작 마커: <<EOF / <<-EOF / <<'EOF' / << "EOF" / <<\EOF 변형.
# (?<!<)와 구분자 문자군이 herestring(<<<)을 배제한다. bare 구분자는 bash가
# WORD로 받는 모든 문자를 허용한다(공백·따옴표·백슬래시·셸 연산자만 제외) —
# 문자군이 좁으면 MY+DELIM 같은 구분자의 종결자를 못 찾아 본문 오차단이 남는다.
_HEREDOC_MARKER_RE = re.compile(
    r"(?<!<)<<(-?)\s*\\?(?:'([^'\n]+)'|\"([^\"\n]+)\"|([^\s'\"\\<>|&;()]+))"
)

def _mask_arithmetic(line: str) -> str:
    """마커 스캔 전에 한 줄의 산술 스팬을 공백으로 가린 사본을 만든다.

    정규식 마스킹은 두 번 깨졌다 — lazy 스팬이 따옴표 산문 속 `((`/`))`
    조각을 가로질러 실제 마커를 가렸고(오차단), 내부 금지문자 베토는
    `${var:-((}` 괄호 주입·산술 내 따옴표(`(( x = "1" ))`)·이중 괄호 중첩
    (`$(( ((a)) << 2 ))`)에서 반증됐다. 균형 괄호는 정규식으로 불가능하므로
    문자 단위 스캔으로 교체한다(리뷰 루프 divergence 수리, 구조적 선택지).

    규칙: 인용('·"·백틱)되지 않고 `${...}` 확장 내부가 아닌 인접 `((`
    (선택적 `$` 프리픽스 포함)부터 균형 잡힌 `))`까지가 산술 스팬이다.
    스팬 내부에서도 같은 인용 규칙으로 괄호를 세되, **명령 치환 내용물**
    (단일 괄호 `$(...)`와 백틱)은 마스킹에서 면제한다 — 시프트는 산술식
    레벨에만, 진짜 리다이렉션은 명령 치환 내부에만 오기 때문이다
    (`$(( $(cat <<XX) + 1 ))`의 실마커 보존). 면제 여부는 문자를 감싸는
    **가장 안쪽 arith/cmdsub 프레임**이 결정하고(plain 괄호는 투명), 닫는
    괄호는 pop 이전 스택으로 판정한다. 산술 내부의 인접 `$((`는 명령
    치환이 아니라 중첩 산술이라 마스킹을 계속한다. 산술 내 따옴표 내용은
    산술식의 일부라 마스킹한다. 큰따옴표·백틱 안의 백슬래시는 "다음 한
    문자 이스케이프"로 단순화한다 — 각각 bash의 정밀 이스케이프 집합
    (큰따옴표: 달러·백틱·큰따옴표·백슬래시 / 백틱: 달러·백틱·백슬래시)에
    상태 관련 문자가 전부 포함되므로 인용 상태 판정이 등가다(큰따옴표
    쪽은 전수 대조로도 확인).

    미폐쇄 처리: 줄 끝까지 안 닫힌 산술 스팬은 마스킹하지 않는다(여러 줄
    산술식 — 문서화된 한계). 잔여 한계는 _strip_heredocs docstring 참조.

    Args:
        line: 명령의 한 줄.

    Returns:
        산술 스팬이 공백으로 치환된(길이 보존) 마커 스캔용 사본.
    """
    masked = list(line)
    n = len(line)
    to_mask: list[int] = []      # 확정된 마스킹 인덱스
    span: list[int] | None = None  # 진행 중인 산술 스팬의 마스킹 후보
    stack: list[str] = []        # 산술 내 괄호 종류: "arith" | "cmdsub" | "plain"
    in_single = in_double = in_backtick = False
    escaped = False
    brace_depth = 0
    i = 0
    while i < n:
        c = line[i]
        # 면제 판정은 문자를 감싸는 가장 안쪽 arith/cmdsub 프레임이 결정한다
        # (plain 괄호는 투명). 안쪽 산술은 어떤 깊이에서든 산술이므로
        # 마스킹되고, 명령 치환 직속 내용물만 면제된다. 닫는 괄호는 pop
        # 이전 스택으로 판정되므로 cmdsub를 닫는 괄호도 면제 쪽이다.
        exempt = next(
            (kind == "cmdsub" for kind in reversed(stack) if kind != "plain"),
            False,
        )

        def keep(idx: int) -> None:
            if span is not None and not exempt:
                span.append(idx)

        if escaped:
            escaped = False
            keep(i)
            i += 1
            continue
        if in_single:
            if c == "'":
                in_single = False
            keep(i)
            i += 1
            continue
        if in_double:
            if c == "\\":
                escaped = True
            elif c == '"':
                in_double = False
            keep(i)
            i += 1
            continue
        if in_backtick:
            # 백틱 내용물은 명령 치환 — 마스킹 면제. bash의 백틱 내
            # 이스케이프 집합은 달러·백틱·백슬래시이고 상태 관련 문자
            # (백틱·백슬래시)가 전부 포함되므로 "다음 한 문자 소비"가
            # 인용 상태 판정과 등가다 — 큰따옴표 단순화와 같은 논리.
            if c == "\\":
                i += 2
                continue
            if c == "`":
                in_backtick = False
            i += 1
            continue
        if c == "\\":
            escaped = True
            keep(i)
            i += 1
            continue
        if c == "'":
            in_single = True
            keep(i)
            i += 1
            continue
        if c == '"':
            in_double = True
            keep(i)
            i += 1
            continue
        if c == "`":
            in_backtick = True
            i += 1
            continue
        if brace_depth > 0:
            if c == "{":
                brace_depth += 1
            elif c == "}":
                brace_depth -= 1
            keep(i)
            i += 1
            continue
        if c == "$" and i + 1 < n and line[i + 1] == "{":
            brace_depth = 1
            keep(i)
            keep(i + 1)
            i += 2
            continue
        if span is None:
            if c == "(" and i + 1 < n and line[i + 1] == "(":
                start = i - 1 if i > 0 and line[i - 1] == "$" else i
                span = list(range(start, i + 2))
                stack = ["arith", "arith"]
            i += 1 if c != "(" or i + 1 >= n or line[i + 1] != "(" else 2
            continue
        # --- 산술 스팬 내부 ---
        if c == "$" and i + 1 < n and line[i + 1] == "(":
            if i + 2 < n and line[i + 2] == "(":
                # 인접 $(( → 중첩 산술: 마스킹 계속
                keep(i)
                keep(i + 1)
                keep(i + 2)
                stack.extend(["arith", "arith"])
                i += 3
                continue
            # 단일 $( → 명령 치환: 내용물 면제
            stack.append("cmdsub")
            i += 2
            continue
        if c == "(":
            keep(i)
            stack.append("plain")
            i += 1
            continue
        if c == ")":
            keep(i)
            if stack:
                stack.pop()
            if not stack:
                to_mask.extend(span)
                span = None
            i += 1
            continue
        keep(i)
        i += 1
    # 미폐쇄 스팬(span이 남음)은 버린다 — 마스킹하지 않음.
    for idx in to_mask:
        masked[idx] = " "
    return "".join(masked)


def _strip_heredocs(command: str) -> str:
    """토큰화 전에 heredoc 본문 라인을 제거한다.

    heredoc은 shlex를 실패시키지 않아 폴백으로 빠지지 않고, 본문이 줄 단위로
    명령처럼 파싱돼 "문자열 내부 언급은 명령 위치에 올 수 없다" 불변식을
    깨뜨린다. 마커 라인 자체는 보존하므로 heredoc으로 body를 먹이는 실제
    생성 명령은 계속 감지된다. 검사 입력만 바꾸며 실행 명령은 불변이다.

    안전 규칙: 종결자 라인을 실제로 찾은 경우에만 제거하고, 미종결 구분자가
    하나라도 남으면 전체 원문을 유지한다(all-or-nothing) — 마커 오인이 실제
    생성 명령의 감지를 지우는 회귀를 막는다. 마커 스캔 전에 _mask_arithmetic
    (라인 수준 인용·확장 인지 스캔 — 정규식 휴리스틱이 아님)으로 산술
    스팬을 가려 시프트(`1<<2`)의 오인을 배제한다.

    잔여 한계(전부 문서화된 수용 범위): 여러 줄에 걸친 산술식·인용·치환
    구조는 라인 단위 스캔이라 문맥이 이어지지 않고, 괄호 밖 산술 시프트
    (`let x=1<<2` 등)는 못 가린다. 명령 치환 내부의 case 패턴 등 불균형
    단독 괄호는 스팬 추적을 오염시킨다 — 마커가 가려지거나 스팬이 조기
    확정돼 꼬리 시프트가 노출되며, 대부분 all-or-nothing abort로 눕지만
    우연한 종결자 줄과 겹치면 통과 방향까지 간다(case 문법 파싱 없이는
    원리적으로 못 닫는 가족). 마스킹 면제 구역의 quoted `<<` 언급과 면제
    구역(백틱 등) 내부의 중첩 산술은 여전히 마스킹되지 않는다(단일 괄호
    명령 치환 직속의 중첩 산술만 최근접 프레임 판정으로 마스킹됨). 종결자
    없이 입력 끝으로 종결되는 heredoc(bash는 경고 후 실행)은 스트립 대상
    외라 본문이 여전히 파싱된다. 따옴표 문자열이나 `#` 주석 등 실행되지
    않는 텍스트 안의 heredoc 언급 뒤에 구분자와 일치하는 단독 줄이
    실존하면 그 사이가 통과 방향으로 스트립될 수 있다(이 hook은 리터럴
    `#` 보존을 위해 주석 해석을 끄므로 주석과 명령을 구분하지 못한다).

    Args:
        command: Bash 명령 문자열 전체.

    Returns:
        본문·종결자 라인이 제거된 명령. 미종결 시 원문 그대로.
    """
    lines = command.split("\n")
    kept: list[str] = []
    # (구분자, <<- 여부) 대기 큐 — 한 줄 다중 heredoc은 선언 순서대로 소비된다.
    pending: list[tuple[str, bool]] = []
    for line in lines:
        if pending:
            delimiter, tab_stripped = pending[0]
            candidate = line.rstrip("\r")
            if tab_stripped:
                candidate = candidate.lstrip("\t")
            if candidate == delimiter:
                pending.pop(0)
            continue
        # 길이 보존 마스킹: 빈 문자열 치환은 a<$((x))<b → a<<b 같은 가짜
        # 마커를 만들 수 있다.
        masked = _mask_arithmetic(line)
        for match in _HEREDOC_MARKER_RE.finditer(masked):
            delimiter = match.group(2) or match.group(3) or match.group(4)
            pending.append((delimiter, match.group(1) == "-"))
        kept.append(line)
    if pending:
        return command
    return "\n".join(kept)


@dataclass
class CreateInvocation:
    """감지된 이슈 생성 명령 하나.

    Attributes:
        cli: 호출된 CLI 이름 ("gh" 또는 "glab").
        title: `--title`/`-t`로 지정된 제목. 없으면 None.
        repo: `-R`/`--repo`로 지정된 대상 저장소. 없으면 None.
        override: 같은 세그먼트 선두에 ATOM_DUP_REVIEWED=1이 있었는지.
        cwd_unsafe: 선행 `cd` 때문에 검색 기준 디렉터리가 불명인지.
    """

    cli: str
    title: str | None
    repo: str | None
    override: bool
    cwd_unsafe: bool = False


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _tokenize(text: str) -> list[str]:
    """셸 문법을 인식해 토큰화한다.

    따옴표 문자열은 단일 토큰이 되고 연산자는 별도 토큰으로 분리된다.
    기본 commenters('#')는 제목 등의 리터럴 '#'을 잘라먹으므로 끈다.

    Args:
        text: Bash 명령 문자열.

    Returns:
        토큰 목록.

    Raises:
        ValueError: 미폐쇄 따옴표 등 shlex가 소화 못 하는 구문.
    """
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _split_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in OPERATORS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _parse_segment(segment: list[str]) -> CreateInvocation | None:
    """세그먼트 하나에서 이슈 생성 명령을 파싱한다.

    명령 위치 판정: 선행 VAR=val 할당을 건너뛴 첫 토큰이 gh/glab이어야
    한다. 인자 위치의 리터럴(`echo gh issue create` 등)은 대상이 아니다.

    Args:
        segment: 연산자로 분리된 토큰 세그먼트.

    Returns:
        감지된 생성 명령, 대상이 아니면 None.
    """
    index = 0
    override = False
    while index < len(segment) and _ENV_ASSIGNMENT_RE.match(segment[index]):
        if segment[index] == OVERRIDE_TOKEN:
            override = True
        index += 1
    rest = segment[index:]
    if len(rest) < 3 or _basename(rest[0]) not in FORGE_CLIS:
        return None
    if rest[1] != "issue" or rest[2] != "create":
        return None

    title: str | None = None
    repo: str | None = None
    j = 3
    while j < len(rest):
        token = rest[j]
        if token in ("--title", "-t"):
            if j + 1 < len(rest):
                title = rest[j + 1]
                j += 1
        elif token.startswith("--title="):
            title = token[len("--title="):]
        elif token.startswith("-t") and not token.startswith("--") and len(token) > 2:
            title = token[2:]
        elif token in ("--repo", "-R"):
            if j + 1 < len(rest):
                repo = rest[j + 1]
                j += 1
        elif token.startswith("--repo="):
            repo = token[len("--repo="):]
        elif token.startswith("-R") and not token.startswith("--") and len(token) > 2:
            # pflag는 -R=값 형태에서 =를 벗기고 값을 취한다.
            repo = token[2:].removeprefix("=") or None
        j += 1
    return CreateInvocation(cli=_basename(rest[0]), title=title, repo=repo, override=override)


def _changes_directory(segment: list[str]) -> bool:
    """세그먼트가 검색 기준 디렉터리를 옮기는지 판정한다 (sibling과 동일 규약).

    `cd` 뒤로는 페이로드의 `cwd`가 어느 디렉터리를 가리키는지 알 수 없다.
    되돌릴 방법이 없으므로 이 표시는 한 번 켜지면 유지된다.

    Args:
        segment: 연산자로 분리된 토큰 세그먼트.

    Returns:
        디렉터리가 바뀌면 True.
    """
    first = next((t for t in segment if not _ENV_ASSIGNMENT_RE.match(t)), None)
    return first is not None and _basename(first) == "cd"


def detect_invocations(command: str) -> list[CreateInvocation]:
    """Bash 명령 문자열에서 모든 이슈 생성 명령을 감지한다.

    개행으로 나뉜 다중 명령을 잡기 위해 줄 단위 토큰화를 먼저 시도하고
    (따옴표가 줄을 넘는 경우엔 실패하므로) 전체 문자열 토큰화 순서로
    내려간다. 둘 다 실패하면 전면 fail-open이다 — 정규식 폴백은 따옴표
    문자열 속 언급을 명령으로 오인해 무관한 Bash를 차단했으므로 제거했다
    (sibling commit_guard의 #45와 같은 결론). 그 감지 공백은 claude-md 쪽
    issue-workflow 규칙의 관례가 커버한다 — 단 sibling과 달리 이 guard에는
    실행 후 안전망(backstop)이 없으므로, 놓친 생성은 기계가 아니라 관례만이
    잡는다.

    Args:
        command: Bash 도구가 실행하려는 명령 전체.

    Returns:
        감지된 생성 명령 목록. 대상이 없으면 빈 목록.
    """
    command = _strip_heredocs(command)
    token_groups: list[list[str]] | None = None
    try:
        token_groups = [_tokenize(line) for line in command.split("\n")]
    except ValueError:
        try:
            token_groups = [_tokenize(command)]
        except ValueError:
            return []

    invocations: list[CreateInvocation] = []
    directory_changed = False
    for tokens in token_groups:
        for segment in _split_segments(tokens):
            invocation = _parse_segment(segment)
            if invocation is not None:
                invocation.cwd_unsafe = directory_changed
                invocations.append(invocation)
            elif _changes_directory(segment):
                directory_changed = True
    return invocations


def _run_search(argv: list[str], cwd: str | None = None) -> str | None:
    """검색 명령을 실행하고 stdout을 돌려준다.

    비정상 종료·타임아웃·CLI 부재·소멸한 cwd는 전부 None(→ fail-open)으로
    수렴한다. 리스트 인자 + shell=False라 제목의 특수문자가 셸로 새지 않는다.

    Args:
        argv: 실행할 명령과 인자.
        cwd: hook 페이로드가 알려준 Bash 작업 디렉터리 (없으면 프로세스 cwd).

    Returns:
        성공 시 stdout, 실패 시 None.
    """
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=SEARCH_TIMEOUT_SECONDS,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def search_duplicates(
    invocation: CreateInvocation, cwd: str | None = None
) -> list[str] | None:
    """생성하려는 제목으로 기존 이슈(열림+닫힘)를 검색한다.

    Args:
        invocation: 감지된 이슈 생성 명령 (title은 비어 있지 않아야 함).
        cwd: hook 페이로드가 알려준 Bash 작업 디렉터리 (없으면 프로세스 cwd).

    Returns:
        후보 설명 문자열 목록(없으면 빈 목록), 검색 실패 시 None.
    """
    assert invocation.title
    if invocation.cli == "gh":
        argv = [
            "gh", "issue", "list",
            "--state", "all",
            "--search", invocation.title,
            "--json", "number,title,state",
            "--limit", str(MAX_CANDIDATES),
        ]
        if invocation.repo:
            argv += ["--repo", invocation.repo]
        output = _run_search(argv, cwd)
        if output is None:
            return None
        try:
            issues = json.loads(output)
            return [f"#{issue['number']} [{issue['state']}] {issue['title']}" for issue in issues]
        except (ValueError, KeyError, TypeError):
            return None

    # glab: 구조화 출력이 버전에 따라 달라 텍스트를 보수적으로 파싱한다.
    # 이슈로 확신되는 라인(#번호 시작)만 채택하고, 애매하면 빈 결과(통과).
    # glab 1.108.0 / GitLab CE 19.2.0 실인스턴스로 검증됨 (2026-07-22, issue #9).
    # 드리프트 카나리아: tests/test_guard_gitlab.py (-m gitlab, meta/infra/gitlab).
    argv = [
        "glab", "issue", "list",
        "--all",
        "--search", invocation.title,
        "--per-page", str(MAX_CANDIDATES),
    ]
    if invocation.repo:
        argv += ["--repo", invocation.repo]
    output = _run_search(argv, cwd)
    if output is None:
        return None
    return [
        line.strip()
        for line in output.splitlines()
        if _GLAB_ISSUE_LINE_RE.match(line.strip())
    ][:MAX_CANDIDATES]


def _block_message(invocation: CreateInvocation, candidates: list[str]) -> str:
    lines = [
        f"[issue-duplicate-guard] similar existing issues found for title {invocation.title!r}:",
        *(f"  {candidate}" for candidate in candidates),
        "Review them first: comment on or reopen an existing issue instead of creating a duplicate.",
        "If this is genuinely new, re-run with the override marker prefixed to the",
        f"command segment that runs {invocation.cli} (immediately before the invocation):",
        f"  {OVERRIDE_TOKEN} {invocation.cli} issue create ...",
    ]
    return "\n".join(lines)


def main() -> int:
    """stdin의 PreToolUse JSON을 판정한다.

    Returns:
        종료 코드 (0 통과, 1 내부 경고, 42 차단 — 래퍼가 2로 되매핑).
    """
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        print("[issue-duplicate-guard] malformed hook input (fail-open)", file=sys.stderr)
        return 1
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return 0
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = None

    for invocation in detect_invocations(command):
        if invocation.override:
            continue
        if not invocation.title:
            print(
                "[issue-duplicate-guard] issue creation without --title is blocked: "
                "pass an explicit --title so the duplicate search can run "
                "(without a title there is nothing to search — this includes "
                "interactive and --web creation).\n"
                "If it must run without --title, re-run with the override marker "
                f"prefixed to the {invocation.cli} segment: "
                f"{OVERRIDE_TOKEN} {invocation.cli} issue create ...",
                file=sys.stderr,
            )
            return EXIT_BLOCK
        # 제목 검사보다 뒤: 제목 없는 create는 검색이 필요 없어 cd와 무관하게
        # 차단되고, 검색만이 기준 디렉터리에 의존한다.
        if invocation.cwd_unsafe and not invocation.repo:
            print(
                f"[issue-duplicate-guard] duplicate check skipped: {invocation.cli} "
                "target repo unknown (working directory changed by cd)",
                file=sys.stderr,
            )
            continue
        candidates = search_duplicates(invocation, cwd)
        if candidates is None:
            print(
                f"[issue-duplicate-guard] duplicate check skipped: {invocation.cli} "
                "search failed (not authenticated / offline / CLI missing)",
                file=sys.stderr,
            )
            continue
        if candidates:
            print(_block_message(invocation, candidates), file=sys.stderr)
            return EXIT_BLOCK
    return 0


def run() -> int:
    """최상위 방어 실행기: 어떤 내부 오류도 차단으로 새지 않게 한다.

    Returns:
        종료 코드 (내부 오류 시 1 — 비차단).
    """
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 — fail-open이 설계 요구사항
        print(f"[issue-duplicate-guard] internal error (fail-open): {exc}", file=sys.stderr)
        return 1
