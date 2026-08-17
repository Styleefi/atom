# commit-backstop 규칙의 degraded 사유 열거와 DEGRADED_REASONS 상수의 동기화 테스트
"""규칙 프로즈 ↔ 훅 상수 동기화 테스트.

commit-backstop.md의 "`degraded` reasons: ..." 행과 backstop.DEGRADED_REASONS는
같은 집합의 두 사본이다(규칙은 오너 대면 산문이, 훅은 상수가 필요해 어느 쪽도
제거할 수 없음). 이 테스트가 둘을 기계적으로 결속해 조용한 drift를 막는다.

경위: PR #118 리뷰 루프가 같은 열거의 사본 다섯 개를 따로 어긋나게 두었고, 산문을
다시 쓰는 처방이 라운드마다 새 결함을 만들었다. 지울 수 없는 사본만 남기고 결속하는
것이 그 라운드의 처분이다. 저장소 루트는 test_types_sync와 같은 고정 오프셋으로
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
    parsed = {token.strip().strip("`") for token in matches[0].split(",")}
    assert parsed == set(backstop.DEGRADED_REASONS)


def test_module_docstring_does_not_copy_the_reason_names() -> None:
    """모듈 docstring은 사유 이름을 복제하지 않는다.

    어휘의 사본은 상수와 규칙 파일 둘뿐이고 둘은 위 테스트가 결속한다. 세 번째
    사본이 생기면 결속 밖에서 조용히 낡으므로, 복제를 관례가 아니라 여기서 막는다.
    """
    doc = backstop.__doc__ or ""
    for reason in backstop.DEGRADED_REASONS:
        assert reason not in doc, f"docstring이 사유 이름을 복제한다: {reason}"
