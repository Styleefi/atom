# 리뷰 루프 커밋의 트레일러·본문·산문 추가를 사후 점검해 보고하는 비차단 PostToolUse hook
"""review-loop-trailer-check hook.

무엇을 보고 무엇을 보고하는지는 `meta/rules/review-loop-trailer-check.md`가
보유한다. 어떤 경로도 exit 2를 만들지 않는다 — 비차단 래퍼(`|| exit 1`)
아래에서 exit 1은 stderr 경고, exit 0은 통과이며, 보고는 exit 0의 stdout
JSON(`hookSpecificOutput.additionalContext`)에 실린다.
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tokenize

TAG = "[review-loop-trailer-check]"

# 보고 사유. 규칙 파일의 "`report` reasons:" 행과 test_reasons_sync가 결속한다.
REASON_MISSING_REVIEW_LOOP = "missing-review-loop-trailer"
REASON_MISSING_PROSE = "missing-prose-trailer"
REASON_MALFORMED_REVIEW_LOOP = "malformed-review-loop"
REASON_MALFORMED_PROSE = "malformed-prose"
REASON_NONE_WITH_NEW_PROSE = "none-with-new-prose"
REASON_BODY_BEYOND_TRAILERS = "body-beyond-trailers"
REASONS = (
    REASON_MISSING_REVIEW_LOOP,
    REASON_MISSING_PROSE,
    REASON_MALFORMED_REVIEW_LOOP,
    REASON_MALFORMED_PROSE,
    REASON_NONE_WITH_NEW_PROSE,
    REASON_BODY_BEYOND_TRAILERS,
)


_REVIEW_LOOP_RE = re.compile(r"^PR #(\d{1,9}) round (\d{1,9})$")
_PROSE_VALUES = ("none", "attacked")
_TRAILER_LINE_RE = re.compile(r"^([^\s:]+)\s*:\s*")


def parse_review_loop(value: str) -> tuple[int, int] | None:
    """`Review-loop:` 트레일러 값을 푼다.

    Returns:
        (PR 번호, 라운드). 앞뒤 공백을 뗀 값이 `_REVIEW_LOOP_RE`에 맞지 않으면 None.
    """
    match = _REVIEW_LOOP_RE.match(value.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def parse_prose(value: str) -> str | None:
    """`Prose:` 트레일러 값을 푼다.

    Returns:
        앞뒤 공백을 뗀 값이 `none`이나 `attacked`면 그 값, 아니면 None.
    """
    stripped = value.strip()
    return stripped if stripped in _PROSE_VALUES else None


def _trailer_lines(text: str) -> list[str]:
    return [
        _TRAILER_LINE_RE.sub(r"\1: ", line.rstrip())
        for line in text.splitlines()
        if line.strip()
    ]


def body_beyond_trailers(body: str, trailers: str) -> bool:
    """메시지에 트레일러 밖의 본문이 있는가."""
    remaining = _trailer_lines(body)
    for line in _trailer_lines(trailers):
        if line in remaining:
            remaining.remove(line)
    return bool(remaining)


GIT_TIMEOUT_SECONDS = 10


def _run_git(cwd: str | None, *args: str) -> str | None:
    # commit_backstop/backstop.py의 같은 이름 함수와 의도적으로 동일 — 세 번째
    # 사용자가 생기면 공용화한다.
    argv = ["git"]
    if cwd:
        argv += ["-C", cwd]
    argv += list(args)
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


PROSE_SUFFIXES = (".py", ".md")
MIN_PROSE_CHARS = 4

_BACKTICK_SPAN_RE = re.compile(r"`[^`]*`")
_STRIP_CHARS_RE = re.compile(r"[.,;:!?—–\-()\[\]{}\"'«»#*>|]")
_WORD_CHAR_RE = re.compile(r"[0-9A-Za-z가-힣]")
_HANGUL_RE = re.compile(r"[가-힣]")
_NOISE_PREFIXES = ("# noqa", "# type:", "# fmt:", "# pragma:", "#!", "# -*- coding")
_DOCSTRING_LABELS = frozenset(
    {"Args:", "Returns:", "Raises:", "Yields:", "Attributes:", "Example:", "Examples:", "Note:", "Notes:"}
)
_TABLE_RULE_RE = re.compile(r"^\|?[\s:|-]+\|?$")
_HUNK_RE = re.compile(r"^@@ -\S+ \+(\d+)")


def _tokens(line: str) -> list[str]:
    text = _BACKTICK_SPAN_RE.sub("", line)
    return _STRIP_CHARS_RE.sub("", text).split()


def _same_token(a: str, b: str) -> bool:
    if a == b:
        return True
    # 한국어 절 삭제는 살아남은 낱말의 조사를 바꾼다(`문구도` → `문구가`).
    return bool(_HANGUL_RE.match(a) and _HANGUL_RE.match(b)) and a[:2] == b[:2]


def _is_subsequence(block: list[str], pool: list[str]) -> bool:
    i = 0
    for token in pool:
        if i < len(block) and _same_token(block[i], token):
            i += 1
    return i == len(block)


def _is_noise(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith(_NOISE_PREFIXES):
        return True
    if stripped.strip("\"' ") in _DOCSTRING_LABELS:
        return True
    return len(_WORD_CHAR_RE.findall(stripped)) < MIN_PROSE_CHARS


def _prose_lines_py(text: str) -> set[int]:
    # tokenize는 어휘 단계라 구문 오류가 있어도 주석은 낸다; ast는 실패하면 docstring만 포기한다.
    lines: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                lines.add(token.start[0])
    except (tokenize.TokenError, SyntaxError):
        pass
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return lines
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            lines.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    return lines


def _prose_lines_md(text: str) -> set[int]:
    lines: set[int] = set()
    in_fence = False
    in_frontmatter = False
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if number == 1 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or not stripped or _TABLE_RULE_RE.match(stripped):
            continue
        lines.add(number)
    return lines


def _parse_diff(diff: str) -> tuple[dict[str, list[tuple[int, str]]], list[str]]:
    # 내용 줄은 `+`/`-` 접두를 달고 오므로 `+++ b/x`나 `@@`처럼 생긴 내용이
    # 헤더로 읽히지 않는다.
    added: dict[str, list[tuple[int, str]]] = {}
    removed: list[str] = []
    in_header = True
    path: str | None = None
    line_no: int | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_header, path, line_no = True, None, None
            continue
        if in_header:
            if line.startswith("+++ "):
                target = line[4:].rstrip("\t")
                path = None if target == "/dev/null" else target.removeprefix("b/")
            elif line.startswith("@@"):
                in_header = False
                match = _HUNK_RE.match(line)
                line_no = int(match.group(1)) if match else None
            continue
        if line.startswith("@@"):
            match = _HUNK_RE.match(line)
            line_no = int(match.group(1)) if match else None
        elif line.startswith("+"):
            if path is not None and line_no is not None:
                added.setdefault(path, []).append((line_no, line[1:]))
                line_no += 1
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def prose_lines_added(cwd: str | None, sha: str) -> bool:
    """커밋이 산문 줄을 새로 썼는가."""
    diff = _run_git(cwd, "-c", "core.quotePath=false", "show", "--format=", "-U0", "--no-color", sha)
    if diff is None:
        return False
    added, removed = _parse_diff(diff)
    pool = [token for line in removed for token in _tokens(line)]
    for path, lines in added.items():
        suffix = os.path.splitext(path)[1]
        if suffix not in PROSE_SUFFIXES:
            continue
        content = _run_git(cwd, "show", f"{sha}:{path}")
        if content is None:
            continue
        prose = _prose_lines_py(content) if suffix == ".py" else _prose_lines_md(content)
        block: list[str] = []
        previous: int | None = None
        for number, text in lines:
            if number not in prose or _is_noise(text):
                continue
            if previous is not None and number != previous + 1:
                if block and not _is_subsequence(block, pool):
                    return True
                block = []
            block += _tokens(text)
            previous = number
        if block and not _is_subsequence(block, pool):
            return True
    return False


HARNESS_ID = "review-loop-trailer-check"
RULE_PATH = "meta/rules/review-loop-trailer-check.md"
STATE_FILENAME = "atom-review-loop-trailer.json"
PROTECTED_BRANCHES = ("main", "master")
COMMIT_LIMIT = 50
CHECKED_CAP = 200
AUTHORING_VERBS = ("commit", "revert", "cherry-pick")
OPERATORS = {"&&", "||", "|", ";", ";;", "&", "(", ")"}

_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_MESSAGE_FORMAT = (
    "%(trailers:key=Review-loop,valueonly)%x00%(trailers:key=Prose,valueonly)"
    "%x00%b%x00%(trailers:only=true)"
)


def _repo_dirs(cwd: str | None) -> tuple[str, str] | None:
    # commit_backstop/backstop.py의 같은 이름 함수와 의도적으로 동일 — 세 번째
    # 사용자가 생기면 공용화한다.
    out = _run_git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir")
    if out is None:
        return None
    lines = out.splitlines()
    if len(lines) < 2 or not lines[0].strip() or not lines[1].strip():
        return None
    return os.path.realpath(lines[0].strip()), os.path.realpath(lines[1].strip())


def _rev_parse(cwd: str | None, ref: str) -> str | None:
    out = _run_git(cwd, "rev-parse", "--verify", "--quiet", ref)
    if out is None:
        return None
    return out.strip() or None


def _load_state(path: str) -> dict:
    # 읽지 못하거나 모양이 다르면 빈 상태 — 비차단 보고기라 기준선 상실을 알릴 필요가 없다.
    fresh: dict = {"heads": {}, "checked": []}
    try:
        with open(path, encoding="utf-8") as fp:
            raw = json.load(fp)
    except (OSError, ValueError):
        return fresh
    heads = raw.get("heads") if isinstance(raw, dict) else None
    checked = raw.get("checked") if isinstance(raw, dict) else None
    if not isinstance(heads, dict) or not isinstance(checked, list):
        return fresh
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in heads.items()):
        return fresh
    if not all(isinstance(sha, str) for sha in checked):
        return fresh
    return {"heads": heads, "checked": checked}


def _store_state(path: str, state: dict) -> None:
    # 임시 파일 + os.replace — commit_backstop/backstop.py의 `_store_state`와 같은
    # 원자적 쓰기. 실패는 삼킨다.
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(state, fp)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _main_refs(cwd: str | None) -> list[str]:
    """존재하는 로컬·원격 main/master ref 목록 — 검사 제외집합."""
    refs = []
    for branch in PROTECTED_BRANCHES:
        ref = f"refs/heads/{branch}"
        if _rev_parse(cwd, ref) is not None:
            refs.append(ref)
    remotes = _run_git(cwd, "remote")
    for remote in (remotes or "").splitlines():
        for branch in PROTECTED_BRANCHES:
            ref = f"refs/remotes/{remote.strip()}/{branch}"
            if remote.strip() and _rev_parse(cwd, ref) is not None:
                refs.append(ref)
    return refs


def _new_commits(cwd: str | None, old: str, new: str, exclusions: list[str]) -> list[str] | None:
    out = _run_git(
        cwd, "rev-list", "--no-merges", f"--max-count={COMMIT_LIMIT}", f"{old}..{new}", "--not", *exclusions
    )
    if out is None:
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


def _branch_in_loop(cwd: str | None, exclusions: list[str]) -> bool:
    if not exclusions:
        return False
    out = _run_git(
        cwd, "log", f"--max-count={COMMIT_LIMIT}", "--format=%(trailers:key=Review-loop,valueonly)",
        "HEAD", "--not", *exclusions,
    )
    return bool(out) and any(line.strip() for line in out.splitlines())


def _authoring_verb(command: str) -> bool:
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        tokens = list(lex)
    except ValueError:
        return False
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in OPERATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    for segment in segments:
        index = 0
        while index < len(segment) and _ENV_ASSIGNMENT_RE.match(segment[index]):
            index += 1
        rest = segment[index:]
        if not rest or rest[0].rsplit("/", 1)[-1] != "git":
            continue
        i = 1
        while i < len(rest) and rest[i].startswith("-"):
            i += 2 if rest[i] in ("-C", "-c") else 1
        if i < len(rest) and rest[i] in AUTHORING_VERBS:
            return True
    return False


def _message_parts(cwd: str | None, sha: str) -> tuple[str, str, str, str] | None:
    out = _run_git(cwd, "log", "-1", f"--format={_MESSAGE_FORMAT}", sha)
    if out is None:
        return None
    parts = out.split("\0")
    if len(parts) != 4:
        return None
    return parts[0], parts[1], parts[2], parts[3]


def _reasons(cwd: str | None, sha: str, parts: tuple[str, str, str, str]) -> list[str]:
    review_loop, prose, body, trailers = parts
    reasons = []
    if parse_review_loop(review_loop) is None:
        reasons.append(REASON_MALFORMED_REVIEW_LOOP)
    prose_value = None
    if not prose.strip():
        reasons.append(REASON_MISSING_PROSE)
    else:
        prose_value = parse_prose(prose)
        if prose_value is None:
            reasons.append(REASON_MALFORMED_PROSE)
    if prose_value != "attacked" and prose_lines_added(cwd, sha):
        reasons.append(REASON_NONE_WITH_NEW_PROSE)
    if body_beyond_trailers(body, trailers):
        reasons.append(REASON_BODY_BEYOND_TRAILERS)
    return reasons


def _log(**kwargs) -> None:
    # 원장 기록은 제어 흐름에 영향을 주지 않는다 — commit_backstop의 `_log`와 같은 근거.
    try:
        from harness.blocklog.blocklog import record_block

        record_block(**kwargs)
    except Exception:  # noqa: BLE001 — fail-open이 설계 요구사항
        pass


def _emit(reports: dict[str, list[str]]) -> None:
    summary = "; ".join(f"{sha[:7]}: {'+'.join(reasons)}" for sha, reasons in reports.items())
    context = f"{TAG} {summary} — see {RULE_PATH}"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}))


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
    """페이로드를 읽고 새 커밋을 점검한다.

    Returns:
        0이면 통과(보고가 있으면 stdout JSON에 실린다), 1이면 fail-open 경고.
    """
    try:
        payload = _read_payload()
    except ValueError:
        print(f"{TAG} malformed hook input (fail-open)", file=sys.stderr)
        return 1
    if payload is None:
        return 0
    command, cwd, session_id = payload
    dirs = _repo_dirs(cwd)
    if dirs is None:
        return 0
    common_dir, git_dir = dirs
    head = _rev_parse(cwd, "HEAD")
    if head is None:
        return 0
    state_path = os.path.join(common_dir, STATE_FILENAME)
    state = _load_state(state_path)
    key = f"HEAD@{git_dir}"
    old = state["heads"].get(key)
    state["heads"][key] = head
    if old is None or old == head:
        _store_state(state_path, state)
        return 0
    exclusions = _main_refs(cwd)
    commits = _new_commits(cwd, old, head, exclusions)
    if commits is None:
        _store_state(state_path, state)  # 재기준화: 이번 호출은 검사 없이 지나간다
        return 0
    known = set(state["checked"])
    newly: list[str] = []
    reports: dict[str, list[str]] = {}
    in_loop: bool | None = None
    authoring = _authoring_verb(command)
    for sha in commits:
        if sha in known:
            continue
        known.add(sha)
        newly.append(sha)
        parts = _message_parts(cwd, sha)
        if parts is None:
            continue
        if not parts[0].strip():
            if in_loop is None:
                in_loop = authoring and _branch_in_loop(cwd, exclusions)
            if in_loop:
                reports[sha] = [REASON_MISSING_REVIEW_LOOP]
            continue
        reasons = _reasons(cwd, sha, parts)
        if reasons:
            reports[sha] = reasons
    state["checked"] = (state["checked"] + newly)[-CHECKED_CAP:]
    _store_state(state_path, state)
    for sha, reasons in reports.items():
        _log(
            event="report",
            harness=HARNESS_ID,
            reason="+".join(reasons),
            command=None,
            cwd=cwd,
            session_id=session_id,
        )
    if reports:
        _emit(reports)
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
