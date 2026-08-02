# blocklog의 경로 해석·권한·직렬화·fail-open 경로를 검증하는 테스트
"""blocklog 모듈 테스트.

설계 불변식 — 어떤 실패도 호출자에게 전파되지 않는다, 원장은 언제나 사용자 레벨
절대 경로에 놓인다, 한 이벤트는 정확히 한 줄이다 — 을 케이스로 고정한다.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from harness.blocklog import blocklog


def _read_lines(path: Path) -> list[dict]:
    """원장 파일을 줄 단위로 파싱해 돌려준다."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _ledger(tmp_path: Path) -> Path:
    """conftest가 심은 XDG_STATE_HOME 기준의 원장 경로."""
    return tmp_path / "state" / "atom" / "guard-blocklog.jsonl"


def _record(**overrides) -> None:
    """필수 인자를 채운 record_block 호출."""
    kwargs = {
        "event": "block",
        "harness": "commit-guard",
        "reason": "protected-branch",
        "command": "git commit -m 'feat: x'",
        "cwd": "/repo",
        "session_id": "sess-1",
    }
    kwargs.update(overrides)
    blocklog.record_block(**kwargs)


# ---------- 기본 기록 ----------

def test_writes_one_line_with_expected_fields(tmp_path: Path) -> None:
    _record()

    entries = _read_lines(_ledger(tmp_path))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event"] == "block"
    assert entry["harness"] == "commit-guard"
    assert entry["reason"] == "protected-branch"
    assert entry["command"] == "git commit -m 'feat: x'"
    assert entry["cwd"] == "/repo"
    assert entry["session_id"] == "sess-1"
    assert entry["ts"].endswith("+00:00")
    assert "candidates" not in entry


def test_schema_version_is_recorded(tmp_path: Path) -> None:
    # 세대가 섞인 원장에서 "필드 없음"이 구버전인지 누락인지 가리기 위한 표식.
    _record()

    assert _read_lines(_ledger(tmp_path))[0]["v"] == blocklog.SCHEMA_VERSION


def test_creates_parent_directories(tmp_path: Path) -> None:
    assert not (tmp_path / "state").exists()

    _record()

    assert _ledger(tmp_path).is_file()


def test_appends_instead_of_overwriting(tmp_path: Path) -> None:
    _record(reason="protected-branch")
    _record(reason="subject-rejected")

    entries = _read_lines(_ledger(tmp_path))
    assert [entry["reason"] for entry in entries] == [
        "protected-branch",
        "subject-rejected",
    ]


def test_override_event_carries_null_reason(tmp_path: Path) -> None:
    _record(event="override", reason=None)

    entry = _read_lines(_ledger(tmp_path))[0]
    assert entry["event"] == "override"
    assert entry["reason"] is None


# ---------- 경로 해석 ----------

@pytest.mark.parametrize(
    ("raw", "label"),
    [(None, "unset"), ("", "empty"), ("relative/state", "relative")],
)
def test_falls_back_to_home_when_xdg_is_unusable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str | None, label: str
) -> None:
    # 세 경우 모두 폴백해야 한다. 폴백이 없으면 빈 값·상대 경로가 프로세스 cwd
    # 기준으로 해석되어, 훅 래퍼가 cwd를 meta/로 바꾸는 탓에 저장소 안에 원장이
    # 생긴다(인벤토리 체커가 잡지 못하는 오염이다).
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    if raw is None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_STATE_HOME", raw)

    assert blocklog.ledger_path() == str(
        home / ".local" / "state" / "atom" / "guard-blocklog.jsonl"
    ), label


def test_absolute_xdg_state_home_is_honored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "custom"))

    assert blocklog.ledger_path() == str(
        tmp_path / "custom" / "atom" / "guard-blocklog.jsonl"
    )


# ---------- 권한 ----------

def test_created_file_and_directory_are_owner_only(tmp_path: Path) -> None:
    # 원장은 명령 원문을 담는다 — 복합 명령이면 비밀이 섞일 수 있다.
    _record()

    ledger = _ledger(tmp_path)
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
    assert stat.S_IMODE(ledger.parent.stat().st_mode) == 0o700


