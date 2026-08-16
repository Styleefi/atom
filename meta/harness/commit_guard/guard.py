# main 직커밋과 비규격 커밋 메시지를 차단하는 PreToolUse hook
"""커밋 규율 강제 hook (commit-guard 규칙의 배포체).

Claude Code의 PreToolUse(Bash) hook으로 실행되어 `git commit` 명령을 감지하면
두 가지를 기계적으로 강제한다.

1. 보호 브랜치(main/master) 직커밋 차단 — 피처 브랜치 + PR 워크플로 강제.
2. 커밋 메시지 제목의 Conventional Commits 형식 검사 — 타입 화이트리스트,
   대문자 시작 금지, 마침표 금지, 제목 50자 이하.

행동 지침(논리 단위 커밋, 브랜치 명명, PR 전용 머지, push 금지)은 claude-md
쪽 commit-discipline 규칙이 담당한다 — 이 hook은 기계 검사 가능한 부분만.

**동결 선언 (#52): 이 모듈의 감지 구멍은 더 고치지 않는다.** 텍스트 추론은
셸 의미론을 판정할 수 없고, PR #46이 실측했듯 구멍을 하나 메울 때마다 새
구멍이 생긴다. 이 모듈의 역할은 흔한 형태의 사전 차단(best-effort 예방)까지다.
정확한 적발은 실행 후 커밋 그래프를 보는 commit_backstop(PostToolUse)이
담당하되, 그 hook 자신의 비주장 범위 안에서만이다(commit_backstop 모듈
docstring이 SSOT). #52의 표에 정리된 일곱 이슈 계열(#30 #44 #45 #47 #49
#50 #51)은 모두 #52로 종결됐다 — 여섯은 구멍을 남긴 채 동결해 backstop이
사후에 받고(#47은 #64가 한정한 대로 공유 브랜치 ref 전진에 한하며
test_commit_in_secondary_worktree_blocks가 그 절반을 고정한다), #45는
원인인 정규식 폴백을 삭제해 닫았으며 그 삭제가 연 감지 공백도 같은 방식으로
backstop이 받는다. 아래 한계 목록도 설계상 수용된
상태다. 메시지 검사가 저장소·브랜치와 무관하게 명령 텍스트만으로 차단하는
잔여 오차단(스크래치 저장소 실험 등)도 동결된 한계다. 주석 해석도 리터럴 `#`
보존을 위해 꺼져 있어 주석 뒤 텍스트가 명령으로 읽힌다 — bash는 `git commit
-m "feat(x): a" # ; git commit -m "Bad."`에서 첫 커밋만 실행하지만 두 번째
제목까지 검사해 차단한다(tests의 _KNOWN_FALSE_BLOCKS에 잠금, #74 시금석 —
수리는 텍스트 추론 정련의 재개라 이 동결에 따라 하지 않는다). 복구는 언제나
ATOM_COMMIT_OVERRIDE=1.

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
  복원 형태와, 대상이 보호 브랜치인 이동, 그리고 대상을 읽어내지 못한 경우는
  제외한다 — 대상은 철자뿐 아니라 `--track origin/main`처럼 원격 접두를 벗긴
  이름까지 본다. rev-parse 실패·timeout·detached HEAD(`HEAD`)는 통과.
- 메시지는 첫 `-m`/`--message`(결합 단축 `-am` 등 포함)의 첫 줄만 검사.
  heredoc(`-m "$(cat <<'EOF' ...)"`) 형태는 첫 줄을 추출한다. 추출 불가
  (`-F`, 에디터, `--amend --no-edit`)와 빈 제목은 메시지 검사만 통과시키고
  브랜치 검사는 그대로 수행한다.
- 모든 차단 메시지는 `ATOM_COMMIT_OVERRIDE=1` 재실행 안내를 포함한다 —
  오차단은 언제나 복구 가능해야 한다.
- 감지 못하는 형태(`bash -c` 내부, 스크립트 경유)와 push 차단은 v1 범위
  밖이며 fail-open 방향의 한계다(commit 차단으로 main에 신규 커밋 자체가
  생기지 않아 실효 공백은 작다 — 잔여 경로는 #4에서 검토).
- 브랜치 변경 판정은 **모르면 막는 쪽**으로 기운다. 대상을 읽어내지 못하면
  래치를 세우지 않으므로, 판정 실패는 통과가 아니라 과차단으로 나타난다.
  철자를 바꾸는 것만으로 guard가 무력화되는 일을 막기 위한 선택이다.
  - 그 대가인 오차단: 묶음 단축 옵션(`-qBmain`), 대화형 `-p`, 인자 없는
    `git checkout`, 이전 브랜치(`-`, `@{-1}`), 셸 변수·glob 대상(shlex는
    확장하지 않는다). 브랜치가 실제로 바뀌지 않는 형태도 여기 섞이지만
    전부 `ATOM_COMMIT_OVERRIDE=1`로 복구된다.
  - 대상이 원격 ref 모양이면 접두를 벗긴 이름도 함께 대조하므로,
    `git checkout origin/main`처럼 detached HEAD가 되는 형태나 마지막 성분이
    보호 브랜치와 같은 브랜치(`feat/main`)도 오차단된다.
  - 감지는 세그먼트 선두가 `git`인 경우만 본다(`env`/`xargs`/별칭 경유).
  - shlex가 두 단계(줄 단위→전체) 모두 실패하는 명령은 전면 fail-open이다 —
    정규식 폴백은 문자열 리터럴 속 텍스트를 실행으로 오인해 무관한 Bash를
    차단했으므로 제거했다(#45). 그 감지 공백은 commit_backstop이 받는다.
- 여전히 통과(fail-open)하는 형태:
  - `--` 없이 경로를 지정하는 복원(`git checkout HEAD~1 src/foo.py`)은 대상이
    ref로 읽혀 래치가 켜진다. ref와 경로의 구분은 저장소 조회 없이는 불가능하다.
  - 분리형 옵션 값이 위치 인자를 가리는 형태(`--conflict merge main`).
  - **실행되지 않는 checkout도 래치를 켠다.** 래치는 명령 텍스트만 보므로
    heredoc 본문에 적힌 checkout, 실패한 checkout(브랜치가 이미 있어서),
    단락된 `false && git checkout …`, 선택되지 않은 `if`/`case` 가지가 모두
    검사를 끈다. 실행 여부 판정은 셸 의미론이 필요해 범위 밖이다(#49).
  - 래치는 커밋의 `git -C <path>`도 조건 실행(`||`)도 고려하지 않는다.
  - 비보호 브랜치에서 출발하는 `git checkout main && git commit`은 통과한다 —
    hook이 실행 전에 조회한 브랜치가 아직 비보호라 막을 근거가 없다(#44).

차단과 오버라이드 통과는 harness.blocklog 원장에 한 줄씩 남는다(#76) — 이 가드의
마찰 비용을 트랜스크립트 재구성 없이 세기 위한 것이다. 기록은 best-effort이며
실패는 침묵으로 삼킨다. 로깅은 어떤 경우에도 판정에 영향을 주지 않는다(_log 참조).

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
# 셸 래퍼가 42만 차단(2)으로 되매핑하고 그 외 nonzero는 1(비차단 경고)로
# 수렴시키는 설계다(계약은 rules_checker의 test_hook_command_contract.py에 있다).
EXIT_BLOCK = 42

# shlex(punctuation_chars=True)가 별도 토큰으로 분리하는 셸 연산자.
OPERATORS = {"&&", "||", "|", ";", ";;", "&", "(", ")"}

PROTECTED_BRANCHES = {"main", "master"}

# 커밋이 얹힐 브랜치를 바꿀 수 있는 서브커맨드.
BRANCH_CHANGE_SUBCOMMANDS = {"checkout", "switch"}

# 브랜치를 새로 만들며 이동하는 플래그 (checkout -b/-B, switch -c/-C).
NEW_BRANCH_FLAGS = {"-b", "-B", "-c", "-C"}

# 같은 뜻의 긴 이름 (switch 전용). 값은 `=`로 붙여 온다.
NEW_BRANCH_LONG_FLAGS = ("--create=", "--force-create=")

GIT_TIMEOUT_SECONDS = 10

COMMIT_TYPES = ("feat", "fix", "refactor", "test", "docs", "chore", "build", "perf", "style")

_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Conventional Commits 헤더: type(scope)?!?: subject
_HEADER_RE = re.compile(rf"^({'|'.join(COMMIT_TYPES)})(\([a-z0-9-]+\))?!?: (.+)$")

# 결합 단축 플래그 포함 메시지 플래그 (-m, -am, -sm ...).
_MESSAGE_FLAG_RE = re.compile(r"^-[a-zA-Z]*m$")

# heredoc 마커 다음 첫 줄 추출: <<EOF / <<'EOF' / << "EOF" 변형 모두.
_HEREDOC_FIRST_LINE_RE = re.compile(r"<<\s*['\"]?\w+['\"]?[ \t]*\n([^\n]*)")

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


def _remote_stripped(ref: str | None) -> str | None:
    """원격 추적 ref에서 파생되는 로컬 브랜치 이름을 구한다.

    `refs/`·`remotes/` 접두를 벗긴 뒤 **첫 경로 성분 하나만** 잘라낸다. 실측
    기준(git 2.53): `--track origin/main`은 로컬 `main`을, `origin/topic`은
    `topic`을 만들지만 `origin/feature/x`는 `x`가 아니라 `feature/x`다. 실행
    파일 경로용 `_basename`(rsplit)을 여기 재사용하면 마지막 성분만 남아 틀린다.

    Args:
        ref: 대상 토큰.

    Returns:
        접두를 벗긴 이름. `/`가 없어 벗길 것이 없으면 None.
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

    생성 플래그(`-b`/`-B`/`-c`/`-C`)가 있으면 위치와 무관하게 그 값이 대상이다 —
    `git checkout main -b feat/x`도 `feat/x`를 만든다. 값은 다음 토큰으로도
    (`-b feat/x`), 붙여서도(`-bfeat/x`), 긴 이름의 `=` 형태로도
    (`--create=feat/x`) 올 수 있다. 생성 플래그가 없으면 첫 비플래그 토큰이
    대상이다 — `-b feat/x main`의 시작점 `main`은 대상이 아니다.

    묶음 단축 옵션(`-qBmain`)처럼 git의 parse-options를 흉내내야만 풀리는
    형태는 일부러 풀지 않는다. 그런 경우 None을 돌려주고, 호출부가 "대상 불명"을
    검사 유지 쪽으로 처리한다.

    Args:
        args: 서브커맨드 뒤 인자 토큰들.

    Returns:
        대상 브랜치 이름. 판정 불가면 None.
    """
    for i, token in enumerate(args):
        if token in NEW_BRANCH_FLAGS:
            return args[i + 1] if i + 1 < len(args) else None
        for prefix in NEW_BRANCH_LONG_FLAGS:
            if token.startswith(prefix):
                return token[len(prefix):] or None
        if not token.startswith("--") and token[:2] in NEW_BRANCH_FLAGS and len(token) > 2:
            return token[2:]
    return next((t for t in args if not t.startswith("-")), None)


def _changes_repository(segment: list[str]) -> bool:
    """세그먼트가 작업 저장소 자체를 옮기는지 판정한다.

    `cd` 뒤로는 페이로드의 `cwd`로 조회한 브랜치가 어느 저장소 것인지 알 수
    없다. 되돌릴 방법이 없으므로 이 표시는 한 번 켜지면 유지된다.

    Args:
        segment: 연산자로 분리된 토큰 세그먼트.

    Returns:
        저장소가 바뀌면 True.
    """
    first = next((t for t in segment if not _ENV_ASSIGNMENT_RE.match(t)), None)
    return first is not None and _basename(first) == "cd"


def _branch_latch(segment: list[str]) -> bool | None:
    """브랜치 변경 세그먼트가 이후 커밋의 브랜치 검사를 어떻게 바꾸는지 판정한다.

    브랜치를 바꾸는 `git checkout`/`git switch`는 hook이 실행 전에 조회한
    브랜치를 쓸모없게 만든다. 단 대상이 보호 브랜치이거나 **대상을 읽어내지
    못한 경우**는 검사를 유지한다. 후자가 핵심이다 — 판정 실패가 곧 통과가
    되면 철자를 바꾸는 것만으로 guard가 무력화되므로 모르면 막는 쪽으로 기운다.

    Args:
        segment: 연산자로 분리된 토큰 세그먼트.

    Returns:
        검사를 건너뛰어야 하면 True, 유지해야 하면 False.
        브랜치와 무관한 세그먼트면 None(기존 판정을 그대로 둔다).
    """
    parsed = _git_subcommand(segment)
    if parsed is None:
        return None
    subcommand, args, c_path, _ = parsed
    if subcommand not in BRANCH_CHANGE_SUBCOMMANDS:
        return None
    # 다른 저장소의 브랜치를 바꾼 것이므로 이 저장소의 판정 기준은 그대로다.
    if c_path is not None:
        return None
    # `git checkout [<ref>] -- <path>`는 경로 복원이라 브랜치를 안 바꾼다.
    # 뒤가 빈 `--`는 복원이 아니라 실제로 브랜치를 바꾼다(git 2.53 확인).
    if subcommand == "checkout" and "--" in args and args.index("--") < len(args) - 1:
        return None
    target = _checkout_target(args)
    if target is None:
        return False
    # 대상이 보호 브랜치면 커밋이 거기에 얹히므로 검사를 건너뛰면 안 된다.
    # 원격 접두를 벗긴 형태도 함께 본다 — `--track origin/main`은 로컬 `main`을
    # 만들고, `-qt origin/main`처럼 플래그를 못 알아본 경우도 이쪽에서 걸린다.
    return (
        target not in PROTECTED_BRANCHES
        and _remote_stripped(target) not in PROTECTED_BRANCHES
    )


def detect_invocations(command: str) -> list[CommitInvocation]:
    """Bash 명령 문자열에서 모든 git commit 명령을 감지한다.

    줄 단위 토큰화 → 전체 문자열 토큰화 순서로 내려가고, 둘 다 실패하면
    전면 fail-open이다(정규식 폴백은 #45 오차단으로 제거 — 공백은
    commit_backstop이 받는다). 판정 기준은 두 갈래로 추적한다. `cd`는 저장소
    자체를 옮기므로 한 번 켜지면 유지되고(되돌릴 방법이 없다), 브랜치 변경은
    **마지막 것이 이긴다** — `git checkout feat/x && git checkout main`이면
    커밋은 main에 얹히므로 검사를 되살려야 한다.

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
            return []

    invocations: list[CommitInvocation] = []
    other_repository = False
    branch_unsafe = False
    for tokens in token_groups:
        for segment in _split_segments(tokens):
            parsed = _parse_segment(segment)
            if parsed is not None:
                subject, c_path, override = parsed
                invocations.append(
                    CommitInvocation(
                        subject=subject,
                        c_path=c_path,
                        override=override,
                        branch_check_unsafe=other_repository or branch_unsafe,
                    )
                )
            elif _changes_repository(segment):
                other_repository = True
            else:
                latch = _branch_latch(segment)
                if latch is not None:
                    branch_unsafe = latch
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


