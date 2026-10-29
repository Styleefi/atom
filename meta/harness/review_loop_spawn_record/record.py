# 리뷰 루프 bar 선언 프롬프트와 리뷰 패스(forked Skill·Agent)의 spawn·반환·메시지·모델을 기록하는 비차단 PostToolUse/SubagentStop hook
"""review-loop-spawn-record hook.

무엇을 기록하고 무엇을 알리는지는 `meta/rules/review-loop-spawn-record.md`가
보유한다. 어떤 경로도 exit 2를 만들지 않는다 — 비차단 래퍼(`|| exit 1`)
아래에서 exit 1은 stderr 경고, exit 0은 통과이며, owner 알림은 exit 0의
stdout JSON(`systemMessage`)에 실린다. 기록 쓰기 실패는 blocklog와 달리
삼키지 않고 exit 1로 드러낸다 — 이 기록은 근사 카운터가 아니라 대조용 이력이다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime

from harness.blocklog.blocklog import ledger_path
from harness.hookio.payload import read_payload

TAG = "[review-loop-spawn-record]"
RULE_PATH = "meta/rules/review-loop-spawn-record.md"
RECORD_FILENAME = "review-loop-spawns.jsonl"
SCHEMA_VERSION = 2

# Bar declaration prompt의 고정 첫머리(공백을 접은 형태). test_phrase_sync가 규칙 파일의
# 프롬프트 블록과 결속한다.
PHRASE = "You author the severity-bar declaration for a review loop on pull or merge request"
# Pass prompt opening의 고정 첫머리. test_phrase_sync가 규칙 파일의 `### Pass prompt opening`
# 블록과 결속한다.
PASS_PHRASE = "You run a review pass for a review loop on pull or merge request"

_WS_RE = re.compile(r"\s+")
# 줄 머리의 마크다운 인용 표시 — 규칙 파일 원문을 그대로 복사하면 구절 중간에 끼어든다.
_QUOTE_RE = re.compile(r"^[ \t]*(?:>[ \t]?)+", re.MULTILINE)
_REQUEST_RE = re.compile(r" ?`?[#!]?([0-9]{1,9})(?![0-9])")
# 알림에 싣는 식별자 값의 허용 모양. 벗어나면 `?`로 바꾼다.
_TOKEN_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def normalize(text: str) -> str:
    """줄 머리의 `>` 인용 표시를 지우고, 공백 연속(줄바꿈 포함)을 공백 하나로 접고, 앞뒤 공백을 뗀다."""
    return _WS_RE.sub(" ", _QUOTE_RE.sub("", text)).strip()


def match_prompt(prompt: str, phrase: str = PHRASE) -> tuple[bool, int | None] | None:
    """프롬프트가 고정 첫머리를 담았는지 본다.

    Args:
        prompt: `Agent` 호출의 프롬프트 원문.
        phrase: 찾을 첫머리 — bar 선언의 `PHRASE`(기본) 또는 패스의 `PASS_PHRASE`.

    Returns:
        정규화한 프롬프트에 첫머리가 없으면 None. 있으면 (정규화한 프롬프트가 첫머리로
        시작하는가, 첫머리 바로 뒤에서 `_REQUEST_RE`가 읽은 요청 번호 또는 None).
    """
    text = normalize(prompt)
    index = text.find(phrase)
    if index < 0:
        return None
    match = _REQUEST_RE.match(text, index + len(phrase))
    return index == 0, int(match.group(1)) if match else None


def record_path() -> str:
    """기록 파일의 절대 경로 — blocklog 원장과 같은 디렉터리의 전용 파일."""
    return os.path.join(os.path.dirname(ledger_path()), RECORD_FILENAME)


def _text_fields(text: object) -> dict[str, object]:
    if not isinstance(text, str):
        return {"text": None, "sha256": None, "len": None}
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
    return {"text": text, "sha256": digest, "len": len(text)}


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _token(value: object) -> str:
    return value if isinstance(value, str) and _TOKEN_RE.fullmatch(value) else "?"


def _append(path: str, entries: list[dict[str, object]]) -> None:
    # 한 호출의 줄들은 한 페이로드로 모아 쓴다.
    payload = "".join(json.dumps(entry) + "\n" for entry in entries).encode("utf-8")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if _ends_mid_line(path):
        # 잘린 마지막 줄에 이어 붙으면 이 줄까지 파싱할 수 없게 된다.
        payload = b"\n" + payload
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
    finally:
        os.close(fd)


def _ends_mid_line(path: str) -> bool:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    try:
        if os.fstat(fd).st_size == 0:
            return False
        os.lseek(fd, -1, os.SEEK_END)
        return os.read(fd, 1) != b"\n"
    finally:
        os.close(fd)


def _recorded_ids(path: str) -> set[str]:
    # `spawn`(bar 선언)과 `pass`(리뷰 패스) 줄의 agent id. 파싱하지 못한 줄은 건너뛴다.
    # 파일이 없으면 빈 집합, 그 밖의 읽기 실패는 올린다.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return set()
    ids: set[str] = set()
    with os.fdopen(fd, encoding="utf-8", errors="replace") as fp:
        for line in fp:
            try:
                row = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(row, dict) and row.get("kind") in ("spawn", "pass"):
                agent_id = _str_or_none(row.get("agent_id"))
                if agent_id is not None:
                    ids.add(agent_id)
    return ids


def _transcript_models(path: object) -> tuple[list[str] | None, str | None]:
    """서브에이전트 트랜스크립트의 assistant 항목에서 `message.model` 값을 처음 본 순서로 모은다.

    Args:
        path: SubagentStop 페이로드의 `agent_transcript_path`.

    Returns:
        (모델 목록 또는 None, 경고 문장 또는 None). 경로가 없거나 열 수 없거나 모델을 단
        assistant 항목이 하나도 없으면 목록은 None이고 경고가 그 까닭을 말한다.
    """
    if not isinstance(path, str) or not path:
        return None, f"{TAG} no agent_transcript_path on SubagentStop (models not recorded)"
    models: list[str] = []
    try:
        # 열기뿐 아니라 읽기 실패(디렉터리, NUL이 든 경로 등)도 여기서 받는다.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, encoding="utf-8", errors="replace") as fp:
            for line in fp:
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(row, dict) or row.get("type") != "assistant":
                    continue
                message = row.get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                model = _str_or_none(message.get("model"))
                if model is not None and model not in models:
                    models.append(model)
    except (OSError, ValueError) as exc:
        return None, f"{TAG} transcript not readable ({type(exc).__name__}): {path!r}"
    if not models:
        return None, f"{TAG} transcript has no assistant entry with message.model: {path!r}"
    return models, None


def _base(kind: str, payload: dict) -> dict[str, object]:
    return {
        "v": SCHEMA_VERSION,
        "ts": datetime.now(UTC).isoformat(),
        "kind": kind,
        "session_id": _str_or_none(payload.get("session_id")),
        "cwd": _str_or_none(payload.get("cwd")),
    }


def _on_agent(payload: dict, tool_input: dict, response: dict, path: str) -> str | None:
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return None
    # bar 선언 구절을 먼저 본다 — 둘 다 담은 프롬프트는 선언으로 적는다.
    matched = match_prompt(prompt)
    kind, label = "spawn", "spawn recorded"
    extra: dict[str, object] = {}
    if matched is None:
        matched = match_prompt(prompt, PASS_PHRASE)
        kind, label = "pass", "pass recorded"
        extra = {"launcher": "agent"}
    if matched is None:
        return None
    prefix, request = matched
    agent_id = _str_or_none(response.get("agentId"))
    entry = _base(kind, payload)
    entry.update(
        {
            **extra,
            "caller": _str_or_none(payload.get("agent_id")),
            "agent_id": agent_id,
            "request": request,
            "subagent_type": _str_or_none(tool_input.get("subagent_type")),
            "model": _str_or_none(tool_input.get("model")),
            "resolved_model": _str_or_none(response.get("resolvedModel")),
            "status": _str_or_none(response.get("status")),
            "prefix": prefix,
            **_text_fields(prompt),
        }
    )
    entries = [entry]
    content = response.get("content")
    if response.get("status") == "completed" and isinstance(content, list):
        texts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        result = _base("return", payload)
        result.update({"agent_id": agent_id, "via": "result", **_text_fields("\n".join(texts))})
        entries.append(result)
    _append(path, entries)
    return (
        f"{TAG} {label}: request {request if request is not None else '?'}, "
        f"agent {_token(agent_id)}, {_token(tool_input.get('subagent_type'))}, "
        f"model {_token(tool_input.get('model'))} -> {_token(response.get('resolvedModel'))}, "
        f"prefix {'yes' if prefix else 'no'} — see {RULE_PATH}"
    )


def _on_skill(payload: dict, tool_input: dict, response: dict, path: str) -> str | None:
    if response.get("status") != "forked":
        return None
    agent_id = _str_or_none(response.get("agentId"))
    skill = _str_or_none(tool_input.get("skill"))
    args = tool_input.get("args")
    args = args if isinstance(args, str) else None
    entry = _base("pass", payload)
    entry.update(
        {
            "launcher": "skill",
            "caller": _str_or_none(payload.get("agent_id")),
            "agent_id": agent_id,
            "status": "forked",
            "skill": skill,
            "args": args,
            "command_name": _str_or_none(response.get("commandName")),
        }
    )
    _append(path, [entry])
    return (
        f"{TAG} pass recorded: skill {_token(skill)}, agent {_token(agent_id)}, "
        f"args {len(args) if args is not None else 0} chars, forked — see {RULE_PATH}"
    )


def _message_target(tool_input: dict, response: dict) -> str | None:
    pin = response.get("pin")
    if isinstance(pin, dict) and _str_or_none(pin.get("id")) is not None:
        return pin["id"]
    resumed = _str_or_none(response.get("resumedAgentId"))
    if resumed is not None:
        return resumed
    return _str_or_none(tool_input.get("to"))


def _on_send_message(payload: dict, tool_input: dict, response: dict, path: str) -> str | None:
    ids = _recorded_ids(path)
    if not ids:
        return None
    text = tool_input.get("message")
    if not isinstance(text, str):
        text = tool_input.get("content")
    notice = None
    entries: list[dict[str, object]] = []
    sender = _str_or_none(payload.get("agent_id"))
    target = _message_target(tool_input, response)
    if target in ids:
        delivered = response.get("success") is True
        entry = _base("message", payload)
        entry.update({"caller": sender, "agent_id": target, "delivered": delivered, **_text_fields(text)})
        entries.append(entry)
        length = len(text) if isinstance(text, str) else 0
        notice = (
            f"{TAG} message to recorded agent {_token(target)} recorded "
            f"({length} chars, delivered {'yes' if delivered else 'no'}) — see {RULE_PATH}"
        )
    if sender in ids:
        entry = _base("return", payload)
        entry.update({"agent_id": sender, "via": "message", **_text_fields(text)})
        entries.append(entry)
    if entries:
        _append(path, entries)
    return notice


def _on_return(payload: dict, via: str, text: object, path: str) -> None:
    agent_id = _str_or_none(payload.get("agent_id"))
    if agent_id is None or agent_id not in _recorded_ids(path):
        return
    entry = _base("return", payload)
    entry.update({"agent_id": agent_id, "via": via, **_text_fields(text)})
    _append(path, [entry])


def _on_stop(payload: dict, path: str) -> str | None:
    # 기록된 id인지 먼저 본다 — 매처 없는 SubagentStop은 모든 서브에이전트에 오므로,
    # 남의 트랜스크립트는 열지 않는다.
    agent_id = _str_or_none(payload.get("agent_id"))
    if agent_id is None or agent_id not in _recorded_ids(path):
        return None
    transcript_path = payload.get("agent_transcript_path")
    models, warning = _transcript_models(transcript_path)
    entry = _base("return", payload)
    entry.update(
        {
            "agent_id": agent_id,
            "via": "stop",
            **_text_fields(payload.get("last_assistant_message")),
            "models": models,
            "transcript_path": _str_or_none(transcript_path),
        }
    )
    _append(path, [entry])
    return warning


def main() -> int:
    """페이로드를 읽고 해당하면 기록한다.

    Returns:
        0이면 통과(알림이 있으면 stdout JSON에 실린다), 1이면 경고.
    """
    try:
        payload = read_payload()
    except ValueError:
        print(f"{TAG} malformed hook input (nothing recorded)", file=sys.stderr)
        return 1
    if payload is None:
        return 0
    path = record_path()
    event = payload.get("hook_event_name")
    notice = None
    warning = None
    if event == "SubagentStop":
        warning = _on_stop(payload, path)
    elif event == "PostToolUse":
        tool_name = payload.get("tool_name")
        tool_input = payload.get("tool_input")
        response = payload.get("tool_response")
        tool_input = tool_input if isinstance(tool_input, dict) else {}
        response = response if isinstance(response, dict) else {}
        if tool_name == "Agent":
            notice = _on_agent(payload, tool_input, response, path)
        elif tool_name == "Skill":
            notice = _on_skill(payload, tool_input, response, path)
        elif tool_name == "SendMessage":
            notice = _on_send_message(payload, tool_input, response, path)
        elif tool_name == "SubagentHandback":
            _on_return(payload, "handback", tool_input.get("message"), path)
    if notice is not None:
        print(json.dumps({"systemMessage": notice}))
    if warning is not None:
        print(warning, file=sys.stderr)
        return 1
    return 0


def run() -> int:
    """최상위 방어 실행기."""
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 — 비차단이 설계 요구사항
        print(f"{TAG} {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
