# main 직커밋과 비규격 커밋 메시지를 차단하는 PreToolUse hook
"""커밋 규율 강제 hook (commit-guard 규칙의 배포체).

Claude Code의 PreToolUse(Bash) hook으로 실행되어 `git commit` 명령을 감지하면
두 가지를 기계적으로 강제한다.

1. 보호 브랜치(main/master) 직커밋 차단 — 피처 브랜치 + PR 워크플로 강제.
2. 커밋 메시지 제목의 Conventional Commits 형식 검사 — 타입 화이트리스트,
   대문자 시작 금지, 마침표 금지, 제목 50자 이하.

행동 지침(논리 단위 커밋, 브랜치 명명, PR 전용 머지, push 금지)은 claude-md
쪽 commit-discipline 규칙이 담당한다 — 이 hook은 기계 검사 가능한 부분만.

설계 불변식:
- 차단은 "커밋 명령 감지 + 위반 확증 + override 없음"의 교집합에서만.
  그 외 모든 실패 경로는 fail-open(통과) — 이 hook은 모든 Bash 호출에
  실행되므로 절대 Bash 전체를 막으면 안 된다.
- 브랜치 판정 기준 디렉터리는 hook 페이로드의 `cwd`(Bash 도구의 작업
  디렉터리는 호출 간 유지되므로 프로세스 cwd만으로는 어긋날 수 있다).
  명령에 `git -C <path>`가 있으면 동일하게 전달하고, 선행 세그먼트에
  `cd`나 브랜치를 바꾸는 `git checkout`/`git switch`가 있으면 커밋이 얹힐
  대상이 불명이므로 브랜치 검사를 건너뛴다(타 저장소·선행 브랜치 생성
  오차단 방지 — hook은 실행 *전*에 돌므로 `git checkout -b feat/x && git
  commit`의 현재 브랜치는 아직 main이다). 단 `--` 뒤에 경로를 지정하는
  복원 형태와, 대상이 보호 브랜치인 이동은 커밋이 실제로 보호 브랜치에
  얹히므로 제외한다 — 대상은 철자뿐 아니라 `--track origin/main`처럼 원격
  ref에서 파생되는 이름까지 본다. rev-parse 실패·timeout·detached
  HEAD(`HEAD`)는 통과.
- 메시지는 첫 `-m`/`--message`(결합 단축 `-am` 등 포함)의 첫 줄만 검사.
  heredoc(`-m "$(cat <<'EOF' ...)"`) 형태는 첫 줄을 추출한다. 추출 불가
  (`-F`, 에디터, `--amend --no-edit`)와 빈 제목은 메시지 검사만 통과시키고
  브랜치 검사는 그대로 수행한다.
- 모든 차단 메시지는 `ATOM_COMMIT_OVERRIDE=1` 재실행 안내를 포함한다 —
  오차단은 언제나 복구 가능해야 한다.
- 감지 못하는 형태(`bash -c` 내부, 스크립트 경유)와 push 차단은 v1 범위
  밖이며 fail-open 방향의 한계다(commit 차단으로 main에 신규 커밋 자체가
  생기지 않아 실효 공백은 작다 — 잔여 경로는 #4에서 검토).
- 브랜치 변경 판정의 알려진 한계 — 통과(fail-open) 방향:
  - `--` 없이 경로를 지정하는 복원(`git checkout HEAD~1 src/foo.py`), 대화형
    `-p`, 인자 없는 `git checkout`은 브랜치 변경으로 오분류돼 브랜치 검사가
    생략된다. ref와 경로의 구분은 저장소 조회 없이는 불가능하다.
  - 이전 브랜치(`git checkout -`, `@{-1}`), 셸 변수·glob 대상(shlex는 확장
    하지 않는다), 분리형 옵션 값(`--conflict merge main`), 단축 옵션의
    클러스터·결합값(`-qt origin/main`, `-tdirect origin/main`)은 대상을
    알아내지 못한다.
  - `--track`의 ref에서 이름을 파생할 수 없는 형태(`--track foo`)도 마찬가지다.
    git이 브랜치 생성을 포기하는 무효 명령이라 `&&`로는 커밋이 돌지 않는다.
  - 래치는 커밋의 `git -C <path>`도 조건 실행(`||`)도 고려하지 않고, 한 번
    켜지면 뒤 세그먼트가 되돌리지 못한다 — `git checkout feat/x && git
    checkout main && git commit`은 통과한다. 비보호 브랜치에서 출발하는
    `git checkout main && git commit`도 통과한다(둘 다 #44).
- 같은 판정의 한계 — 차단(오차단) 방향:
  - 감지는 세그먼트 선두가 `git`인 경우만 본다(`env`/`xargs`/별칭 경유는
    브랜치 변경으로 인식되지 않아 오차단이 남는다).
  - 폴백(shlex 실패) 경로는 checkout을 모른다 — 명령 텍스트만으로는 언급과
    실행을 구분할 수 없어 새 구멍을 만들지 않는 쪽을 택했다(#45).

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

# override 마커: 규칙 예외를 선언했다는 표시. 세그먼트 선두에 있으면 통과.
OVERRIDE_TOKEN = "ATOM_COMMIT_OVERRIDE=1"

# 차단 sentinel 종료 코드. Claude Code의 차단 코드는 2지만 uv(자체 오류 2)와
# python(예외 1, CLI 오류 2)이 같은 코드를 낼 수 있어, exec 배선에서는 도구
# 실패가 차단으로 샜다(#31). 자연 발생 불가능한 42를 반환하고 settings.json의
# 셸 래퍼가 42→2로 되매핑하며, 그 외 nonzero는 전부 1(비차단 경고)로 수렴한다.
EXIT_BLOCK = 42

# shlex(punctuation_chars=True)가 별도 토큰으로 분리하는 셸 연산자.
OPERATORS = {"&&", "||", "|", ";", ";;", "&", "(", ")"}

PROTECTED_BRANCHES = {"main", "master"}

# 커밋이 얹힐 브랜치를 바꿀 수 있는 서브커맨드.
BRANCH_CHANGE_SUBCOMMANDS = {"checkout", "switch"}

# 브랜치를 새로 만들며 이동하는 플래그 (checkout -b/-B, switch -c/-C).
NEW_BRANCH_FLAGS = {"-b", "-B", "-c", "-C"}

# `-b`/`-c` 없이도 원격 추적 ref에서 이름을 따 브랜치를 만드는 플래그.
# 값 결합형(`--track=direct|inherit`)은 `_checkout_target`에서 접두 비교로 잡는다.
TRACK_FLAGS = {"-t", "--track"}

GIT_TIMEOUT_SECONDS = 10

COMMIT_TYPES = ("feat", "fix", "refactor", "test", "docs", "chore", "build", "perf", "style")

_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Conventional Commits 헤더: type(scope)?!?: subject
_HEADER_RE = re.compile(rf"^({'|'.join(COMMIT_TYPES)})(\([a-z0-9-]+\))?!?: (.+)$")

# 결합 단축 플래그 포함 메시지 플래그 (-m, -am, -sm ...).
_MESSAGE_FLAG_RE = re.compile(r"^-[a-zA-Z]*m$")

# heredoc 마커 다음 첫 줄 추출: <<EOF / <<'EOF' / << "EOF" 변형 모두.
_HEREDOC_FIRST_LINE_RE = re.compile(r"<<\s*['\"]?\w+['\"]?[ \t]*\n([^\n]*)")

# shlex 실패 시 폴백: 명령 위치(문자열 시작 또는 연산자 뒤)에 앵커된 감지.
_FALLBACK_CMD_RE = re.compile(
    r"(?:^|[;&|(]\s*)(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:\S*/)?git\s+(?:-\S+\s+)*commit\b",
    re.MULTILINE,
)
_FALLBACK_MESSAGE_RE = re.compile(r"(?:--message|-[a-zA-Z]*m)(?:=|\s+)(?:\"([^\"\n]*)\"|'([^'\n]*)')")
_FALLBACK_C_PATH_RE = re.compile(r"git\s+(?:-\S+\s+)*-C\s+(\S+)")
_FALLBACK_CD_RE = re.compile(r"(?:^|[;&|(]\s*)cd\s", re.MULTILINE)


@dataclass
class CommitInvocation:
    """감지된 git commit 명령 하나.

    Attributes:
        subject: 추출된 커밋 메시지 제목(첫 줄). 추출 불가면 None.
        c_path: `git -C`로 지정된 대상 디렉터리. 없으면 None.
        override: 세그먼트 선두에 ATOM_COMMIT_OVERRIDE=1이 있었는지.
        branch_check_unsafe: 선행 `cd`나 브랜치 변경(`git checkout`/`git switch`)
            때문에 커밋이 얹힐 브랜치가 불명인지.
    """

    subject: str | None
    c_path: str | None
    override: bool
    branch_check_unsafe: bool


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _tokenize(text: str) -> list[str]:
    """셸 문법을 인식해 토큰화한다 (dup guard와 동일한 규약의 의식적 복제).

    따옴표 문자열은 단일 토큰이 되고 연산자는 별도 토큰으로 분리된다.

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


