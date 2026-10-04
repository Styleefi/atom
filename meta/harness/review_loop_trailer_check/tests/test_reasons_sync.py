# review-loop-trailer-check 규칙 파일과 test_check.py 모듈 docstring을 훅 상수에 묶는 동기화 테스트
"""산문 ↔ 훅 상수 동기화."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from harness.review_loop_trailer_check import check

RULE_PATH = (
    Path(check.__file__).resolve().parents[3]
    / "meta/rules/review-loop-trailer-check.md"
)
TEST_CHECK_PATH = Path(__file__).with_name("test_check.py")
# 산문이 백틱으로 감싸 가리키는 비공개 대문자 상수 이름.
_POINTER_RE = re.compile(r"`(_[A-Z][A-Z0-9_]*)`")


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
    """규칙 파일과 test_check.py 모듈 docstring에서 `_POINTER_RE`에 걸리는 이름은 트레일러 읽기 상수 하나여야 한다 (#181)."""
    name = "_MESSAGE_FORMAT"
    assert hasattr(check, name)
    rule = RULE_PATH.read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(TEST_CHECK_PATH.read_text(encoding="utf-8"))) or ""
    assert set(_POINTER_RE.findall(rule)) == {name}
    assert set(_POINTER_RE.findall(docstring)) == {name}