def _log(**kwargs) -> None:
    """차단 이력 원장에 한 줄을 남긴다 — 절대 제어 흐름에 영향을 주지 않는다.

    맨몸 호출을 금지하는 이유가 둘이다. (1) 인자 불일치 TypeError는 record_block
    본문 진입 **전에** 나므로 그 안의 방어가 못 잡고, 예외가 main() 밖으로 나가면
    run()이 1을 반환해 **차단이 통과로 강등된다**(래퍼는 42만 2로 되매핑한다).
    (2) 모듈 최상단 import면 blocklog가 import 불가능해질 때(자식 프로젝트가 이
    모듈을 제거·수정한 경우) 가드 자체가 죽어 차단 기능 전체가 사라진다.

    대가: 예외를 삼키므로 호출부 키워드 오타가 조용한 무기록이 된다. 그래서
    호출부 3곳을 각각 확인하는 테스트는 선택이 아니라 이 설계의 필수 조건이다.
    """
    try:
        from harness.blocklog.blocklog import record_block

        record_block(**kwargs)
    except Exception:  # noqa: BLE001 — fail-open이 설계 요구사항
        pass


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
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        session_id = None

    for invocation in detect_invocations(command):
        if invocation.override:
            _log(
                event="override",
                harness="commit-guard",
                reason=None,
                command=command,
                cwd=cwd,
                session_id=session_id,
            )
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
                _log(
                    event="block",
                    harness="commit-guard",
                    reason="protected-branch",
                    command=command,
                    cwd=cwd,
                    session_id=session_id,
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
                _log(
                    event="block",
                    harness="commit-guard",
                    reason="subject-rejected",
                    command=command,
                    cwd=cwd,
                    session_id=session_id,
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