def _extract_subject(raw_message: str | None) -> str | None:
    """-m 인자 토큰에서 제목(첫 줄)을 뽑는다.

    Args:
        raw_message: shlex가 돌려준 -m 인자 값. heredoc 치환식이면
            `$(cat <<'EOF' ...)` 전체가 한 토큰으로 들어온다.

    Returns:
        제목 문자열. 추출 불가·빈 제목이면 None(→ 메시지 검사 fail-open).
    """
    if raw_message is None:
        return None
    if "$(" in raw_message:
        match = _HEREDOC_FIRST_LINE_RE.search(raw_message)
        if not match:
            return None
        subject = match.group(1)
    else:
        subject = raw_message.split("\n", 1)[0]
    subject = subject.strip()
    return subject or None


def _git_subcommand(
    segment: list[str],
) -> tuple[str, list[str], str | None, bool] | None:
    """세그먼트가 실행하는 git 서브커맨드를 뽑는다.

    명령 위치 판정: 선행 VAR=val 할당을 건너뛴 첫 토큰이 git이고, git 글로벌
    옵션(`-C <path>`, `-c k=v` 등 `-` 시작 토큰)을 지나 처음 만나는 토큰이
    서브커맨드다. 인자 위치의 리터럴("git commit" 언급)은 대상이 아니다.

    Args:
        segment: 연산자로 분리된 토큰 세그먼트.

    Returns:
        (서브커맨드, 그 뒤 인자들, `-C` 경로, override 여부) 튜플.
        git 명령이 아니거나 서브커맨드가 없으면 None.
    """
    index = 0
    override = False
    while index < len(segment) and _ENV_ASSIGNMENT_RE.match(segment[index]):
        if segment[index] == OVERRIDE_TOKEN:
            override = True
        index += 1
    rest = segment[index:]
    if not rest or _basename(rest[0]) != "git":
        return None

    c_path: str | None = None
    i = 1
    while i < len(rest) and rest[i].startswith("-"):
        if rest[i] == "-C" and i + 1 < len(rest):
            c_path = rest[i + 1]
            i += 2
        elif rest[i] == "-c" and i + 1 < len(rest):
            i += 2
        else:
            # 값 결합형(--git-dir=x 등)은 단일 토큰이라 그냥 건너뛴다.
            i += 1
    if i >= len(rest):
        return None
    return rest[i], rest[i + 1:], c_path, override


