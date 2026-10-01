# 테스트가 오너의 실제 기록 파일과 blocklog 원장을 오염시키지 않도록 경로를 격리하는 conftest
"""기록 경로 격리 — 기록 파일은 blocklog 원장과 같은 XDG 디렉터리를 쓴다."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """이 패키지의 모든 테스트에서 XDG 상태 경로를 tmp_path 아래로 돌린다."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
