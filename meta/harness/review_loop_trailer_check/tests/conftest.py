# 테스트가 오너의 실제 blocklog 원장을 오염시키지 않도록 경로를 격리하는 conftest
"""blocklog 원장 경로 격리 — commit_backstop/tests/conftest.py와 같은 근거."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_blocklog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """이 패키지의 모든 테스트에서 원장 경로를 tmp_path 아래로 돌린다."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
