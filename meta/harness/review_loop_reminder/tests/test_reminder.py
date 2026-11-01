# review_loop_reminder의 gh pr create 감지·판정 흐름·fail-open·불변식을 검증하는 테스트
"""reminder 모듈 테스트.

감지는 is_pr_create를 직접, 판정 흐름은 stdin JSON monkeypatch로 검증한다.
설계 불변식 — 어떤 입력 클래스에서도 차단 코드(42) 없음, stdout은 빈 문자열
또는 정확히 리마인더 JSON 한 줄(payload 내용이 주입 채널로 새지 않음) — 를
케이스로 고정한다. 비주장 (b)·(c)·(d)·(f)는 accident test로 경계를 선언한다:
그 동작이 바뀌면 테스트가 떨어져 "의도된 경계"였음을 알린다.
"""

from __future__ import annotations

import io
import json
import random
import runpy
import sys
import time

import pytest

from harness.review_loop_reminder import reminder

EXPECTED_STDOUT = (
    json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": reminder.REMINDER,
            }
        }
    )
    + "\n"
)


def _payload(command: str, **extra: object) -> dict:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": "/repo",
        "session_id": "s1",
        **extra,
    }


def _run_main(monkeypatch, payload) -> int:
    """payload(dict 또는 원시 문자열)를 stdin으로 넣고 main()을 실행한다."""
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    return reminder.main()


# --- 감지: 발화 ---------------------------------------------------------------

_HEREDOC_BODY = 'gh pr create --title "feat: x" --body "$(cat <<\'EOF\'\nbody line\n\nCloses #27\nEOF\n)"'


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create",
        "gh pr create --fill",
        "gh pr create --draft --title t --body b",
        "gh pr create --base main --head feat/x --body-file /tmp/body.md",
        _HEREDOC_BODY,
        "git push -u origin HEAD && gh pr create --fill",
        "git push -u origin HEAD; gh pr create --fill",
        "cd /repo && gh pr create --fill | cat",
        "git push -u origin HEAD\ngh pr create --fill",
        "gh pr new --fill",
        "gh pr create --fill --dry-run-not-really",
        "gh pr create --dry-run=false --fill",  # 거짓값은 PR을 연다
        "git push -u origin HEAD\n" + _HEREDOC_BODY,  # 둘째 줄부터 히어독이 줄을 걸침
        "git push -u origin HEAD && \\\n  gh pr create --fill",  # 줄 연속
        "git push -u origin HEAD\r\ngh pr create --fill",
        "# push first (it's ready)\ngit push -u origin HEAD\ngh pr create --fill",
        "cat > /tmp/pr.md <<'EOF'\n- Don't fail\nEOF\ngh pr create --title t --body-file /tmp/pr.md",
        'echo "a\nb" && gh pr create --fill',  # 줄을 걸친 인용 뒤의 세그먼트
        "git commit -F - <<'EOF'\nfeat: x\nEOF\ngh pr create --fill",
        "gh pr create --title x --body-file - <<'EOF'\nIt's done\nEOF",  # 첫 줄이 홀로 토큰화된다
    ],
)
def test_pr_create_fires(command: str) -> None:
    assert reminder.is_pr_create(command)


# --- 감지: 비발화 -------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "gh pr list",
        "gh pr view 27 --json body",
        "gh pr edit 27 --body b",
        "gh pr ready 27",  # 비주장 (f)
        "gh pr create --help",
        "gh pr create -h",
        "gh pr create --dry-run --fill",
        "gh pr new --dry-run",
        "gh pr create --dry-run=true --fill",
        "gh pr create --help=true",
        "gh pr create-x",
        "gh issue create --title t",
        "glab mr create --fill",  # 비주장 (d)
        "git commit -m 'feat(x): y'",
        'echo "gh pr create"',  # 인용 리터럴은 한 토큰
        "echo gh pr create",  # 첫 토큰이 gh가 아님
        "",
        "   ",
    ],
)
def test_other_commands_do_not_fire(command: str) -> None:
    assert not reminder.is_pr_create(command)


