# hookio.shell 세그먼테이션 계층 테스트: 이동한 표식 테스트와 두 판정 모드의 경계
"""세그먼테이션 계층 테스트.

표식(protected_flags) 테스트 세 개는 issue_duplicate_guard tests에서 코드와
함께 옮겨 왔다. 계층의 **계약**(코퍼스·알려진 오차단)은 그 패키지의
_SEGMENT_CORPUS·_KNOWN_FALSE_BLOCKS에 남아 있다 — 여기는 두 호출 모드의
경계만 잠근다.
"""

from __future__ import annotations

from harness.hookio import shell


def test_protected_flags_marks_escaped_operator() -> None:
    # 이스케이프된 연산자는 진짜 구분자가 아니다 — 표식이 붙어야 경계 판정에서 빠진다
    text = "echo \\; gh issue create"
    flags = shell.protected_flags(text, len(shell.tokenize(text)))
    assert flags is not None
    assert flags[1] is True  # `;` 자리
    assert flags[0] is False  # `echo`


def test_protected_flags_gives_up_when_input_holds_sentinel() -> None:
    # 센티널이 입력에 있으면 정규화 결과를 신뢰할 수 없다 — 판별 포기
    text = f"echo {shell._ESCAPE_SENTINEL} gh issue create"
    assert shell.protected_flags(text, len(shell.tokenize(text))) is None


def test_protected_flags_ignores_apostrophe_inside_double_quotes() -> None:
    # bash는 큰따옴표 안 작은따옴표를 리터럴로 둔다 — 인용 상태가 뒤집히면
    # 뒤따르는 이스케이프가 접히지 않아 정렬이 깨지고 오차단이 되살아난다
    text = 'echo "it\'s" a\\ b && gh issue create'
    flags = shell.protected_flags(text, len(shell.tokenize(text)))
    assert flags is not None  # 상태가 뒤집혔다면 정렬 실패로 None이 된다
    assert flags[-1] is False  # 진짜 && 는 보호 대상이 아니다


def test_split_without_flags_cuts_on_exact_operators_only() -> None:
    # protected=None은 commit_guard가 들고 있던 사본과 같은 판정이다: 정확 일치
    # 연산자만 경계, 예약어는 평범한 토큰. 모드 전환 시 이 항목의 기대값이 바뀐다.
    tokens = shell.tokenize("if true; then git commit -m x; fi")
    assert shell.split_segments(tokens, None) == [
        ["if", "true"],
        ["then", "git", "commit", "-m", "x"],
        ["fi"],
    ]


def test_split_with_flags_skips_command_position_keywords() -> None:
    text = "if true; then git commit -m x; fi"
    tokens = shell.tokenize(text)
    flags = shell.protected_flags(text, len(tokens))
    assert shell.split_segments(tokens, flags) == [
        ["true"],
        ["git", "commit", "-m", "x"],
    ]


def test_split_with_flags_keeps_protected_operator_inside_segment() -> None:
    text = "echo \\; git commit -m x"
    tokens = shell.tokenize(text)
    flags = shell.protected_flags(text, len(tokens))
    assert shell.split_segments(tokens, flags) == [
        ["echo", ";", "git", "commit", "-m", "x"]
    ]
    # 표식 없이는 같은 `;`가 경계가 된다 — 두 모드가 갈리는 지점
    assert shell.split_segments(tokens, None) == [
        ["echo"],
        ["git", "commit", "-m", "x"],
    ]
