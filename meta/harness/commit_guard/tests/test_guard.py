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


@pytest.mark.parametrize(
    "body",
    [
        "git checkout -b feat/x",
        "git switch -c feat/x",
        "\tgit checkout -b feat/x",
        "```bash\ngit checkout -b feat/x\n```",
    ],
)
def test_heredoc_body_does_not_latch(monkeypatch, body: str) -> None:
    # 본문은 파일에 쓰이는 내용이라 실행되지 않는다 — 커밋은 여전히 main에 얹힌다.
    _set_branch(monkeypatch, "main")
    command = (
        "cat > notes.md <<'EOF'\n" + body + "\nEOF\ngit commit -m \"docs: notes\""
    )
    assert _run_main(monkeypatch, _bash_payload(command)) == 42


@pytest.mark.parametrize(
    "command",
    [
        # 종료어는 완전 일치여야 한다 — 들여쓰거나 뒤에 공백이 붙으면 본문이다.
        "cat > a.md <<'EOF'\nplain\n  EOF\ngit checkout -b feat/x\nEOF\ngit commit -m \"docs: n\"",
        "cat > a.md <<'EOF'\nplain\n\tEOF\ngit checkout -b feat/x\nEOF\ngit commit -m \"docs: n\"",
        "cat > a.md <<'EOF'\nplain\nEOF \ngit checkout -b feat/x\nEOF\ngit commit -m \"docs: n\"",
        # `\w+`로는 못 읽는 종료어.
        "cat > a.md <<'EOF-X'\ngit checkout -b feat/x\nEOF-X\ngit commit -m \"docs: n\"",
        # 한 줄에 열린 heredoc 두 개는 순서대로 본문을 갖는다.
        "cat <<A > f.md <<B\nfirst\nA\ngit checkout -b feat/x\nB\ngit commit -m \"docs: n\"",
        # 본문에 따옴표가 하나 들어가도 줄 구조가 살아 있어야 한다.
        "cat > a.md <<'EOF'\n; git checkout -b feat/x\ndon't\nit's\nEOF\ntrue && git commit -m \"docs: n\"",
    ],
)
def test_heredoc_body_variants_do_not_latch(monkeypatch, command: str) -> None:
    _set_branch(monkeypatch, "main")
    assert _run_main(monkeypatch, _bash_payload(command)) == 42


def test_heredoc_overmatch_cannot_hide_a_move_to_protected(monkeypatch) -> None:
    # 따옴표 안의 `<<`가 heredoc으로 오인돼도, 실행되는 `checkout main`이
    # 가려져선 안 된다. 본문 줄은 검사를 되살리는 방향만 허용한다.
    _set_branch(monkeypatch, "main")
    command = (
        'git checkout -b feat/x\necho "see a << b"\n'
        'git checkout main\ngit commit -m "docs: n"'
    )
    assert _run_main(monkeypatch, _bash_payload(command)) == 42


def test_heredoc_commit_message_idiom_still_latches() -> None:
    # `-m "$(cat <<'EOF' ...)"`는 줄 단위 토큰화가 실패해 전체 문자열 경로로
    # 간다. #39의 핵심 관용구이므로 래치와 제목 추출이 모두 살아 있어야 한다.
    command = (
        'git checkout -b feat/x && git commit -m "$(cat <<\'EOF\'\n'
        "feat: add thing\n\nbody\nEOF\n)\""
    )
    (inv,) = guard.detect_invocations(command)
    assert inv.branch_check_unsafe
    assert inv.subject == "feat: add thing"


def test_real_checkout_after_a_heredoc_still_latches(monkeypatch) -> None:
    # 종료어 뒤의 checkout은 진짜 명령이다.
    calls = _set_branch(monkeypatch, "main")
    command = (
        "cat > notes.md <<'EOF'\nplain text\nEOF\n"
        'git checkout -b feat/x\ngit commit -m "feat: x"'
    )
    assert _run_main(monkeypatch, _bash_payload(command)) == 0
    assert calls == []


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
