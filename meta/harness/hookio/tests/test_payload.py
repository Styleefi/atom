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


def test_read_payload_decodes_bytes_strictly(monkeypatch) -> None:
    # 버퍼가 닿으면 바이트로 읽는다 — 깨진 UTF-8은 ValueError(UnicodeDecodeError)
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"a": "\xff"}'), encoding="utf-8")
    )
    with pytest.raises(ValueError):
        payload.read_payload()


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
    {"tool_name": "Write", "tool_input": {"command": "ls"}},
    {"tool_name": "Bash"},
    {"tool_name": "Bash", "tool_input": "ls"},
    {"tool_name": "Bash", "tool_input": {"command": "   "}},
    {"tool_name": "Bash", "tool_input": {"command": 7}},
])
def test_bash_call_returns_none_when_not_a_bash_command(obj: dict) -> None:
    assert payload.bash_call(obj) is None


def test_exit_block_is_the_wrapper_sentinel() -> None:
    assert payload.EXIT_BLOCK == 42