def test_unclosed_quote_keeps_the_head() -> None:
    # 불균형 따옴표는 그 뒤만 버린다 — 머리의 gh pr create는 살아 발화한다.
    assert reminder.is_pr_create('gh pr create --title "unclosed')


def test_accident_shell_semantics_are_not_judged() -> None:
    # 비주장 (b): 경계 선언. 이 테스트가 떨어지면 텍스트 추론을 정련한 것이고,
    # #52 동결에 따라 되돌린다. 미감지 쪽 —
    for command in [
        "bash -c 'gh pr create --fill'",
        "sh scripts/open-pr.sh",
        "gh -R Styleefi/atom pr create --fill",
        "GH_REPO=o/r gh pr create --fill",
        "env gh pr create --fill",
        "timeout 60 gh pr create --fill",
        "/usr/bin/gh pr create --fill",
        "gh api repos/o/r/pulls -f title=t",
        "url=`gh pr create --fill`",  # 백틱
        "gh pr create --title --help --body b",  # 값 자리의 --help
        'echo "(;" gh pr create --fill',  # 인용된 연산자는 경계가 아니다
        "echo 'x; gh pr create --fill",  # 같은 줄 불균형 따옴표 뒤
        "# path C:\\\ngh pr create --fill",  # 백슬래시로 끝나는 주석 줄
    ]:
        assert not reminder.is_pr_create(command), command
    # — 그리고 bash는 실행하지 않지만 토큰으로는 세그먼트 머리에 오는 오발화 쪽.
    for command in [
        "echo hi # ; gh pr create",
        "git commit -F - <<'EOF'\nrun git push && gh pr create after\nEOF",
        "gh issue comment 1 --body-file - <<EOF\ngh pr create then wait\nEOF",
        "echo 'see:\ngh pr create --fill\n'",  # 줄을 걸친 인용 안의 줄 머리
        'git commit -m "feat: x\n\nFires after a PR opens; gh pr create only."',  # 인용 안 `;` 뒤
        'gh pr create --title x --body "a\nb" --dry-run',  # 줄을 걸친 인용 뒤의 플래그
        "false && gh pr create",
    ]:
        assert reminder.is_pr_create(command), command


# --- 판정 흐름 ----------------------------------------------------------------


def test_pr_create_payload_prints_reminder_json(monkeypatch, capsys) -> None:
    code = _run_main(monkeypatch, _payload("gh pr create --fill"))
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == EXPECTED_STDOUT
    assert captured.err == ""


def test_tool_response_is_not_read(monkeypatch, capsys) -> None:
    # 비주장 (c)의 코드 쪽 절반: 응답 내용은 판정에 쓰지 않는다. 실패한 호출에
    # 훅이 아예 안 오는 것(PostToolUse는 exit 0에만 발화)은 하네스 밖이라 여기서
    # 재현할 수 없다 — 실측 2026-11-01, docstring 참조.
    payload = _payload("gh pr create --fill", tool_response={"stderr": "boom"})
    code = _run_main(monkeypatch, payload)
    assert code == 0
    assert capsys.readouterr().out == EXPECTED_STDOUT


def test_other_bash_payload_is_silent(monkeypatch, capsys) -> None:
    code = _run_main(monkeypatch, _payload("gh pr view 27"))
    assert code == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Edit", "tool_input": {"command": "gh pr create"}},
        {"tool_name": "Bash", "tool_input": {}},
        {"tool_name": "Bash", "tool_input": {"command": ""}},
        {"tool_name": "Bash", "tool_input": "gh pr create"},
        {"tool_input": {"command": "gh pr create"}},
        {},
    ],
)
def test_non_bash_or_commandless_payload_is_silent(
    monkeypatch, capsys, payload
) -> None:
    code = _run_main(monkeypatch, payload)
    assert code == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("raw", ['"gh pr create"', "[]", "42", "null"])
