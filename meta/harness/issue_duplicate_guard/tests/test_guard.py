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


def _bash_payload(command: str, cwd: str | None = None) -> dict:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


# ---------- 감지: 오탐 방지 ----------

def test_quoted_mention_with_operator_is_not_detected() -> None:
    # 따옴표 안 연산자 포함 — 이번 작업 커밋 메시지 같은 형태가 절대 걸리면 안 됨
    assert guard.detect_invocations('git commit -m "fix && gh issue create guard"') == []


def test_argument_position_literal_is_not_detected() -> None:
    assert guard.detect_invocations("echo gh issue create") == []


def test_bash_dash_c_inner_command_is_not_detected() -> None:
    # 문서화된 한계: 놓치는(통과) 방향이므로 안전
    assert guard.detect_invocations("bash -c 'gh issue create -t x'") == []


def test_heredoc_body_mention_is_not_detected() -> None:
    # 본문 라인은 명령이 아니다 — 무고한 파일 쓰기가 차단되면 안 됨 (#32 괴리 1)
    cmd = 'cat > notes.sh <<EOF\ngh issue create --title "T"\nEOF'
    assert guard.detect_invocations(cmd) == []


def test_heredoc_file_write_passes_without_search(monkeypatch) -> None:
    def _fail(argv, cwd=None):
        raise AssertionError("search must not run for a heredoc body mention")

    monkeypatch.setattr(guard, "_run_search", _fail)
    cmd = 'cat > notes.sh <<EOF\ngh issue create --title "T"\nEOF'
    assert _run_main(monkeypatch, _bash_payload(cmd)) == 0


def test_arithmetic_shift_with_real_heredoc_still_strips() -> None:
    # $((...)) 마스킹 검증: 산술 <<가 마커 큐를 오염시켜 스트립을 무효화하면 안 됨
    cmd = 'echo $(( (1<<2) + 3 ))\ncat > x.sh <<EOF\ngh issue create --title "T"\nEOF'
    assert guard.detect_invocations(cmd) == []


def test_bare_arithmetic_with_real_heredoc_still_strips() -> None:
    # bash의 산술 명령 (( ... ))는 $ 없이도 산술이다 — 마스킹 대상 (라운드 1 C3)
    cmd = '(( 1<<2 ))\ncat > x.sh <<EOF\ngh issue create --title "T"\nEOF'
    assert guard.detect_invocations(cmd) == []


def test_exotic_delimiter_heredoc_body_not_detected() -> None:
    # bash는 +@! 등이 든 구분자도 허용한다 — 문자군이 좁으면 종결자를 못 찾아
    # all-or-nothing이 원문을 유지하고 본문 오차단이 되살아난다 (라운드 1 C1)
    cmd = 'cat > x.sh <<MY+DELIM\ngh issue create --title "T"\nMY+DELIM'
    assert guard.detect_invocations(cmd) == []


# ---------- 감지: 잡아야 하는 형태 ----------

def test_heredoc_fed_create_is_still_detected() -> None:
    # 마커 라인 자체는 보존된다 — heredoc으로 body를 먹이는 실제 생성은 감지
    cmd = 'gh issue create -t "T" --body-file - <<EOF\nbody text\nEOF'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_arithmetic_shift_does_not_break_detection() -> None:
    # 산술 시프트가 heredoc 마커로 오인돼 뒤 명령 감지를 지우면 안 됨
    cmd = 'echo $((1<<2))\ngh issue create -t "T"'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_bare_arithmetic_does_not_erase_real_create() -> None:
    # (( x << y ))의 시프트가 마커로 오인되면 실감지가 통째로 지워진다 (라운드 1 C3)
    cmd = '(( total << shift ))\ngh issue create --title "T"\nshift'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_operator_without_spaces_is_detected() -> None:
    invs = guard.detect_invocations('cd x&&gh issue create -t "T"')
    assert len(invs) == 1 and invs[0].title == "T"
    assert invs[0].cwd_unsafe is True


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


def test_attached_repo_flag_is_parsed() -> None:
    # -t 결합형(-tX)과 대칭 — 놓치면 cd 래치·검색 대상 판정이 둘 다 틀어진다
    invs = guard.detect_invocations("gh issue create -t x -Rowner/repo")
    assert invs[0].repo == "owner/repo"


