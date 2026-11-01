# gh pr create 직후 리뷰 루프 의무를 문맥에 상기시키는 PostToolUse hook
"""review-loop 리마인더 hook (review-loop-reminder 규칙의 배포체).

Claude Code의 PostToolUse(Bash) hook으로 실행되어, 방금 실행된 명령에
`gh pr create` 세그먼트가 있으면 상수 한 줄(REMINDER)을 additionalContext로
문맥에 넣는다. 의무 문장의 SSOT는 규칙 파일 `meta/rules/review-loop-reminder.md`이고,
이 모듈은 PR이 열린 그 순간에 그 의무를 다시 공급할 뿐이다. 아무것도 막지 않는다.

**비주장 (SSOT는 이 docstring, #27 결정 2026-11-01; (c)는 첫 공격에서 정정).**
아래는 설계상 보지 못하거나 하지 않는 것이며, 수리 대상이 아니라 선언된 경계다.

(a) hook은 명령에 발화하지 인계에 발화하지 않는다 — 인계가 PR을 만든 세션이
    아닌 세션에서 일어나거나 생성 후 여러 턴 뒤에 일어나면 리마인더는 없다.
(b) 셸 의미론은 판정하지 않는다(#52 동결): `bash -c '…'`·스크립트·별칭·Bash 외
    도구 안의 `gh pr create`, 전역 플래그나 접두어가 앞서는 형태(`gh -R o/r pr
    create`, `GH_REPO=x gh …`, `env`/`command`/`timeout` 뒤, 절대 경로 `gh`),
    백틱 안, `gh api …/pulls`, `--help`·`-h`·`--dry-run`이 값 자리에 온 형태
    (`--title --help`), 같은 줄에서 불균형 따옴표 뒤에 오는 텍스트(`echo 'x; gh
    pr create`), 백슬래시로 끝나는 주석 줄의 다음 줄(bash는 잇지 않지만 여기서는
    잇는다)는 보지 못한다. 반대로 주석(`# ; gh pr create`), 히어독 본문 속
    `&& gh pr create`, 줄을 걸친 인용 안에서 줄 머리나 `;`·`(` 뒤에 온 `gh pr
    create`(이 훅을 설명하는 여러 줄 커밋 메시지·댓글 본문이 흔한 예), 따옴표가
    붙은 `create"x`, `false && gh pr create`처럼 bash는 실행하지 않지만 토큰으로는
    세그먼트 머리에 오는 텍스트에는 발화하고, 줄을 걸친 인용 뒤에 둔
    `--help`·`-h`·`--dry-run`(`--body "$(cat <<'EOF' … )" --dry-run`)은 잘린 첫
    줄이 보지 못해 그래도 발화한다.
(c) PostToolUse는 포그라운드 Bash 호출이 0으로 끝났을 때, 그리고 백그라운드
    호출은 그 종료와 무관하게 시작 시점에 발화한다(실측 2026-11-01;
    PostToolUseFailure는 배선하지 않았다). 실패한 포그라운드 `gh pr create`에는
    리마인더가 없고, 생성은 됐지만 뒤 세그먼트가 실패해 전체가 nonzero로 끝난
    명령에도 없다. `tool_response`는 읽지 않는다.
(d) `glab mr create`는 #10이 끝날 때까지 매처에 없다.
(e) 루프가 실제로 돌았는지는 검증하지 않는다. Stop 시점 검증안은 턴마다
    포지 호출이 드는 비용으로 기각됐다(#27, 2026-10-26).
(f) `gh pr ready`(드래프트 해제)는 매처에 없다.

매처는 세그먼트의 첫 토큰 `gh`, 둘째 `pr`, 셋째 `create` 또는 그 내장 별칭 `new`이고,
같은 토큰화 단위의 그 세그먼트에 토큰 `--help`·`-h`·`--dry-run`이 있으면 발화하지
않는다(`--dry-run=true`처럼 참값이 붙은 꼴도 같고, `=false`가 붙으면 발화한다).
명령은 바로 앞이 백슬래시가 아닌 백슬래시+개행을 공백으로 이은 뒤 줄마다 토큰화하고,
여러 줄이면 명령 전체로도 한 번 토큰화한다. 혼자 토큰화되지 않는 줄은 마지막
따옴표부터 잘라 내며 최대 8회 다시 시도하고(인용된 히어독 본문이 다음 줄로
이어지는 줄의 머리를 살린다), 그래도 안 되면 건너뛴다(fail-open).

설계 불변식: 어떤 입력에도 차단 코드(42)를 내지 않고, stdout은 비어 있거나
정확히 REMINDER를 담은 JSON 한 줄이다 — 페이로드 내용이 주입 채널로 새지
않는다. 실패 경로는 전부 fail-open(exit 1 경고 또는 exit 0 통과)이다.
"""

from __future__ import annotations

import json
import re
import sys

from harness.hookio.payload import bash_call, read_payload
from harness.hookio.shell import protected_flags, split_segments, tokenize

TAG = "[review-loop-reminder]"
RULE_PATH = "meta/rules/review-loop-reminder.md"