# ---------- 직렬화 ----------

def test_multiline_and_unicode_command_stays_one_line(tmp_path: Path) -> None:
    command = 'git commit -m "제목\n\n본문 \'따옴표\' 그리고 \\백슬래시"'
    _record(command=command)

    raw = _ledger(tmp_path).read_text(encoding="utf-8")
    assert raw.count("\n") == 1
    assert _read_lines(_ledger(tmp_path))[0]["command"] == command


def test_short_writes_still_produce_a_complete_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # os.write는 요청보다 적게 쓰고 반환할 수 있다. 반환값을 무시하는 구현이면
    # 줄이 잘려 집계 파서가 깨진다 — 전량 쓰기 루프를 잠근다.
    real_write = os.write
    calls: list[int] = []

    def chunked(fd: int, data) -> int:
        half = max(1, len(data) // 2)
        written = real_write(fd, bytes(data)[:half])
        calls.append(written)
        return written

    monkeypatch.setattr(blocklog.os, "write", chunked)
    _record()

    assert len(calls) > 1
    assert _read_lines(_ledger(tmp_path))[0]["harness"] == "commit-guard"


# ---------- 출처 판별 ----------

@pytest.mark.parametrize("hooked", [True, False])
def test_hooked_reflects_claude_project_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, hooked: bool
) -> None:
    # 훅 프로세스만 CLAUDE_PROJECT_DIR을 받는다. 판별은 필드로만 남기고 조기
    # 종료에는 쓰지 않는다 — 계약이 바뀌면 원장이 조용히 비기 때문이다.
    if hooked:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/repo")
    else:
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    _record()

    assert _read_lines(_ledger(tmp_path))[0]["hooked"] is hooked


# ---------- 후보 번호 ----------

def test_candidates_record_numbers_only(tmp_path: Path) -> None:
    # 제목 원문은 제3자가 쓴 텍스트라 원장의 인젝션 표면을 넓힌다 — 번호만 남긴다.
    _record(
        reason="similar-titles",
        candidates=["#12 [OPEN] 제목", "#45 [CLOSED] 다른 제목"],
    )

    entry = _read_lines(_ledger(tmp_path))[0]
    assert entry["candidates"] == [12, 45]
    assert "제목" not in json.dumps(entry["candidates"])


def test_candidates_without_a_number_are_skipped(tmp_path: Path) -> None:
    _record(reason="similar-titles", candidates=["#7 [OPEN] a", "쓰레기 줄", ""])

    assert _read_lines(_ledger(tmp_path))[0]["candidates"] == [7]


def test_absurdly_long_issue_number_is_skipped(tmp_path: Path) -> None:
    # int() 문자열 변환 상한에 걸리지 않도록 자릿수를 제한한다.
    _record(reason="similar-titles", candidates=["#" + "1" * 5000 + " x"])

    assert _read_lines(_ledger(tmp_path))[0]["candidates"] == []


def test_candidate_extraction_failure_keeps_the_line(tmp_path: Path) -> None:
    # 보조 필드가 터져도 차단 건수는 유실되지 않아야 한다. null = 추출 실패이며
    # 빈 목록(번호를 못 찾음)과 구분된다.
    _record(reason="similar-titles", candidates=[123])  # type: ignore[list-item]

    entry = _read_lines(_ledger(tmp_path))[0]
    assert entry["candidates"] is None
    assert entry["reason"] == "similar-titles"


# ---------- fail-open ----------

def test_open_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # mode 000 디렉터리는 root와 DrvFs에서 무력해 오탐이 난다 — 경계에 주입한다.
    def boom(*args, **kwargs):
        raise PermissionError("nope")

    monkeypatch.setattr(blocklog.os, "open", boom)

    _record()  # 예외가 새면 여기서 실패한다

    assert not _ledger(tmp_path).exists()


def test_unserializable_payload_is_swallowed(tmp_path: Path) -> None:
    _record(command=object())  # type: ignore[arg-type]

    assert not _ledger(tmp_path).exists()


def test_makedirs_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(blocklog.os, "makedirs", boom)

    _record()

    assert not _ledger(tmp_path).exists()
