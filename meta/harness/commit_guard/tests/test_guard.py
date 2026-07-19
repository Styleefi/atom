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
    assert _run_main(monkeypatch, _bash_payload('git commit -m "feat: x"')) == 2


def test_commit_on_master_is_blocked(monkeypatch) -> None:
    _set_branch(monkeypatch, "master")
    assert _run_main(monkeypatch, _bash_payload('git commit -m "feat: x"')) == 2


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


def test_branch_lookup_failure_passes(monkeypatch) -> None:
    _set_branch(monkeypatch, None)
    assert _run_main(monkeypatch, _bash_payload('git commit -m "feat: x"')) == 0


def test_detached_head_passes(monkeypatch) -> None:
    _set_branch(monkeypatch, "HEAD")
    assert _run_main(monkeypatch, _bash_payload('git commit -m "feat: x"')) == 0


def test_bad_message_is_blocked(monkeypatch) -> None:
    _set_branch(monkeypatch, "feat/thing")
    assert _run_main(monkeypatch, _bash_payload('git commit -m "Update stuff"')) == 2


def test_bad_heredoc_message_is_blocked(monkeypatch) -> None:
    _set_branch(monkeypatch, "feat/thing")
    command = 'git commit -m "$(cat <<\'EOF\'\nbad subject line\nEOF\n)"'
    assert _run_main(monkeypatch, _bash_payload(command)) == 2


def test_unextractable_message_passes_on_feature_branch(monkeypatch) -> None:
    _set_branch(monkeypatch, "feat/thing")
    assert _run_main(monkeypatch, _bash_payload("git commit --amend --no-edit")) == 0


def test_unextractable_message_still_blocks_on_main(monkeypatch) -> None:
    _set_branch(monkeypatch, "main")
    assert _run_main(monkeypatch, _bash_payload("git commit --amend --no-edit")) == 2


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
