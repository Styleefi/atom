# 보고된 커밋이 원격 main/master에 실제로 있는지 갓 fetch한 상태로 판정하는 오너 실행 도구
"""commit-publication: 보고된 커밋의 발행 여부를 원격에 직접 물어 판정한다 (#116).

commit_backstop 훅은 **로컬에 존재하는** 원격 main/master ref만 제외 집합으로 쓰므로,
그 ref가 없거나 낡은 구성(`--single-branch`·pruned clone, `git pull <URL>`)에서는 이미
발행된 커밋도 보고한다. 그 판별을 규칙 산문이 아니라 실행 파일이 수행하게 한다.

주장하는 것:
    - 나열된 SHA 각각이 지정된 remote의 `refs/heads/main`·`refs/heads/master`에 대해
      조상인지를, ls-remote가 돌려준 tip과 비교해 판정한다.
    - 판정은 **fetch 시점의 원격 상태**에 대한 것이다. "훅이 틀렸다"는 판정하지 않는다 —
      보고와 fetch 사이의 push를 어떤 git 술어도 구분하지 못한다.
    - 저장소 이력에 대해 아무것도 지시하지 않는다. 어떤 결과가 나오든 오너 보고 의무는
      남는다.

주장하지 않는 것:
    - 얕은 클론은 판정하지 않는다. 깊이 밖 조상이 끊겨 `merge-base`가 "없다"고 답하므로,
      네트워크 전에 물러난다(exit 3).
    - 로컬 그래프 재작성(replace ref·graft 파일)을 중화하지 않는다. 위조하면 on으로 읽힐
      수 있다. 훅도 중화하지 않으므로 도구도 하지 않는다.
    - 축약 SHA와 같은 이름의 ref가 있으면 git은 그 ref를 우선한다(전체 SHA는 객체가
      우선한다). 그때의 답은 ref에 대한 것이다.
    - ls-remote와 fetch 사이에 원격 tip이 움직이면, 그 tip이 로컬에 없어 판정 불가가
      되거나, 옛 tip이 로컬에 있으면 옛 tip 기준으로 답한다.
    - 등록된 remote 하나만 본다(훅은 등록된 remote 전부의 main/master를 제외 집합에 넣는다).
      차이는 not-on 과다 보고 방향으로만 작용한다. 등록되지 않은 원격(`git pull <URL>`로만
      받은 이력)은 `git remote add` 뒤에야 물을 수 있다.
    - fetch는 설정된 refspec이 일치하면 추적 ref를 forced 갱신하므로 되감을 수도 있고,
      그러면 훅이 전에 제외하던 커밋을 새로 보고하거나 유예된 판정이 push 없이 녹을 수
      있다. `--single-branch` 클론에서는 일치하는 refspec이 없어 아무 ref도 만들지
      않는다.
    - `--no-write-fetch-head`는 git 2.29 이상을 요구한다. 그 미만에서는 fetch가 미지 옵션으로
      실패해 exit 3이 된다.
    - 판정 대상은 프로세스 cwd가 속한 저장소다. 규칙이 인용하는 `uv run --directory meta ...`는
      cwd를 `meta/`로 바꾸므로 `meta/`를 담은 저장소를 본다. 다른 저장소(서브모듈 등)의
      보고는 범위 밖이다 — 선언된 경계. 불변식: 이 도구는 cwd가 속한 저장소의 원격에
      대해서만 답한다. 실패 방향: 다른 저장소의 SHA는 대개 이 저장소에 없어 판정 불가(exit 3)가
      되고, 우연히 있으면 이 저장소의 원격에 대한 답이 나간다. 인용: 오너 결정
      2026-09-04, PR #152.
    - 도구 자신의 타임아웃은 git을 SIGKILL로 끝낸다. 그때 전송 프로세스(remote helper·ssh)는
      원격 연결이 닫힐 때까지 남을 수 있다.
    - 호출자가 도구를 죽이면 git 자식 프로세스의 정리는 보장하지 않는다.
    - 자식 프로젝트가 이 하네스를 제거하면 규칙 인용도 함께 제거해야 한다.

출력 채널:
    git의 stderr는 **어떤 경로로도 에코하지 않는다.** 서버가 제어하는 `remote:` 줄이 실리고,
    `merge-base`는 모호한 축약에서 커밋 제목을 덤프한다 — commit_backstop이 절대 에코하지
    않기로 한 바로 그 텍스트다. 원격이 보고한 ref 이름도 인쇄하지 않는다.

종료 코드:
    5 하나 이상 not on / 4 전부 on / 3 판정 불가 / 2 호출자 잘못 /
    1 도구가 돌지 못함(uv·import 실패 — 의도적으로 내지 않는다).

    **5는 하나라도 not-on이면 난다. 4는 모든 SHA가 on일 때만 난다.** 판정 불가 SHA가 섞이면
    5는 그대로 5이고(별도 줄로 이름을 부른다), 4는 3이 된다 — "하나 이상 not-on"은 존재
    명제이고 "전부 on"은 전칭 명제이기 때문이다. 실질 판정을 1이 아니라 5에 둔 이유는
    파이썬이 잡히지 않은 예외에서 1을, uv가 실패 시 1이나 2를 내기 때문이다.
    2와 3의 경계: **2는 에이전트가 스스로 고칠 수 있고 3은 못 고친다.**
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

TAG = "[commit-publication]"

EXIT_CALLER = 2
EXIT_UNDECIDED = 3
EXIT_ALL_ON = 4
EXIT_SOME_NOT_ON = 5

# commit_backstop·commit_guard 와 같은 값이어야 한다 — 테스트가 결속한다.
PROTECTED_BRANCHES = ("main", "master")

NETWORK_TIMEOUT_SECONDS = 60
LOCAL_TIMEOUT_SECONDS = 10

SHA_RE = re.compile(r"^[0-9a-f]{4,64}$")

ON = "on"
NOT_ON = "not_on"

_USAGE = (
    f"{TAG} usage: python -m harness.commit_publication "
    "[--remote <registered name>] <sha>..."
)


class _Undecided(Exception):
    """판정을 내릴 수 없는 상태 (exit 3)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _CallerError(Exception):
    """호출자가 고칠 수 있는 잘못 (exit 2)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def run_git(args: list[str], *, timeout: int) -> tuple[int, str]:
    """git을 실행하고 (종료 코드, stdout)을 돌려준다. 모든 git 호출의 유일한 이음매다.

    `-c credential.helper=`는 자격 증명 helper 채널을 닫는다 — `GIT_TERMINAL_PROMPT`는
    git 자신의 tty 프롬프트만 막고 helper는 막지 못하며, osxkeychain·libsecret·
    git-credential-manager가 전부 그 채널이다. askpass 변수 셋은 GUI·에디터 프롬프트
    경로를 막는다(VS Code는 `GIT_ASKPASS`를 export한다). `SSH_ASKPASS_REQUIRE`는 `force`다 —
    `never`는 제어 tty가 없다는 데 기대지만 `force`는 tty 유무와 무관하게 askpass를 쓰게
    하고, `/bin/false`와 짝지으면 즉시 실패한다. stderr는 돌려주지 않는다(모듈 docstring의
    출력 채널 항목).

    Args:
        args: `git` 뒤에 붙일 인자.
        timeout: 초 단위 상한.

    Returns:
        `(rc, stdout)`. 타임아웃과 실행 실패는 rc 124로 돌려준다(git이 내지 않는 값).
    """
    try:
        proc = subprocess.run(
            ["git", "-c", "credential.helper=", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "/bin/false",
                "SSH_ASKPASS": "/bin/false",
                "SSH_ASKPASS_REQUIRE": "force",
            },
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return 124, ""
    return proc.returncode, proc.stdout


def _parse_args(argv: list[str]) -> tuple[str | None, list[str]]:
    """argv를 (remote, SHA 목록)으로 가른다. git을 부르기 전에 형식만 본다.

    Args:
        argv: 프로그램 이름을 제외한 인자 목록.

    Returns:
        `(remote 또는 None, SHA 목록)`.

    Raises:
        _CallerError: 인자가 없거나 형식이 틀렸을 때.
    """
    remote: str | None = None
    shas: list[str] = []
    rest = list(argv)
    while rest:
        item = rest.pop(0)
        if item == "--remote":
            if not rest:
                raise _CallerError("--remote needs a value")
            remote = rest.pop(0)
        elif item.startswith("-"):
            raise _CallerError(f"unknown option: {item}")
        else:
            shas.append(item)
    if not shas:
        raise _CallerError("no commit SHAs given")
    bad = [s for s in shas if not SHA_RE.match(s)]
    if bad:
        raise _CallerError(f"not hex commit SHAs: {' '.join(bad)}")
    return remote, shas


def _assert_not_shallow() -> None:
    """얕은 클론이면 네트워크 이전에 판정을 포기한다.

    Raises:
        _CallerError: git이 rc 128을 냈을 때.
        _Undecided: 얕은 클론이거나, git이 답하지 않을 때.
    """
    rc, out = run_git(
        ["rev-parse", "--is-shallow-repository"], timeout=LOCAL_TIMEOUT_SECONDS
    )
    if rc == 128:
        raise _CallerError("not a git repository")
    if rc != 0:
        raise _Undecided("git did not answer when asked about this repository")
    if out.strip() == "true":
        raise _Undecided("this repository is a shallow clone")


def _resolve_remote(requested: str | None) -> str:
    """확인할 remote를 정한다. 값은 **먼저 `git remote` 목록과 대조한다.**

    오타 난 이름은 `ls-remote`에서 접속 불가와 똑같이 128을 내므로, 목록 대조 없이는
    호출자의 오타가 "환경 문제"로 오너에게 보고된다.

    Args:
        requested: `--remote` 값. 없으면 None.

    Returns:
        git에 넘길 remote 이름.

    Raises:
        _CallerError: 기본 remote를 정할 수 없거나 이름이 등록돼 있지 않을 때.
        _Undecided: `git remote` 자체가 실패했을 때.
    """
    rc, out = run_git(["remote"], timeout=LOCAL_TIMEOUT_SECONDS)
    if rc != 0:
        raise _Undecided("`git remote` failed")
    names = [line.strip() for line in out.splitlines() if line.strip()]
    if not names:
        raise _CallerError("no remote is registered in this repository")
    if requested is None:
        if len(names) == 1:
            return names[0]
        if "origin" in names:
            return "origin"
        raise _CallerError("cannot pick a default remote; name one with --remote")
    if requested in names:
        return requested
    raise _CallerError(f"no such remote is registered: {requested}")


def _tips(remote: str) -> dict[str, str]:
    """원격의 보호명 브랜치 tip을 ls-remote로 읽는다.

    `ls-remote`의 ref 인자는 꼬리 글롭이라 `refs/heads/refs/heads/main` 같은 이름도
    걸리므로, ref 필드를 정확 비교한다.

    Args:
        remote: 등록된 remote 이름.

    Returns:
        `{브랜치 이름: tip SHA}`. 존재하는 브랜치만 담기며 비어 있지 않다.

    Raises:
        _Undecided: 접속 실패, 또는 main·master가 둘 다 없을 때.
    """
    patterns = [f"refs/heads/{name}" for name in PROTECTED_BRANCHES]
    rc, out = run_git(["ls-remote", remote, *patterns], timeout=NETWORK_TIMEOUT_SECONDS)
    if rc != 0:
        raise _Undecided("could not reach the remote")
    tips: dict[str, str] = {}
    for line in out.splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            continue
        sha, ref = fields[0].strip(), fields[1].strip()
        for name in PROTECTED_BRANCHES:
            if ref == f"refs/heads/{name}":
                tips[name] = sha
    if not tips:
        raise _Undecided("the remote has neither main nor master")
    return tips


def _fetch(remote: str, names: list[str]) -> None:
    """보호명 브랜치의 객체를 한 번 받아온다. 실패하면 아무것도 판정하지 않는다.

    `--no-write-fetch-head`: 도구는 FETCH_HEAD를 읽지 않으므로 쓰지도 않는다. 오너가
    `git fetch origin x` 뒤 `git merge FETCH_HEAD`를 하려던 참이면 덮어쓴 값이 엉뚱한
    머지가 된다.

    Args:
        remote: 등록된 remote 이름.
        names: ls-remote가 존재를 확인한 브랜치 이름들.

    Raises:
        _Undecided: fetch가 0이 아닌 코드로 끝났을 때.
    """
    refs = [f"refs/heads/{name}" for name in names]
    rc, _ = run_git(
        ["fetch", "--no-tags", "--no-write-fetch-head", remote, *refs],
        timeout=NETWORK_TIMEOUT_SECONDS,
    )
    if rc != 0:
        raise _Undecided("the fetch failed")


def judge(sha: str, tips: dict[str, str]) -> str | None:
    """SHA 하나를 tip들에 대해 판정한다.

    **있다**는 어느 tip에서든 조상이면 확정이다. **없다**는 전칭 명제라 모든 tip이 rc 1로
    답해야 성립한다. 그 밖의 rc(128 = 미지 SHA·모호한 축약·미도착 tip, 124 = 타임아웃)는
    판정 불가다 — 그것을 "없다"로 읽는 것이 산문 시절부터의 함정이었다.

    Args:
        sha: 판정할 커밋(축약형 가능).
        tips: `{브랜치 이름: tip SHA}`. 비어 있지 않다(`_tips`가 보장).

    Returns:
        `ON` / `NOT_ON` / None(판정 불가).
    """
    rcs = set()
    for tip in tips.values():
        rc, _ = run_git(
            ["merge-base", "--is-ancestor", sha, tip], timeout=LOCAL_TIMEOUT_SECONDS
        )
        if rc == 0:
            return ON
        rcs.add(rc)
    return NOT_ON if rcs == {1} else None


def _report(remote: str, shas: list[str], verdicts: dict[str, str | None]) -> int:
    """판정 결과를 인쇄하고 종료 코드를 돌려준다.

    not-on이 하나라도 있으면 exit 5다. "하나 이상 not-on"은 SHA에 대한 존재 명제라 다른
    SHA가 판정 불가여도 성립한다. "전부 on"은 전칭 명제라 하나라도 판정 불가면 exit 4를
    낼 수 없고, 그때는 exit 3이다. 판정 불가 SHA는 어느 경우든 별도 줄로 이름을 부른다.

    Args:
        remote: 출력에 쓸 remote 이름.
        shas: 넘어온 순서의 SHA 목록.
        verdicts: SHA별 판정.

    Returns:
        종료 코드.
    """
    unjudged = [s for s in shas if verdicts[s] not in (ON, NOT_ON)]
    not_on = [s for s in shas if verdicts[s] == NOT_ON]

    def _verb(n: int) -> str:
        return "is" if n == 1 else "are"

    if not_on:
        print(
            f"{TAG} {len(not_on)} of {len(shas)} listed commits {_verb(len(not_on))} "
            f"not on main/master at {remote} as of this fetch: {' '.join(not_on)}"
        )
        if unjudged:
            print(f"  {len(unjudged)} could not be judged: {' '.join(unjudged)}")
        return EXIT_SOME_NOT_ON
    if unjudged:
        print(
            f"{TAG} {len(shas) - len(unjudged)} of {len(shas)} listed commits were judged "
            f"as of this fetch; none of those is not on main/master at {remote}."
        )
        print(f"  {len(unjudged)} could not be judged: {' '.join(unjudged)}")
        return EXIT_UNDECIDED
    print(
        f"{TAG} {len(shas)} of {len(shas)} listed commits {_verb(len(shas))} "
        f"on main/master at {remote} as of this fetch."
    )
    return EXIT_ALL_ON


def _check(argv: list[str]) -> int:
    """절차 전체를 순서대로 수행한다.

    Args:
        argv: 프로그램 이름을 제외한 인자 목록.

    Returns:
        종료 코드.
    """
    try:
        requested, shas = _parse_args(argv)
        _assert_not_shallow()
        remote = _resolve_remote(requested)
        tips = _tips(remote)
        _fetch(remote, list(tips))
        verdicts = {sha: judge(sha, tips) for sha in shas}
    except _CallerError as exc:
        print(f"{TAG} {exc.reason}", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return EXIT_CALLER
    except _Undecided as exc:
        print(f"{TAG} undecided: {exc.reason}; nothing was compared")
        return EXIT_UNDECIDED
    return _report(remote, shas, verdicts)


def main() -> int:
    """최상위 실행기 — 내부 오류가 판정으로 새지 않게 한다.

    잡히지 않은 예외는 파이썬이 exit 1로 내보내는데, 그건 uv 실패와 겹치고 날것의
    traceback이 에이전트 컨텍스트로 쏟아진다. 예외 문구는 서브프로세스 유래 텍스트를
    담을 수 있으므로 **타입명만** 싣는다.

    Returns:
        종료 코드. 암묵적 None(→ exit 0) 경로가 없다.
    """
    try:
        return _check(sys.argv[1:])
    except Exception as exc:  # noqa: BLE001 — 사망 부류 방어가 설계 요구사항
        print(f"{TAG} internal error ({type(exc).__name__})", file=sys.stderr)
        return EXIT_UNDECIDED
