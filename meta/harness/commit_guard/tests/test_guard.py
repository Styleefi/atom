# commit_guard의 감지·브랜치 판정·형식 검사·fail-open 경로를 검증하는 테스트
"""guard 모듈 테스트.

git 호출은 전부 mock: 감지 로직은 detect_invocations를 직접, 판정 흐름은
stdin JSON + _current_branch monkeypatch로 검증한다. 설계 불변식 —
오차단 금지(문자열 내부 언급·타 저장소), 실패는 전부 통과 방향, 차단
메시지의 override 안내 — 를 케이스로 고정한다.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys

import pytest

from harness.commit_guard import guard


def _run_main(monkeypatch, payload) -> int:
    """payload(dict 또는 원시 문자열)를 stdin으로 넣고 main()을 실행한다."""
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    return guard.main()


def _bash_payload(command: str, cwd: str | None = None) -> dict:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


def _set_branch(monkeypatch, branch: str | None):
    """_current_branch를 고정 결과로 바꾸고 호출 기록을 돌려준다."""
    calls: list[tuple[str | None, str | None]] = []

    def fake(cwd, c_path):
        calls.append((cwd, c_path))
        return branch

    monkeypatch.setattr(guard, "_current_branch", fake)
    return calls


# --- 감지 -------------------------------------------------------------------


def test_detects_plain_commit() -> None:
    (inv,) = guard.detect_invocations('git commit -m "feat: add thing"')
    assert inv.subject == "feat: add thing"
    assert inv.c_path is None
    assert not inv.override
    assert not inv.branch_check_unsafe


def test_detects_combined_short_flag() -> None:
    (inv,) = guard.detect_invocations('git commit -am "fix: repair thing"')
    assert inv.subject == "fix: repair thing"


def test_detects_message_equals_form() -> None:
    (inv,) = guard.detect_invocations('git commit --message="docs: update readme"')
    assert inv.subject == "docs: update readme"


def test_detects_c_path_global_option() -> None:
    (inv,) = guard.detect_invocations('git -C /tmp/other commit -m "feat: x"')
    assert inv.c_path == "/tmp/other"


def test_skips_git_config_global_option() -> None:
    (inv,) = guard.detect_invocations('git -c user.name=x commit -m "feat: x"')
    assert inv.subject == "feat: x"


def test_heredoc_subject_extracted() -> None:
    command = 'git commit -m "$(cat <<\'EOF\'\nfeat(core): add parser\n\nbody text\nEOF\n)"'
    (inv,) = guard.detect_invocations(command)
    assert inv.subject == "feat(core): add parser"


def test_heredoc_unquoted_marker() -> None:
    command = 'git commit -m "$(cat <<EOF\nfix: close leak\nEOF\n)"'
    (inv,) = guard.detect_invocations(command)
    assert inv.subject == "fix: close leak"


def test_amend_without_message_gives_none_subject() -> None:
    (inv,) = guard.detect_invocations("git commit --amend --no-edit")
    assert inv.subject is None


def test_message_from_file_gives_none_subject() -> None:
    (inv,) = guard.detect_invocations("git commit -F message.txt")
    assert inv.subject is None


def test_empty_subject_gives_none() -> None:
    (inv,) = guard.detect_invocations('git commit -m ""')
    assert inv.subject is None


def test_cd_before_commit_marks_branch_check_unsafe() -> None:
    (inv,) = guard.detect_invocations('cd /elsewhere && git commit -m "feat: x"')
    assert inv.branch_check_unsafe


@pytest.mark.parametrize(
    "command",
    [
        'git checkout -b feat/x main && git commit -m "feat: x"',
        'git switch -c feat/x && git commit -m "feat: x"',
        'git checkout feat/x && git commit -m "feat: x"',
    ],
)
def test_branch_change_before_commit_marks_branch_check_unsafe(command: str) -> None:
    (inv,) = guard.detect_invocations(command)
    assert inv.branch_check_unsafe


@pytest.mark.parametrize(
    "command",
    [
        'git checkout -- src/foo.py && git commit -m "fix: x"',
        'git checkout HEAD~1 -- src/foo.py && git commit -m "fix: x"',
    ],
)
def test_path_restore_checkout_keeps_branch_check(command: str) -> None:
    # `--` 뒤 경로를 되돌리는 형태는 브랜치를 바꾸지 않는다.
    (inv,) = guard.detect_invocations(command)
    assert not inv.branch_check_unsafe


def test_trailing_bare_dashes_still_changes_branch() -> None:
    # `git checkout side --`는 경로가 없어 복원이 아니라 실제 브랜치 이동이다.
    (inv,) = guard.detect_invocations('git checkout side -- && git commit -m "feat: x"')
    assert inv.branch_check_unsafe


@pytest.mark.parametrize(
    "command",
    [
        'git checkout main && git commit -m "feat: x"',
        'git switch master && git commit -m "feat: x"',
    ],
)
def test_move_to_protected_branch_keeps_branch_check(command: str) -> None:
    # 대상이 보호 브랜치면 커밋이 거기 얹히므로 검사를 건너뛰면 안 된다.
    (inv,) = guard.detect_invocations(command)
    assert not inv.branch_check_unsafe


def test_protected_start_point_still_marks_unsafe() -> None:
    # `-b` 뒤 토큰이 대상이다 — 시작점 main은 대상이 아니다(이슈 #39의 핵심 형태).
    (inv,) = guard.detect_invocations(
        'git checkout -b feat/x main && git commit -m "feat: x"'
    )
    assert inv.branch_check_unsafe


@pytest.mark.parametrize(
    "command",
    [
        'git checkout --track origin/main && git commit -m "feat: x"',
        'git checkout -t origin/master && git commit -m "feat: x"',
        'git switch --track origin/main && git commit -m "feat: x"',
        'git switch -t origin/main && git commit -m "feat: x"',
        'git checkout --track=inherit origin/main && git commit -m "feat: x"',
        'git checkout --track refs/remotes/origin/main && git commit -m "feat: x"',
    ],
)
def test_track_to_protected_remote_keeps_branch_check(command: str) -> None:
    # `--track origin/main`은 로컬 `main`을 만든다 — 토큰만 보면 놓친다.
    (inv,) = guard.detect_invocations(command)
    assert not inv.branch_check_unsafe


@pytest.mark.parametrize(
    "command",
    [
        'git checkout --track origin/feature/main && git commit -m "feat: x"',
        'git switch --track origin/release/master && git commit -m "feat: x"',
    ],
)
def test_track_strips_only_the_first_path_component(command: str) -> None:
    # 원격 접두만 벗긴다 — 로컬 브랜치는 `feature/main`이지 `main`이 아니다.
    (inv,) = guard.detect_invocations(command)
    assert inv.branch_check_unsafe


def test_track_to_feature_remote_still_marks_unsafe() -> None:
    (inv,) = guard.detect_invocations(
        'git checkout --track origin/feat/x && git commit -m "feat: x"'
    )
    assert inv.branch_check_unsafe


def test_remote_ref_target_keeps_branch_check_even_when_detaching() -> None:
    # 의도한 과차단: `--no-track origin/main`은 detached HEAD가 되므로 커밋이
    # main에 얹히지 않는다. 그래도 검사를 유지한다 — 원격 접두를 벗긴 형태까지
    # 보는 규칙이 `-qt origin/main` 같은 미인식 플래그를 막아주는 대가다.
    (inv,) = guard.detect_invocations(
        'git checkout --no-track origin/main && git commit -m "feat: x"'
    )
    assert not inv.branch_check_unsafe


@pytest.mark.parametrize(
    "command",
    [
        'git checkout --track origin/main -b feat/x && git commit -m "feat: x"',
        'git switch --track origin/main -c feat/x && git commit -m "feat: x"',
        'git checkout main -b feat/x && git commit -m "feat: x"',
    ],
)
def test_create_flag_wins_over_track_and_position(command: str) -> None:
    (inv,) = guard.detect_invocations(command)
    assert inv.branch_check_unsafe


def test_create_flag_to_protected_keeps_branch_check() -> None:
    # 생성 플래그가 이기므로 대상은 `main`이다 — `--track`의 feat/x가 아니다.
    (inv,) = guard.detect_invocations(
        'git checkout --track origin/feat/x -b main && git commit -m "feat: x"'
    )
    assert not inv.branch_check_unsafe


@pytest.mark.parametrize(
    "command",
    [
        'git checkout --track && git commit -m "feat: x"',
        'git checkout -b && git commit -m "feat: x"',
        'git checkout --track origin/ && git commit -m "feat: x"',
        'git checkout - && git commit -m "feat: x"',
    ],
)
def test_degenerate_branch_change_args_do_not_raise(command: str) -> None:
    # 예외가 나면 run()이 삼켜 exit 1이 되고 해당 형태에서 guard가 무력화된다.
    assert len(guard.detect_invocations(command)) == 1


@pytest.mark.parametrize(
    "command",
    [
        # 값이 붙은 생성 플래그.
        'git checkout -bmain && git commit -m "feat: x"',
        'git checkout -Bmain && git commit -m "feat: x"',
        'git switch -cmain && git commit -m "feat: x"',
        'git switch -Cmain && git commit -m "feat: x"',
        'git switch --create=main && git commit -m "feat: x"',
        'git switch --force-create=main && git commit -m "feat: x"',
        # 묶음 단축 옵션 — 대상을 못 읽으므로 검사 유지로 떨어진다.
        'git checkout -qBmain && git commit -m "feat: x"',
        'git checkout -fBmain && git commit -m "feat: x"',
        'git switch -qCmain && git commit -m "feat: x"',
        # 플래그를 못 알아봐도 원격 접두를 벗긴 형태에서 걸린다.
        'git checkout -qt origin/main && git commit -m "feat: x"',
        'git checkout -tdirect origin/main && git commit -m "feat: x"',
    ],
)
def test_protected_target_spellings_keep_branch_check(command: str) -> None:
    # 전부 실제 git에서 로컬 `main`에 올라간다(git 2.53 실측). 철자를 바꾼다고
    # guard가 뚫리면 안 된다.
    (inv,) = guard.detect_invocations(command)
    assert not inv.branch_check_unsafe


@pytest.mark.parametrize(
    "command",
    [
        'git checkout -p && git commit -m "feat: x"',
        'git checkout - && git commit -m "feat: x"',
        'git checkout && git commit -m "feat: x"',
    ],
)
def test_unresolved_target_keeps_branch_check(command: str) -> None:
    # 대상을 못 읽으면 검사를 유지한다. 브랜치가 안 바뀌는 형태에서는 과차단이지만
    # override로 복구되고, 반대 방향은 main 직커밋이 조용히 통과하는 것이다.
    (inv,) = guard.detect_invocations(command)
    assert not inv.branch_check_unsafe


@pytest.mark.parametrize(
    "command",
    [
        'git checkout -bfeat/x main && git commit -m "feat: x"',
        'git switch --create=feat/x main && git commit -m "feat: x"',
    ],
)
def test_attached_create_value_is_the_target(command: str) -> None:
    # 붙여 쓴 값이 대상이다 — 시작점 `main`이 아니다.
    (inv,) = guard.detect_invocations(command)
    assert inv.branch_check_unsafe


def test_git_branch_does_not_mark_branch_check_unsafe() -> None:
    (inv,) = guard.detect_invocations('git branch feat/x && git commit -m "feat: x"')
    assert not inv.branch_check_unsafe


def test_commit_before_branch_change_is_still_checked() -> None:
    (inv,) = guard.detect_invocations('git commit -m "feat: x" && git checkout main')
    assert not inv.branch_check_unsafe


def test_branch_change_latch_spans_lines() -> None:
    (inv,) = guard.detect_invocations(
        'git checkout -b feat/x\ngit commit -m "feat: x"'
    )
    assert inv.branch_check_unsafe


def test_override_prefix_detected() -> None:
    (inv,) = guard.detect_invocations(
        'ATOM_COMMIT_OVERRIDE=1 git commit -m "whatever"'
    )
    assert inv.override


def test_commit_mention_in_string_is_not_detected() -> None:
    assert guard.detect_invocations('echo "git commit -m broken"') == []
    assert (
        guard.detect_invocations('gh pr create --body "run git commit -m x first"')
        == []
    )


def test_other_git_subcommands_not_detected() -> None:
    assert guard.detect_invocations("git status && git push origin feat/x") == []


def test_unparseable_command_is_fully_fail_open() -> None:
    # 폴백 제거 pin (#45): shlex가 줄 단위·전체 두 단계 모두 실패하는 명령은
    # 감지 자체를 포기한다. 문자열 리터럴 속 커밋 텍스트를 정규식이 실행으로
    # 오인해 무관한 Bash를 차단했던 경로 — 감지 공백은 commit_backstop이 받는다.
    heredoc_45 = (
        "uv run python - <<'PY'\n"
        "cases = [\n"
        "    'git checkout -b feat/x && git commit -m \"feat: x,\n"
        "]\n"
        "PY\n"
    )
    assert guard.detect_invocations(heredoc_45) == []
    assert guard.detect_invocations("echo it's broken\ngit commit -m \"feat: x\"") == []


def test_unparseable_command_passes_end_to_end(monkeypatch) -> None:
    # 보호 브랜치 위였더라도 파싱 불가 명령은 차단 없이 통과해야 한다 (#45).
    _set_branch(monkeypatch, "main")
    payload = _bash_payload("echo it's broken\ngit commit -m \"feat: x\"")
    assert _run_main(monkeypatch, payload) == 0


# --- 형식 검사 ---------------------------------------------------------------


def test_validate_accepts_standard_headers() -> None:
    assert guard.validate_subject("feat(core): add lazy builder") is None
    assert guard.validate_subject("fix: repair timeout") is None
    assert guard.validate_subject("refactor(api)!: drop legacy endpoint") is None
    # 숫자 시작 제목은 정상 (대문자 시작만 금지).
    assert guard.validate_subject("perf: 3x faster parsing") is None


def test_validate_rejects_bad_headers() -> None:
    assert guard.validate_subject("update stuff") is not None
    assert guard.validate_subject("feat: Add thing") is not None
    assert guard.validate_subject("feat: add thing.") is not None
    assert guard.validate_subject("feat: " + "x" * 51) is not None


# --- 알려진 오차단 잠금 ------------------------------------------------------
# sibling issue_duplicate_guard의 _KNOWN_FALSE_BLOCKS와 같은 규약. 각 항목은
# bash가 차단 사유대로 실행하지 않는데 가드가 감지하는, owner가 수용한
# 한계다(#52 동결 — 수리는 텍스트 추론 정련의 재개라 금지, #74 carve-out —
# 실사용 발생의 자동 기록은 차단 이력 로그(#76, 구현 전에는 세션 트랜스크립트가
# 기록 경로)가 맡고, 수리는 owner 결정).
# 설명은 bash 실행 상태를 밝힌다. 무조건 실행이면 "실행됨", 무조건 미실행이면
# "미실행", 조건부면 "실행은 …때"로 조건을 병기한다(규약 준수는 아래 테스트가
# 검사한다).
#
# **이 테스트가 깨졌다면 버그가 고쳐진 것이다.** 기대값을 되돌리지 말고 해당
# 항목을 지워라.
#
# **이 표가 망라적이라는 증명은 없다.** 여기 있는 것은 실측으로 확인된 것들뿐이다.
#
# 형식: (설명, 명령, 현재 감지되는 제목 튜플, 출처).

_KNOWN_FALSE_BLOCKS = [
    ("주석 해석이 꺼져 있어(리터럴 `#` 보존) 주석 뒤 텍스트가 명령으로 읽힌다 "
     "— bash: 첫 커밋만 실행됨(`#` 뒤는 주석). 규격인 첫 제목만 실행되는데 "
     "규격 위반인 두 번째 제목까지 감지돼 차단된다 — 기록만",
     'git commit -m "feat(x): a" # ; git commit -m "Bad."',
     ("feat(x): a", "Bad."), "#74 시금석(구성 발견)"),
]

# 설명이 bash 실행 상태를 밝히는 세 가지 형식 (sibling 규약과 동일).
_EXECUTION_CLAIM_FORMS = ("실행됨", "미실행", "실행은")


def test_known_false_blocks_state_execution() -> None:
    # 규약을 통째로 빠뜨린 항목과 조건부 형식 누락만 잡는다. 설명이 사실인지는
    # 검사하지 못한다 — 그건 bash 실측 대조가 담당한다(sibling 코퍼스 헤더 참조).
    for description, _cmd, _subjects, _origin in _KNOWN_FALSE_BLOCKS:
        assert any(form in description for form in _EXECUTION_CLAIM_FORMS), (
            f"설명이 bash 실행 상태를 밝히지 않는다 — {description!r}"
        )
        if "실행은" in description:
            assert "때" in description, f"조건부 실행인데 조건이 없다 — {description!r}"


def test_known_false_blocks() -> None:
    for description, cmd, expected_subjects, origin in _KNOWN_FALSE_BLOCKS:
        subjects = tuple(inv.subject for inv in guard.detect_invocations(cmd))
        assert subjects == expected_subjects, (
            f"{description} ({origin}): got {subjects!r} — 오차단이 사라졌다면 "
            "이 항목을 지워라. 기대값을 되돌리지 마라"
        )


# --- 판정 흐름 ---------------------------------------------------------------


def test_commit_on_main_is_blocked(monkeypatch) -> None:
    _set_branch(monkeypatch, "main")
    assert _run_main(monkeypatch, _bash_payload('git commit -m "feat: x"')) == 42


def test_commit_on_master_is_blocked(monkeypatch) -> None:
    _set_branch(monkeypatch, "master")
    assert _run_main(monkeypatch, _bash_payload('git commit -m "feat: x"')) == 42


def test_commit_on_feature_branch_passes(monkeypatch) -> None:
    _set_branch(monkeypatch, "feat/thing")
    assert _run_main(monkeypatch, _bash_payload('git commit -m "feat: x"')) == 0


def test_payload_cwd_is_passed_to_branch_lookup(monkeypatch) -> None:
    calls = _set_branch(monkeypatch, "feat/thing")
    payload = _bash_payload('git commit -m "feat: x"', cwd="/work/dir")
    assert _run_main(monkeypatch, payload) == 0
    assert calls == [("/work/dir", None)]


def test_c_path_is_passed_to_branch_lookup(monkeypatch) -> None:
    calls = _set_branch(monkeypatch, "feat/thing")
    payload = _bash_payload('git -C /tmp/other commit -m "feat: x"')
    assert _run_main(monkeypatch, payload) == 0
    assert calls == [(None, "/tmp/other")]


def test_cd_prefix_skips_branch_check(monkeypatch) -> None:
    calls = _set_branch(monkeypatch, "main")  # 차단됐어야 할 브랜치
    payload = _bash_payload('cd /elsewhere && git commit -m "feat: x"')
    assert _run_main(monkeypatch, payload) == 0
    assert calls == []  # 브랜치 조회 자체를 하지 않는다


def test_checkout_prefix_skips_branch_check(monkeypatch) -> None:
    calls = _set_branch(monkeypatch, "main")  # 차단됐어야 할 브랜치
    payload = _bash_payload('git checkout -b feat/x main && git commit -m "feat: x"')
    assert _run_main(monkeypatch, payload) == 0
    assert calls == []  # 브랜치 조회 자체를 하지 않는다


def test_switch_prefix_skips_branch_check(monkeypatch) -> None:
    _set_branch(monkeypatch, "main")
    payload = _bash_payload('git switch -c feat/x && git commit -m "feat: x"')
    assert _run_main(monkeypatch, payload) == 0


def test_header_check_survives_branch_change(monkeypatch) -> None:
    # 브랜치 검사는 건너뛰되 헤더 검사는 그대로 — 42의 원인이 헤더임을 calls로 증명한다.
    calls = _set_branch(monkeypatch, "feat/x")
    payload = _bash_payload('git checkout -b feat/x && git commit -m "Update stuff"')
    assert _run_main(monkeypatch, payload) == 42
    assert calls == []


def test_path_restore_checkout_still_blocks_on_main(monkeypatch) -> None:
    _set_branch(monkeypatch, "main")
    payload = _bash_payload('git checkout -- src/foo.py && git commit -m "fix: x"')
    assert _run_main(monkeypatch, payload) == 42


def test_checkout_to_main_while_on_main_still_blocks(monkeypatch) -> None:
    # 래치가 대상을 안 보면 `git checkout main && ` 한 토큰으로 guard가 무력화된다.
    _set_branch(monkeypatch, "main")
    payload = _bash_payload('git checkout main && git commit -m "feat: x"')
    assert _run_main(monkeypatch, payload) == 42


def test_checkout_to_main_from_feature_branch_passes(monkeypatch) -> None:
    # 설계상 통과한다: hook이 실행 전에 돌아 조회되는 브랜치가 아직 feat/x다.
    # 결함을 방치하는 게 아니라 알려진 한계를 코드베이스에 고정한다 — #44.
    _set_branch(monkeypatch, "feat/x")
    payload = _bash_payload('git checkout main && git commit -m "feat: x"')
    assert _run_main(monkeypatch, payload) == 0


@pytest.mark.parametrize("branch", ["main", "master"])
def test_track_to_protected_remote_still_blocks(monkeypatch, branch: str) -> None:
    _set_branch(monkeypatch, branch)
    payload = _bash_payload('git checkout --track origin/main && git commit -m "feat: x"')
    assert _run_main(monkeypatch, payload) == 42


def test_track_to_feature_remote_skips_branch_check(monkeypatch) -> None:
    # main에서 재야 의미가 있다 — feat/x면 래치와 무관하게 0이라 동어반복이 된다.
    calls = _set_branch(monkeypatch, "main")
    payload = _bash_payload(
        'git checkout --track origin/feat/x && git commit -m "feat: x"'
    )
    assert _run_main(monkeypatch, payload) == 0
    assert calls == []


def test_track_to_main_from_feature_branch_passes(monkeypatch) -> None:
    # 설계상 통과한다(#44). calls 단언이 있어야 "래치가 안 켜졌다"가 고정된다 —
    # 반환값만 보면 래치가 켜져도 0이라 아무것도 증명하지 못한다.
    calls = _set_branch(monkeypatch, "feat/x")
    payload = _bash_payload('git checkout --track origin/main && git commit -m "feat: x"')
    assert _run_main(monkeypatch, payload) == 0
    assert calls == [(None, None)]


def test_later_move_to_protected_restores_branch_check(monkeypatch) -> None:
    # 마지막 브랜치 변경이 이긴다 — 커밋은 main에 얹히므로 검사가 되살아난다.
    _set_branch(monkeypatch, "main")
    payload = _bash_payload(
        'git checkout feat/x && git checkout main && git commit -m "feat: x"'
    )
    assert _run_main(monkeypatch, payload) == 42


def test_cd_latch_survives_a_later_branch_change(monkeypatch) -> None:
    # `cd` 뒤로는 어느 저장소인지 알 수 없으므로 브랜치 변경이 되돌리지 못한다.
    calls = _set_branch(monkeypatch, "main")
    payload = _bash_payload(
        'cd /elsewhere && git checkout main && git commit -m "feat: x"'
    )
    assert _run_main(monkeypatch, payload) == 0
    assert calls == []


def test_checkout_in_another_repository_does_not_latch(monkeypatch) -> None:
    # `git -C /other checkout`은 이 저장소의 브랜치를 바꾸지 않는다.
    _set_branch(monkeypatch, "main")
    payload = _bash_payload(
        'git -C /other checkout -b feat/x && git commit -m "feat: x"'
    )
    assert _run_main(monkeypatch, payload) == 42


def test_branch_change_mention_in_string_does_not_unlatch(monkeypatch) -> None:
    _set_branch(monkeypatch, "main")
    payload = _bash_payload('echo "git checkout -b x" && git commit -m "feat: y"')
    assert _run_main(monkeypatch, payload) == 42


def test_branch_lookup_failure_passes(monkeypatch) -> None:
    _set_branch(monkeypatch, None)
    assert _run_main(monkeypatch, _bash_payload('git commit -m "feat: x"')) == 0


def test_detached_head_passes(monkeypatch) -> None:
    _set_branch(monkeypatch, "HEAD")
    assert _run_main(monkeypatch, _bash_payload('git commit -m "feat: x"')) == 0


def test_bad_message_is_blocked(monkeypatch) -> None:
    _set_branch(monkeypatch, "feat/thing")
    assert _run_main(monkeypatch, _bash_payload('git commit -m "Update stuff"')) == 42


def test_bad_heredoc_message_is_blocked(monkeypatch) -> None:
    _set_branch(monkeypatch, "feat/thing")
    command = 'git commit -m "$(cat <<\'EOF\'\nbad subject line\nEOF\n)"'
    assert _run_main(monkeypatch, _bash_payload(command)) == 42


def test_unextractable_message_passes_on_feature_branch(monkeypatch) -> None:
    _set_branch(monkeypatch, "feat/thing")
    assert _run_main(monkeypatch, _bash_payload("git commit --amend --no-edit")) == 0


def test_unextractable_message_still_blocks_on_main(monkeypatch) -> None:
    _set_branch(monkeypatch, "main")
    assert _run_main(monkeypatch, _bash_payload("git commit --amend --no-edit")) == 42


def test_override_passes_on_main(monkeypatch) -> None:
    _set_branch(monkeypatch, "main")
    command = 'ATOM_COMMIT_OVERRIDE=1 git commit -m "hotfix"'
    assert _run_main(monkeypatch, _bash_payload(command)) == 0


def test_block_message_mentions_override(monkeypatch, capsys) -> None:
    _set_branch(monkeypatch, "main")
    _run_main(monkeypatch, _bash_payload('git commit -m "feat: x"'))
    err = capsys.readouterr().err
    assert guard.OVERRIDE_TOKEN in err


def test_non_bash_tool_passes(monkeypatch) -> None:
    payload = {"tool_name": "Write", "tool_input": {"file_path": "x"}}
    assert _run_main(monkeypatch, payload) == 0


def test_malformed_input_warns_not_blocks(monkeypatch) -> None:
    assert _run_main(monkeypatch, "{not json") == 1


# --- _current_branch 자체의 fail-open ----------------------------------------


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("origin/main", "main"),
        ("origin/feature/x", "feature/x"),
        ("refs/remotes/origin/main", "main"),
        ("remotes/origin/master", "master"),
        ("foo", None),
        ("origin/", None),
        (None, None),
    ],
)
def test_remote_stripped_removes_only_the_first_component(ref, expected) -> None:
    assert guard._remote_stripped(ref) == expected


def test_current_branch_timeout_returns_none(monkeypatch) -> None:
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(guard.subprocess, "run", raise_timeout)
    assert guard._current_branch(None, None) is None


def test_current_branch_oserror_returns_none(monkeypatch) -> None:
    def raise_oserror(*args, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(guard.subprocess, "run", raise_oserror)
    assert guard._current_branch(None, None) is None
