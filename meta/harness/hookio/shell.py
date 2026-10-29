# 훅 가드가 공유하는 셸 명령 토크나이저와 세그먼트 분리기
"""셸 세그먼테이션 계층 (hookio 공유 모듈).

Bash 명령 문자열을 shlex 토큰으로 쪼개고(tokenize), 토큰별 인용·이스케이프
표식을 복원하며(protected_flags), 명령 구분자에서 명령 세그먼트로 나눈다
(split_segments). 가드는 각 세그먼트의 선두 토큰으로 `git commit`·`gh issue
create` 같은 호출을 식별한다.

이 계층은 issue_duplicate_guard에서 옮긴 것이다(#4) — 코드는 그대로, 함수
이름은 공개형으로, 독스트링의 출처·위치 참조를 고쳤다.
commit_guard가 들고 있던 사본은 `split_segments(tokens, None)`과 같은 판정을
했으므로 그 호출로 대체된다. **동결 선언(#74)은 위치와 무관하게 이 계층에
그대로 적용된다** — 잔여 bash 충실도 구멍은 더 수리하지 않으며, 계약은
issue_duplicate_guard tests의 _SEGMENT_CORPUS·_KNOWN_FALSE_BLOCKS다.
"""

from __future__ import annotations

import shlex

# shlex(punctuation_chars=True)가 별도 토큰으로 분리하는 셸 연산자.
OPERATORS = {"&&", "||", "|", ";", ";;", "&", "(", ")"}

# bash 예약어. bash와 동일하게 **명령 위치에서만** 예약어로 취급한다(세그먼트
# 선두에서만 건너뛰고, 인자 위치의 같은 단어는 평범한 토큰이다 — `echo then gh
# issue create`가 걸리면 안 된다). `[[`는 넣지 않는다. 뒤에 오는 것은 명령이
# 아니라 조건식이다.
KEYWORDS = frozenset(
    {
        "if",
        "then",
        "elif",
        "else",
        "fi",
        "while",
        "until",
        "for",
        "select",
        "do",
        "done",
        "case",
        "esac",
        "function",
        "time",
        "coproc",
        "{",
        "}",
        "!",
    }
)

# 결합 연산자 토큰 판정용 문자군. shlex는 인접 문장부호를 한 토큰으로 묶으므로
# `((x));`가 `));`라는 토큰을 낳고, OPERATORS 정확 일치로는 경계가 되지 않는다.
# 세 조건이 모두 필요하다(전부 bash 실측 근거).
# - 문자군에 꺾쇠(<>)를 넣지 않는다. 넣으면 `&>`·`>&`·`>|`·`&>>`가 경계가 되어
#   `echo a &> gh issue create -t T`를 오차단한다(bash는 gh라는 파일로 리다이렉션할
#   뿐 create를 실행하지 않는다). 셸의 명령 구분자에는 꺾쇠가 없다.
# - 구분자(;&|)를 요구한다. `))` 단독까지 경계면 `echo $((1<<2)) gh issue create`가
#   오차단된다(bash 출력은 `4 gh issue create ...`, create 미실행).
# - 괄호를 요구한다. 이건 오차단 방어가 아니라 **적용 범위 제한**이다. 인용
#   리터럴의 오차단은 protected_flags가 막고, 여기서는 결합 토큰이 필요한 실사용
#   동기(`((x));`·`(cmd);` 같은 괄호 닫기 계열)로 판정을 한정한다. 대가로
#   `echo hi |& gh issue create`(bash 실행됨)는 미감지지만 통과 방향이다.
_OPERATOR_CHARS = frozenset("();&|")
_SEPARATOR_CHARS = frozenset(";&|")

# 인용 표식 복원용 센티널. bash 명령 문자열은 NUL을 담을 수 없으므로(execve 인자가
# NUL 종료) 실제 입력과 충돌하지 않는다. 그래도 입력에 있으면 판별을 포기한다.
_ESCAPE_SENTINEL = "\x00"


def tokenize(text: str) -> list[str]:
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


def protected_flags(text: str, count: int) -> list[bool] | None:
    """토큰별로 "인용되었거나 이스케이프를 포함하는지"를 표시한다.

    tokenize는 posix 모드라 따옴표와 백슬래시를 벗기므로, `echo "(;" gh issue
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
    모델링하지 않는다(issue_duplicate_guard의 동결된 _mask_arithmetic과 다른 점).

    토큰 수가 그래도 어긋나면 **모른다(None)**를 돌려준다. 호출자는 그때 이
    계층이 도입한 판정 두 가지를 끄고 정확 일치 연산자만 본다(split_segments).

    사전 존재 오차단 계열이 있다 — `;;`처럼 case 밖에서는 문법 오류인
    토큰, 인용·이스케이프된 연산자가 명령 이름 자리에 오는 형태 등. 전부 정확 일치
    OPERATORS 경로에서 나오며 이 판정 도입 이전부터 있었다. 알려진 것은 issue_duplicate_guard tests의
    _KNOWN_FALSE_BLOCKS 표에 잠겨 있으나 **그 목록이 망라적이라고 증명된 바 없다.**
    전부 owner가 수용한 한계다. 실사용 발생의 자동 기록은 차단 이력 원장(#76,
    harness.blocklog)이 맡고, 수리 여부는 그 기록을 근거로 한 owner
    결정이다(모듈 docstring의 동결 선언 참조).

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
    return [
        bool(token) and (token[0] in "\"'" or _ESCAPE_SENTINEL in token)
        for token in raw_tokens
    ]


def is_operator(token: str) -> bool:
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
    return any(c in _SEPARATOR_CHARS for c in token) and any(c in "()" for c in token)


def split_segments(tokens: list[str], protected: list[bool] | None) -> list[list[str]]:
    """토큰을 명령 세그먼트로 나눈다.

    보호된 토큰은 연산자도 예약어도 아니다 — bash에서 따옴표 안이나 백슬래시
    뒤의 `;`·`then`은 그냥 낱말이다. posix 토큰화가 그 구분을 지우므로
    protected_flags가 복원한 표식을 받아 쓴다.

    표식이 없으면(None = 보호 여부 불명) 이 계층이 새로 도입한 판정 두
    가지 — 결합 연산자 토큰과 명령 위치 예약어 — 를 끄고 정확 일치
    연산자만 경계로 본다. 두 판정은 토큰의 **내용**을 셸 구조의 근거로
    삼기 때문에, 보호 여부를 모르는 상태에서 적용하면 인용 리터럴을
    구분자·예약어로 오인해 차단한다(오차단).

    정확 일치 연산자는 폴백에서도 경계로 둔다. 끄면 백슬래시가 섞인 흔한
    명령에서 진짜 `&&` 경계까지 잃기 때문인데, 대가로 그 경로에 오차단이
    남는다 — 폴백이 오차단을 완전히 없앤다는 뜻이 아니다.

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
            is_boundary = bare and is_operator(token)
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
