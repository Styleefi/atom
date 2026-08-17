# 테스트가 오너의 실제 차단 원장을 오염시키지 않도록 경로를 격리하는 conftest
"""blocklog 원장 경로 격리.

backstop의 차단·오버라이드·강등 경로는 blocklog에 한 줄을 쓴다. 이 패키지의
테스트는 `main()`을 그대로 실행하므로, 격리가 없으면 pytest 한 번이 오너의 실제
원장(`~/.local/state/atom/guard-blocklog.jsonl`)에 픽스처 이벤트 수십 줄을 남긴다 —
#76이 없애려는 "수동 실행을 손으로 걸러내는" 고고학을 첫 커밋부터 재생산하는 셈이다.

`HOME`은 건드리지 않는다: git을 셸아웃하는 테스트의 동작이 함께 바뀐다.
`XDG_STATE_HOME`이 설정돼 있으면 blocklog는 `HOME` 폴백 분기를 타지 않으므로
이 한 변수로 충분하다.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_blocklog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """이 패키지의 모든 테스트에서 원장 경로를 tmp_path 아래로 돌린다."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