# 문맥에 넣는 상수. 문안은 #27 결정 3(2026-11-01) 그대로이며 규칙 파일이 인용한다.
REMINDER = (
    f"{TAG} A PR was just opened. Before it is handed over to the owner for "
    "review, a review loop (meta/rules/review-loop.md) must reach its exit "
    "observation on the PR's current HEAD; its passes are fresh-context "
    "subagents on the model the owner names. Report the PR to the owner as "
    "awaiting its review loop, not as ready for merge; an owner waiver is "
    f"recorded in the PR body per {RULE_PATH}."
)

_PR_CREATE = ("gh", "pr")
_CREATE_VERBS = frozenset({"create", "new"})
_NO_PR_FLAGS = frozenset({"--help", "-h", "--dry-run"})
# gh(Go pflag)가 불 플래그의 참값으로 받는 문자열. `--dry-run=false`는 PR을 연다.
_TRUE_VALUES = frozenset({"1", "t", "T", "TRUE", "true", "True"})
# 바로 앞이 백슬래시가 아닌 백슬래시+개행. 백슬래시 홀수 연속(`\\\`)은 bash와 달리 잇지 않는다.
_CONTINUATION_RE = re.compile(r"(?<!\\)\\\n")
_PREFIX_RETRIES = 8


def _groups(command: str) -> list[tuple[str, list[str]]]:
    """토큰화 단위를 만든다: 줄마다 하나, 그리고 명령 전체로 하나.

    혼자 토큰화되지 않는 줄은 마지막 따옴표부터 잘라 내고 다시 시도한다(최대
    _PREFIX_RETRIES회) — `gh pr create --body "$(cat <<'EOF'`처럼 인용된 본문이
    다음 줄로 이어지는 줄의 머리를 살리기 위해서다. 그래도 안 되면 그 줄은
    건너뛴다. 명령 전체 단위는 줄을 걸친 인용 뒤에 오는 세그먼트를 위한 것이다.
    줄을 누적해 다시 토큰화하는 방식은 닫히지 않는 따옴표 하나에 2차 시간이
    됐다(셋째 공격, 3000줄 140초); 지금 방식의 실측은 tests의 시간 테스트가 든다.

    Returns:
        (원문, 토큰) 쌍의 목록. 원문은 protected_flags가 따옴표·이스케이프를
        다시 보는 데 쓴다 — 토큰을 다시 이어 붙인 문자열은 그 정보를 잃는다.
    """
    joined = _CONTINUATION_RE.sub(" ", command)
    groups: list[tuple[str, list[str]]] = []
    for line in joined.split("\n"):
        text = line
        for _ in range(_PREFIX_RETRIES + 1):
            try:
                groups.append((text, tokenize(text)))
                break
            except ValueError:
                cut = max(text.rfind('"'), text.rfind("'"))
                if cut < 0:
                    break
                text = text[:cut]
    if "\n" in joined:
        try:
            groups.append((joined, tokenize(joined)))
        except ValueError:
            pass
    return groups


def _is_no_pr_flag(token: str) -> bool:
    """토큰이 `--help`·`-h`·`--dry-run`인지 판정한다.

    Args:
        token: 세그먼트의 인자 토큰 하나.

    Returns:
        플래그 이름이 _NO_PR_FLAGS에 있고 값이 없거나 참값이면 True.
    """
    name, sep, value = token.partition("=")
    return name in _NO_PR_FLAGS and (not sep or value in _TRUE_VALUES)


def is_pr_create(command: str) -> bool:
    """명령 문자열에 PR을 여는 `gh pr create`/`gh pr new` 세그먼트가 있는지 판정한다.

    hookio의 세그먼테이션(tokenize · protected_flags · split_segments)으로
    복합 명령을 나누고, 어느 세그먼트든 첫 세 토큰이 `gh pr create`(또는 `new`)이고
    토큰 `--help`·`-h`·`--dry-run`(참값 `=true` 꼴 포함)이 없으면 참이다. 인용된
    리터럴(`echo "gh pr create"`)은 한 토큰이라 매치되지 않는다.

    Args:
        command: Bash 도구가 실행한 명령 전체.

    Returns:
        매치되면 True. shlex가 소화 못 하는 단위는 건너뛴다 — fail-open.
    """
    for text, tokens in _groups(command):
        for segment in split_segments(tokens, protected_flags(text, len(tokens))):
            if (
                tuple(segment[:2]) == _PR_CREATE
                and len(segment) > 2
                and segment[2] in _CREATE_VERBS
                and not any(_is_no_pr_flag(token) for token in segment[3:])
            ):
                return True
    return False


def _emit() -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": REMINDER,
                }
            }
        )
    )


def main() -> int:
    """stdin의 PostToolUse JSON을 읽고 `gh pr create`면 리마인더를 낸다.

    Returns:
        종료 코드 (0 통과 — 매치되면 stdout으로 리마인더 JSON, 1 fail-open 경고).
    """
    try:
        payload = read_payload()
    except ValueError:
        print(f"{TAG} malformed hook input (fail-open)", file=sys.stderr)
        return 1
    call = bash_call(payload)
    if call is None:
        return 0
    if is_pr_create(call.command):
        _emit()
    return 0


def run() -> int:
    """최상위 방어 실행기: 어떤 내부 오류도 차단으로 새지 않게 한다.

    Returns:
        종료 코드 (main()의 반환값 그대로. main()이 Exception을 던지면 대신
        1 — 비차단).
    """
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 — fail-open이 설계 요구사항
        print(f"{TAG} internal error (fail-open): {exc}", file=sys.stderr)
        return 1
