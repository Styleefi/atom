# hookio.payload 프롤로그 테스트: stdin 읽기 경로와 Bash 호출 필드 추출
from __future__ import annotations

import io
import json
import sys

import pytest

from harness.hookio import payload


def test_read_payload_returns_object_from_text_stream(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_name": "Bash"})))
    assert payload.read_payload() == {"tool_name": "Bash"}


def test_read_payload_default_keeps_text_mode_reading(monkeypatch) -> None:
    # 기본 읽기는 이동 전 다섯 하네스의 `sys.stdin.read()`와 같다: surrogateescape
    # 스트림에서 깨진 UTF-8은 예외 없이 통과한다(2026-10-27 재공격이 잡은 C1 드리프트).
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(
        io.BytesIO(b'{"tool_name": "Bash", "tool_input": {"command": "\xff"}}'),
        encoding="utf-8", errors="surrogateescape",
    ))
    assert payload.read_payload()["tool_name"] == "Bash"


def test_read_payload_strict_decodes_bytes(monkeypatch) -> None:
    # strict_utf8은 answer_first_reminder의 이동 전 읽기 — 깨진 UTF-8은 ValueError
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(
        io.BytesIO(b'{"a": "\xff"}'), encoding="utf-8", errors="surrogateescape",
    ))
    with pytest.raises(ValueError):
        payload.read_payload(strict_utf8=True)


@pytest.mark.parametrize("raw", ["", "{not json"])
def test_read_payload_raises_on_malformed(monkeypatch, raw: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    with pytest.raises(ValueError):
        payload.read_payload()


@pytest.mark.parametrize("raw", ["[]", "42", "null"])
def test_read_payload_returns_none_for_non_object(monkeypatch, raw: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    assert payload.read_payload() is None


def test_bash_call_extracts_fields() -> None:
    call = payload.bash_call({
        "tool_name": "Bash", "tool_input": {"command": "ls"},
        "cwd": "/x", "session_id": "s1",
    })
    assert call == payload.BashCall(command="ls", cwd="/x", session_id="s1")


def test_bash_call_normalises_missing_or_empty_optional_fields() -> None:
    call = payload.bash_call({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": ""})
    assert call is not None
    assert call.cwd is None and call.session_id is None


@pytest.mark.parametrize("obj", [
    None,
    {"tool_name": "Write", "tool_input": {"command": "ls"}},
    {"tool_name": "Bash"},
    {"tool_name": "Bash", "tool_input": "ls"},
    {"tool_name": "Bash", "tool_input": {"command": "   "}},
    {"tool_name": "Bash", "tool_input": {"command": 7}},
])
def test_bash_call_returns_none_when_not_a_bash_command(obj: dict) -> None:
    assert payload.bash_call(obj) is None


def test_exit_block_is_42() -> None:
    assert payload.EXIT_BLOCK == 42
