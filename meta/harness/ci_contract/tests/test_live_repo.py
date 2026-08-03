# 실저장소의 두 CI 파일에 계약을 강제하는 라이브 테스트 — #80의 강제 지점
"""실저장소 대상 CI 계약 강제 테스트.

이 테스트가 pytest 스위트에 실려 양 포지 CI에서 자동 실행되는 것이 #80의
강제 메커니즘 전부다. 마커(.dual-forge-ci)가 없으면 계약 철회로 보고 전면
skip한다 — 단일 포지 자식 프로젝트의 옵트아웃 경로.
"""

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
