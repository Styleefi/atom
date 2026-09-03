# 보고된 커밋이 원격 main/master에 실제로 있는지 갓 fetch한 상태로 판정하는 오너 실행 도구
"""commit-publication: 보고된 커밋의 발행 여부를 원격에 직접 물어 판정한다 (#116).

commit_backstop 훅은 **로컬에 존재하는** 원격 main/master ref만 제외 집합으로 쓰므로,
그 ref가 없거나 낡은 구성(`--single-branch`·pruned clone, `git pull <URL>`)에서는 이미
발행된 커밋도 보고한다. 그 판별을 규칙 산문이 아니라 실행 파일이 수행하게 한다 — 산문
6줄이 결함 4개를 실었고, 그 부류는 규약이 테스트로 옮겨갈 때 닫힌다(review-loop).

주장하는 것:
    - 나열된 SHA 각각이 지정된 remote의 `refs/heads/main`·`refs/heads/master`에 대해
      조상인지를, ls-remote로 고정한 tip과 비교해 판정한다.
    - 판정은 **fetch 시점의 원격 상태**에 대한 것이다. "훅이 틀렸다"는 판정하지 않는다 —
      보고와 fetch 사이의 push를 어떤 git 술어도 구분하지 못한다.
    - 저장소 이력에 대해 아무것도 지시하지 않는다. 어떤 결과가 나오든 오너 보고 의무는
      남는다(원래 산문의 첫 결함이 그런 조언이었고 자율 탈출구로 기능했다).

주장하지 않는 것:
    - **훅의 술어와 같지 않고 의도적으로 더 좁다.** 훅은 등록된 모든 remote의 main/master를
      제외 집합에 넣고, 이 도구는 하나만 본다. 차이는 안전한 방향으로만 작용한다 — not-on을
      과다 보고할 수는 있어도 훅이 옳았던 자리에서 면죄하지는 않는다.
    - **판정 대상은 cwd의 저장소다.** 훅은 payload cwd 기준으로 보고하므로, 서브모듈이나
      다른 worktree의 보고를 저장소 루트에서 확인하면 다른 저장소에 대해 답한다.
    - **ls-remote와 fetch 사이의 경쟁은 양방향이다.** 그 사이 push가 들어오면 not-on으로
      보고되고(안전), 원격 main이 **되감기면** 로컬에 이미 있던 옛 tip이 pin이 되어 on으로
      보고된다(위험).
    - **fetch의 추적 ref 갱신은 훅의 사각지대를 치유하지 않는다.** 설정된 refspec이 일치할
      때만 일어나므로 `--single-branch`/pruned 클론과 URL 형태에서는 아무 ref도 만들지
      않는다. 일치할 때는 forced 갱신이라 추적 ref를 되감을 수도 있고, 그러면 훅이 전에
      제외하던 커밋을 새로 보고하거나 유예된 판정이 push 없이 녹을 수 있다.
    - hex 이름 ref가 나열된 SHA의 접두사와 정확히 같으면 그 SHA는 **판정 불가**가 된다.
      `rev-parse`가 ref를 먼저 보므로 객체를 되찾을 방법이 없어, 면죄가 아니라 판정 불가
      쪽으로 떨어뜨린다.
    - fetch가 해석 가능하던 축약을 모호하게 만들 수 있다(그 SHA는 판정 불가가 된다).
    - 도구가 남기는 것은 fetch가 refspec에 따라 갱신하는 추적 ref뿐이다. `FETCH_HEAD`는
      쓰지 않고, 타임아웃은 SIGTERM 유예를 두어 git이 lock 파일을 치울 기회를 준다.
    - 카운트는 argv 항목 기준이다. 같은 커밋을 축약형과 전체 SHA로 두 번 넘기면 둘로 센다.
    - 자식 프로젝트가 이 하네스를 제거하면 규칙이 존재하지 않는 명령을 인용하게 된다.

출력 채널:
    git의 stderr는 **어떤 경로로도 에코하지 않는다.** 근거가 둘이고 로컬 쪽이 더 강하다 —
    서버가 제어하는 `remote:` 줄이 실리고(원격), `merge-base`는 모호한 축약에서 커밋
    제목을 덤프한다(로컬). 후자는 commit_backstop이 절대 에코하지 않기로 한 바로 그
    텍스트다. 원격이 보고한 ref 이름도 인쇄하지 않으며, `--remote`에 URL을 받으면 URL
    자체를 인쇄하지 않는다(자격 증명을 담을 수 있고 출력은 에이전트 컨텍스트로 들어간다).

종료 코드:
    5 하나 이상 not on / 4 전부 on / 3 판정 불가 / 2 호출자 잘못 /
    1 도구가 돌지 못함(uv·import 실패 — 의도적으로 내지 않는다).

    계약은 한 문장이다 — **4와 5만 모든 SHA에 대해 답한 것이고, 나머지는 답이 아니다.**
    실질 판정을 1이 아니라 5에 둔 이유는 파이썬이 잡히지 않은 예외에서 1을, uv가 실패 시
    1이나 2를 내기 때문이다(docs/design.md의 sentinel 42와 같은 근거). 1에 두면 도구가
    죽은 것과 진짜 전진이 구분되지 않고, 뒤쪽 해석이 보호 브랜치 `branch -f`로 이어진다.

    2와 3의 경계: **2는 에이전트가 스스로 고칠 수 있고 3은 못 고친다.** 섞으면 오타가
    "환경 문제"로 오너에게 보고된다.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path

TAG = "[commit-publication]"

EXIT_TOOL_FAILED = 1
EXIT_CALLER = 2
EXIT_UNDECIDED = 3
EXIT_ALL_ON = 4
EXIT_SOME_NOT_ON = 5

# 훅과 같은 브랜치 이름을 본다. 이 튜플이 backstop.PROTECTED_BRANCHES와 어긋나면
# 네 번째 결함(GitFlow 면죄)이 다시 열리므로 테스트가 두 값을 결속한다.
PROTECTED_BRANCHES = ("main", "master")

# 네트워크 호출은 실제 전송을 하므로 로컬 호출과 상한이 다르다. 단일 상수는 한쪽을
# 반드시 잘못 맞춘다.
NETWORK_TIMEOUT_SECONDS = 60
LOCAL_TIMEOUT_SECONDS = 10

# 타임아웃 시 SIGTERM 을 먼저 보내고 이만큼 기다린 뒤에야 SIGKILL 한다. git 은 SIGTERM
# 에서 *.lock 과 임시 packfile 을 지우지만 SIGKILL 은 잡지 못하고, 그러면 오너의 다음
# git 명령이 이유를 알 수 없는 "Unable to create '...lock'" 으로 죽는다 — 이 도구는
# git 의 stderr 를 지워 놓으므로 그 원인을 아무도 설명해 주지 않는다.
TERM_GRACE_SECONDS = 3

# 보고문이 12자 축약을 싣고, 오너가 손으로 전체 SHA를 넘길 수도 있다. 상한 64는
# SHA-256 저장소를 배제하지 않기 위한 것이다.
SHA_RE = re.compile(r"^[0-9a-f]{4,64}$")
PIN_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")

# URL 형태를 인쇄할 때 쓰는 고정 문구. URL은 자격 증명을 담을 수 있다.
URL_TARGET_LABEL = "the remote given on the command line"

_USAGE = (
    f"{TAG} usage: python -m harness.commit_publication "
    "[--remote <name|url>] <sha>..."
)


class _Undecided(Exception):
    """판정 불가 — 이유 한 줄을 들고 exit 3으로 나간다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _CallerError(Exception):
    """호출자가 고칠 수 있는 잘못 — 이유 한 줄을 들고 exit 2로 나간다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _isolated_env() -> dict[str, str]:
    """자격 증명 프롬프트 채널을 닫은 환경을 만든다.

    각 항목이 막는 것이 다르다. `GIT_TERMINAL_PROMPT`는 git 자신의 tty 프롬프트만 막고
    자격 증명 helper는 막지 못하며, helper는 `-c credential.helper=`(러너가 argv로
    넣는다)라야 멈춘다 — osxkeychain·libsecret·git-credential-manager가 전부 그 채널이고
    마지막 것은 브라우저를 열어 분 단위로 대기한다. askpass 변수들은 GUI 경로를 막는다.

    `SSH_ASKPASS_REQUIRE`는 `never`가 아니라 `force`다 — `never`는 "제어 tty가 없다"에
    기대지만 `force`는 tty 유무와 무관하게 askpass를 쓰게 만들고(man ssh), `/bin/false`와
    짝지으면 passphrase도 미지 호스트 확인도 결정적으로 즉시 실패한다.

    `GIT_SSH_COMMAND`는 **건드리지 않는다.** ssh는 첫 `-o` 값을 취하므로 덧붙이기가 통하지
    않고, 무조건 설정하면 `core.sshCommand`에 커스텀 키·프록시를 잡아 둔 오너의 fetch를
    깨뜨려 도구가 만든 회귀를 "환경 문제"로 보고하게 된다. 대신 stdin 차단과 새 세션,
    그리고 타임아웃이 ssh 경로를 유계로 만든다 — agent에 없는 passphrase 키나 처음 보는
    호스트는 타임아웃까지 기다린 뒤 판정 불가가 된다.

    Returns:
        자식 프로세스에 넘길 환경 매핑.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "/bin/false"
    env["SSH_ASKPASS"] = "/bin/false"
    env["SSH_ASKPASS_REQUIRE"] = "force"
    return env


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    """자식의 프로세스 그룹에 신호를 보낸다 (전송 손자까지 닿게).

    `start_new_session=True` 로 띄웠으므로 자식이 그룹 리더이고 pid == pgid 다.
    이미 죽은 뒤면 조용히 지나간다.
    """
    try:
        os.killpg(proc.pid, sig)
    except (OSError, ProcessLookupError):
        try:
            proc.send_signal(sig)
        except (OSError, ProcessLookupError):
            pass


