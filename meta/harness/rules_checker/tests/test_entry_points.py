# 하네스 __main__.py 진입점이 실제로 실행 가능한지 서브프로세스로 검증하는 테스트
"""진입점 실행 가능성 테스트 (#107).

배포되는 훅 명령은 두 겹이다 — 셸 래퍼(uv 존재 확인, 42→2 되매핑)와 파이썬
진입점(`python -m harness.<pkg>`). 앞 겹은 test_hook_command_contract.py가
uv를 PATH 스텁으로 갈아 끼워 핀한다. 이 파일은 그 스텁이 대신하고 있던
나머지 겹, 즉 진입점 자체가 뜨는지를 핀한다. 둘을 합쳐도 실제 uv가 실제
하네스 모듈을 띄우는 조합은 어느 쪽도 태우지 않는다 — 그건 CI의 `uv sync`가
잡는다.

체커는 `__main__.py`의 **존재만** 본다(check_rules.py의 is_file(); 그 한계는
meta/rules/README.md가 "empty files still pass"로 명시). 그래서 문법 오류·
import 오류·빈 파일이 스위트와 체커를 모두 통과하고 훅만 런타임에 죽는다.
settings.json이 42가 아닌 종료 코드를 비차단 경고로 취급하므로 가드가
조용히 가드를 멈춘다.

import이 아니라 서브프로세스인 이유: 다섯 중 넷은 모듈 최상단에서
`sys.exit(run())`을 실행하고 run()은 곧바로 stdin을 읽는다. pytest 기본
캡처에서 그 read()는 DontReadFromInput이 OSError를 던지고(`-s`에서만 실제로
터미널 stdin을 삼킨다), 어느 쪽이든 포괄 예외를 거쳐 SystemExit으로 끝난다.
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
# run()의 포괄 예외가 같은 태그로 fail-open 문구를 찍는다. 가드 넷의 그 잔여는
# 각 하네스 자신의 테스트(main()/run()을 직접 호출한다)가 덮는다.
#
# rules_checker만 종료 코드가 None이다. 그 코드는 진입점 건강이 아니라 **저장소
# 전체의 규칙 건강**을 보고하므로(위반이 있으면 1), 0을 단언하면 무관한 규칙
# 위반에 이 테스트가 빨개진다 — 옳게 빨간데 진단이 엉뚱한 대상을 가리킨다.
# 태그 `rules_checker:`는 통과·위반 양쪽 모두에 찍히므로 상태와 무관하고,
# 크래시(태그 없음)는 그대로 걸린다.
#
# 대가는 적어 둔다: 이 완화로 rules_checker의 `main()`이 위반을 1로 옮기는
# 매핑은 이 파일이 덮지 않는다. test_real_repo_rules_all_pass는 check_rules()의
# 반환값을 볼 뿐 main()을 부르지 않는다. 그 매핑이 조용히 0으로 회귀하면 두 CI의
# 체커 게이트가 영구히 초록이 된다.
ENTRY_POINTS = {
    "answer_first_reminder": (1, "[answer-first-reminder]"),
    "commit_backstop": (1, "[commit-backstop]"),
    "commit_guard": (1, "[commit-guard]"),
    "issue_duplicate_guard": (1, "[issue-duplicate-guard]"),
    "rules_checker": (None, "rules_checker:"),
}


def _entry_point_path(package: str) -> Path:
    return find_repo_root() / "meta" / "harness" / package / "__main__.py"


def _run_entry_point(package: str, state_home: Path) -> subprocess.CompletedProcess[str]:
    """배포 명령의 파이썬 겹과 같은 방식으로 진입점을 실행한다.

    `input=""`는 위생이 아니라 필수다 — 넘기지 않으면 자식이 부모의 stdin을
    상속한다. 기본 캡처에서는 fd 0이 devnull이라 끝나지만 `pytest -s`에서는
    터미널이라 가드 넷이 stdin.read()에서 막힌다. timeout은 그 회귀를 멈춤이
    아니라 실패로 바꾼다.

    `harness` import를 지탱하는 것은 `cwd`다 — `python -m`이 cwd를
    sys.path[0]에 넣는다. env 병합은 그 import와 무관하며(sys.executable은
    절대 경로라 PATH가 필요 없고 PYTHONPATH는 스위트에 설정되어 있지 않다),
    배포된 훅이 물려받는 환경(HOME·TMPDIR·VIRTUAL_ENV 등)에 대한 충실도를
    지키기 위한 것이다. XDG_STATE_HOME은 blocklog 원장만 격리한다 —
    commit_backstop의 상태 파일은 대상 저장소의 git 디렉터리에 쓰이므로 이
    보험 밖이다. 정상 페이로드를 먹이도록 바꾸려면 그쪽도 함께 봐야 한다.
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
    # 손으로 유지하는 목록이 조용히 낡는 것을 막는 동반 단언 — 새 하네스가
    # 진입점을 들고 들어오면 여기서 시끄럽게 터진다. blocklog·ci_contract는
    # __main__.py가 없어 glob에서 자연 제외되며, 그 제외는 우연이 아니라
    # 이 단언이 성립하는 근거다(고치지 말 것).
    #
    # 한 방향으로만 단언한다 — 표에 있는데 없는 쪽은 위반이 아니다. atom은
    # 자식 프로젝트가 상속하는 SSOT이고, 가드를 정합하게 제거한 자식에서
    # 양방향 단언은 상속된 스위트를 영구히 빨갛게 만든다(형제 테스트가 #43에서
    # 같은 이유로 skip을 둔다). atom 자신에서의 삭제는 체커의 존재 검사가 잡는다.
    discovered = {
        path.parent.name
        for path in (find_repo_root() / "meta" / "harness").glob("*/__main__.py")
    }
    assert discovered - set(ENTRY_POINTS) == set()


@pytest.mark.parametrize("package", sorted(ENTRY_POINTS))
def test_entry_point_runs(package: str, tmp_path: Path) -> None:
    # 문법 오류·import 오류·빈 파일이면 태그가 나오지 않는다. 스트림은 갈린다
    # (가드는 stderr, rules_checker는 stdout)이라 합쳐서 본다.
    #
    # skip은 진입점 파일 **자체의 부재**로 좁힌다 — 정합하게 제거한 자식
    # 프로젝트만을 위한 완화이며, 넓게 잡으면 깨진 진입점까지 삼켜 이 테스트가
    # 조용히 무력해진다(#43에서 importorskip이 그렇게 실패했다).
    if not _entry_point_path(package).is_file():
        pytest.skip(f"entry point removed: harness/{package}/__main__.py")
    expected_code, tag = ENTRY_POINTS[package]
    result = _run_entry_point(package, tmp_path)
    assert tag in result.stdout + result.stderr
    if expected_code is not None:
        assert result.returncode == expected_code
