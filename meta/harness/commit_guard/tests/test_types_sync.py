# commit-discipline 규칙의 타입 목록과 COMMIT_TYPES 상수의 동기화를 검증하는 테스트
"""규칙 프로즈 ↔ 훅 상수 동기화 테스트.

commit-discipline.md의 "Types: ..." 행과 guard.COMMIT_TYPES는 같은 집합의
두 사본이다(규칙은 프로즈가, 훅은 상수가 필요해 어느 쪽도 제거할 수 없음).
이 테스트가 둘을 기계적으로 결속해 조용한 drift를 막는다. 저장소 루트는
rules_checker와 같은 고정 오프셋 방식으로 찾는다 — meta/는 자체 uv
프로젝트라 pyproject 마커 탐색이 오히려 오답을 낸다.
"""

from __future__ import annotations

import re
from pathlib import Path

from harness.commit_guard import guard

RULE_PATH = Path(guard.__file__).resolve().parents[3] / "meta/rules/commit-discipline.md"


def test_rule_types_line_matches_commit_types() -> None:
    """규칙 파일의 Types: 행이 COMMIT_TYPES와 집합으로 일치해야 한다."""
    text = RULE_PATH.read_text(encoding="utf-8")
    matches = re.findall(r"Types:\s*([a-z, ]+)", text)
    # 문구 개편으로 행이 사라지거나 늘면 공허 통과 대신 여기서 실패한다.
    assert len(matches) == 1, f"'Types:' 행이 정확히 1개여야 함: {len(matches)}개 발견"
    parsed = {t.strip() for t in matches[0].split(",")}
    assert parsed == set(guard.COMMIT_TYPES)
