# issue_duplicate_guard의 감지·판정·fail-open 경로를 검증하는 테스트
"""guard 모듈 테스트.

외부 CLI(gh/glab)는 전부 mock: 감지 로직은 detect_invocations를 직접,
판정 흐름은 stdin JSON + _run_search monkeypatch로 검증한다.
설계 불변식 — 오차단 금지(문자열 내부 언급), 실패는 전부 통과 방향 —
을 케이스로 고정한다.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys

from harness.issue_duplicate_guard import guard


def _run_main(monkeypatch, payload) -> int:
    """payload(dict 또는 원시 문자열)를 stdin으로 넣고 main()을 실행한다."""
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    return guard.main()


def _bash_payload(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


# ---------- 감지: 오탐 방지 ----------

def test_quoted_mention_with_operator_is_not_detected() -> None:
    # 따옴표 안 연산자 포함 — 이번 작업 커밋 메시지 같은 형태가 절대 걸리면 안 됨
    assert guard.detect_invocations('git commit -m "fix && gh issue create guard"') == []


def test_argument_position_literal_is_not_detected() -> None:
    assert guard.detect_invocations("echo gh issue create") == []


def test_bash_dash_c_inner_command_is_not_detected() -> None:
    # 문서화된 한계: 놓치는(통과) 방향이므로 안전
    assert guard.detect_invocations("bash -c 'gh issue create -t x'") == []


# ---------- 감지: 잡아야 하는 형태 ----------

def test_operator_without_spaces_is_detected() -> None:
    invs = guard.detect_invocations('cd x&&gh issue create -t "T"')
    assert len(invs) == 1 and invs[0].title == "T"


def test_multiline_command_is_detected() -> None:
    invs = guard.detect_invocations('git add .\ngh issue create --title "T"')
    assert len(invs) == 1 and invs[0].title == "T"


def test_title_flag_forms() -> None:
    for cmd in (
        'gh issue create --title "X"',
        "gh issue create --title=X",
        "gh issue create -t X",
    ):
        invs = guard.detect_invocations(cmd)
        assert len(invs) == 1 and invs[0].title == "X", cmd


def test_glab_and_path_prefixed_cli_are_detected() -> None:
    assert guard.detect_invocations("glab issue create -t x")[0].cli == "glab"
    assert guard.detect_invocations("/usr/bin/gh issue create -t x")[0].cli == "gh"


def test_repo_flag_is_parsed() -> None:
    invs = guard.detect_invocations("gh issue create -t x -R owner/repo")
    assert invs[0].repo == "owner/repo"


def test_override_prefix_alone_and_in_compound() -> None:
    assert guard.detect_invocations(f"{guard.OVERRIDE_TOKEN} gh issue create -t x")[0].override
    invs = guard.detect_invocations(f"cd y && {guard.OVERRIDE_TOKEN} gh issue create -t x")
    assert invs[0].override


def test_fallback_on_unclosed_quote_still_detects() -> None:
    # shlex ValueError 경로: 보수적 정규식 폴백
    invs = guard.detect_invocations('gh issue create --title "T" --body "unclosed')
    assert len(invs) == 1 and invs[0].title == "T"


# ---------- 판정 흐름 (main) ----------

def test_non_bash_tool_passes(monkeypatch) -> None:
    assert _run_main(monkeypatch, {"tool_name": "Write", "tool_input": {}}) == 0


def test_malformed_stdin_warns_without_blocking(monkeypatch, capsys) -> None:
    assert _run_main(monkeypatch, "not json{") == 1
    assert "fail-open" in capsys.readouterr().err


def test_no_duplicates_passes_silently(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_run_search", lambda argv: "[]")
    assert _run_main(monkeypatch, _bash_payload("gh issue create -t brand-new")) == 0


def test_duplicates_block_with_candidates_and_override_hint(monkeypatch, capsys) -> None:
    issues = json.dumps([{"number": 12, "state": "OPEN", "title": "같은 작업"}])
    monkeypatch.setattr(guard, "_run_search", lambda argv: issues)
    assert _run_main(monkeypatch, _bash_payload('gh issue create -t "같은 작업"')) == 2
    err = capsys.readouterr().err
    assert "#12" in err and guard.OVERRIDE_TOKEN in err


def test_override_skips_search_entirely(monkeypatch) -> None:
    def _fail(argv):
        raise AssertionError("override인데 검색이 호출됨")

    monkeypatch.setattr(guard, "_run_search", _fail)
    cmd = f"{guard.OVERRIDE_TOKEN} gh issue create -t anything"
    assert _run_main(monkeypatch, _bash_payload(cmd)) == 0


def test_missing_title_blocks(monkeypatch, capsys) -> None:
    assert _run_main(monkeypatch, _bash_payload("gh issue create --body x")) == 2
    assert "--title" in capsys.readouterr().err


def test_empty_title_blocks(monkeypatch, capsys) -> None:
    assert _run_main(monkeypatch, _bash_payload('gh issue create --title ""')) == 2
    assert "--title" in capsys.readouterr().err


def test_search_failure_fails_open(monkeypatch, capsys) -> None:
    monkeypatch.setattr(guard, "_run_search", lambda argv: None)
    assert _run_main(monkeypatch, _bash_payload("gh issue create -t x")) == 0
    assert "skipped" in capsys.readouterr().err


def test_repo_is_forwarded_to_search(monkeypatch) -> None:
    seen: list[list[str]] = []

    def _capture(argv):
        seen.append(argv)
        return "[]"

    monkeypatch.setattr(guard, "_run_search", _capture)
    _run_main(monkeypatch, _bash_payload("gh issue create -t x -R owner/repo"))
    assert ["--repo", "owner/repo"] == seen[0][-2:]


# ---------- glab 어댑터 (보수 파싱) ----------
# 픽스처 문자열은 실측 출력 그대로 (glab 1.108.0 / GitLab CE 19.2.0, 2026-07-22).
# 실인스턴스 드리프트 카나리아는 test_guard_gitlab.py (-m gitlab).

def test_glab_confident_lines_block(monkeypatch, capsys) -> None:
    out = (
        "Showing 1 issue in root/scratch that match your search. (Page 1)\n"
        "\n"
        "ID\tTitle\tLabels\tCreated at\n"
        "#3\tcapture sample issue\t\tless than a minute ago\n"
        "\n"
    )
    monkeypatch.setattr(guard, "_run_search", lambda argv: out)
    assert _run_main(monkeypatch, _bash_payload("glab issue create -t x")) == 2
    assert "#3" in capsys.readouterr().err


def test_glab_ambiguous_output_fails_open(monkeypatch) -> None:
    out = "No issues match your search in root/scratch.\n\n\n"
    monkeypatch.setattr(guard, "_run_search", lambda argv: out)
    assert _run_main(monkeypatch, _bash_payload("glab issue create -t x")) == 0


# ---------- 하위 실행기 ----------

def test_run_search_timeout_returns_none(monkeypatch) -> None:
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=guard.SEARCH_TIMEOUT_SECONDS)

    monkeypatch.setattr(subprocess, "run", _timeout)
    assert guard._run_search(["gh", "issue", "list"]) is None


def test_run_wrapper_converts_crash_to_nonblocking(monkeypatch, capsys) -> None:
    def _boom() -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(guard, "main", _boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert guard.run() == 1
    assert "fail-open" in capsys.readouterr().err
