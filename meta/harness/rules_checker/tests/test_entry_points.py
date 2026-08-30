# 하네스 __main__.py 진입점이 실제로 실행 가능한지 서브프로세스로 검증하는 테스트
"""진입점 실행 가능성 테스트 (#107).

`python -m harness.<pkg>`가 뜨는지만 본다. 왜 이 검사가 필요한지(체커가 무엇을
보고 무엇을 안 보는지, 훅이 어떤 종료 코드를 어떻게 다루는지)는 #107이 보유한다.

import이 아니라 서브프로세스인 이유는 대상 파일들의 형태다: 다섯 중 넷은 모듈
최상단에서 `sys.exit(run())`을 실행하고 run()은 곧바로 stdin을 읽는다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.rules_checker.check_rules import find_repo_root

_SUBPROCESS_TIMEOUT = 30

# 진입점별 기대값: {패키지: (기대 종료 코드 | None, 자기 태그)}.
#
# 태그는 메시지 전문이 아니라 하네스의 정체다 — 문구를 다듬어도 빨개지면 안 된다.
# 태그가 서는 범위는 "진입점이 import를 풀고 자기 모듈의 코드에 도달했다"까지다.
# 하네스 내부가 건강하다는 뜻은 아니다: main()이 던져도 run()의 포괄 예외가 같은
# 태그를 찍는다.
#
# rules_checker만 종료 코드가 None이다. 그 코드는 진입점이 아니라 저장소 규칙
# 건강을 보고하므로(위반이면 1), 0을 단언하면 무관한 위반에 이 테스트가 빨개진다.
# 태그 `rules_checker:`는 양쪽 경로에 모두 찍혀 상태와 무관하고, 크래시는 태그가
# 없어 그대로 걸린다.
ENTRY_POINTS = {
    "answer_first_reminder": (1, "[answer-first-reminder]"),
    "commit_backstop": (1, "[commit-backstop]"),
    "commit_guard": (1, "[commit-guard]"),
    "issue_duplicate_guard": (1, "[issue-duplicate-guard]"),
    "rules_checker": (None, "rules_checker:"),
}

# 부재를 허용하는 진입점. atom은 자식 프로젝트가 상속하는 SSOT이고, 가드를
# 정합하게 제거한 자식에서 부재를 실패로 다루면 상속된 스위트가 영구히 빨개진다
# (형제 test_hook_command_contract.py가 #43에서 같은 완화를 둔다).
#
# rules_checker는 제외한다 — 이 파일이 그 패키지의 tests 안에 살므로 패키지가
# 없으면 이 테스트도 없다. 제거 관용이 성립하지 않는 대신, 그 진입점의 삭제를
# 보는 것은 이 단언뿐이다(단방향 커버리지 단언은 부재를 보지 않는다).
REMOVABLE = set(ENTRY_POINTS) - {"rules_checker"}


def _entry_point_path(package: str) -> Path:
    return find_repo_root() / "meta" / "harness" / package / "__main__.py"


def _run_entry_point(package: str, state_home: Path) -> subprocess.CompletedProcess[str]:
    """진입점을 `python -m`으로 실행한다.

    `input=""`는 위생이 아니라 필수다 — 넘기지 않으면 자식이 부모의 stdin을
    상속하고, `pytest -s`에서는 그게 터미널이라 가드 넷이 stdin.read()에서
    막힌다. timeout은 그 회귀를 멈춤이 아니라 실패로 바꾼다.

    `harness` import를 지탱하는 것은 `cwd`다 — `python -m`이 cwd를 sys.path[0]에
    넣는다(패키지는 venv에 설치되지 않는다). env 병합은 그 import와 무관하며,
    배포된 훅이 물려받는 환경에 대한 충실도를 위한 것이다. XDG_STATE_HOME은
    blocklog 원장만 격리한다 — commit_backstop의 상태 파일은 대상 저장소의 git
    디렉터리에 쓰이므로 밖이다(잘못된 입력 경로는 그 전에 반환한다).
    """
    return subprocess.run(
        [sys.executable, "-m", f"harness.{package}"],
        cwd=find_repo_root() / "meta",
        input="",
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        env={**os.environ, "XDG_STATE_HOME": str(state_home)},
    )


def test_no_entry_point_is_uncovered() -> None:
    # 손으로 유지하는 표가 조용히 낡는 것을 막는 동반 단언 — 새 하네스가 진입점을
    # 들고 들어오면 여기서 시끄럽게 터진다. blocklog·ci_contract는 __main__.py가
    # 없어 glob에서 자연 제외되며, 그 제외는 이 단언이 성립하는 근거다.
    #
    # 한 방향으로만 단언한다 — 표에 있는데 없는 쪽은 REMOVABLE 관용의 몫이다.
    discovered = {
        path.parent.name
        for path in (find_repo_root() / "meta" / "harness").glob("*/__main__.py")
    }
    assert discovered - set(ENTRY_POINTS) == set()


@pytest.mark.parametrize("package", sorted(ENTRY_POINTS))
def test_entry_point_runs(package: str, tmp_path: Path) -> None:
    # 문법 오류·import 오류·빈 파일·부재면 태그가 나오지 않는다. 스트림은 갈리므로
    # (가드는 stderr, rules_checker는 stdout) 합쳐서 본다.
    #
    # skip은 진입점 파일 자체의 부재로 좁힌다 — 넓게 잡으면 깨진 진입점까지 삼켜
    # 이 테스트가 조용히 무력해진다(#43에서 importorskip이 그렇게 실패했다).
    if package in REMOVABLE and not _entry_point_path(package).is_file():
        pytest.skip(f"entry point removed: harness/{package}/__main__.py")
    expected_code, tag = ENTRY_POINTS[package]
    result = _run_entry_point(package, tmp_path)
    assert tag in result.stdout + result.stderr
    if expected_code is not None:
        assert result.returncode == expected_code
