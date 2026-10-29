# 실저장소의 두 CI 파일에 계약을 강제하는 라이브 테스트 — #80의 강제 지점
"""실저장소 대상 CI 계약 강제 테스트.

이 테스트가 pytest 스위트에 실려 양 포지 CI에서 자동 실행되는 것이 #80의
강제 메커니즘 전부다. 마커(.dual-forge-ci)가 없으면 계약 철회로 보고
skip한다 — 단일 포지 자식 프로젝트의 옵트아웃 경로.
"""

import tomllib

import pytest

from harness.ci_contract import contract


def test_ci_contract_live():
    """마커가 선언된 저장소에서 양 포지 CI 계약 전체를 단언한다."""
    root = contract.find_repo_root()
    mode, reason = contract.decide_mode(root)
    if mode == "skip":
        pytest.skip(reason)
    assert mode == "strict", reason

    violations = contract.check_contract(
        (root / contract.GITLAB_CI).read_text(encoding="utf-8"),
        (root / contract.GITHUB_WORKFLOW).read_text(encoding="utf-8"),
        (root / contract.PYTHON_VERSION_FILE).read_text(encoding="utf-8"),
    )
    assert not violations, "\n".join(violations)


def test_meta_pyproject_declares_tool_ruff():
    """meta/pyproject.toml이 `[tool.ruff]` 표를 선언한다.

    표가 없으면 ruff는 이 파일을 건너뛰고 상위 디렉터리의 ruff.toml이나
    `[tool.ruff]`를 설정으로 삼으므로, 자식 프로젝트의 product 층 설정이
    CI의 `ruff check`·`ruff format --check`를 지배한다 (PR #222 라운드 1).
    """
    root = contract.find_repo_root()
    data = tomllib.loads((root / "meta" / "pyproject.toml").read_text(encoding="utf-8"))
    assert "ruff" in data.get("tool", {})