def _parse_segment(segment: list[str]) -> tuple[str | None, str | None, bool] | None:
    """세그먼트 하나에서 git commit 명령을 파싱한다.

    Args:
        segment: 연산자로 분리된 토큰 세그먼트.

    Returns:
        (subject, c_path, override) 튜플, 대상이 아니면 None.
    """
    parsed = _git_subcommand(segment)
    if parsed is None:
        return None
    subcommand, args, c_path, override = parsed
    if subcommand != "commit":
        return None

    raw_message: str | None = None
    j = 0
    while j < len(args):
        token = args[j]
        if _MESSAGE_FLAG_RE.match(token) or token == "--message":
            if j + 1 < len(args):
                raw_message = args[j + 1]
            break
        if token.startswith("--message="):
            raw_message = token[len("--message="):]
            break
        j += 1
    return _extract_subject(raw_message), c_path, override


def _detect_fallback(command: str) -> list[CommitInvocation]:
    """shlex가 실패한 명령에 대한 보수적 정규식 감지.

    잔여 오탐 가능성은 있으나 모든 오차단은 ATOM_COMMIT_OVERRIDE=1 재실행으로
    복구 가능하다.

    Args:
        command: 원본 명령 문자열.

    Returns:
        감지된 커밋 명령 목록 (폴백에서는 최대 1건으로 요약).
    """
    if not _FALLBACK_CMD_RE.search(command):
        return []
    subject: str | None = None
    heredoc = _HEREDOC_FIRST_LINE_RE.search(command)
    if heredoc:
        subject = heredoc.group(1).strip() or None
    else:
        quoted = _FALLBACK_MESSAGE_RE.search(command)
        if quoted:
            subject = next((g for g in quoted.groups() if g is not None), None)
            subject = (subject or "").strip() or None
    c_path_match = _FALLBACK_C_PATH_RE.search(command)
    return [
        CommitInvocation(
            subject=subject,
            c_path=c_path_match.group(1) if c_path_match else None,
            override=OVERRIDE_TOKEN in command,
            branch_check_unsafe=bool(_FALLBACK_CD_RE.search(command)),
        )
    ]


