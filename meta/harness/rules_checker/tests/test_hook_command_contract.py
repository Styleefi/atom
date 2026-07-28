# settings.json 훅 래퍼 커맨드의 종료 코드 되매핑 계약을 /bin/sh로 검증하는 테스트
"""훅 래퍼 셸 계약 테스트.

rules_checker의 정본 템플릿(HOOK_COMMAND_BLOCKING/NON_BLOCKING)을 실제
/bin/sh로 실행해 종료 코드 되매핑 계약(#31)을 핀한다. uv를 PATH 스텁으로
바꿔, 가드/uv의 각 종료 코드가 Claude Code에 도달하는 값(차단 2, 비차단
경고 1, 통과 0)으로 수렴하는지 본다. test_real_repo_rules_all_pass가
실저장소 settings.json ↔ 템플릿 일치를 보증하므로, 여기가 green이면
실배선의 동작도 같다.

주의: 스텁은 반드시 외부 실행 파일이어야 한다 — 셸 함수로 흉내 내면 함수
안의 exit가 셸 전체를 종료시켜 래퍼 검증이 오탐이 된다. PATH는
`<스텁dir>:/usr/bin:/bin`로 잡아 진짜 uv(~/.local/bin 등)는 가리되 스텁이
쓰는 외부 명령(cat)은 표준 경로에서 찾게 한다.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harness.rules_checker.check_rules import (
    HOOK_COMMAND_BLOCKING,
    HOOK_COMMAND_NON_BLOCKING,
    find_repo_root,
)

MODULE = "harness.fake_guard"

BLOCKING_COMMAND = HOOK_COMMAND_BLOCKING.replace("{module}", MODULE)
NON_BLOCKING_COMMAND = HOOK_COMMAND_NON_BLOCKING.replace("{module}", MODULE)


def _run_hook(
    tmp_path: Path,
    command: str,
    stub_script: str | None,
    stdin: str = "{}",
    sh_flags: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """스텁 uv를 PATH 앞에 놓고 훅 커맨드를 /bin/sh로 실행한다.

    Args:
        tmp_path: 스텁을 둘 임시 디렉토리.
        command: 실행할 훅 커맨드 문자열.
        stub_script: 스텁 uv의 본문(셔뱅 제외). None이면 uv 부재 상황.
        stdin: 훅 stdin으로 넘길 페이로드.
        sh_flags: /bin/sh에 줄 추가 플래그(예: ("-e",)).

    Returns:
        완료된 subprocess 결과.
    """
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir(exist_ok=True)
    if stub_script is not None:
        stub = stub_dir / "uv"
        stub.write_text(f"#!/bin/sh\n{stub_script}\n", encoding="utf-8")
        stub.chmod(0o755)
    env = {"PATH": f"{stub_dir}:/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(tmp_path)}
    return subprocess.run(
        ["/bin/sh", *sh_flags, "-c", command],
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# (가드/uv 종료 코드, Claude Code에 도달해야 하는 값). 42만 차단(2)으로
# 되매핑되고, uv 자체 오류(2)·내부 경고(1)·시그널(130)은 전부 1로 수렴한다.
BLOCKING_REMAP = [(0, 0), (1, 1), (2, 1), (42, 2), (130, 1)]


@pytest.mark.parametrize(("stub_exit", "expected"), BLOCKING_REMAP)
def test_blocking_wrapper_remaps_exit_codes(
    tmp_path: Path, stub_exit: int, expected: int
) -> None:
    result = _run_hook(tmp_path, BLOCKING_COMMAND, f"exit {stub_exit}")
    assert result.returncode == expected, result.stderr


@pytest.mark.parametrize(("stub_exit", "expected"), BLOCKING_REMAP)
def test_blocking_wrapper_is_set_e_immune(
    tmp_path: Path, stub_exit: int, expected: int
) -> None:
    # 훅 실행 셸이 set -e여도 동작이 같아야 한다 — uv run을 if 조건으로
    # 감싼 이유. rc=$? 단독 패턴은 -e에서 uv 코드가 그대로 새어 #31이 재발한다.
    result = _run_hook(
        tmp_path, BLOCKING_COMMAND, f"exit {stub_exit}", sh_flags=("-e",)
    )
    assert result.returncode == expected, result.stderr


def test_blocking_wrapper_passes_stdin_to_uv(tmp_path: Path) -> None:
    # exec 제거 후에도 훅 JSON(stdin)이 uv까지 상속되는지 — 스텁이 stdin을
    # 읽어 확인되면 42(→2)로 신호한다.
    stub = '[ "$(cat)" = ping ] && exit 42\nexit 0'
    result = _run_hook(tmp_path, BLOCKING_COMMAND, stub, stdin="ping")
    assert result.returncode == 2, result.stderr


def test_wrapper_passes_when_uv_is_absent(tmp_path: Path) -> None:
    if shutil.which("uv", path="/usr/bin:/bin"):
        pytest.skip("uv exists in the standard PATH — cannot simulate absence")
    result = _run_hook(tmp_path, BLOCKING_COMMAND, None)
    assert result.returncode == 0, result.stderr


# 비차단형: 어떤 종료 코드도 차단(2)이 될 수 없다 — 42조차 1로 수렴한다.
NON_BLOCKING_REMAP = [(0, 0), (1, 1), (2, 1), (42, 1)]


@pytest.mark.parametrize(("stub_exit", "expected"), NON_BLOCKING_REMAP)
def test_non_blocking_wrapper_never_blocks(
    tmp_path: Path, stub_exit: int, expected: int
) -> None:
    result = _run_hook(tmp_path, NON_BLOCKING_COMMAND, f"exit {stub_exit}")
    assert result.returncode == expected, result.stderr


def test_non_blocking_wrapper_passes_stdout_through(tmp_path: Path) -> None:
    # UserPromptSubmit은 exit 0의 stdout이 컨텍스트로 주입된다 — 통과 경로의
    # stdout은 그대로 흘러야 한다.
    result = _run_hook(tmp_path, NON_BLOCKING_COMMAND, "echo REMINDER\nexit 0")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "REMINDER"


def test_blocking_template_remaps_the_guards_sentinel() -> None:
    # 가드 상수 ↔ 래퍼 템플릿의 실결합(리뷰 발견: 트리비얼 ==42 단정은 아무
    # 것도 묶지 못함). 값이 갈라지면 차단이 조용히 비차단 경고로 강등되므로,
    # 템플릿 문자열이 각 가드의 EXIT_BLOCK을 되매핑 조건으로 쓰는지 대조한다.
    # importorskip: 가드를 정합하게 제거한 자식 프로젝트에서 이 파일 수집이
    # 통째로 죽지 않도록(리뷰 2R) 모듈 부재는 이 테스트만 skip으로 수렴시킨다.
    for module_name in (
        "harness.commit_backstop.backstop",
        "harness.commit_guard.guard",
        "harness.issue_duplicate_guard.guard",
    ):
        guard = pytest.importorskip(module_name)
        assert f'-eq {guard.EXIT_BLOCK} ' in HOOK_COMMAND_BLOCKING


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_real_uv_propagates_sentinel_42() -> None:
    # 설계 전체가 "uv가 자식의 42를 그대로 전파한다"에 걸려 있다 — 전파가
    # 깨지면 차단이 조용히 경고로 강등되므로 실제 uv로 핀한다. --directory는
    # 절대 경로여야 한다(pytest cwd가 meta/라 상대 경로는 meta/meta로 오해석).
    meta_dir = find_repo_root() / "meta"
    result = subprocess.run(
        ["uv", "run", "--directory", str(meta_dir), "python", "-c", "raise SystemExit(42)"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 42, result.stderr
