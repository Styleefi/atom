# 테스트가 오너의 실제 차단 원장을 오염시키지 않도록 경로를 격리하는 conftest
"""blocklog 원장 경로 격리.

이 패키지의 테스트는 `record_block`을 직접 부르므로, 격리가 없으면 pytest 한 번이
오너의 실제 원장(`~/.local/state/atom/guard-blocklog.jsonl`)에 줄을 남긴다.

폴백 경로 자체를 검증하는 테스트는 이 fixture가 심은 `XDG_STATE_HOME`을 지우고
`HOME`을 국소적으로 monkeypatch한다 — 그때만 `HOME`을 건드린다.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_blocklog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """이 패키지의 모든 테스트에서 원장 경로를 tmp_path 아래로 돌린다."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