def test_non_dict_payload_is_silent(monkeypatch, capsys, raw: str) -> None:
    code = _run_main(monkeypatch, raw)
    assert code == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("raw", ["", "   ", "{not json", "{'single': 1}"])
def test_malformed_or_empty_stdin_fails_open(monkeypatch, capsys, raw: str) -> None:
    code = _run_main(monkeypatch, raw)
    captured = capsys.readouterr()
    assert code == 1
    assert "fail-open" in captured.err
    assert captured.out == ""


# --- run() / 진입점 ------------------------------------------------------------


def test_run_catchall_fails_open(monkeypatch, capsys) -> None:
    def boom() -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(reminder, "main", boom)
    code = reminder.run()
    captured = capsys.readouterr()
    assert code == 1
    assert "fail-open" in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("code", [0, 1])
def test_run_passes_main_return_through(monkeypatch, code: int) -> None:
    calls = []

    def boom() -> int:
        calls.append(1)
        return code

    monkeypatch.setattr(reminder, "main", boom)
    assert reminder.run() == code
    assert len(calls) == 1


@pytest.mark.parametrize("code", [0, 1])
def test_entry_point_propagates_the_exit_code(monkeypatch, code: int) -> None:
    monkeypatch.setattr(reminder, "run", lambda: code)
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("harness.review_loop_reminder", run_name="__main__")
    assert exc_info.value.code == code


# --- 불변식 --------------------------------------------------------------------

_ALL_INPUT_CLASSES: list[object] = [
    _payload("gh pr create --fill"),
    _payload(_HEREDOC_BODY),
    _payload("gh pr view 27"),
    _payload('gh pr create --title "unclosed'),
    _payload("x" * 20000),
    {"tool_name": "Bash", "tool_input": {"command": 7}},
    {"tool_name": None},
    "[]",
    "",
    "{bad",
]


def _token_soup(rng: random.Random) -> str:
    words = [
        "gh",
        "pr",
        "create",
        "&&",
        "||",
        ";",
        "|",
        "'",
        '"',
        "\\",
        "(",
        ")",
        "$(",
        "<<",
        "EOF",
        "echo",
        "bash",
        "-c",
        "#",
        "\n",
        "--fill",
    ]
    return " ".join(rng.choice(words) for _ in range(rng.randint(0, 24)))


def test_unclosed_quote_early_is_linear() -> None:
    # 셋째 공격: 닫히지 않는 작은따옴표 하나 뒤 3000줄이 누적 재토큰화로 140초였다.
    # 줄 단위 방식의 실측은 0.1초 미만(여유 20배). 한 토큰이 아주 길면(200KB 한 줄)
    # shlex 자체가 느려지는 것은 넷째 공격이 쟀고 여기서 잡지 않는다.
    command = "# it's\n" + "\n".join(f"line {i} of text" for i in range(3000))
    started = time.perf_counter()
    assert not reminder.is_pr_create(command)
    assert time.perf_counter() - started < 2.0


def test_invariant_never_blocks_and_stdout_is_constant_only(
    monkeypatch, capsys
) -> None:
    """전 입력 클래스에서 (1) 차단 코드(42) 불가, (2) stdout은 빈 값 또는 정확히 리마인더 JSON.

    (2)는 payload 내용이 컨텍스트 주입 채널(stdout)로 새는 것을 막는 보안
    불변식이다 — 리마인더가 프롬프트 주입 증폭기가 되면 안 된다.
    """
    rng = random.Random(27)
    inputs = list(_ALL_INPUT_CLASSES) + [_payload(_token_soup(rng)) for _ in range(300)]
    for payload in inputs:
        code = _run_main(monkeypatch, payload)
        captured = capsys.readouterr()
        assert code in (0, 1)
        assert captured.out in ("", EXPECTED_STDOUT)

    def boom() -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(reminder, "main", boom)
    code = reminder.run()
    assert code in (0, 1)
    assert capsys.readouterr().out == ""
