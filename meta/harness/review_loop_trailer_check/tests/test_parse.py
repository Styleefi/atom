# 트레일러 문법 파서(Review-loop·Prose)와 본문 검사의 단위 테스트
"""트레일러 파서 테스트.

REAL_* 표는 PR #158·#159·#161의 커밋 20건에서 복사한 실측값이다.
"""

from __future__ import annotations

import pytest

from harness.review_loop_trailer_check.check import (
    body_beyond_trailers,
    parse_prose,
    parse_review_loop,
)

# git log --format='%(trailers:key=Review-loop,valueonly)' 3489aa8~1..HEAD (2026-09-13)
REAL_REVIEW_LOOP = {
    "PR #161 round 8": (161, 8),
    "PR #161 round 7": (161, 7),
    "PR #161 round 5": (161, 5),
    "PR #159 round 4": (159, 4),
    "PR #159 round 3": (159, 3),
    "PR #159 round 0": (159, 0),
    "PR #158 round 1": (158, 1),
    "PR #158 round 0": (158, 0),
}

# 같은 커밋들의 `Prose:` 값 — 개정 전 형식.
REAL_PROSE_OLD_FORMAT = [
    "1 new; attacked by fresh Opus subagent; 0 falsified",
    "1 new; attacked twice by fresh Opus subagent; 3 falsified",
    "0 new; attacked by fresh Opus subagent; 1 falsified",
    "1 new; attacked twice by fresh Opus subagent; 0 falsified",
    "1 new; attacked twice by fresh Opus subagent; 1 falsified",
    "10 new; attacked twice by fresh Opus subagent; 2 falsified",
    "3 new; attacked twice by fresh Opus subagent; 0 falsified",
    "2 new; attacked twice by fresh Opus subagent; 0 falsified",
    "3 new; attacked twice by fresh Opus subagent; 1 falsified",
    "7 new; attacked twice by fresh Opus subagent; 5 falsified",
]


@pytest.mark.parametrize("value,expected", sorted(REAL_REVIEW_LOOP.items()))
def test_real_review_loop_values_parse(value: str, expected: tuple[int, int]) -> None:
    assert parse_review_loop(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "PR #? round 0",
        "PR #162",
        "round 3",
        "PR 162 round 3",
        "PR #162 round 3 (draft)",
        "pr #162 round 3",
        "",
    ],
)
def test_review_loop_rejects_anything_but_the_grammar(value: str) -> None:
    assert parse_review_loop(value) is None


def test_review_loop_tolerates_surrounding_whitespace() -> None:
    assert parse_review_loop("  PR #162 round 0 \n") == (162, 0)


def test_review_loop_rejects_absurdly_long_numbers() -> None:
    assert parse_review_loop("PR #" + "1" * 5000 + " round 0") is None


@pytest.mark.parametrize("value", ["none", "attacked", " attacked\n"])
def test_prose_accepts_the_two_values(value: str) -> None:
    assert parse_prose(value) == value.strip()


@pytest.mark.parametrize(
    "value",
    REAL_PROSE_OLD_FORMAT
    + ["None", "attacked twice", "attacked by fresh Opus subagent", "", "none; attacked"],
)
def test_prose_rejects_the_old_format_and_variants(value: str) -> None:
    assert parse_prose(value) is None


TRAILERS = (
    "Review-loop: PR #162 round 0\n"
    "Prose: attacked\n"
    "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\n"
)


def test_body_that_is_only_trailers_is_clean() -> None:
    # 실측: 본문 없는 커밋에서 `%b`와 `%(trailers:only=true)`는 바이트까지 같다.
    assert body_beyond_trailers(TRAILERS, TRAILERS) is False
    assert body_beyond_trailers(TRAILERS + "\n", TRAILERS) is False


def test_text_before_the_trailer_block_is_a_body() -> None:
    assert body_beyond_trailers("Why this change.\n\n" + TRAILERS, TRAILERS) is True


def test_revert_autobody_is_a_body() -> None:
    body = "This reverts commit 0123456789abcdef.\n\n" + TRAILERS
    assert body_beyond_trailers(body, TRAILERS) is True


def test_body_without_any_trailer_block() -> None:
    assert body_beyond_trailers("just prose\n", "") is True
    assert body_beyond_trailers("", "") is False


def test_non_trailer_line_inside_the_last_paragraph_is_a_body() -> None:
    body = "Review-loop: PR #162 round 0\nnot a trailer\nProse: attacked\n"
    assert body_beyond_trailers(body, "") is True


def test_git_normalises_trailer_spacing_and_so_do_we() -> None:
    # 실측(git 2.53): `Review-loop:PR …`/`Prose:   attacked`로 쓴 메시지도
    # `%(trailers:only=true)`는 `키: 값` 한 칸으로 정규화해 낸다.
    body = "Review-loop:PR #162 round 0\nProse:   attacked\n"
    only = "Review-loop: PR #162 round 0\nProse: attacked\n"
    assert body_beyond_trailers(body, only) is False
    assert body_beyond_trailers("Prose : attacked\n", "Prose: attacked\n") is False
