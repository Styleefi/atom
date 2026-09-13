# 리뷰 루프 커밋의 트레일러를 점검할 비차단 PostToolUse hook — 이 커밋은 골격(페이로드 읽기와 fail-open)만 담는다
"""review-loop-trailer-check hook의 골격.

PostToolUse 페이로드를 읽고 통과한다. 어떤 경로도 exit 2를 만들지 않는다 —
비차단 래퍼(`|| exit 1`) 아래에서 exit 1은 stderr 경고, exit 0은 통과다.
"""

from __future__ import annotations

import json
import sys

TAG = "[review-loop-trailer-check]"


def _read_payload() -> tuple[str, str | None, str | None] | None:
    """stdin의 hook 페이로드에서 (command, cwd, session_id)를 꺼낸다.

    Returns:
        Bash 도구 호출이 아니거나 명령이 비어 있으면 None.
    """
    payload = json.loads(sys.stdin.read())
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return None
    cwd = payload.get("cwd")
    session_id = payload.get("session_id")
    return (
        command,
        cwd if isinstance(cwd, str) and cwd else None,
        session_id if isinstance(session_id, str) and session_id else None,
    )


def main() -> int:
    """페이로드를 검증하고 종료 코드를 정한다.

    Returns:
        0이면 통과, 1이면 fail-open 경고(stderr에 태그 한 줄).
    """
    try:
        payload = _read_payload()
    except ValueError:
        print(f"{TAG} malformed hook input (fail-open)", file=sys.stderr)
        return 1
    if payload is None:
        return 0
    return 0


def run() -> int:
    """최상위 방어 실행기."""
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 — fail-open이 설계 요구사항
        print(f"{TAG} internal error (fail-open): {exc}", file=sys.stderr)
        return 1