def _tracked_branch_name(ref: str | None) -> str | None:
    """`--track`이 원격 추적 ref에서 파생시키는 새 로컬 브랜치 이름을 구한다.

    `refs/`·`remotes/` 접두를 벗긴 뒤 **첫 경로 성분 하나만** 잘라낸다. 실측
    기준(git 2.53): `origin/main`은 `main`, `origin/topic`은 `topic`이지만
    `origin/feature/x`는 `x`가 아니라 `feature/x`다. 실행 파일 경로용
    `_basename`(rsplit)을 여기 재사용하면 마지막 성분만 남아 틀린다.

    Args:
        ref: `--track`/`-t`와 함께 주어진 원격 추적 ref 토큰.

    Returns:
        파생된 로컬 브랜치 이름. `/`가 없어 git이 추측을 포기하는 형태면 None.
    """
    if ref is None:
        return None
    for prefix in ("refs/", "remotes/"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
    _, slash, name = ref.partition("/")
    return name if slash and name else None


def _checkout_target(args: list[str]) -> str | None:
    """checkout/switch가 이동할 대상 브랜치 이름을 찾는다.

    git의 이름 결정 순서를 따른다. (1) 생성 플래그(`-b`/`-B`/`-c`/`-C`)가 있으면
    위치와 무관하게 그 뒤 토큰이 대상이다 — `git checkout main -b feat/x`도
    `feat/x`를 만든다. (2) 없이 `--track`/`-t`만 있으면 원격 추적 ref에서 파생된
    이름이 대상이다 — `--track origin/main`은 로컬 `main`을 만든다. (3) 둘 다
    없으면 첫 비플래그 토큰이 대상이다 — `-b feat/x main`의 시작점 `main`은
    대상이 아니다.

    Args:
        args: 서브커맨드 뒤 인자 토큰들.

    Returns:
        대상 브랜치 이름. 판정 불가면 None.
    """
    for i, token in enumerate(args):
        if token in NEW_BRANCH_FLAGS:
            return args[i + 1] if i + 1 < len(args) else None
    positional = next((t for t in args if not t.startswith("-")), None)
    if any(t in TRACK_FLAGS or t.startswith("--track=") for t in args):
        return _tracked_branch_name(positional)
    return positional


def _makes_branch_check_unsafe(segment: list[str]) -> bool:
    """이 세그먼트가 이후 커밋의 브랜치 판정 기준을 불명으로 만드는지 판정한다.

    `cd`는 저장소 자체가 바뀌고, 브랜치를 바꾸는 `git checkout`/`git switch`는
    커밋이 얹힐 브랜치가 바뀐다 — 둘 다 hook이 실행 전에 조회한 브랜치를
    쓸모없게 만든다. 단 브랜치를 바꾸지 않거나(경로 복원) 대상이 보호
    브랜치인 경우는 검사를 유지해야 하므로 제외한다.

    Args:
        segment: 연산자로 분리된 토큰 세그먼트.

    Returns:
        이후 커밋의 브랜치 검사를 건너뛰어야 하면 True.
    """
    first = next((t for t in segment if not _ENV_ASSIGNMENT_RE.match(t)), None)
    if first is not None and _basename(first) == "cd":
        return True

    parsed = _git_subcommand(segment)
    if parsed is None:
        return False
    subcommand, args, _, _ = parsed
    if subcommand not in BRANCH_CHANGE_SUBCOMMANDS:
        return False
    # `git checkout [<ref>] -- <path>`는 경로 복원이라 브랜치를 안 바꾼다.
    # 뒤가 빈 `--`는 복원이 아니라 실제로 브랜치를 바꾼다(git 2.53 확인).
    if subcommand == "checkout" and "--" in args and args.index("--") < len(args) - 1:
        return False
    # 대상이 보호 브랜치면 커밋이 거기에 얹히므로 검사를 건너뛰면 안 된다.
    # `--track origin/main`처럼 원격 ref에서 이름을 따는 형태도 여기에 포함된다.
    # 단 앞선 세그먼트가 이미 래치를 켰다면 이 판정은 뒤집지 못한다(#44).
    return _checkout_target(args) not in PROTECTED_BRANCHES


def detect_invocations(command: str) -> list[CommitInvocation]:
    """Bash 명령 문자열에서 모든 git commit 명령을 감지한다.

    줄 단위 토큰화 → 전체 문자열 토큰화 → 정규식 폴백 순서로 내려간다
    (dup guard와 동일한 전략). 커밋보다 앞선 세그먼트가 브랜치 판정 기준을
    불명으로 만들면(`_makes_branch_check_unsafe`) 이후 커밋들을 unsafe로
    표시한다.

    Args:
        command: Bash 도구가 실행하려는 명령 전체.

    Returns:
        감지된 커밋 명령 목록. 대상이 없으면 빈 목록.
    """
    token_groups: list[list[str]] | None = None
    try:
        token_groups = [_tokenize(line) for line in command.split("\n")]
    except ValueError:
        try:
            token_groups = [_tokenize(command)]
        except ValueError:
            return _detect_fallback(command)

    invocations: list[CommitInvocation] = []
    branch_unsafe = False
    for tokens in token_groups:
        for segment in _split_segments(tokens):
            parsed = _parse_segment(segment)
            if parsed is None:
                if _makes_branch_check_unsafe(segment):
                    branch_unsafe = True
            else:
                subject, c_path, override = parsed
                invocations.append(
                    CommitInvocation(
                        subject=subject,
                        c_path=c_path,
                        override=override,
                        branch_check_unsafe=branch_unsafe,
                    )
                )
    return invocations


def _current_branch(cwd: str | None, c_path: str | None) -> str | None:
    """현재 브랜치 이름을 조회한다.

    Args:
        cwd: hook 페이로드가 알려준 Bash 작업 디렉터리 (없으면 프로세스 cwd).
        c_path: 명령의 `git -C` 인자 (있으면 동일하게 전달).

    Returns:
        브랜치 이름. 실패(비 git 디렉터리·git 부재·timeout)는 None(→ 통과).
    """
    argv = ["git"]
    if c_path:
        argv += ["-C", c_path]
    argv += ["rev-parse", "--abbrev-ref", "HEAD"]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def validate_subject(subject: str) -> str | None:
    """커밋 제목의 Conventional Commits 위반을 찾는다.

    Args:
        subject: 커밋 메시지 첫 줄.

    Returns:
        위반 설명 문자열, 규격에 맞으면 None.
    """
    match = _HEADER_RE.match(subject)
    if not match:
        return (
            "header must be 'type(scope): subject' with type in "
            f"{{{', '.join(COMMIT_TYPES)}}} and a lowercase kebab-case scope"
        )
    description = match.group(3)
    if description[0].isupper():
        return "subject must not start with an uppercase letter"
    if description.rstrip().endswith("."):
        return "subject must not end with a period"
    if len(description) > 50:
        return f"subject is {len(description)} chars (max 50)"
    return None


def _block_message(reason: str) -> str:
    return "\n".join(
        [
            f"[commit-guard] {reason}",
            "See the commit-discipline rule (meta/rules/commit-discipline.md).",
            "If this commit is a deliberate exception, re-run the SAME command "
            f"prefixed with: {OVERRIDE_TOKEN} git commit ...",
        ]
    )


def main() -> int:
    """stdin의 PreToolUse JSON을 판정한다.

    Returns:
        종료 코드 (0 통과, 1 내부 경고, 42 차단 — 래퍼가 2로 되매핑).
    """
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        print("[commit-guard] malformed hook input (fail-open)", file=sys.stderr)
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
        if not invocation.branch_check_unsafe:
            branch = _current_branch(cwd, invocation.c_path)
            if branch in PROTECTED_BRANCHES:
                print(
                    _block_message(
                        f"direct commit to '{branch}' is blocked — create a "
                        "feature branch (type/short-description) and merge via PR"
                    ),
                    file=sys.stderr,
                )
                return EXIT_BLOCK
        if invocation.subject is not None:
            problem = validate_subject(invocation.subject)
            if problem is not None:
                print(
                    _block_message(
                        f"commit message {invocation.subject!r} rejected: {problem}"
                    ),
                    file=sys.stderr,
                )
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
        print(f"[commit-guard] internal error (fail-open): {exc}", file=sys.stderr)
        return 1
