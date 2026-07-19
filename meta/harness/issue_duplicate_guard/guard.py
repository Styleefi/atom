# gh/glab 이슈 생성 전 중복 검색을 강제하는 PreToolUse hook
"""이슈 중복 생성 방지 hook (issue-duplicate-guard 규칙의 배포체).

Claude Code의 PreToolUse(Bash) hook으로 실행되어, `gh issue create` 또는
`glab issue create` 명령을 감지하면 같은 CLI로 열림+닫힘 전체 이슈를 제목
검색하고, 유사 이슈가 있으면 차단(exit 2)하며 후보 목록을 제시한다.
모델이 후보를 검토한 뒤 진짜 신규라고 판단하면 `ATOM_DUP_REVIEWED=1`
프리픽스로 같은 명령을 재실행해 통과한다 — 판단은 모델이, 검색이 반드시
일어났다는 사실은 기계가 보장한다.

설계 불변식:
- 차단은 "대상 명령 + 검색 성공 + 유사 이슈 존재 + override 없음"의
  교집합에서만 일어난다. 그 외 모든 실패 경로는 fail-open(통과)이다 —
  이 hook은 모든 Bash 호출에 실행되므로 절대 Bash 전체를 막으면 안 된다.
- 오탐 방지가 최우선: 전체 명령을 shlex로 먼저 토큰화해 따옴표 문자열을
  단일 토큰으로 만든 뒤 연산자 위치에서 세그먼트를 나누므로, 커밋 메시지
  등 문자열 내부의 "gh issue create" 언급은 명령 위치에 올 수 없다.
- 감지 못하는 형태(`bash -c` 내부, backtick 치환, `env` 프리픽스)는 전부
  통과 방향의 한계이며, claude-md 쪽 issue-workflow 규칙의 관례가 커버한다.

종료 코드: 0 통과, 1 내부 오류(비차단 경고), 2 차단.
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

# shlex(punctuation_chars=True)가 별도 토큰으로 분리하는 셸 연산자.
OPERATORS = {"&&", "||", "|", ";", ";;", "&", "(", ")"}

FORGE_CLIS = {"gh", "glab"}

SEARCH_TIMEOUT_SECONDS = 15
MAX_CANDIDATES = 10

_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# shlex 실패 시 폴백: 명령 위치(문자열 시작 또는 연산자 뒤)에 앵커된 감지.
_FALLBACK_CMD_RE = re.compile(
    r"(?:^|[;&|(]\s*)(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:\S*/)?(gh|glab)\s+issue\s+create\b",
    re.MULTILINE,
)
_FALLBACK_TITLE_RE = re.compile(r"(?:--title|-t)(?:=|\s+)(?:\"([^\"]*)\"|'([^']*)'|(\S+))")
_FALLBACK_REPO_RE = re.compile(r"(?:--repo|-R)(?:=|\s+)(?:\"([^\"]*)\"|'([^']*)'|(\S+))")

# glab 텍스트 출력에서 이슈로 확신할 수 있는 라인(#번호로 시작)만 채택.
_GLAB_ISSUE_LINE_RE = re.compile(r"^#\d+\s+\S.*$")


@dataclass
class CreateInvocation:
    """감지된 이슈 생성 명령 하나.

    Attributes:
        cli: 호출된 CLI 이름 ("gh" 또는 "glab").
        title: `--title`/`-t`로 지정된 제목. 없으면 None.
        repo: `-R`/`--repo`로 지정된 대상 저장소. 없으면 None.
        override: 같은 세그먼트 선두에 ATOM_DUP_REVIEWED=1이 있었는지.
    """

    cli: str
    title: str | None
    repo: str | None
    override: bool


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
        j += 1
    return CreateInvocation(cli=_basename(rest[0]), title=title, repo=repo, override=override)


def _detect_fallback(command: str) -> list[CreateInvocation]:
    """shlex가 실패한 명령(heredoc 등)에 대한 보수적 정규식 감지.

    잔여 오탐 가능성은 있으나 모든 오차단은 ATOM_DUP_REVIEWED=1 재실행으로
    복구 가능하다.

    Args:
        command: 원본 명령 문자열.

    Returns:
        감지된 생성 명령 목록 (폴백에서는 최대 1건으로 요약).
    """
    match = _FALLBACK_CMD_RE.search(command)
    if not match:
        return []
    title_match = _FALLBACK_TITLE_RE.search(command)
    repo_match = _FALLBACK_REPO_RE.search(command)
    title = next((g for g in title_match.groups() if g is not None), None) if title_match else None
    repo = next((g for g in repo_match.groups() if g is not None), None) if repo_match else None
    return [
        CreateInvocation(
            cli=match.group(1),
            title=title,
            repo=repo,
            override=OVERRIDE_TOKEN in command,
        )
    ]


def detect_invocations(command: str) -> list[CreateInvocation]:
    """Bash 명령 문자열에서 모든 이슈 생성 명령을 감지한다.

    개행으로 나뉜 다중 명령을 잡기 위해 줄 단위 토큰화를 먼저 시도하고
    (따옴표가 줄을 넘는 경우엔 실패하므로) 전체 문자열 토큰화, 그것도
    실패하면 정규식 폴백 순서로 내려간다.

    Args:
        command: Bash 도구가 실행하려는 명령 전체.

    Returns:
        감지된 생성 명령 목록. 대상이 없으면 빈 목록.
    """
    token_groups: list[list[str]] | None = None
    try:
        token_groups = [_tokenize(line) for line in command.split("\n")]
    except ValueError:
        try:
            token_groups = [_tokenize(command)]
        except ValueError:
            return _detect_fallback(command)

    invocations: list[CreateInvocation] = []
    for tokens in token_groups:
        for segment in _split_segments(tokens):
            invocation = _parse_segment(segment)
            if invocation is not None:
                invocations.append(invocation)
    return invocations


def _run_search(argv: list[str]) -> str | None:
    """검색 명령을 실행하고 stdout을 돌려준다.

    비정상 종료·타임아웃·CLI 부재는 전부 None(→ fail-open)으로 수렴한다.
    리스트 인자 + shell=False라 제목의 특수문자가 셸로 새지 않는다.

    Args:
        argv: 실행할 명령과 인자.

    Returns:
        성공 시 stdout, 실패 시 None.
    """
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def search_duplicates(invocation: CreateInvocation) -> list[str] | None:
    """생성하려는 제목으로 기존 이슈(열림+닫힘)를 검색한다.

    Args:
        invocation: 감지된 이슈 생성 명령 (title은 비어 있지 않아야 함).

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
        output = _run_search(argv)
        if output is None:
            return None
        try:
            issues = json.loads(output)
            return [f"#{issue['number']} [{issue['state']}] {issue['title']}" for issue in issues]
        except (ValueError, KeyError, TypeError):
            return None

    # glab: 구조화 출력이 버전에 따라 달라 텍스트를 보수적으로 파싱한다.
    # 이슈로 확신되는 라인(#번호 시작)만 채택하고, 애매하면 빈 결과(통과).
    # 실인스턴스 검증 전이므로 잘못 차단보다 놓침을 택한다 (issue #9).
    argv = [
        "glab", "issue", "list",
        "--all",
        "--search", invocation.title,
        "--per-page", str(MAX_CANDIDATES),
    ]
    if invocation.repo:
        argv += ["--repo", invocation.repo]
    output = _run_search(argv)
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
        "If this is genuinely new, re-run the SAME command prefixed with the override marker:",
        f"  {OVERRIDE_TOKEN} {invocation.cli} issue create ...",
    ]
    return "\n".join(lines)


def main() -> int:
    """stdin의 PreToolUse JSON을 판정한다.

    Returns:
        종료 코드 (0 통과, 1 내부 경고, 2 차단).
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

    for invocation in detect_invocations(command):
        if invocation.override:
            continue
        if not invocation.title:
            print(
                "[issue-duplicate-guard] issue creation without --title is blocked: "
                "pass an explicit --title so the duplicate search can run "
                "(interactive creation does not work in this environment anyway).",
                file=sys.stderr,
            )
            return 2
        candidates = search_duplicates(invocation)
        if candidates is None:
            print(
                f"[issue-duplicate-guard] duplicate check skipped: {invocation.cli} "
                "search failed (not authenticated / offline / CLI missing)",
                file=sys.stderr,
            )
            continue
        if candidates:
            print(_block_message(invocation, candidates), file=sys.stderr)
            return 2
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
