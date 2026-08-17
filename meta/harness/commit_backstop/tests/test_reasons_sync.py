# commit-backstop 규칙의 degraded 사유 열거와 DEGRADED_REASONS 상수의 동기화 테스트
"""규칙 프로즈 ↔ 훅 상수 동기화 테스트.

commit-backstop.md의 "`degraded` reasons: ..." 행과 backstop.DEGRADED_REASONS를
순서까지 결속해 조용한 drift를 막는다(규칙은 오너 대면 산문이, 훅은 상수가 필요해
어느 쪽도 제거할 수 없음).

경위: PR #118 리뷰 루프가 같은 열거의 사본 다섯 개를 따로 어긋나게 두었고, 산문을
다시 쓰는 처방이 라운드마다 새 결함을 만들었다. 결속이 덮는 범위는 이 두 곳이며,
테스트 파일의 wire-value 리터럴은 그 밖이다. 저장소 루트는 test_types_sync와 같은 고정 오프셋으로
찾는다 — meta/는 자체 uv 프로젝트라 pyproject 마커 탐색이 오답을 낸다.
"""

from __future__ import annotations

import re
from pathlib import Path

from harness.commit_backstop import backstop

RULE_PATH = (
    Path(backstop.__file__).resolve().parents[3] / "meta/rules/commit-backstop.md"
)


def test_rule_degraded_reasons_match_the_constant() -> None:
    """규칙 파일의 사유 열거가 DEGRADED_REASONS와 집합으로 일치해야 한다."""
    text = RULE_PATH.read_text(encoding="utf-8")
    matches = re.findall(r"`degraded` reasons:\s*([^.]+)\.", text)
    # 문구 개편으로 행이 사라지거나 늘면 공허 통과 대신 여기서 실패한다.
    assert len(matches) == 1, f"'`degraded` reasons:' 행이 정확히 1개여야 함: {len(matches)}개"
    # 순서까지 비교한다 — 규칙 파일의 설명이 열거를 위치로 지칭하므로,
    # 집합만 맞추면 재정렬이 그 설명을 조용히 뒤집는다.
    parsed = [token.strip().strip("`") for token in matches[0].split(",")]
    assert parsed == list(backstop.DEGRADED_REASONS)


def test_reason_names_appear_once_in_the_source() -> None:
    """사유 이름은 소스 파일에 상수 정의 한 번만 등장한다.

    `backstop.py` 안에서 어휘가 상수 정의 하나로만 유지되는지 확인한다. 그 파일에
    두 번째 사본이 생기면 결속 밖에서 조용히 낡으므로, 복제를 관례가 아니라 여기서
    막는다. 모듈 docstring만이 아니라 파일 전체를 본다 — 함수 docstring·주석·
    문자열도 결속 밖이며, 모듈 docstring만 보던 판이 그 구멍을 남겼다
    (PR #118 라운드 4).
    """
    source = Path(backstop.__file__).read_text(encoding="utf-8")
    for reason in backstop.DEGRADED_REASONS:
        # 토큰 경계를 요구한다 — 부분 문자열로 세면 접미사가 붙은 미래의 사유가
        # 기존 사유의 복제로 오탐된다.
        hits = re.findall(rf"(?<![\w-]){re.escape(reason)}(?![\w-])", source)
        assert len(hits) == 1, f"사유 이름이 복제됐다: {reason}"