def test_attached_repo_flag_with_equals_is_parsed() -> None:
    # pflag는 -R=owner/repo에서 =를 벗긴다 — 그대로 두면 검색 --repo가
    # "=owner/repo"로 실패해 중복 검사가 조용히 스킵된다 (리뷰 라운드 1 C3)
    invs = guard.detect_invocations("gh issue create -t x -R=owner/repo")
    assert invs[0].repo == "owner/repo"


def test_cd_on_earlier_line_marks_cwd_unsafe() -> None:
    invs = guard.detect_invocations('cd /elsewhere\ngh issue create -t "T"')
    assert len(invs) == 1 and invs[0].cwd_unsafe is True


def test_cd_after_invocation_does_not_mark() -> None:
    invs = guard.detect_invocations('gh issue create -t "T" && cd /elsewhere')
    assert len(invs) == 1 and invs[0].cwd_unsafe is False


def test_override_prefix_alone_and_in_compound() -> None:
    assert guard.detect_invocations(f"{guard.OVERRIDE_TOKEN} gh issue create -t x")[0].override
    invs = guard.detect_invocations(f"cd y && {guard.OVERRIDE_TOKEN} gh issue create -t x")
    assert invs[0].override


def test_unparseable_command_fails_open() -> None:
    # shlex 이중 실패(따옴표 불균형)는 전면 fail-open — 규칙 파일의 계약
    # "unparseable command fails open"을 코드가 그대로 이행한다 (#32 괴리 2)
    assert guard.detect_invocations('gh issue create --title "T" --body "unclosed') == []


def test_quoted_mention_in_unparseable_command_is_not_detected() -> None:
    # 구 폴백의 오차단 재현 케이스: 따옴표 속 언급이 절대 걸리면 안 됨
    assert guard.detect_invocations('echo "todo; gh issue create for tracker later') == []


def test_comment_with_unbalanced_quote_fails_open(monkeypatch) -> None:
    # 수용 한계 잠금: 주석 뒤 불균형 따옴표는 유효 bash지만 shlex가 이중
    # 실패한다(commenters=""). 감지가 아니라 통과가 의도된 행동이다.
    def _fail(argv, cwd=None):
        raise AssertionError("no search may run for an undetected command")

    monkeypatch.setattr(guard, "_run_search", _fail)
    cmd = 'gh issue create -t "T" # comment with " unclosed quote'
    assert _run_main(monkeypatch, _bash_payload(cmd)) == 0


# ---------- 판정 흐름 (main) ----------

def test_non_bash_tool_passes(monkeypatch) -> None:
    assert _run_main(monkeypatch, {"tool_name": "Write", "tool_input": {}}) == 0


def test_malformed_stdin_warns_without_blocking(monkeypatch, capsys) -> None:
    assert _run_main(monkeypatch, "not json{") == 1
    assert "fail-open" in capsys.readouterr().err


def test_no_duplicates_passes_silently(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: "[]")
    assert _run_main(monkeypatch, _bash_payload("gh issue create -t brand-new")) == 0


def test_duplicates_block_with_candidates_and_override_hint(monkeypatch, capsys) -> None:
    issues = json.dumps([{"number": 12, "state": "OPEN", "title": "같은 작업"}])
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: issues)
    assert _run_main(monkeypatch, _bash_payload('gh issue create -t "같은 작업"')) == 42
    err = capsys.readouterr().err
    assert "#12" in err and guard.OVERRIDE_TOKEN in err


def test_override_skips_search_entirely(monkeypatch) -> None:
    def _fail(argv, cwd=None):
        raise AssertionError("search was invoked despite override")

    monkeypatch.setattr(guard, "_run_search", _fail)
    cmd = f"{guard.OVERRIDE_TOKEN} gh issue create -t anything"
    assert _run_main(monkeypatch, _bash_payload(cmd)) == 0


def test_missing_title_blocks(monkeypatch, capsys) -> None:
    assert _run_main(monkeypatch, _bash_payload("gh issue create --body x")) == 42
    err = capsys.readouterr().err
    # 모든 차단 메시지는 override 복구 경로를 안내해야 한다 (#32 괴리 3)
    assert "--title" in err and guard.OVERRIDE_TOKEN in err


