# 하네스 __main__.py 진입점이 실제로 실행 가능한지 서브프로세스로 검증하는 테스트
"""진입점 실행 가능성 테스트 (#107).

배포되는 훅 명령은 두 겹이다 — 셸 래퍼(uv 존재 확인, 42→2 되매핑)와 파이썬
진입점(`python -m harness.<pkg>`). 앞 겹은 test_hook_command_contract.py가
uv를 PATH 스텁으로 갈아 끼워 핀한다. 이 파일은 그 스텁이 대신하고 있던
나머지 겹, 즉 진입점 자체가 뜨는지를 핀한다.

체커는 `__main__.py`의 **존재만** 본다(check_rules.py의 is_file(); 그 한계는
meta/rules/README.md가 "empty files still pass"로 명시). 그래서 문법 오류·
import 오류·빈 파일이 스위트와 체커를 모두 통과하고 훅만 런타임에 죽는다.
settings.json이 42가 아닌 종료 코드를 비차단 경고로 취급하므로 가드가
조용히 가드를 멈춘다.

import이 아니라 서브프로세스인 이유: 다섯 중 넷은 모듈 최상단에서
`sys.exit(run())`을 실행하고 run()은 곧바로 stdin을 읽는다. import하면
pytest의 stdin을 삼키고 훅이 실제로 돌다가 SystemExit이 난다.
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
# 태그는 메시지 전문이 아니라 하네스의 **정체**다 — 문구를 다듬어도 이 테스트가
# 빨개지면 안 된다. 태그가 증명하는 것은 "진입점이 import를 풀고 자기 모듈의
# 코드에 도달했다"까지이며, 하네스가 건강하다는 뜻은 아니다: main()이 던지면
# run()의 포괄 예외가 같은 태그로 fail-open 문구를 찍는다. 그 잔여는 각
# 하네스 자신의 테스트(main()/run()을 직접 호출한다)가 덮는다.
#
# rules_checker만 종료 코드가 None이다. 그 코드는 진입점 건강이 아니라 **저장소
# 전체의 규칙 건강**을 보고하므로(위반이 있으면 1), 0을 단언하면 무관한 규칙
# 위반에 이 테스트가 빨개진다 — 옳게 빨간데 진단이 엉뚱한 대상을 가리킨다.
# 태그 `rules_checker:`는 통과·위반 양쪽 모두에 찍히므로 상태와 무관하고,
# 크래시(태그 없음)는 그대로 걸린다. 저장소 규칙 건강은 이미 자기 테스트가
# 있다(test_real_repo_rules_all_pass). 둘은 각자 이름으로 따로 실패해야 한다.
ENTRY_POINTS = {
    "answer_first_reminder": (1, "[answer-first-reminder]"),
    "commit_backstop": (1, "[commit-backstop]"),
    "commit_guard": (1, "[commit-guard]"),
    "issue_duplicate_guard": (1, "[issue-duplicate-guard]"),
    "rules_checker": (None, "rules_checker:"),
}


def _run_entry_point(package: str, state_home: Path) -> subprocess.CompletedProcess[str]:
    """배포와 같은 방식으로 진입점을 실행한다.

    `input=""`는 위생이 아니라 필수다 — 넘기지 않으면 자식이 부모의 stdin을
    상속하고, `pytest -s`에서는 그게 터미널이라 가드 넷이 stdin.read()에서
    막혀 **스위트가 멈춘다**. timeout은 그 회귀를 멈춤이 아니라 실패로 바꾼다.

    env는 병합이다 — 통째로 넘기면 PATH/PYTHONPATH가 사라져 전부 실패한다.
    XDG_STATE_HOME은 보험이다: 잘못된 입력 경로는 원장을 쓰기 전에 반환하지만,
    나중에 정상 입력을 먹이도록 바꾸면 사용자의 실제 원장에 쓰게 된다.
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


def test_every_entry_point_is_covered() -> None:
    # 손으로 유지하는 목록이 조용히 낡는 것을 막는 동반 단언 — 새 하네스가
    # 진입점을 들고 들어오면 여기서 시끄럽게 터진다. blocklog·ci_contract는
    # __main__.py가 없어 glob에서 자연 제외되며, 그 제외는 우연이 아니라
    # 이 단언이 정확히 5개로 성립하는 근거다(고치지 말 것).
    discovered = {
        path.parent.name
        for path in (find_repo_root() / "meta" / "harness").glob("*/__main__.py")
    }
    assert discovered == set(ENTRY_POINTS)


@pytest.mark.parametrize("package", sorted(ENTRY_POINTS))
def test_entry_point_runs(package: str, tmp_path: Path) -> None:
    # 문법 오류·import 오류·빈 파일이면 태그가 나오지 않는다. 스트림은 갈린다
    # (가드는 stderr, rules_checker는 stdout)이라 합쳐서 본다.
    expected_code, tag = ENTRY_POINTS[package]
    result = _run_entry_point(package, tmp_path)
    assert tag in result.stdout + result.stderr
    if expected_code is not None:
        assert result.returncode == expected_code
