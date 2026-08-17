# 실행 후 git 그래프 연산으로 보호 브랜치 위반·헤더 위반을 정확히 적발하는 PostToolUse hook
"""commit-backstop: 실행이 끝난 뒤 git 저장소의 실제 상태로 커밋 규율을 판정한다.

commit_guard(PreToolUse)는 명령 텍스트를 추론하는 best-effort 예방이고, 이
모듈은 그 뒤의 정확한 안전망이다(#52). 텍스트/reflog 추론 없이 커밋 그래프
집합 연산만 사용한다.

주장하는 것:
    - 보호 브랜치(main/master)는 원격 main/master에 이미 존재하는 커밋으로만
      전진할 수 있다. `<직전>..<현재>` 중 원격 main/master에 없는 커밋이 있으면
      경로 불문(직접 commit, 로컬 merge, cherry-pick, plumbing) 위반으로 보고한다.
      보고는 사실(양쪽 tip + 위반 SHA 전부)과 오너 라우팅뿐이며 복구 절차는
      싣지 않는다 — 절차는 commit-backstop 규칙 파일이 보유한다.
    - HEAD에 새로 도달한 미공개(non-merge) 커밋의 제목은 Conventional Commits를
      따라야 한다. 위반은 처리 지시와 함께 보고한다(브랜치 보고가 동반되면
      이력 수정 지시는 한 블록에 하나만 남기고 라우팅으로 대체한다).
    - 같은 위반은 보통 한 번만 보고한다 — 브랜치 위반은 기록 tip으로,
      헤더 위반은 `checked` SHA 목록으로 중복을 제거한다. 재보고가 일어나는
      경로는 아래 비주장 목록에 있다.

주장하지 않는 것 (v1 설계상 수용한 한계):
    - remote가 없는 저장소는 전체 스킵한다(PR이 불가능한 스크래치/로컬 전용
      저장소는 주장 밖).
    - remote는 있으나 원격 main이 아직 없는 부트스트랩 저장소는 main 전진이
      적발된다(ATOM_COMMIT_OVERRIDE=1로 우회).
    - 커밋과 push가 한 명령에 묶이면(`git commit && git push origin main`)
      push가 원격 tip을 먼저 갱신해 놓칠 수 있다 — 이 계층은 서버 브랜치
      보호의 몫이다(평범한 단일 라인 형태는 commit_guard가 실행 전에 차단).
    - payload cwd 밖 저장소(`git -C`, 서브모듈→부모)는 cwd가 그 저장소로
      돌아온 뒤에야 지연 적발된다.
    - ref를 처음 관찰한 시점 이전의 커밋은 판정하지 않는다(기록만).
    - 오래된 미push 커밋이 HEAD로 들어오면(checkout·merge·rebase·cherry-pick
      불문) 그 커밋이 헤더 보고를 유발한다 — 이 명령이 만든 커밋이 아니어도.
      수용한 트레이드오프다(#52, PR #54: checked dedup으로 ts 필터 대체).
      훅은 작성 시점을 판정하지 않는다 — 보고문이 호출자에게 "방금 실행된
      명령이 이 커밋을 만들었는가 + 그 커밋이 HEAD인가"를 묻고, 둘 다
      성립할 때만 수정을 허용한다.
    - 헤더 위반의 1회성은 `checked`(SHA 목록)에 의존하므로 CHECKED_CAP을
      넘겨 밀려나거나 rebase·amend로 같은 논리적 커밋이 새 SHA를 얻으면
      다시 보고된다.
    - 상태 쓰기가 실패하면 워터마크가 전진하지 못해 1회 보고를 보증할 수 없다.
      그때는 판정을 집행하지 않고 로그 채널로 강등해 유예하며, 쓰기가 가능해지면
      다시 보고한다(#115).
    - 유예된 판정은 그 사이 push가 성공하면 영구히 소멸한다 — 모델이 보고를 보지
      못하므로 아무도 막지 않는다. 두 lane의 창이 다르다. 브랜치 lane은 원격
      main/master만 제외 집합에 넣으므로 main push라야 녹고, 그건 그 자체가 보고
      대상인 규칙 위반이며 서버 브랜치 보호가 받는 층이다(위 commit+push 항목과
      동일). 헤더 lane은 `--not --remotes`로 제외하므로 **어떤 원격 ref로든**
      녹는다 — 이 저장소가 요구하는 정상 작업인 feature 브랜치 push가 방아쇠이고,
      커밋 제목 규격을 검사하는 서버 계층은 없다. 막으려면 유예한 판정을 기억해야
      하는데 그 기억 장치가 바로 실패한 상태 파일이므로, 헤더 lane의 이 소멸은
      이 훅이 방어하는 범위 **밖**이다 — 선언된 경계다(PR #118 오너 결정).
      두 lane 모두, 창은 `.git`은 쓸 수 있고 상태 파일만 못 쓰는 구성에서만
      열린다. `.git` 자체를 못 쓰면 remote-tracking ref 갱신도 실패해 판정이
      제외 집합에 걸리지 않고 살아남는다.
    - 위반 없이 상태 쓰기만 실패하면 침묵한다. 매 호출 알리면 항시 노이즈가
      되므로 판정이 억제된 순간에만 알린다.
    - 상태 파일을 **읽을 수 없으면**(경로가 디렉터리 등) `_load_state`가 최초
      실행을 돌려주므로 훅은 영구히 기록만 하고 아무것도 적발하지 않는다. 이
      퇴화는 조용하며 이 모듈이 해결하지 않는다.
    - 평가에 실패한 구간(git 오류·timeout, 직전 tip 소멸)은 두 lane 모두 tip을
      그대로 전진시키므로 다시 검사되지 않는다. 경고만 남는다 — 붙들고 재시도하면
      영구 실패에서 매 호출 같은 실패를 반복해 훅이 세션을 마비시킨다.
    - 보고와 평가 실패 경고가 같은 실행에서 나면 stderr에는 보고만 나간다 —
      "42 = 확인된 위반" 채널에 "검사 안 됨"을 섞지 않기로 한 #52 결정 때문이다.
      그 경고는 원장의 `degraded` 줄로 보존된다.
    - 상태 저장이 계속 실패하는 동안 `degraded` 줄이 매 호출 원장에 쌓인다.
      중복 제거는 지속성 없이 불가능하다. 한 호출이 한 줄이라는 뜻은 아니다 —
      평가 실패는 보호 브랜치마다, 그리고 HEAD lane에서 따로 기록되므로 한 호출이
      최대 세 줄을 낸다. `override` 줄도 사용자가 그 명령을 반복한 횟수만큼 남는다.
    - 위반 사유는 REPORT_SHA_LIMIT개까지만 붙인다. SHA는 전부 싣지만(잘라내면
      `checked`에 기록된 나머지가 다시는 호명되지 않는다) 위반이 많은
      브랜치에서는 그만큼 stderr가 길어진다.
    - remote-tracking ref를 만들지 않는 `git pull <URL>`은 오탐할 수 있다.
    - 로컬 브랜치가 비표준 원격명(예: origin/trunk)을 추적하는 구성은
      고려하지 않는다.
    - 위 두 구성(URL pull·비표준 원격명)과 원격 ref 부재는 같은 한계의 세
      얼굴이다. 훅은 로컬에 존재하는 원격 main/master ref만 제외 집합으로
      쓰므로, published 커밋이 그 밖에 있으면 보고가 난다. 훅 자신은 이를
      가려낼 수 없다 — 선언된 범위 경계다(PR #114 라운드 4 오너 결정).
      published 여부 판별은 복구 절차의 fetch 확인이 맡고(규칙 파일), 브랜치
      보고는 사실과 오너 라우팅만 내보내므로 최악 결과는 불필요한 보고
      한 번이다.
    - merge 커밋은 부모 수로 걸러져(`--no-merges`) 제목을 읽지 않는다.
      이와 별개로 git 자동 생성 제목 3종(`Revert "`, `fixup! `, `squash! `)은
      접두사 매칭으로 헤더 검사에서 면제한다 — 누가 썼는지는 보지 않는다.

보고 채널:
    stderr는 exit 42(래퍼가 2로 되매핑)일 때만 모델 컨텍스트에 주입된다.
    보호 ref 평가 실패 경고(exit 1)는 로그 전용이며 의도된 것이다 — 그 경고가
    유의미한 시나리오(gc로 직전 SHA 은폐)는 적대 모델이라 위협 모델 밖이고,
    "42 = 확인된 위반" 계약을 흐리지 않는 쪽을 택했다(#52).
    상태를 저장하지 못한 실행의 보고도 같은 로그 채널로 강등된다 — 그때는 경고를
    함께 실어 내보낸다(42를 쓰지 않으므로 같은 계약에 닿지 않는다).
    프롬프트 주입 방지를 위해 커밋 제목 원문은 절대 에코하지 않는다
    (SHA + 위반 사유만 — answer_first_reminder의 상수 출력과 같은 근거).
    차단·오버라이드·강등은 blocklog 원장(#76)에도 남는다 — 저장소 밖 경로라
    `.git`을 못 쓰게 되는 실패에서 살아남는 유일한 흔적이다. `degraded`의
    `reason`은 셋뿐이다. `state-unwritable`(판정을 냈으나 집행하지 못함),
    `branch-eval-failed`·`head-eval-failed`(평가 자체를 수행하지 못함 — 판정이
    없다). 뒤 둘을 억제된 위반으로 세면 안 된다. 원장의 `block`·`override` 줄은
    **Bash 명령 원문을 그대로 담는다**(위 비에코 방침은 stderr 채널에 대한
    것이다). `degraded` 줄은 명령을 싣지 않는다.

상태:
    `<git-common-dir>/atom-commit-backstop.json` —
    `{"seen": {ref: sha, ...}, "checked": [sha, ...]}`. HEAD는 worktree별
    `HEAD@<git-dir>` 키. 쓰기는 임시 파일 + os.replace로 원자적. 손상·스키마
    불일치는 최초 실행으로 취급한다(기록만, 적발 없음).

종료 코드:
    0 통과 / 1 비차단(로그 전용) / 42 위반(래퍼가 2로 되매핑).
    exit 1은 내부 경고만이 아니라 **집행되지 않은 확정 위반**의 강등 채널이기도
    하다 — 상태를 저장하지 못하면 1회 보고를 보증할 수 없으므로 차단하지 않는다.
    모든 내부 실패는 차단으로 새지 않는다(fail-open).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

try:
    from harness.commit_guard.guard import validate_subject
except ImportError:  # commit_guard를 제거한 자식 프로젝트: 헤더 검사만 생략
    validate_subject = None  # type: ignore[assignment]

# override 마커: 규칙 예외 선언. 명령 텍스트에 있으면 평가·보고만 생략한다
# (tip 기록은 수행 — 해당 1회만 면제되고 이후 위반은 다시 적발된다).
OVERRIDE_TOKEN = "ATOM_COMMIT_OVERRIDE=1"

# 차단 sentinel 종료 코드 — commit_guard와 동일한 계약(#31 참고).
EXIT_BLOCK = 42

PROTECTED_BRANCHES = ("main", "master")

GIT_TIMEOUT_SECONDS = 10

STATE_FILENAME = "atom-commit-backstop.json"

# 헤더 검사 완료 커밋 기록 상한 (FIFO — 초과분은 오래된 것부터 버린다).
CHECKED_CAP = 200

# 위반 사유를 붙여 나열하는 상한 (stderr는 모델 컨텍스트에 주입되므로).
# SHA 자체는 상한 없이 전부 싣는다 — 판정한 커밋은 모두 `checked`에 기록되어
# 다시 호명되지 않으므로, 잘라내면 오너 보고가 영구히 불완전해진다.
REPORT_SHA_LIMIT = 5

# git이 자동 생성하는 제목 — 에이전트가 규격을 만족시킬 수 없거나(revert 타입
# 부재) autosquash로 PR 전에 소멸하므로 헤더 검사에서 면제한다.
EXEMPT_SUBJECT_PREFIXES = ('Revert "', "fixup! ", "squash! ")


def _run_git(cwd: str | None, *args: str) -> str | None:
    """git 하위 명령을 실행하고 stdout을 돌려준다.

    Args:
        cwd: 대상 저장소 디렉터리 (`git -C`로 전달; None이면 프로세스 cwd).
        *args: git 하위 명령과 인자.

    Returns:
        성공 시 stdout(개행 유지), 실패(비 0 종료·git 부재·timeout)는 None.
    """
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


def _repo_dirs(cwd: str | None) -> tuple[str, str] | None:
    """저장소의 공용 .git 디렉터리와 worktree별 git 디렉터리를 해석한다.

    `--path-format=absolute`로 절대 경로를 직접 받고, symlink 경유 cwd와
    실경로 cwd가 같은 상태 키로 수렴하도록 realpath로 정규화한다.

    Args:
        cwd: hook 페이로드의 Bash 작업 디렉터리.

    Returns:
        (git-common-dir, git-dir) 절대 경로. 저장소가 아니면 None.
    """
    out = _run_git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir")
    if out is None:
        return None
    lines = out.splitlines()
    if len(lines) < 2 or not lines[0].strip() or not lines[1].strip():
        return None
    return os.path.realpath(lines[0].strip()), os.path.realpath(lines[1].strip())


def _rev_parse(cwd: str | None, ref: str) -> str | None:
    """ref의 커밋 SHA를 조회한다. 부재·실패는 None."""
    out = _run_git(cwd, "rev-parse", "--verify", "--quiet", ref)
    if out is None:
        return None
    return out.strip() or None


def _load_state(path: str) -> dict:
    """상태 파일을 읽는다. 부재·손상·스키마 불일치는 전부 최초 실행 취급.

    Args:
        path: 상태 파일 절대 경로.

    Returns:
        `{"seen": dict[str, str], "checked": list[str]}` (항상 이 스키마).
    """
    fresh: dict = {"seen": {}, "checked": []}
    try:
        with open(path, encoding="utf-8") as fp:
            raw = json.load(fp)
    except (OSError, ValueError):
        return fresh
    if not isinstance(raw, dict):
        return fresh
    seen = raw.get("seen")
    checked = raw.get("checked")
    if not isinstance(seen, dict) or not isinstance(checked, list):
        return fresh
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in seen.items()):
        return fresh
    if not all(isinstance(sha, str) for sha in checked):
        return fresh
    return {"seen": seen, "checked": checked}


def _store_state(path: str, state: dict) -> bool:
    """상태를 원자적으로 쓴다(임시 파일 + os.replace). 실패는 호출자에게 알린다.

    쓰기 도중 중단으로 반쪽짜리 파일이 남으면 다음 실행이 최초 실행으로
    오인해 위반 tip을 조용히 기록하므로, 원자성은 적발 누락 방지 요건이다.

    실패를 삼키지 않고 돌려주는 이유: 지속에 실패하면 워터마크가 전진하지 못해
    "같은 위반은 한 번만 보고한다"를 보증할 수 없고, 그 상태로 차단하면 무관한
    명령까지 매 호출 오염된다(#115). 호출자가 채널을 강등하는 근거다.

    Args:
        path: 상태 파일 절대 경로.
        state: 저장할 상태 딕셔너리.

    Returns:
        지속에 성공하면 True, `OSError`로 실패하면 False.
    """
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
        return False
    return True


def _moved_refs(
    current: dict[str, str], seen: dict[str, str]
) -> dict[str, tuple[str | None, str]]:
    """현재 tip과 기록 tip을 비교해 처리 대상을 산출한다 (순수 함수).

    Args:
        current: 이번 실행에서 관찰한 `{ref 키: sha}`.
        seen: 상태 파일에 기록된 `{ref 키: sha}`.

    Returns:
        `{ref 키: (직전 sha | None, 현재 sha)}` — 값이 달라진 키만.
        직전 sha가 None이면 최초 관찰(기록만 하고 절대 적발하지 않음).
    """
    return {
        key: (seen.get(key), sha)
        for key, sha in current.items()
        if seen.get(key) != sha
    }


def _remote_names(cwd: str | None) -> list[str] | None:
    """등록된 remote 이름 목록. 실패는 None (fail-open 스킵용 구분)."""
    out = _run_git(cwd, "remote")
    if out is None:
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


def _exclusion_refs(cwd: str | None, remotes: list[str]) -> list[str]:
    """보호 브랜치 판정의 제외 ref를 명시적으로 조립한다.

    glob(`refs/remotes/*/main`)은 와일드카드가 `/`를 관통하면 `origin/backup/main`
    같은 브랜치까지 제외해 push-후-merge 구멍이 재개방되므로, remote 목록에서
    존재 확인된 원격 main/master만 정확히 나열한다.
    """
    refs = []
    for remote in remotes:
        for branch in PROTECTED_BRANCHES:
            ref = f"refs/remotes/{remote}/{branch}"
            if _rev_parse(cwd, ref) is not None:
                refs.append(ref)
    return refs


def _offending_commits(
    cwd: str | None, old: str, new: str, exclusions: list[str]
) -> list[str] | None:
    """`old..new` 중 원격 main/master에 없는 커밋 SHA 목록. 실패는 None."""
    args = ["rev-list", f"{old}..{new}"]
    if exclusions:
        args += ["--not", *exclusions]
    out = _run_git(cwd, *args)
    if out is None:
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


def _new_head_commits(
    cwd: str | None, old: str, new: str
) -> list[tuple[str, str]] | None:
    """HEAD에 새로 도달한 미공개 non-merge 커밋의 (SHA, 제목) 목록.

    `--no-commit-header` + 탭 구분 포맷 필수 — 기본 포맷은 `commit <sha>` 헤더
    라인이 끼어들어 제목이 `commit `으로 시작하면 오파싱된다. 실패는 None.
    """
    out = _run_git(
        cwd,
        "rev-list",
        f"{old}..{new}",
        "--no-merges",
        "--not",
        "--remotes",
        "--no-commit-header",
        "--format=%H%x09%s",
    )
    if out is None:
        return None
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        commits.append((sha.strip(), subject))
    return commits


def _branch_report(
    branch: str, old: str, new: str, offending: list[str], has_remote_ref: bool
) -> str:
    """보호 브랜치 위반 보고문 — 사실 진술과 오너 라우팅뿐이다.

    복구 절차(rescue 브랜치 → `branch -f` → PR)는 여기서 지시하지 않고
    commit-backstop 규칙 파일이 보유한다. 절차를 stderr에 실으면 그 문장이
    저장소 상태에 대한 전제를 지게 되고, 훅의 로컬 시야가 불완전한 구성(원격
    ref 부재 등)에서 정당한 전진을 되감으라는 지시가 된다 — PR #114
    라운드 1-4에서 반복 실측.
    보호 브랜치 이력 수술은 plan-deviation 원칙상 오너 결정이기도 하다.

    SHA는 상한 없이 전부 싣는다 — 판정한 커밋은 전부 `seen`에 반영되어 다시
    호명되지 않으므로, 잘라내면 오너 보고가 영구히 불완전해진다.

    원격 ref 부재는 사실로만 알린다 — 정당성도, 무엇이 제외됐는지도 주장하지
    않는다. 이 상태가 곧 오탐이라는 뜻이 아니며(부트스트랩은 이 모듈이
    의도적으로 적발하는 대상이다), 판별은 오너가 규칙 파일의 fetch 확인으로
    한다. ref가 존재하면 이 사실 자체가 성립하지 않으므로 싣지 않는다.

    Args:
        branch: 위반이 난 보호 브랜치명.
        old: 직전 관찰 tip.
        new: 현재 tip.
        offending: 원격에 없는 커밋 SHA 목록.
        has_remote_ref: 이 브랜치의 원격 ref가 로컬에 존재하는지.

    Returns:
        stderr에 실을 보고문.
    """
    caveat = (
        ""
        if has_remote_ref
        else (
            f" No remote '{branch}' exists here; include that when you report."
        )
    )
    return "\n".join(
        [
            f"[commit-backstop] {len(offending)} commit(s) not present on any "
            f"remote '{branch}' landed on local '{branch}': "
            f"{' '.join(sha[:12] for sha in offending)}",
            f"  '{branch}' moved {old} -> {new}",
            f"Do NOT rewrite or move '{branch}' yourself. Give the owner the "
            "SHAs above in your next reply, and do not push until they "
            f"decide.{caveat} Recovery steps, if the owner asks for them, are "
            "in meta/rules/commit-backstop.md (which also carries this hook's "
            f"non-claims and the {OVERRIDE_TOKEN} escape).",
        ]
    )


def _header_report(problems: list[tuple[str, str]], prescribe: bool = True) -> str:
    """헤더 위반 보고문 — 제목 원문은 에코하지 않는다(SHA + 사유만).

    지시는 작성 시점을 훅이 판정하지 않는다. 어떤 git 술어도 "이 커밋을
    누가 언제 만들었나"를 건전하게 답하지 못한다 — committer date는
    rebase·cherry-pick이 갱신하고, `old` 조상 판정은 fast-forward checkout에서
    뒤집힌다. 대신 호출자가 확실히 아는 사실 하나에 기댄다. 방금 실행된 Bash
    명령은 호출자가 직접 낸 것이므로, 그 명령이 커밋을 만들었는지 아니면
    HEAD만 옮겼는지는 읽으면 안다. 판정이 서지 않으면 재작성을 금지한다.

    상한을 넘은 위반도 SHA만은 전부 싣는다 — 잘라내면 `checked`에 기록된
    나머지가 다시는 호명되지 않아 오너 보고가 불완전해진다.

    `prescribe=False`는 같은 실행에서 브랜치 보고가 함께 나갈 때 쓴다. 훅은
    amend 대상이 브랜치 위반과 얽혀 있는지(보호 브랜치 tip인지, 방금 라우팅한
    SHA인지) 판별하지 않는다. 판별하지 못하면 지시하지 않는다는 이 레인의
    원칙 그대로, 동반 발화 시 라우팅만 한다.

    Args:
        problems: (SHA, 위반 사유) 목록.
        prescribe: 수정 지시를 실을지. 브랜치 보고와 동반될 때 False.

    Returns:
        stderr에 실을 보고문.
    """
    lines = [
        f"[commit-backstop] {len(problems)} commit(s) newly reachable from HEAD "
        "violate the Conventional Commits header rule — they may predate this "
        "command:"
    ]
    for sha, problem in problems[:REPORT_SHA_LIMIT]:
        lines.append(f"  - {sha[:12]}: {problem}")
    if len(problems) > REPORT_SHA_LIMIT:
        rest = " ".join(sha[:12] for sha, _ in problems[REPORT_SHA_LIMIT:])
        lines.append(f"  - also ({len(problems)} total): {rest}")
    if prescribe:
        lines.append(
            "A listed commit is yours to fix only if the Bash command that "
            "just ran created it and it is HEAD (`git rev-parse HEAD`) — you "
            "issued that command, so read it. Fix that one with "
            "`git commit --amend`. Every other listed SHA stays as it is, "
            "including one this command created that is no longer HEAD: do "
            "NOT rewrite history — give the owner those SHAs in your next "
            "reply, and do not push this branch or open a PR until they decide."
        )
    else:
        lines.append(
            "Do NOT rewrite these yourself — the protected-branch report above "
            "already routes this to the owner; include these SHAs in the same "
            "report."
        )
    lines.append(
        "That command already ran and was not undone; do not re-run it. See "
        f"meta/rules/commit-backstop.md (claims, non-claims, and the "
        f"{OVERRIDE_TOKEN} escape) and meta/rules/commit-discipline.md."
    )
    return "\n".join(lines)


def _log(**kwargs) -> None:
    """이벤트 원장에 한 줄을 남긴다 — 절대 제어 흐름에 영향을 주지 않는다.

    맨몸 호출을 금지하는 이유가 둘이다. (1) 인자 불일치 TypeError는 record_block
    본문 진입 **전에** 나므로 그 안의 방어가 못 잡고, 예외가 main() 밖으로 나가면
    run()이 1을 반환해 **차단이 통과로 강등된다**(래퍼는 42만 2로 되매핑한다).
    (2) 모듈 최상단 import면 blocklog가 import 불가능해질 때(자식 프로젝트가 이
    모듈을 제거·수정한 경우) 훅 자체가 죽어 적발 기능이 통째로 사라진다.

    대가: 예외를 삼키므로 호출부 키워드 오타가 조용한 무기록이 된다. 그래서
    호출부 5곳을 각각 확인하는 테스트는 선택이 아니라 이 설계의 필수 조건이다.
    """
    try:
        from harness.blocklog.blocklog import record_block

        record_block(**kwargs)
    except Exception:  # noqa: BLE001 — fail-open이 설계 요구사항
        pass


def _degraded_notice(state_path: str) -> str:
    """지속 실패로 판정을 집행하지 못했음을 알리는 고지문.

    로그 채널(exit 1)로만 나가므로 모델 컨텍스트에 주입되지 않는다. 명령 텍스트도
    커밋 제목도 싣지 않는다 — 상태 경로와 사실 진술뿐이다.

    Args:
        state_path: 저장에 실패한 상태 파일 경로.

    Returns:
        stderr에 실을 고지문.
    """
    return "\n".join(
        [
            f"[commit-backstop] state could not be persisted at {state_path}; "
            "the report(s) below were NOT enforced (log only).",
            "  Reporting once cannot be guaranteed while the write fails, so "
            "this verdict is held - it is reported again once the state file "
            "becomes writable.",
        ]
    )


def main() -> int:
    """stdin의 PostToolUse JSON을 판정한다.

    Returns:
        종료 코드 (0 통과, 1 비차단 — 내부 경고 또는 집행되지 않은 강등 보고,
        42 위반 — 래퍼가 2로 되매핑).
    """
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        print("[commit-backstop] malformed hook input (fail-open)", file=sys.stderr)
        return 1
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return 0
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = None
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        session_id = None
    override = OVERRIDE_TOKEN in command

    dirs = _repo_dirs(cwd)
    if dirs is None:
        return 0
    common_dir, git_dir = dirs

    current: dict[str, str] = {}
    for branch in PROTECTED_BRANCHES:
        sha = _rev_parse(cwd, f"refs/heads/{branch}")
        if sha is not None:
            current[f"refs/heads/{branch}"] = sha
    head_key = f"HEAD@{git_dir}"
    head_sha = _rev_parse(cwd, "HEAD")
    if head_sha is not None:
        current[head_key] = head_sha

    state_path = os.path.join(common_dir, STATE_FILENAME)
    state = _load_state(state_path)
    moved = _moved_refs(current, state["seen"])
    if not moved:
        return 0

    # remote 확인은 기록·평가 전부에 선행한다: remote 없는 저장소는 주장 밖.
    remotes = _remote_names(cwd)
    if remotes is None:
        print("[commit-backstop] `git remote` failed (fail-open)", file=sys.stderr)
        return 1
    if not remotes:
        return 0

    branch_reports: list[str] = []
    header: str | None = None
    warnings: list[str] = []
    newly_checked: list[str] = []
    if override:
        _log(
            event="override",
            harness="commit-backstop",
            reason=None,
            command=command,
            cwd=cwd,
            session_id=session_id,
        )
    if not override:
        exclusions: list[str] | None = None
        for branch in PROTECTED_BRANCHES:
            ref = f"refs/heads/{branch}"
            if ref not in moved:
                continue
            old, new = moved[ref]
            if old is None:
                continue  # 최초 관찰: 기록만
            if exclusions is None:
                exclusions = _exclusion_refs(cwd, remotes)
            offending = _offending_commits(cwd, old, new, exclusions)
            if offending is None:
                warnings.append(
                    f"[commit-backstop] '{branch}' advanced but the previous tip "
                    "is unreachable or git failed - this advance was NOT checked"
                )
                # 경고를 append하는 이 자리에서 기록한다. main() 꼬리에 두면
                # 보고가 동반될 때 stderr에서 버려지며 흔적도 함께 사라진다.
                _log(
                    event="degraded",
                    harness="commit-backstop",
                    reason="branch-eval-failed",
                    command=None,
                    cwd=cwd,
                    session_id=session_id,
                )
            elif offending:
                has_ref = any(ref.endswith(f"/{branch}") for ref in exclusions)
                branch_reports.append(
                    _branch_report(branch, old, new, offending, has_ref)
                )
        if head_key in moved and validate_subject is not None:
            old, new = moved[head_key]
            if old is not None:
                commits = _new_head_commits(cwd, old, new)
                if commits is not None:
                    known = set(state["checked"])
                    problems: list[tuple[str, str]] = []
                    for sha, subject in commits:
                        if sha in known:
                            continue
                        known.add(sha)
                        newly_checked.append(sha)
                        if subject.startswith(EXEMPT_SUBJECT_PREFIXES):
                            continue
                        problem = validate_subject(subject)
                        if problem is not None:
                            problems.append((sha, problem))
                    if problems:
                        # 동반 발화 시 헤더 레인은 라우팅만 한다 — 근거는
                        # _header_report docstring.
                        header = _header_report(problems, not branch_reports)
                else:
                    # 브랜치 레인과 같은 형식으로 알린다. 이 레인만 침묵하면
                    # 검사되지 않은 전진이 아무 흔적도 남기지 않는다.
                    warnings.append(
                        "[commit-backstop] HEAD advanced but the previous tip "
                        "is unreachable or git failed - the header check was "
                        "NOT run for this advance"
                    )
                    _log(
                        event="degraded",
                        harness="commit-backstop",
                        reason="head-eval-failed",
                        command=None,
                        cwd=cwd,
                        session_id=session_id,
                    )

    seen = dict(state["seen"])
    seen.update({key: new for key, (_, new) in moved.items()})
    checked = (state["checked"] + newly_checked)[-CHECKED_CAP:]
    persisted = _store_state(state_path, {"seen": seen, "checked": checked})

    reports = list(branch_reports)
    if header is not None:
        reports.append(header)

    if reports and not persisted:
        # 지속 실패: 1회 보고를 보증할 수 없으므로 집행하지 않고 유예한다.
        # 경고도 같은 로그 채널로 함께 내보낸다 — 42를 쓰지 않으므로
        # "42 = 확인된 위반" 계약(#52)에 닿지 않는다.
        _log(
            event="degraded",
            harness="commit-backstop",
            reason="state-unwritable",
            command=None,
            cwd=cwd,
            session_id=session_id,
        )
        print(
            "\n".join([_degraded_notice(state_path), *reports, *warnings]),
            file=sys.stderr,
        )
        return 1
    if reports:
        _log(
            event="block",
            harness="commit-backstop",
            reason=(
                "protected-branch+header"
                if branch_reports and header is not None
                else "protected-branch"
                if branch_reports
                else "header"
            ),
            command=command,
            cwd=cwd,
            session_id=session_id,
        )
        print("\n".join(reports), file=sys.stderr)
        return EXIT_BLOCK
    if warnings:
        # 로그 전용: exit 1의 stderr는 모델에 주입되지 않는다(의도됨 — #52).
        # 지속 여부와 무관하게 내보낸다 — 강등이 현행 경고를 삼키면 안 된다.
        print("\n".join(warnings), file=sys.stderr)
        return 1
    return 0


def run() -> int:
    """최상위 방어 실행기: 어떤 내부 오류도 차단으로 새지 않게 한다.

    Returns:
        종료 코드 (내부 오류 시 1 — 비차단).
    """
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 — fail-open이 설계 요구사항
        print(f"[commit-backstop] internal error (fail-open): {exc}", file=sys.stderr)
        return 1
