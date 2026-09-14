# review-loop-trailer-check 규칙의 report 사유 열거와 REASONS 상수의 동기화 테스트
"""규칙 프로즈 ↔ 훅 상수 동기화 — commit_backstop/tests/test_reasons_sync.py와 같은 결속."""

from __future__ import annotations

import re
from pathlib import Path

from harness.review_loop_trailer_check import check

RULE_PATH = (
    Path(check.__file__).resolve().parents[3]
    / "meta/rules/review-loop-trailer-check.md"
)


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