def test_whole_command_prefix_does_not_override_later_segment(monkeypatch) -> None:
    # 의미론 잠금(#32 괴리 4): 마커는 gh/glab이 있는 세그먼트 선두에서만
    # 유효하다 — 셸 의미론과 동일. 이 테스트는 문서 정합 커밋에서 기존
    # 동작을 고정하며, 수정 전에도 통과한다(선실패 원칙의 명시적 예외).
    issues = json.dumps([{"number": 12, "state": "OPEN", "title": "T"}])
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: issues)
    cmd = f'{guard.OVERRIDE_TOKEN} git add . && gh issue create -t "T"'
    assert _run_main(monkeypatch, _bash_payload(cmd)) == 42


def test_empty_title_blocks(monkeypatch, capsys) -> None:
    assert _run_main(monkeypatch, _bash_payload('gh issue create --title ""')) == 42
    assert "--title" in capsys.readouterr().err


def test_search_failure_fails_open(monkeypatch, capsys) -> None:
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: None)
    assert _run_main(monkeypatch, _bash_payload("gh issue create -t x")) == 0
    assert "skipped" in capsys.readouterr().err


def test_repo_is_forwarded_to_search(monkeypatch) -> None:
    seen: list[list[str]] = []

    def _capture(argv, cwd=None):
        seen.append(argv)
        return "[]"

    monkeypatch.setattr(guard, "_run_search", _capture)
    _run_main(monkeypatch, _bash_payload("gh issue create -t x -R owner/repo"))
    assert ["--repo", "owner/repo"] == seen[0][-2:]


def test_payload_cwd_is_forwarded_to_search(monkeypatch) -> None:
    # #33 회귀: 훅 프로세스 cwd가 아니라 Bash 도구의 작업 디렉터리에서 검색해야 한다
    seen: list[str | None] = []

    def _capture(argv, cwd=None):
        seen.append(cwd)
        return "[]"

    monkeypatch.setattr(guard, "_run_search", _capture)
    payload = _bash_payload("gh issue create -t x", cwd="/divergent/dir")
    assert _run_main(monkeypatch, payload) == 0
    assert seen == ["/divergent/dir"]


def test_cd_makes_search_unsafe_and_fails_open(monkeypatch, capsys) -> None:
    # 선행 cd 뒤에는 검색 대상 저장소가 불명 → 검색 없이 통과 (fail-open)
    def _fail(argv, cwd=None):
        raise AssertionError("search must not run after cd")

    monkeypatch.setattr(guard, "_run_search", _fail)
    payload = _bash_payload("cd /elsewhere && gh issue create -t x", cwd="/orig")
    assert _run_main(monkeypatch, payload) == 0
    assert "skipped" in capsys.readouterr().err


def test_cd_with_explicit_repo_still_searches(monkeypatch) -> None:
    # 명시적 --repo는 디렉터리와 무관하므로 cd가 있어도 검색은 수행돼야 한다
    seen: list[list[str]] = []

    def _capture(argv, cwd=None):
        seen.append(argv)
        return "[]"

    monkeypatch.setattr(guard, "_run_search", _capture)
    payload = _bash_payload("cd /elsewhere && gh issue create -t x -R owner/repo")
    assert _run_main(monkeypatch, payload) == 0
    assert len(seen) == 1 and ["--repo", "owner/repo"] == seen[0][-2:]


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
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: out)
    assert _run_main(monkeypatch, _bash_payload("glab issue create -t x")) == 42
    assert "#3" in capsys.readouterr().err


def test_glab_ambiguous_output_fails_open(monkeypatch) -> None:
    out = "No issues match your search in root/scratch.\n\n\n"
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: out)
    assert _run_main(monkeypatch, _bash_payload("glab issue create -t x")) == 0


# ---------- 하위 실행기 ----------

def test_run_search_timeout_returns_none(monkeypatch) -> None:
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=guard.SEARCH_TIMEOUT_SECONDS)

    monkeypatch.setattr(subprocess, "run", _timeout)
    assert guard._run_search(["gh", "issue", "list"]) is None


def test_run_search_passes_cwd_to_subprocess(monkeypatch) -> None:
    captured: dict = {}

    def _record(argv, **kwargs):
        captured.update(kwargs)

        class Result:
            returncode = 0
            stdout = "[]"

        return Result()

    monkeypatch.setattr(subprocess, "run", _record)
    guard._run_search(["gh", "issue", "list"], cwd="/somewhere")
    assert captured["cwd"] == "/somewhere"


def test_run_wrapper_converts_crash_to_nonblocking(monkeypatch, capsys) -> None:
    def _boom() -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(guard, "main", _boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert guard.run() == 1
    assert "fail-open" in capsys.readouterr().err
