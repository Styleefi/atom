# 훅 stdin 페이로드를 읽고 Bash 호출 필드를 꺼내는 공유 프롤로그
"""훅 페이로드 프롤로그 (hookio 공유 모듈).

Claude Code 훅은 stdin으로 JSON 객체 하나를 받는다. 여섯 하네스가 각자 들고
있던 "읽기 → 파싱 → 객체 검사"와 그중 넷의 "Bash 명령·cwd·session_id 추출"을
여기 모은다(#4). 잘못된 입력의 **메시지와 종료 코드는 호출자 몫**이다 — stderr 태그가
하네스마다 다르고, 종료 코드의 의미(fail-open 1)는 각 규칙 파일이 선언한다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

# 차단 sentinel 종료 코드. Claude Code의 차단 코드는 2지만 uv(자체 오류 2)와
# python(예외 1, CLI 오류 2)이 같은 코드를 낼 수 있어, exec 배선에서는 도구
# 실패가 차단으로 샜다(#31). 자연 발생 불가능한 42를 반환하고 settings.json의
# 셸 래퍼가 42만 차단(2)으로 되매핑하고 그 외 nonzero는 1(비차단 경고)로
# 수렴시키는 설계다(계약은 rules_checker의 test_hook_command_contract.py에 있다).
EXIT_BLOCK = 42


@dataclass(frozen=True)
class BashCall:
    """Bash 도구 호출의 페이로드 필드.

    Attributes:
        command: 실행하려는 명령 전체(비어 있지 않음).
        cwd: 호출 시점의 작업 디렉터리. 없거나 빈 문자열이면 None.
        session_id: 세션 식별자. 없거나 빈 문자열이면 None.
    """

    command: str
    cwd: str | None
    session_id: str | None


def read_payload(*, strict_utf8: bool = False) -> dict | None:
    """stdin의 훅 JSON을 객체로 돌려준다.

    기본은 텍스트 모드 `sys.stdin.read()` — 다섯 하네스가 이동 전에 쓰던 읽기
    그대로다. 텍스트 모드 stdin은 UTF-8 모드·C 로케일에서 surrogateescape로
    깨진 바이트를 조용히 통과시키는데, 그 동작도 그대로 둔다(동작 불변이 이
    모듈의 목적이다; 통일 여부는 별도 결정). answer_first_reminder만 이동 전에
    바이트로 읽어 엄격히 디코드했으므로 `strict_utf8=True`로 그 동작을 지킨다.
    UnicodeDecodeError는 ValueError의 하위 클래스라 호출자는 ValueError 하나만
    잡으면 된다.

    Args:
        strict_utf8: True면 `sys.stdin.buffer`에서 바이트로 읽어 UTF-8로 엄격히
            디코드한다.

    Returns:
        JSON 객체(dict). 파싱은 됐지만 객체가 아니면 None.

    Raises:
        ValueError: 디코드 실패(strict_utf8) 또는 JSON 문법 오류.
    """
    raw = sys.stdin.buffer.read().decode("utf-8") if strict_utf8 else sys.stdin.read()
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else None


def bash_call(payload: dict) -> BashCall | None:
    """Bash 도구 호출의 명령·cwd·session_id를 꺼낸다.

    Args:
        payload: read_payload가 돌려준 훅 JSON 객체.

    Returns:
        Bash 호출이 아니거나 명령이 비어 있으면(공백뿐이면) None.
    """
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return None
    return BashCall(
        command=command,
        cwd=_text(payload.get("cwd")),
        session_id=_text(payload.get("session_id")),
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
