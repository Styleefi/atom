# answer_first_reminder의 질문 휴리스틱·판정 흐름·불변식을 검증하는 테스트
"""reminder 모듈 테스트.

휴리스틱은 contains_question을 직접, 판정 흐름은 stdin JSON monkeypatch로
검증한다. 설계 불변식 — 어떤 입력 클래스에서도 차단 코드(42) 없음, stdout은
빈 문자열 또는 정확히 REMINDER 한 줄(payload 내용이 주입 채널로 새지 않음),
~야/~지 평서문 제외 — 를 케이스로 고정한다.
"""

from __future__ import annotations

import io
import json
import runpy
import sys

import pytest

from harness.answer_first_reminder import reminder


def _run_main(monkeypatch, payload) -> int:
    """payload(dict 또는 원시 문자열)를 UTF-8 stdin으로 넣고 main()을 실행한다."""
    raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    stdin = io.TextIOWrapper(io.BytesIO(raw.encode("utf-8")), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stdin)
    return reminder.main()


# --- 휴리스틱: 질문 감지 -----------------------------------------------------


def test_korean_with_question_mark() -> None:
    assert reminder.contains_question("이게 맞아?")


def test_fullwidth_question_mark() -> None:
    assert reminder.contains_question("정말 그렇게 되나요？")


@pytest.mark.parametrize(
    "text",
    [
        "이렇게 하면 어떨까",
        "이 방식이 더 낫습니까",
        "여기서 멈출까요",
        "테스트는 통과되나요",
        "이게 맞는 건가요",
        "결과는 확인했니",
        "이걸로 되겠냐",
        "이 판단이 옳은가",
        "그게 최선인가",
    ],
)
def test_markless_korean_endings(text: str) -> None:
    assert reminder.contains_question(text)


def test_mixed_approval_and_question() -> None:
    # 실제 위반 사례의 형태: 승인과 질문이 한 메시지에 섞여 있다.
    assert reminder.contains_question("승인하겠어. 그런데 이건 왜 그렇게 된 거야?")


@pytest.mark.parametrize("text", ["이건 버그야.", "이게 맞지."])
def test_declarative_ya_ji_excluded(text: str) -> None:
    # 물음표 없는 ~야/~지는 평서문과 구분 불가라 의도적으로 감지하지 않는다.
    assert not reminder.contains_question(text)


@pytest.mark.parametrize("text", ["진행해 줘.", "테스트 통과했으면 커밋해."])
def test_korean_non_question(text: str) -> None:
    assert not reminder.contains_question(text)


def test_fullwidth_exclamation_splits_sentences() -> None:
    assert reminder.contains_question("정말요！이렇게 하면 어떨까")


def test_korean_ending_behind_trailing_quote() -> None:
    assert reminder.contains_question("“이 방향이 맞을까요”")


def test_english_with_question_mark() -> None:
    assert reminder.contains_question("Is this correct?")


def test_markless_english_interrogative() -> None:
    assert reminder.contains_question("Why is this failing")


def test_markless_english_in_later_sentence() -> None:
    # 프롬프트 첫 단어만 보면 놓치는 형태 — 문장 단위 검사를 고정한다.
    assert reminder.contains_question("Fix the parser. Why does it crash")


@pytest.mark.parametrize("text", ["> Why did this fail", "- What is the plan"])
def test_english_behind_markdown_prefix(text: str) -> None:
    assert reminder.contains_question(text)


def test_english_declarative() -> None:
    assert not reminder.contains_question("Run the tests and commit.")


def test_question_mark_only_inside_code_fence() -> None:
    assert not reminder.contains_question("```\nx = a ? b : c\n```\n이대로 진행해.")


def test_question_outside_code_fence() -> None:
    assert reminder.contains_question("```\nx = a ? b : c\n```\n이렇게 하면 어떨까")


def test_unclosed_fence_keeps_question_mark() -> None:
    # 닫히지 않은 펜스 본문은 제거되지 않는다 — 무해한 오탐으로 동작을 고정.
    assert reminder.contains_question("```\nx = a ? b : c")


def test_empty_and_whitespace() -> None:
    assert not reminder.contains_question("")
    assert not reminder.contains_question("   \n\t")


# --- 판정 흐름 ---------------------------------------------------------------


