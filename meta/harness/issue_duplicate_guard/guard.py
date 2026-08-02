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

**동결 선언 (#74, owner 결정 2026-08-02): 세그먼테이션 계층 — _tokenize ·
_protected_flags · _is_operator · _split_segments · _join_continuations — 의
잔여 bash 충실도 구멍은 더 수리하지 않는다.** 토큰 내용으로 셸 구조를
추론하는 게임은 끝이 없다(동결된 _mask_arithmetic·sibling commit_guard의
#52와 같은 결론이며, #67~#69의 수리가 #71→#72→#75 연쇄를 낳은 것이 실측
근거다). **코퍼스(tests의 _SEGMENT_CORPUS·_KNOWN_FALSE_BLOCKS)가 이 계층의
계약이다.** 이후 발견되는 구멍의 처리는 방향과 무관하게 기록이 기본이다.

- 통과 방향(감지 누락)은 코퍼스에 bash 실측 기대값과 함께 기록하고 아래
  한계 목록에 남긴다. 이슈로 승격하지 않는다.
- 오차단은 **언제나 기록한다** — 구성(리뷰·퍼즈·조사) 발견은 발견한 세션이
  그 자리에서 _KNOWN_FALSE_BLOCKS에 기록하고, 실사용 발생의 자동 기록은
  차단 이력 로그(#76)가 맡는다(#76 구현 전에는 세션 트랜스크립트 조사가
  유일한 기록 경로다). 게이트되는 것은 수리뿐이다. 이슈 신설·수리·동결
  해제는 반복 발생 등 실증된 비용을 근거로 한 owner 결정 사안이며, 동결을
  인용한 보고 묵살은 허용되지 않는다. 오차단 복구는 언제나
  ATOM_DUP_REVIEWED=1(관측된 전 사례가 재시도 1회로 복구 — #74 결정
  코멘트의 실측).

_strip_heredocs는 이 동결의 대상이 아니며, 자신의 docstring에 문서화된
한계 체제(마스킹 스캐너 동결이 참조하는 그 체제)를 유지한다.

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
  posix 토큰화는 따옴표와 백슬래시를 벗기므로 인용·이스케이프된 리터럴
  (`echo ";" gh issue create`, `echo \\; gh issue create`)이 진짜 구분자와
  구분되지 않는다. 그래서 같은 텍스트를 non-posix로 한 번 더 토큰화해 보호
  여부를 표시하고(_protected_flags), 보호된 토큰은 연산자로도 예약어로도
  보지 않는다. 표식을 못 만들면(정렬 실패) 토큰 내용을 셸 구조의 근거로 삼는
  판정 두 가지(결합 연산자 토큰·명령 위치 예약어)를 끄고 정확 일치 연산자만
  본다 — **그 두 판정에 한해** 오차단 대신 미감지로 눕는다는 뜻이다.
  정확 일치 경로는 폴백에서도, 정렬에 성공했을 때도 토큰 내용만 보므로 오차단이
  완전히 사라지지는 않는다(인용된 연산자가 명령 이름 자리에 오는 형태, `;;`처럼
  case 밖에서 문법 오류인 토큰 등 — 조건과 수용 근거는 _protected_flags
  docstring 참조). 인용과 무관한 오차단 계열도 있다 — 주석 해석이 리터럴 `#`
  보존을 위해 꺼져 있어(_tokenize) 주석 뒤 텍스트가 명령으로 읽히므로
  `echo x # ; gh issue create -t "T"`(bash 미실행)가 감지·차단되고,
  `gh issue create --help`(gh는 도움말만 출력, 생성 없음)는 세그먼테이션 밖
  _parse_segment가 --help를 모르므로 제목 없는 create로 차단된다(실사용에서
  반복 관측 — 횟수·일자는 tests의 _KNOWN_FALSE_BLOCKS 항목이 담는다. #74
  결정으로 기록만). 알려진 것은 그 표에 잠겨 있으나 망라적이라는 증명은 없다.
- 검색 기준 디렉터리는 hook 페이로드의 `cwd`(Bash 도구의 작업 디렉터리는
  호출 간 유지되므로 프로세스 cwd만으로는 어긋날 수 있다). `--repo`/`-R`가
  없으면 gh/glab이 그 디렉터리로 대상 저장소를 해석하므로, 선행 세그먼트에
  `cd`가 있으면 대상이 불명 → 검색을 건너뛴다(fail-open). 이 표시는 한 번
  켜지면 유지된다. 서브셸에 갇힌 cd(`(cd x) && ...`)도 래치를 켜는 오판과
  `pushd`/`cd -` 미커버는 sibling commit_guard와 동일한 수용 한계다 —
  실행 의미론 판정은 범위 밖.
- 세그먼트 선두(= 명령 위치)의 셸 예약어는 건너뛴다. bash에서 예약어는 명령
  위치에서만 예약어이므로 `if ...; then gh issue create`는 감지하고, 인자
  위치의 같은 단어(`echo then gh issue create`)나 인용된 낱말(`"then" gh
  issue create` — bash는 `then`을 명령으로 찾다 실패한다)은 건드리지 않는다.
- 감지 못하는 형태(`bash -c` 내부, backtick 치환, `env` 프리픽스, 명령을
  인자로 받는 래퍼(`sudo`/`nohup`/`timeout`/`command`/`exec`), 옵션을 동반한
  예약어(`time -p`)와 경로 붙은 형태(`/usr/bin/time`), 함수 정의 계열, 그리고
  유효 bash지만 shlex가 두 단계 모두 실패하는 계열 — 주석 뒤 불균형 따옴표,
  ANSI-C 인용 `$'...\''` 등)는 전부 통과 방향의 한계이며, claude-md 쪽
  issue-workflow 규칙의 관례가 커버한다. 함수 정의는 본문이 단일 명령이면
  (`f() { gh issue create -t T; }`) 선두 토큰이 `f`라 미감지고, 두 개 이상이면
  `;`가 세그먼트를 끊어 **정의 시점에** 감지된다 — 일관성 없는 경계지만
  예약어 도입 이전과 같은 동작이다.

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

# bash 예약어. bash와 동일하게 **명령 위치에서만** 예약어로 취급한다(세그먼트
# 선두에서만 건너뛰고, 인자 위치의 같은 단어는 평범한 토큰이다 — `echo then gh
# issue create`가 걸리면 안 된다). `[[`는 넣지 않는다. 뒤에 오는 것은 명령이
# 아니라 조건식이다.
KEYWORDS = frozenset({
    "if", "then", "elif", "else", "fi", "while", "until", "for", "select",
    "do", "done", "case", "esac", "function", "time", "coproc", "{", "}", "!",
})

# 결합 연산자 토큰 판정용 문자군. shlex는 인접 문장부호를 한 토큰으로 묶으므로
# `((x));`가 `));`라는 토큰을 낳고, OPERATORS 정확 일치로는 경계가 되지 않는다.
# 세 조건이 모두 필요하다(전부 bash 실측 근거).
# - 문자군에 꺾쇠(<>)를 넣지 않는다. 넣으면 `&>`·`>&`·`>|`·`&>>`가 경계가 되어
#   `echo a &> gh issue create -t T`를 오차단한다(bash는 gh라는 파일로 리다이렉션할
#   뿐 create를 실행하지 않는다). 셸의 명령 구분자에는 꺾쇠가 없다.
# - 구분자(;&|)를 요구한다. `))` 단독까지 경계면 `echo $((1<<2)) gh issue create`가
#   오차단된다(bash 출력은 `4 gh issue create ...`, create 미실행).
# - 괄호를 요구한다. 이건 오차단 방어가 아니라 **적용 범위 제한**이다. 인용
#   리터럴의 오차단은 _protected_flags가 막고, 여기서는 결합 토큰이 필요한 실사용
#   동기(`((x));`·`(cmd);` 같은 괄호 닫기 계열)로 판정을 한정한다. 대가로
#   `echo hi |& gh issue create`(bash 실행됨)는 미감지지만 통과 방향이다.
_OPERATOR_CHARS = frozenset("();&|")
_SEPARATOR_CHARS = frozenset(";&|")

FORGE_CLIS = {"gh", "glab"}

SEARCH_TIMEOUT_SECONDS = 15
MAX_CANDIDATES = 10

_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# 인용 표식 복원용 센티널. bash 명령 문자열은 NUL을 담을 수 없으므로(execve 인자가
# NUL 종료) 실제 입력과 충돌하지 않는다. 그래도 입력에 있으면 판별을 포기한다.
_ESCAPE_SENTINEL = "\x00"

# glab 텍스트 출력에서 이슈로 확신할 수 있는 라인(#번호로 시작)만 채택.
_GLAB_ISSUE_LINE_RE = re.compile(r"^#\d+\s+\S.*$")

# heredoc 시작 마커: <<EOF / <<-EOF / <<'EOF' / << "EOF" / <<\EOF 변형.
# (?<!<)와 구분자 문자군이 herestring(<<<)을 배제한다. bare 구분자는 bash가
# WORD로 받는 모든 문자를 허용한다(공백·따옴표·백슬래시·셸 연산자만 제외) —
# 문자군이 좁으면 MY+DELIM 같은 구분자의 종결자를 못 찾아 본문 오차단이 남는다.
_HEREDOC_MARKER_RE = re.compile(
    r"(?<!<)<<(-?)\s*\\?(?:'([^'\n]+)'|\"([^\"\n]+)\"|([^\s'\"\\<>|&;()]+))"
)

class _Frame:
    """전역 문맥 스택의 프레임 하나 (종류 + 진입 시점 인용 상태).

    push 시 인용 상태를 저장하고 pop 시 복원해, cmdsub/brace/arith가
    큰따옴표 안에서 열리고 닫혀도 바깥 인용 경계가 역전되지 않는다
    (bash 관용구 `"A $(echo "B") C"` 실측 근거).
    """

    __slots__ = ("kind", "saved_single", "saved_double", "marks", "brace_depth",
                 "opened_in_double")

    def __init__(self, kind: str, saved_single: bool, saved_double: bool,
                 marks: list[int] | None = None) -> None:
        self.kind = kind
        self.saved_single = saved_single
        self.saved_double = saved_double
        self.marks = marks          # arith 그룹 하단 프레임만 버퍼 보유
        self.brace_depth = 1        # brace 프레임 전용 (중첩 리터럴 중괄호)
        # brace 프레임 전용: 큰따옴표 안에서 열렸는지. bash는 `"${...}"`
        # 내부에서 작은따옴표를 리터럴로 두지만(`"${u:-'$((1<<2))'}"` →
        # `'4'`), 인용 밖 `${...}` 내부의 작은따옴표는 진짜 인용이다
        # (`${u:-'$((1<<2))'}` → 리터럴 출력 — 실측). 닫는 `}` 판정은
        # brace-로컬 인용 상태로 한다(`"${x:-"}"}"`의 인용된 `}`는 안 닫힘).
        self.opened_in_double = saved_double


# 닫는 괄호가 pop할 수 있는 프레임 — brace는 `}`로만, 백틱은 백틱으로만
# 닫힌다 (`echo ${var:- ) }`가 유효 bash로 `)`를 출력함을 실측).
_PAREN_POPPABLE = {"arith", "cmdsub", "plain"}


def _mask_arithmetic(line: str) -> str:
    """마커 스캔 전에 한 줄의 산술 스팬을 공백으로 가린 사본을 만든다.

    **동결 선언 (PR #66 리뷰 루프 8라운드, owner 결정): 이 스캐너의 잔여
    bash 충실도 구멍은 더 수리하지 않는다.** 문자 스캐너로 bash 인용·확장
    의미론을 완성하는 게임은 끝이 없다(수리마다 새 구석 — sibling
    commit_guard의 #52와 같은 결론). 동결 시점 기준 bash 차등 퍼즈(약
    9천 유효 케이스)에서 오차단 회귀 0이고, 잔여 불일치는 전부
    _strip_heredocs docstring의 문서화된 한계 가족이다. 이후 발견되는
    구멍은 수리 대신 (1) 오라클 코퍼스(tests의 _ORACLE_CORPUS)에 bash
    실측 기대값과 함께 기록하고 (2) 잔여 한계 목록에 방향과 함께
    문서화해 관리한다 — **코퍼스가 이 스캐너의 계약이다.** 동결 해제는
    실사용 오차단의 실증 등 owner 재결정 사안이다. 오차단 복구는 언제나
    ATOM_DUP_REVIEWED=1.

    전역 문맥 스택 모델. 이전 설계("따옴표·중괄호는 불투명")는 bash가
    큰따옴표·`${...}` 안에서도 확장을 수행한다는 사실과 어긋나
    `echo "count: $((1<<2))"`(관용 입력) 등에서 반증됐다. bash 충실 규칙:

    - 작은따옴표: 완전 불투명 (bash도 모든 확장 억제).
    - 큰따옴표: `$((`(산술)·`$(`(cmdsub)·백틱·`${`는 활성, bare `((`와
      작은따옴표는 리터럴 (bash의 큰따옴표 확장 규칙과 동일).
    - `${...}`: 내부 확장 활성, bare `((`·괄호는 리터럴, 닫힘은 리터럴
      중괄호 깊이를 센 `}` (`${x:-{a}}` 계열). 바깥 인용 상태를 보존하고
      (작은따옴표는 `"${...}"` 안에서 리터럴 — `"${u:-'$((1<<2))'}"` →
      `'4'` 실측), 마스킹 판정에는 투명하다(heredoc이 시작될 수 없는
      텍스트라 `$(( ${x:-1<<2} ))`의 시프트도 마스킹돼야 한다 — 실측).
    - 백틱: `$(`형과 동일한 cmdsub 프레임 — 명령 문맥 리셋, 내부 확장
      감지, 닫힘은 비이스케이프 백틱(인용 내부라도 — bash 실측), `\\`는
      한 문자 소비(백틱 이스케이프 집합과 상태 등가).
    - bare `((`: 명령 문맥(톱레벨 또는 최근접 비-plain 프레임이 cmdsub/
      백틱)에서만 산술 시작. arith 내부에서는 그룹핑(plain), brace
      내부에서는 리터럴(`${var:-((}` 주입 방어).

    상태 생명주기: 모든 비-plain 프레임은 push 시 인용 상태를 저장하고
    로컬 인용 추적을 새로 시작하며 pop 시 복원한다(`"A $(echo "B") C
    $((1<<2))"` 실측). brace 프레임은 추가로 "큰따옴표 안에서 열렸는지"를
    기억해 로컬 문맥의 작은따옴표를 리터럴로 처리한다 — bash는
    `"${u:-'$((1<<2))'}"`에서 `'`를 리터럴로 두고 산술을 수행하지만
    (출력 `'4'`), 인용 밖 `${u:-'...'}`의 `'`는 진짜 인용이다(리터럴
    출력 — 실측). 이 큰따옴표 문맥은 중첩 brace로 상속되고
    (`"${u:-${v:-'$((1<<2))'}}"`도 출력 `'4'` — 실측), cmdsub·백틱
    경계에서는 새 스크립트 문맥이라 끊긴다. 닫는 `}` 판정은 brace-로컬
    인용 상태 기준이라
    `"${x:-y}"`가 정상 닫히고(`}`가 바깥 인용의 리터럴로 삼켜지면 이후
    라인 전체의 마스킹이 죽는다 — 실측 회귀) `"${x:-"}"}"`의 인용된
    `}`는 닫지 않는다. 닫는 괄호는 최상단이 arith/cmdsub/plain일 때만
    pop하고(brace/백틱 위에서는 리터럴), 인용 문맥 내부의 여는·닫는
    괄호는 모두 리터럴이다. 백틱 프레임이 열려 있으면 작은따옴표 안의
    백슬래시도 bash 백틱 렉서의 이스케이프 집합(백틱·달러·백슬래시)
    앞에서만 다음 문자를 소비한다 — 집합 밖 문자 앞에서는 리터럴로 남아
    작은따옴표가 정상 닫힌다(둘 다 실측. 백틱이 큰따옴표 안이면 bash
    집합에 큰따옴표가 추가되지만, 미소비 큰따옴표가 작은따옴표 안에
    떨어져 인용 상태는 등가다 — 배치 매트릭스 56/56 실측). 마스킹 판정은
    plain·brace를 투명하게 본 최근접 프레임이 arith인 문자만 후보로
    삼고, 닫는 괄호는 pop 이전 스택 기준이다.

    알려진 근사(전부 실측 근거): cmdsub 안 인자 위치의 bare `((`를
    산술로 취급하지만 그런 입력은 bash가 문법 오류로 거부해 무해하다.
    `$(((`는 greedy로 `$((` 우선 해석한다 — 괄호가 정합하면 bash 선호와
    일치하고(`echo $(((1<<2)))` → 4), 산술로 닫히지 않아 bash가 cmdsub로
    재해석하는 형태의 과마스킹은 문서화 한계 가족으로 눕는다. `$'...'`
    ANSI-C 인용은 모델링하지 않는다 — 해당 입력은 shlex 이중 실패 계열로
    전면 fail-open에 흡수된다(실측). 이스케이프된 백틱 중첩(`` \\` ``)의
    내부는 감지하지 않는다. 미폐쇄 arith 스팬은 마스킹하지 않는다(여러
    줄 — 문서화 한계). 잔여 한계는 _strip_heredocs docstring 참조.

    Args:
        line: 명령의 한 줄.

    Returns:
        산술 스팬이 공백으로 치환된(길이 보존) 마커 스캔용 사본.
    """
    masked = list(line)
    n = len(line)
    to_mask: list[int] = []
    stack: list[_Frame] = []
    in_single = False
    in_double = False
    escaped = False
    i = 0

    def nearest_kind() -> str | None:
        for frame in reversed(stack):
            if frame.kind != "plain":
                return frame.kind
        return None

    def mask_kind() -> str | None:
        # 마스킹 판정 전용: plain에 더해 brace도 투명하다 — `${...}` 내부는
        # bash에서 heredoc이 시작될 수 없는 텍스트라 산술 마스킹이 관통해야
        # 한다(`$(( ${x:-1<<2} ))` 실측). 명령 문맥 판정(nearest_kind)은
        # brace를 유지해 `${var:-((}` 주입 방어를 지킨다.
        for frame in reversed(stack):
            if frame.kind not in ("plain", "brace"):
                return frame.kind
        return None

    def keep(idx: int) -> None:
        # 마스킹 판정상 최근접 프레임이 arith인 문자만 후보. 버퍼는 해당
        # arith 그룹의 하단 프레임이 보유하고, 그룹이 정상 종료될 때만
        # 커밋된다.
        if mask_kind() != "arith":
            return
        for frame in reversed(stack):
            if frame.kind == "arith" and frame.marks is not None:
                frame.marks.append(idx)
                return

    def push(kind: str, marks: list[int] | None = None) -> None:
        nonlocal in_single, in_double
        frame = _Frame(kind, in_single, in_double, marks)
        # 모든 비-plain 프레임은 독립(로컬) 인용 추적을 시작한다. brace는
        # 추가로 opened_in_double을 기억해, 큰따옴표 안에서 열린 경우
        # 로컬 문맥의 작은따옴표를 리터럴로 처리한다(bash 실측 — 아래
        # `'` 분기). 로컬 리셋이라야 닫는 `}`가 바깥 인용의 리터럴로
        # 삼켜지지 않는다(`"${x:-y}" ; (( 1<<2 ))` 회귀의 원인). 큰따옴표
        # 문맥은 중첩 brace로 상속된다 — bash는
        # `"${u:-${v:-'$((1<<2))'}}"`에서도 안쪽 작은따옴표를 리터럴로
        # 두고 산술을 수행한다(출력 `'4'` — 실측). cmdsub·백틱 경계는
        # 새 스크립트 문맥이라 상속하지 않는다.
        if kind == "brace" and stack and stack[-1].kind == "brace":
            frame.opened_in_double = (
                frame.opened_in_double or stack[-1].opened_in_double
            )
        stack.append(frame)
        if kind != "plain":
            in_single = False
            in_double = False

    def pop_restore() -> None:
        nonlocal in_single, in_double
        frame = stack.pop()
        if frame.kind != "plain":
            in_single = frame.saved_single
            in_double = frame.saved_double
        if frame.kind == "arith" and frame.marks is not None:
            to_mask.extend(frame.marks)

    while i < n:
        c = line[i]

        if escaped:
            escaped = False
            keep(i)
            i += 1
            continue

        # 백틱 닫힘은 인용 상태보다 우선 — bash는 인용 내부라도 비이스케이프
        # 백틱에서 치환을 닫는다(실측). 내부 미폐쇄 프레임은 폐기(arith
        # 버퍼 미커밋 — 보수 방향).
        if c == "`" and any(f.kind == "backtick" for f in stack):
            while stack:
                if stack[-1].kind == "backtick":
                    pop_restore()
                    break
                frame = stack.pop()
                if frame.kind != "plain":
                    in_single = frame.saved_single
                    in_double = frame.saved_double
            i += 1
            continue

        if in_single:
            # 백틱 프레임이 열려 있으면 bash의 백틱 렉서가 인용보다 먼저
            # 이스케이프를 처리한다 — 단 그 이스케이프 집합은 백틱·달러·
            # 백슬래시뿐이다. 집합 밖 문자 앞의 백슬래시는 리터럴로
            # 유지되고 뒤 문자는 정상 처리된다(작은따옴표면 인용이
            # 닫힌다: `'a\' ; ...` → bash 출력 a\ — 실측. 집합 문자는
            # 소비: `'a\`b'` → 출력 a`b — 실측).
            if (c == "\\" and i + 1 < n and line[i + 1] in "`$\\"
                    and any(f.kind == "backtick" for f in stack)):
                escaped = True
                keep(i)
                i += 1
                continue
            if c == "'":
                in_single = False
            keep(i)
            i += 1
            continue

        if c == "\\":
            escaped = True
            keep(i)
            i += 1
            continue

        if in_double:
            if c == '"':
                in_double = False
                keep(i)
                i += 1
                continue
            if c not in ("$", "`"):
                # 큰따옴표 안에서 $ 계열·백틱 외 전부 리터럴 (괄호 포함).
                keep(i)
                i += 1
                continue
            # $ / 백틱은 아래 확장 감지로 내려간다.
        elif c == "'":
            # 큰따옴표 안에서 열린 brace 프레임의 로컬 문맥에서 bash는
            # 작은따옴표를 리터럴로 둔다(`"${u:-'$((1<<2))'}"` → `'4'`).
            top_frame = stack[-1] if stack else None
            if not (top_frame is not None and top_frame.kind == "brace"
                    and top_frame.opened_in_double):
                in_single = True
            keep(i)
            i += 1
            continue
        elif c == '"':
            in_double = True
            keep(i)
            i += 1
            continue

        top = stack[-1] if stack else None
        if top is not None and top.kind == "brace":
            if c == "{":
                top.brace_depth += 1
                keep(i)
                i += 1
                continue
            if c == "}":
                top.brace_depth -= 1
                if top.brace_depth == 0:
                    pop_restore()
                else:
                    keep(i)
                i += 1
                continue

        if c == "$":
            if i + 2 < n and line[i + 1] == "(" and line[i + 2] == "(":
                keep(i)
                keep(i + 1)
                keep(i + 2)
                push("arith", marks=[])
                push("arith")
                stack[-2].marks.extend([i, i + 1, i + 2])
                i += 3
                continue
            if i + 1 < n and line[i + 1] == "(":
                keep(i)
                push("cmdsub")
                i += 2
                continue
            if i + 1 < n and line[i + 1] == "{":
                keep(i)
                push("brace")
                i += 2
                continue
            keep(i)
            i += 1
            continue

        if c == "`":
            # 스택에 백틱 프레임 없음(위에서 확인) → 새로 연다.
            push("backtick")
            i += 1
            continue

        if c == "(":
            if i + 1 < n and line[i + 1] == "(":
                kind = nearest_kind()
                if kind is None or kind in ("cmdsub", "backtick"):
                    # 명령 문맥의 bare (( → 산술 명령.
                    keep(i)
                    keep(i + 1)
                    push("arith", marks=[])
                    push("arith")
                    stack[-2].marks.extend([i, i + 1])
                    i += 2
                    continue
                if kind == "brace":
                    keep(i)
                    i += 1
                    continue
                # arith 내부의 (( → 내부 그룹핑.
                keep(i)
                keep(i + 1)
                push("plain")
                push("plain")
                i += 2
                continue
            if nearest_kind() == "brace":
                keep(i)
                i += 1
                continue
            keep(i)
            push("plain")
            i += 1
            continue

        if c == ")":
            if stack and stack[-1].kind in _PAREN_POPPABLE:
                keep(i)  # pop 이전 스택 기준 판정
                pop_restore()
            else:
                keep(i)  # brace/백틱 위 또는 빈 스택 → 리터럴
            i += 1
            continue

        keep(i)
        i += 1

    # EOL: 스택에 남은 프레임의 arith 버퍼는 미폐쇄 → 폐기(마스킹 안 함).
    for idx in to_mask:
        masked[idx] = " "
    return "".join(masked)


def _join_continuations(command: str) -> str:
    """백슬래시 줄 연속(`\\` + 개행)을 bash처럼 이어 붙인다.

    줄 단위 토큰화는 후행 백슬래시에서 실패하고, 전체 문자열 폴백은 `\\ngh`
    같은 토큰을 만들어 gh가 세그먼트 선두에 오지 못한다. 그래서 결합 없이는
    `echo a && \\` + 개행 + `gh issue create`가 통째로 미감지였다.

    `\\` + 임의 문자는 쌍으로 소비하고 `\\` + 개행만 제거한다. 쌍 소비 덕에
    이스케이프된 백슬래시(`echo a\\\\` + 개행)의 개행은 진짜 개행으로 남아
    bash와 일치한다.

    호출 순서가 계약이다 — 반드시 _strip_heredocs **이후**, 그리고 그 함수가
    완결을 보고했을 때만 부른다. 미종결 heredoc으로 롤백한 텍스트에 결합을
    적용하면 본문의 쪼개진 토큰이 조립돼(`g\\`+`h` → `gh`) 실행되지도 않을
    본문이 차단된다(실측). heredoc 구조가 완전히 해소된 텍스트에만 적용한다.

    수용할 비충실성: bash는 작은따옴표 안의 `\\`+개행을 리터럴로 두지만 이
    스캐너는 인용을 모델링하지 않고 제거한다. 영향은 이미 단일 토큰인 문자열의
    내용뿐이고 토큰 경계는 바뀌지 않는다(실측). 인용 모델링은 동결된
    _mask_arithmetic과 같은 끝없는 수리 게임이라 문서화된 한계로 남긴다.

    Args:
        command: heredoc 본문이 제거된 명령 문자열.

    Returns:
        줄 연속이 결합된 명령 문자열.
    """
    out: list[str] = []
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if char == "\\" and index + 1 < length:
            if command[index + 1] == "\n":
                index += 2
                continue
            out.append(char)
            out.append(command[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _strip_heredocs(command: str) -> tuple[str, bool]:
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
    원리적으로 못 닫는 가족). 마스킹 면제 구역(cmdsub·백틱 내용물)의
    quoted `<<` 언급과, 이스케이프된 백틱 중첩 내부는 여전히 감지·마스킹
    대상이 아니다(비이스케이프 백틱·`$(` 내부의 중첩 산술은 전역 문맥
    스택이 마스킹한다). 종결자
    없이 입력 끝으로 종결되는 heredoc(bash는 경고 후 실행)은 스트립 대상
    외라 본문이 여전히 파싱된다. 따옴표 문자열이나 `#` 주석 등 실행되지
    않는 텍스트 안의 heredoc 언급 뒤에 구분자와 일치하는 단독 줄이
    실존하면 그 사이가 통과 방향으로 스트립될 수 있다(이 hook은 리터럴
    `#` 보존을 위해 주석 해석을 끄므로 주석과 명령을 구분하지 못한다).

    Args:
        command: Bash 명령 문자열 전체.

    Returns:
        (본문·종결자 라인이 제거된 명령, 완결 여부). 미종결 구분자가 남으면
        원문 그대로와 False를 돌려준다 — 호출자는 이 표시로 줄 연속 결합을
        건너뛴다(_join_continuations docstring 참조).
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
        return command, False
    return "\n".join(kept), True


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


def _protected_flags(text: str, count: int) -> list[bool] | None:
    """토큰별로 "인용되었거나 이스케이프를 포함하는지"를 표시한다.

    _tokenize는 posix 모드라 따옴표와 백슬래시를 벗기므로, `echo "(;" gh issue
    create`의 토큰 `(;`나 `echo \\; gh ...`의 토큰 `;`가 진짜 연산자와 구분되지
    않는다. 같은 텍스트를 non-posix로 한 번 더 토큰화하면 따옴표가 토큰에 남아
    그 구분이 생긴다. 보호된 토큰은 명령 구분자도 예약어도 될 수 없으므로
    세그먼트 판정에서 제외한다.

    non-posix는 `\\`를 보통 문자로 다뤄 낱말 경계가 posix와 달라진다(`a\\ b`는
    posix 1토큰, non-posix 2토큰). 그래서 재토큰화 **입력만** 손봐서, `\\<문자>`
    두 글자를 센티널 한 글자로 접는다. 경계가 맞춰지는 동시에, 센티널이 토큰에
    남아 있다는 사실이 곧 "이 토큰은 이스케이프를 포함한다"는 표식이 된다.

    접기는 인용 상태를 세 규칙으로만 추적한다 — 작은따옴표 안은 전부 리터럴(접지
    않는다), 큰따옴표는 작은따옴표를 무효화한다, 백슬래시는 작은따옴표 밖에서만
    이스케이프다. 마지막 규칙은 bash보다 **넓다**: bash는 큰따옴표 안에서 `$`·
    백틱·`"`·`\\`·개행 앞의 백슬래시만 이스케이프로 보고 나머지는 두 글자를 그대로
    둔다(`echo "a\\;b"` → `a\\;b`). 여기서 더 접어도 무해한 이유는 그 자리가 이미
    인용 구간 안이라 어느 토큰화도 낱말 경계로 보지 않기 때문이다 — 이 함수의
    목적은 구조 해석이 아니라 **토큰 개수 맞추기**뿐이다. 확장·산술·백틱·heredoc은
    모델링하지 않는다(동결된 _mask_arithmetic과 다른 점).

    토큰 수가 그래도 어긋나면 **모른다(None)**를 돌려준다. 호출자는 그때 이
    모듈이 도입한 판정 두 가지를 끄고 정확 일치 연산자만 본다(_split_segments).

    **이 함수가 다루는 계열**의 잔여 오차단은 세 조건이 동시에 성립할 때다 —
    (1) 이스케이프된 큰따옴표가 낱말 경계를 가로지르는 배치(`"a\\" "a\\"`.
    균형 잡힌 `"say \\"hi\\""`는 안전하다), (2) 인용·이스케이프된 연산자 리터럴이
    인자 위치, (3) 그 뒤에 `gh issue create`가 낱말로.

    **이것이 이 하네스의 오차단 전부라는 뜻은 아니다.** 인용과 무관한 사전 존재
    계열이 따로 있다 — `;;`처럼 case 밖에서는 문법 오류인 토큰, 인용·이스케이프된
    연산자가 명령 이름 자리에 오는 형태 등. 전부 정확 일치 OPERATORS 경로에서
    나오며 이 판정 도입 이전부터 있었다. 알려진 것은 테스트의 _KNOWN_FALSE_BLOCKS
    표에 잠겨 있으나 **그 목록이 망라적이라고 증명된 바 없다.** 전부 owner가
    수용한 한계다. 실사용 발생의 자동 기록은 차단 이력 로그(#76)가 맡고(구현
    전에는 세션 트랜스크립트가 유일한 기록 경로), 수리 여부는 그 기록을
    근거로 한 owner 결정이다(모듈 docstring의 동결 선언 참조).

    Args:
        text: 토큰화한 원본 텍스트(줄 단위 또는 명령 전체).
        count: 같은 텍스트에 대한 posix 토큰 수.

    Returns:
        토큰마다 보호 여부를 담은 리스트. 정렬에 실패하면 None.
    """
    if _ESCAPE_SENTINEL in text:
        return None
    normalized: list[str] = []
    index, length = 0, len(text)
    in_single = in_double = False
    while index < length:
        char = text[index]
        if char == "'" and not in_double:
            in_single = not in_single
            normalized.append(char)
            index += 1
        elif char == '"' and not in_single:
            in_double = not in_double
            normalized.append(char)
            index += 1
        elif char == "\\" and index + 1 < length and not in_single:
            normalized.append(_ESCAPE_SENTINEL)
            index += 2
        else:
            normalized.append(char)
            index += 1
    try:
        lex = shlex.shlex("".join(normalized), posix=False, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        raw_tokens = list(lex)
    except ValueError:
        return None
    if len(raw_tokens) != count:
        return None
    return [bool(token) and (token[0] in "\"'" or _ESCAPE_SENTINEL in token)
            for token in raw_tokens]


def _is_operator(token: str) -> bool:
    """토큰이 세그먼트 경계(명령 구분자)인지 판정한다.

    Args:
        token: shlex가 돌려준 토큰 하나.

    Returns:
        경계면 True. 정확 일치하는 OPERATORS이거나, 전부 연산자 문자이면서
        구분자와 괄호를 각각 하나 이상 포함하는 결합 토큰(`));` 등)이면 True.
    """
    if token in OPERATORS:
        return True
    if not token or not all(c in _OPERATOR_CHARS for c in token):
        return False
    return (any(c in _SEPARATOR_CHARS for c in token)
            and any(c in "()" for c in token))


def _split_segments(
    tokens: list[str], protected: list[bool] | None
) -> list[list[str]]:
    """토큰을 명령 세그먼트로 나눈다.

    보호된 토큰은 연산자도 예약어도 아니다 — bash에서 따옴표 안이나 백슬래시
    뒤의 `;`·`then`은 그냥 낱말이다. posix 토큰화가 그 구분을 지우므로
    _protected_flags가 복원한 표식을 받아 쓴다.

    표식이 없으면(None = 보호 여부 불명) 이 모듈이 새로 도입한 판정 두
    가지 — 결합 연산자 토큰과 명령 위치 예약어 — 를 끄고 정확 일치
    연산자만 경계로 본다. 두 판정은 토큰의 **내용**을 셸 구조의 근거로
    삼기 때문에, 보호 여부를 모르는 상태에서 적용하면 인용 리터럴을
    구분자·예약어로 오인해 차단한다(오차단). 판정을 끄면 그 입력에서
    감지가 줄어들 뿐이라 통과 방향으로 눕는다.

    정확 일치 연산자는 폴백에서도 경계로 둔다. 끄면 백슬래시가 섞인 흔한
    명령에서 진짜 `&&` 경계까지 잃기 때문인데, 대가로 그 경로에 오차단이
    남는다 — 폴백이 오차단을 완전히 없앤다는 뜻이 아니다. 남는 조건과
    수용 근거는 _protected_flags docstring 참조.

    Args:
        tokens: 한 줄(또는 명령 전체)의 posix 토큰.
        protected: 토큰별 보호 여부. 판별 불가면 None.

    Returns:
        연산자로 분리된 토큰 세그먼트 목록.
    """
    segments: list[list[str]] = []
    current: list[str] = []
    for index, token in enumerate(tokens):
        if protected is None:
            is_boundary = token in OPERATORS
            is_keyword = False
        else:
            bare = not protected[index]
            is_boundary = bare and _is_operator(token)
            is_keyword = bare and token in KEYWORDS
        if is_boundary:
            if current:
                segments.append(current)
                current = []
        elif is_keyword and not current:
            # 세그먼트 선두 = 명령 위치. 예약어는 여기서만 예약어이므로 버리고
            # 다음 토큰이 명령 위치를 이어받는다(`then gh issue create` → gh).
            continue
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
            # pflag는 -t=값에서 =를 벗기고 값을 취한다 (아래 -R 분기와 같은 규약).
            # -t= 만 오면 제목이 비어 실제로 검색할 것이 없으므로 None으로 눕혀
            # "제목 없는 create" 차단 경로에 흡수시킨다.
            title = token[2:].removeprefix("=") or None
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

    전처리는 heredoc 본문 제거 → (완결일 때만) 줄 연속 결합 순이다.
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
    stripped, complete = _strip_heredocs(command)
    # 결합은 heredoc 구조가 완전히 해소된 텍스트에만 적용한다 — 롤백 구역에서
    # 결합하면 본문의 쪼개진 토큰이 조립돼 오차단이 된다.
    command = _join_continuations(stripped) if complete else stripped
    # 인용 표식을 만들려면 토큰과 그 원본 텍스트가 함께 필요하다.
    token_groups: list[tuple[str, list[str]]] | None = None
    try:
        token_groups = [(line, _tokenize(line)) for line in command.split("\n")]
    except ValueError:
        try:
            token_groups = [(command, _tokenize(command))]
        except ValueError:
            return []

    invocations: list[CreateInvocation] = []
    directory_changed = False
    for text, tokens in token_groups:
        for segment in _split_segments(tokens, _protected_flags(text, len(tokens))):
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
