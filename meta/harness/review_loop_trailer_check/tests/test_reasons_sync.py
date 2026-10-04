# review-loop-trailer-check 규칙 산문이 가리키는 훅 상수(REASONS, 트레일러 읽기)의 동기화 테스트
"""규칙 프로즈 ↔ 훅 상수 동기화 — commit_backstop/tests/test_reasons_sync.py와 같은 결속."""

from __future__ import annotations

import re
from pathlib import Path

from harness.review_loop_trailer_check import check

RULE_PATH = (
    Path(check.__file__).resolve().parents[3]
    / "meta/rules/review-loop-trailer-check.md"
)
TEST_CHECK_PATH = Path(__file__).with_name("test_check.py")


def test_rule_report_reasons_match_the_constant() -> None:
    """규칙 파일의 사유 열거가 REASONS와 순서까지 일치해야 한다."""
    text = RULE_PATH.read_text(encoding="utf-8")
    matches = re.findall(r"`report` reasons:\s*([^.]+)\.", text)
    assert len(matches) == 1, f"'`report` reasons:' 행이 정확히 1개여야 함: {len(matches)}개"
    parsed = [token.strip().strip("`") for token in matches[0].split(",")]
    assert parsed == list(check.REASONS)


def test_reason_names_appear_once_in_the_source() -> None:
    """사유 이름은 소스 파일에 상수 정의 한 번만 등장한다."""
    source = Path(check.__file__).read_text(encoding="utf-8")
    for reason in check.REASONS:
        hits = re.findall(rf"(?<![\w-]){re.escape(reason)}(?![\w-])", source)
        assert len(hits) == 1, f"사유 이름이 복제됐다: {reason}"


def test_prose_names_the_trailer_read_constant() -> None:
    """규칙 파일과 test_check.py docstring이 가리키는 트레일러 읽기 상수가 실재해야 한다 (#181).

    이름을 산문에서 파싱하지 않고 여기 적는다 — 흐르는 문장 속 괄호에 패턴을 걸면
    문구만 고쳐도 빨개지기 때문이다. 백틱 이름이 남아 있는 한 문구는 자유다.
    """
    name = "_MESSAGE_FORMAT"
    assert hasattr(check, name)
    assert f"`{name}`" in RULE_PATH.read_text(encoding="utf-8")
    assert f"`{name}`" in TEST_CHECK_PATH.read_text(encoding="utf-8")