def test_question_prompt_prints_reminder(monkeypatch, capsys) -> None:
    code = _run_main(
        monkeypatch,
        {"prompt": "이게 맞을까", "hook_event_name": "UserPromptSubmit"},
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == reminder.REMINDER + "\n"
    assert captured.err == ""


def test_non_question_prompt_is_silent(monkeypatch, capsys) -> None:
    code = _run_main(monkeypatch, {"prompt": "진행해 줘."})
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": ""},
        {"hook_event_name": "UserPromptSubmit"},
        {"prompt": ["질문일까?"]},
    ],
)
def test_missing_or_invalid_prompt_is_silent(monkeypatch, capsys, payload) -> None:
    code = _run_main(monkeypatch, payload)
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""


@pytest.mark.parametrize("raw", ["[]", "42"])
def test_non_dict_payload_is_silent(monkeypatch, capsys, raw: str) -> None:
    code = _run_main(monkeypatch, raw)
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""


@pytest.mark.parametrize("raw", ["{not json", ""])
def test_malformed_or_empty_stdin_fails_open(monkeypatch, capsys, raw: str) -> None:
    code = _run_main(monkeypatch, raw)
    captured = capsys.readouterr()
    assert code == 1
    assert "fail-open" in captured.err
    assert captured.out == ""


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
    # run()의 존재 이유는 fail-open이지만, 정상 경로에서 main()의 코드를 그대로
    # 내보내는 것도 같은 함수의 계약이다 — 훅이 관측하는 값이 이쪽이다.
    #
    # 값만 보면 두 번 호출하는 드리프트가 통과한다 — 두 번째 stdin 읽기가 빈
    # 값이라 1이 되고, exit 0에서만 주입되는 리마인더가 사라진다.
    calls = []

    def boom() -> int:
        calls.append(1)
        return code

    monkeypatch.setattr(reminder, "main", boom)
    assert reminder.run() == code
    assert len(calls) == 1


@pytest.mark.parametrize("code", [0, 1])
def test_entry_point_propagates_the_exit_code(monkeypatch, code: int) -> None:
    # __main__.py의 `sys.exit(run())` 이음매. 훅이 실제로 읽는 값은 여기서
    # 만들어진다 — run()의 반환값이 아니라 프로세스 종료 코드다.
    calls = []

    def boom() -> int:
        calls.append(1)
        return code

    monkeypatch.setattr(reminder, "run", boom)
    # stdin은 위생 — 이음매가 드리프트해 진짜 main()에 닿아도 터미널을 물지 않는다.
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(b""), encoding="utf-8")
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("harness.answer_first_reminder", run_name="__main__")
    assert exc.value.code == code
    assert len(calls) == 1


def test_run_reports_malformed_input_without_blocking(monkeypatch, capsys) -> None:
    # 이 패키지에서 가짜 없이 main()→run()이 실제로 도는 유일한 지점.
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{not json"), encoding="utf-8")
    )
    assert reminder.run() == 1
    assert "malformed hook input" in capsys.readouterr().err


# --- 불변식 잠금 -------------------------------------------------------------

_ALL_INPUT_CLASSES = [
    {"prompt": "이게 맞을까"},
    {"prompt": "진행해 줘."},
    {"prompt": ""},
    {"prompt": ["질문일까?"]},
    {},
    "[]",
    "42",
    "{not json",
    "",
]


def test_invariant_never_blocks_and_stdout_is_constant_only(monkeypatch, capsys) -> None:
    """전 입력 클래스에서 (1) 차단 코드(42) 불가, (2) stdout은 빈 값 또는 정확히 REMINDER 한 줄.

    (2)는 payload 내용이 컨텍스트 주입 채널(stdout)로 새는 것을 막는 보안
    불변식이다 — 리마인더가 프롬프트 주입 증폭기가 되면 안 된다.
    """
    for payload in _ALL_INPUT_CLASSES:
        code = _run_main(monkeypatch, payload)
        captured = capsys.readouterr()
        assert code in (0, 1)
        assert captured.out in ("", reminder.REMINDER + "\n")

    def boom() -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(reminder, "main", boom)
    code = reminder.run()
    captured = capsys.readouterr()
    assert code in (0, 1)
    assert captured.out == ""
