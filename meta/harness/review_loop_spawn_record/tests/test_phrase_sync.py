# review-loop 규칙의 Bar declaration prompt·Pass prompt opening 첫머리와 훅 매칭 구절의 동기화 테스트
"""규칙 프로즈 ↔ 훅 상수 동기화.

규칙 프롬프트의 첫머리가 `PHRASE`/`PASS_PHRASE`와 어긋나거나, 요청 번호 슬롯이
`_REQUEST_RE`가 읽지 못하는 모양으로 바뀌면 여기서 실패한다.
"""

from __future__ import annotations

from pathlib import Path

from harness.review_loop_spawn_record import record

RULE_PATH = Path(record.__file__).resolve().parents[3] / "meta/rules/review-loop.md"
BAR_ANCHOR = "### Bar declaration prompt"
PASS_ANCHOR = "### Pass prompt opening"


def _prompt_block(text: str, anchor: str) -> str:
    """`anchor` 절의 첫 인용 블록을 `> ` 없이 돌려준다."""
    lines = text.split(anchor, 1)[1].splitlines()
    block: list[str] = []
    for line in lines:
        if line.startswith(">"):
            block.append(line[1:].removeprefix(" "))
        elif block:
            break
    return "\n".join(block)


def test_rule_prompt_begins_with_the_hook_phrase() -> None:
    prompt = _prompt_block(RULE_PATH.read_text(encoding="utf-8"), BAR_ANCHOR)
    assert record.normalize(prompt).startswith(record.PHRASE)
    assert record.match_prompt(prompt.replace("<n>", "180", 1)) == (True, 180)


def test_rule_prompt_copied_with_quote_markers_matches() -> None:
    text = RULE_PATH.read_text(encoding="utf-8").split(BAR_ANCHOR, 1)[1]
    assert record.match_prompt(text.replace("<n>", "180", 1)) == (False, 180)


def test_rule_pass_opening_begins_with_the_pass_phrase() -> None:
    prompt = _prompt_block(RULE_PATH.read_text(encoding="utf-8"), PASS_ANCHOR)
    assert record.normalize(prompt).startswith(record.PASS_PHRASE)
    assert record.match_prompt(prompt.replace("<n>", "191", 1), record.PASS_PHRASE) == (True, 191)


def test_rule_pass_opening_copied_with_quote_markers_matches() -> None:
    text = RULE_PATH.read_text(encoding="utf-8").split(PASS_ANCHOR, 1)[1]
    assert record.match_prompt(text.replace("<n>", "191", 1), record.PASS_PHRASE) == (False, 191)


def test_the_two_phrases_are_distinct_openings() -> None:
    """어느 쪽도 다른 쪽의 접두가 아니다 — `_on_agent`가 bar 구절을 먼저 보는 순서가 안전한 근거."""
    assert not record.PHRASE.startswith(record.PASS_PHRASE)
    assert not record.PASS_PHRASE.startswith(record.PHRASE)