def run_git(args: list[str], *, timeout: int) -> tuple[int, str]:
    """git을 실행하고 (종료 코드, stdout)을 돌려준다.

    모든 git 호출의 유일한 이음매다. `subprocess.run`을 쓰지 않는 이유는 타임아웃 시
    직접 자식만 죽이고 pid를 노출하지 않아, 전송 손자(`ssh`/`git-remote-https`)가 살아남아
    `.git/objects`에 계속 쓰기 때문이다. 새 세션으로 띄우고 프로세스 그룹째 죽인다.

    **stderr는 돌려주지 않는다** — 모듈 docstring의 출력 채널 항목 참조.

    Args:
        args: `git` 뒤에 붙일 인자.
        timeout: 초 단위 상한. 네트워크 호출과 로컬 호출이 다르다.

    Returns:
        `(rc, stdout)`. 타임아웃은 rc 124로 돌려준다(셸 관례; git이 내지 않는 값).
    """
    argv = ["git", "-c", "credential.helper=", *args]
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
            env=_isolated_env(),
            start_new_session=True,
        )
    except OSError:
        return 127, ""
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGTERM)
        try:
            proc.communicate(timeout=TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(proc, signal.SIGKILL)
            proc.communicate()
        return 124, ""
    return proc.returncode, out


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
        elif item.startswith("--remote="):
            remote = item.split("=", 1)[1]
        elif item.startswith("-"):
            raise _CallerError(f"unknown option: {item}")
        else:
            shas.append(item)
    if not shas:
        raise _CallerError("no commit SHAs given")
    bad = [s for s in shas if not SHA_RE.match(s)]
    if bad:
        raise _CallerError(f"not hex commit SHAs: {' '.join(bad)}")
    if remote is not None and not remote:
        raise _CallerError("--remote needs a value")
    return remote, shas


def _assert_graph_is_trustworthy() -> None:
    """로컬 커밋 그래프가 실제 조상 관계를 반영하지 않는 구성을 걸러낸다.

    불변식 하나, 사례 셋. shallow 클론은 **not-on 쪽으로** 거짓말하고(깊이 밖 조상이
    끊겨 `merge-base`가 1을 낸다) 그 "없다"가 오너를 보호 브랜치 되감기로 보낸다.
    `git replace` 객체와 `.git/info/grafts`는 **on 쪽으로**, 즉 면죄 방향으로 거짓말한다 —
    이 도구가 닫으려는 바로 그 부류다. 어느 쪽이든 네트워크 이전에 판정을 포기한다.

    중화하지 않는 이유: `--no-replace-objects`는 grafts를 막지 못해 반쪽 방어가 되고,
    훅은 중화하지 않으므로 도구만 중화하면 해석 대상과 다른 그래프를 보게 된다.
    재현과 실측은 #116 코멘트가 보유한다.

    Raises:
        _CallerError: 저장소가 아닐 때(cwd 문제는 호출자가 고친다).
        _Undecided: 그래프를 믿을 수 없을 때.
    """
    rc, out = run_git(
        ["rev-parse", "--is-shallow-repository"], timeout=LOCAL_TIMEOUT_SECONDS
    )
    if rc != 0:
        raise _CallerError("not a git repository (or git is unavailable)")
    if out.strip() == "true":
        raise _Undecided("this repository is a shallow clone")

    rc, out = run_git(["for-each-ref", "refs/replace/"], timeout=LOCAL_TIMEOUT_SECONDS)
    if rc == 0 and out.strip():
        raise _Undecided("this repository has replace refs")

    # graft 파일은 세 경로로 지정된다. `--git-path`가 기본 위치와 `GIT_GRAFT_FILE`을
    # 함께 해석해 주지만 `core.graftsFile`은 반영하지 않으므로(실측), 그쪽은 따로 묻는다.
    # 하나라도 존재하면 조상 관계가 조작돼 있을 수 있고, graft 는 면죄 방향으로 거짓말한다.
    for args in (
        ["rev-parse", "--git-path", "info/grafts"],
        ["config", "--get", "core.graftsFile"],
    ):
        rc, out = run_git(args, timeout=LOCAL_TIMEOUT_SECONDS)
        if rc == 0 and out.strip() and Path(out.strip()).exists():
            raise _Undecided("this repository has a grafts file")


def _resolve_remote(requested: str | None) -> tuple[str, str]:
    """확인할 remote와, 출력에 쓸 표기를 정한다.

    `--remote` 값은 **먼저 무조건 `git remote` 목록과 대조한다.** 오타 난 이름은
    `ls-remote`에서 접속 불가와 똑같이 128을 내므로, 목록 대조 없이는 호출자의 오타가
    "환경 문제"로 보고된다.

    Args:
        requested: `--remote` 값. 없으면 None.

    Returns:
        `(git에 넘길 값, 출력용 표기)`. URL 형태의 표기는 고정 문구다 — URL은 자격 증명을
        담을 수 있고 출력은 에이전트 컨텍스트로 들어간다.

    Raises:
        _CallerError: 기본 remote를 정할 수 없거나 이름이 등록돼 있지 않을 때.
        _Undecided: `git remote` 자체가 실패했을 때.
    """
    rc, out = run_git(["remote"], timeout=LOCAL_TIMEOUT_SECONDS)
    if rc != 0:
        raise _Undecided("`git remote` failed")
    names = [line.strip() for line in out.splitlines() if line.strip()]

    if requested is None:
        if len(names) == 1:
            return names[0], names[0]
        if "origin" in names:
            return "origin", "origin"
        raise _CallerError(
            "cannot pick a default remote; name one with --remote"
        )

    if requested in names:
        return requested, requested
    if any(ch in requested for ch in "/:@"):
        return requested, URL_TARGET_LABEL
    raise _CallerError(f"no such remote is registered: {requested}")


def _pin_tips(remote: str) -> dict[str, str]:
    """원격의 보호명 브랜치 tip을 고정한다.

    `--symref`도 `HEAD`도 쓰지 않는다. 구분되는 두 결과(빈 원격 / main·master 부재)가 같은
    판정 불가로 가고, 무엇보다 **원격이 통제하는 ref 이름이 프로세스로 들어오는 유일한
    통로**가 사라진다.

    `ls-remote`의 ref 인자는 정확 일치가 아니라 **꼬리 글롭**이라, 원격에
    `refs/heads/refs/heads/main`이 있으면 `refs/heads/main` 패턴에 함께 걸린다(`fetch`는
    정확 해석하므로 pin 출처와 fetch 출처가 갈린다). 그래서 필드를 바이트로 대조하고,
    한 보호명이 두 줄을 내면 판정하지 않는다.

    Args:
        remote: git에 넘길 remote 값.

    Returns:
        `{브랜치 이름: tip SHA}`. 존재하는 브랜치만 담긴다.

    Raises:
        _Undecided: 접속 실패, 0줄, 또는 한 이름에 두 줄 이상일 때.
    """
    patterns = [f"refs/heads/{name}" for name in PROTECTED_BRANCHES]
    rc, out = run_git(
        ["ls-remote", remote, *patterns], timeout=NETWORK_TIMEOUT_SECONDS
    )
    if rc != 0:
        raise _Undecided("could not reach the remote")

    tips: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 2:
            continue
        sha, ref = fields[0].strip(), fields[1].strip()
        for name in PROTECTED_BRANCHES:
            if ref == f"refs/heads/{name}" and PIN_RE.match(sha):
                if name in tips:
                    raise _Undecided(
                        f"the remote returned more than one {name} ref"
                    )
                tips[name] = sha
    if not tips:
        raise _Undecided("the remote has neither main nor master")
    return tips


def _usable_pins(remote: str, tips: dict[str, str]) -> dict[str, str]:
    """fetch로 객체를 받아오고, 로컬에서 도달 가능한 pin만 남긴다.

    `cat-file -e`가 확인하는 것은 **배달이 아니라 존재**다 — 이전 clone에 이미 있던 객체도
    통과한다. 실패한 pin은 즉시 중단하지 않고 사용 불가로 두며, 판정 합성이 처리한다.
    `^{commit}` peel은 필수다(없으면 blob도 rc 0을 낸다).

    Args:
        remote: git에 넘길 remote 값.
        tips: `_pin_tips`가 고정한 tip.

    Returns:
        사용 가능한 `{브랜치 이름: tip SHA}`.

    Raises:
        _Undecided: fetch가 실패했거나 남는 pin이 없을 때.
    """
    refs = [f"refs/heads/{name}" for name in tips]
    # --no-write-fetch-head: 이 도구는 FETCH_HEAD 를 읽지 않으면서 덮어쓰기만 한다.
    # 오너가 `git fetch origin some-branch` 뒤 `git merge FETCH_HEAD` 를 하려던 참에
    # 이 검사를 돌리면 엉뚱한 커밋을 머지하게 된다.
    rc, _ = run_git(
        ["fetch", "--no-tags", "--no-write-fetch-head", remote, *refs],
        timeout=NETWORK_TIMEOUT_SECONDS,
    )
    if rc != 0:
        raise _Undecided("the fetch failed; nothing was compared")

    usable = {}
    for name, tip in tips.items():
        rc, _ = run_git(
            ["cat-file", "-e", f"{tip}^{{commit}}"], timeout=LOCAL_TIMEOUT_SECONDS
        )
        if rc == 0:
            usable[name] = tip
    if not usable:
        raise _Undecided("the fetched tips did not arrive")
    return usable


def judge(sha: str, pins: dict[str, str]) -> str | None:
    """SHA 하나를 사용 가능한 pin들에 대해 판정한다.

    "있다"와 "없다"의 기준이 다르다. **있다**는 단조라 어느 pin에서든 조상 경로를 찾으면
    확정이다 — 다른 pin이 실패했는지는 무관하다. **없다**는 전칭 명제라 사용 가능한 모든
    pin이 깨끗하게 rc 1로 답해야 성립한다. 그 밖은 판정 불가다.

    종료 코드 규칙은 명령별로 다르다. `rev-parse --verify --quiet`는 미지 SHA·잘못된
    타입·모호한 축약에 전부 **rc 1**을 내고 128을 내지 않는 반면, `merge-base
    --is-ancestor`는 같은 입력에 128을 낸다. 공통 규약을 쓰면 해석 불가 SHA가 rc 1을 타고
    "not on"으로 둔갑한다.

    Args:
        sha: 판정할 커밋(축약형 가능).
        pins: 사용 가능한 `{브랜치 이름: tip SHA}`.

    Returns:
        `"on"` / `"not_on"`, 판정할 수 없으면 None.
    """
    rc, out = run_git(
        ["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
        timeout=LOCAL_TIMEOUT_SECONDS,
    )
    if rc != 0 or not out.strip():
        return None
    full = out.strip()
    # rev-parse 는 ref 를 객체 이름보다 먼저 본다. hex 이름 브랜치(예: `deadbeefdead`)가
    # 있으면 같은 접두사를 가진 진짜 커밋 대신 그 브랜치 tip 이 해석되고, 그 tip 이
    # 발행돼 있으면 미발행 커밋이 on 으로 — 면죄 방향으로 — 뒤집힌다. 해석 결과가 인자를
    # 접두사로 갖지 않으면 그건 우리가 물은 객체가 아니다.
    if not full.startswith(sha):
        return None

    saw_clean_negative = False
    for tip in pins.values():
        rc, _ = run_git(
            ["merge-base", "--is-ancestor", full, tip],
            timeout=LOCAL_TIMEOUT_SECONDS,
        )
        if rc == 0:
            return "on"
        if rc == 1:
            saw_clean_negative = True
        else:
            return None
    return "not_on" if saw_clean_negative else None


def _report(target: str, shas: list[str], verdicts: dict[str, str | None]) -> int:
    """판정 결과를 인쇄하고 종료 코드를 돌려준다.

    판정하지 못한 SHA가 하나라도 있으면 전체가 판정 불가다. `N of M` 형식은 "M 중 k개만
    판정했다"를 표현할 수 없어, 그대로 두면 판정된 적 없는 SHA를 면죄하는 인쇄된 거짓이
    된다 — 네 번째 결함과 같은 부류다. 그때도 not-on 줄은 인쇄해 보고에서 사라지지 않게
    하고, 분모는 실제 판정 수를 밝힌다.

    Args:
        target: 출력에 쓸 remote 표기.
        shas: 넘어온 순서의 SHA 목록.
        verdicts: SHA별 판정.

    Returns:
        종료 코드.
    """
    judged = [s for s in shas if verdicts[s] is not None]
    unresolved = [s for s in shas if verdicts[s] is None]
    not_on = [s for s in judged if verdicts[s] == "not_on"]

    def _verb(n: int) -> str:
        return "is" if n == 1 else "are"

    if unresolved:
        head = (
            f"{TAG} {len(judged)} of {len(shas)} listed commits were judged "
            f"as of this fetch"
        )
        if not_on:
            print(
                f"{head}; {len(not_on)} of those {len(judged)} "
                f"{_verb(len(not_on))} not on main/master at {target}: "
                f"{' '.join(not_on)}"
            )
        else:
            print(f"{head}.")
        print(
            f"  {len(unresolved)} could not be resolved: {' '.join(unresolved)}"
        )
        return EXIT_UNDECIDED

    if not_on:
        print(
            f"{TAG} {len(not_on)} of {len(shas)} listed commits "
            f"{_verb(len(not_on))} not on main/master at {target} "
            f"as of this fetch: {' '.join(not_on)}"
        )
        return EXIT_SOME_NOT_ON

    print(
        f"{TAG} {len(shas)} of {len(shas)} listed commits "
        f"{_verb(len(shas))} on main/master at {target} as of this fetch."
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
        _assert_graph_is_trustworthy()
        remote, target = _resolve_remote(requested)
        tips = _pin_tips(remote)
        pins = _usable_pins(remote, tips)
    except _CallerError as exc:
        print(f"{TAG} {exc.reason}", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return EXIT_CALLER
    except _Undecided as exc:
        print(f"{TAG} undecided: {exc.reason}; nothing was compared")
        return EXIT_UNDECIDED

    verdicts = {sha: judge(sha, pins) for sha in shas}
    return _report(target, shas, verdicts)


def main() -> int:
    """최상위 실행기 — 내부 오류가 판정으로 새지 않게 한다.

    잡히지 않은 예외를 그대로 두면 파이썬이 exit 1을 내는데, 그건 uv 실패와도 겹치고
    무엇보다 날것의 traceback이 에이전트 컨텍스트로 쏟아진다. 예외 문구(`str(exc)`)는
    서브프로세스 유래 텍스트를 담을 수 있으므로 **타입명만** 싣는다.

    Returns:
        종료 코드. 함수는 무조건 반환으로 끝나 암묵적 None(→ exit 0) 경로가 없다.
    """
    try:
        return _check(sys.argv[1:])
    except Exception as exc:  # noqa: BLE001 — 사망 부류 방어가 설계 요구사항
        print(f"{TAG} internal error ({type(exc).__name__})", file=sys.stderr)
        return EXIT_UNDECIDED
