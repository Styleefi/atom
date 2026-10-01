# review-loop 규칙의 Bar declaration prompt 첫머리와 훅 매칭 구절의 동기화 테스트
"""규칙 프로즈 ↔ 훅 상수 동기화.

규칙 프롬프트의 첫머리가 `PHRASE`와 어긋나거나, 요청 번호 슬롯이 `_REQUEST_RE`가
읽지 못하는 모양으로 바뀌면 여기서 실패한다.
"""

from __future__ import annotations

from pathlib import Path

from harness.review_loop_spawn_record import record

RULE_PATH = Path(record.__file__).resolve().parents[3] / "meta/rules/review-loop.md"


def _prompt_block(text: str) -> str:
    """`### Bar declaration prompt` 절의 첫 인용 블록을 `> ` 없이 돌려준다."""
    lines = text.split("### Bar declaration prompt", 1)[1].splitlines()
    block: list[str] = []
    for line in lines:
        if line.startswith(">"):
            block.append(line[1:].removeprefix(" "))
        elif block:
            break
    return "\n".join(block)


def test_rule_prompt_begins_with_the_hook_phrase() -> None:
    prompt = _prompt_block(RULE_PATH.read_text(encoding="utf-8"))
    assert record.normalize(prompt).startswith(record.PHRASE)
    assert record.match_prompt(prompt.replace("<n>", "180", 1)) == (True, 180)


def test_rule_prompt_copied_with_quote_markers_matches() -> None:
    text = RULE_PATH.read_text(encoding="utf-8").split("### Bar declaration prompt", 1)[1]
    assert record.match_prompt(text.replace("<n>", "180", 1)) == (False, 180)
