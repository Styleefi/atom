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
    - 처음 기록되는 tip은 판정하지 않는다(기록만). 예외는 기록의 상속이다 —
      `HEAD@<git-dir>` 키는 worktree가 사라져도 남으므로, 같은 git-dir을
      다시 얻는 새 worktree의 HEAD는 첫 기록이 아니라 옛 tip을 직전 tip으로
      삼는다. 덜 판정하는 방향은 없다. #52의 오래된 미push 커밋
      트레이드오프와 같은 급으로 수용했다 — 선언된 경계다(PR #126 오너
      결정).
    - 오래된 미push 커밋이 HEAD로 들어오면(checkout·merge·rebase·cherry-pick
      불문) 그 커밋이 헤더 보고를 유발한다 — 이 명령이 만든 커밋이 아니어도.
      수용한 트레이드오프다(#52, PR #54: checked dedup으로 ts 필터 대체).
      훅은 작성 시점을 판정하지 않는다 — 보고문이 호출자에게 "방금 실행된
      명령이 이 커밋을 만들었는가 + 그 커밋이 HEAD인가"를 묻고, 둘 다
      성립할 때만 수정을 허용한다.
    - 헤더 위반의 1회성은 `checked`(SHA 목록)에 의존하므로 CHECKED_CAP을
      넘겨 밀려나거나 rebase·amend로 같은 논리적 커밋이 새 SHA를 얻으면
      다시 보고된다.
    - 기준선을 잃으면 `checked`도 함께 비므로, 이미 보고한 헤더 위반이 다시 HEAD에
      도달하면 재보고된다.
    - 상태 쓰기가 실패하면 워터마크가 전진하지 못해 1회 보고를 보증할 수 없다.
      그때는 판정을 집행하지 않고 로그 채널로 강등해 유예하며, 쓰기가 가능해지면
      다시 보고한다(#115).
    - 유예된 판정은 그 사이의 push로 소멸할 수 있고, 어떤 push가 그렇게 하는지는
      lane마다 다르다 — 모델이 보고를 보지 못하므로 아무도 막지 않는다. 브랜치
      판정은 원격 main/master만 제외 집합에 들어가므로 그쪽으로의 push라야 녹는다;
      위 commit+push 항목과 같은 층이며 서버 브랜치 보호의 몫이다(승인된 계획,
      2026-08-17). 헤더 판정은 `--not --remotes`로 제외하므로 그 커밋을 담은
      브랜치가 push되면 녹는다. 막으려면 유예한 판정을 기억해야 하는데 그 기억
      장치가 바로 실패한 상태 파일이고, 커밋 제목 규격을 받아 줄 서버 계층도
      없다 — 이 훅의 방어 밖이며 선언된 경계다(PR #118 오너 결정).
    - 위반 없이 상태 쓰기만 실패하면 침묵한다. 매 호출 알리면 항시 노이즈가
      되므로 판정이 억제된 순간에만 알린다.
    - 상태 파일이 있는데 쓸 수 있는 기준선을 못 내주면(읽기 실패·손상) `_load_state`가
      최초 실행을 돌려주므로, 그동안 훅은 적발하지 않는다. 기준선은 상태를 다시
      쓰는 데 성공한 호출에서 다시 시작한다. 그 호출은 worktree를 열거해 얻은
      HEAD들을 열거 시점의 tip으로 함께 기록하므로, 기록된 worktree의 HEAD
      기준선은 최초 관찰로 퇴행하지 않고 자기 tip에서 재시작한다(#124) — 그
      worktree가 훅을 한 번도 부르지 않았어도 같고, 기준선이 유지되는 한 그쪽
      다음 호출은 기록된 tip을 기준선으로 쓴다. 담기지 않는 경우가 있다 —
      열거 자체가 실패하면 다른 worktree 전부가, 개별 스탠자가 걸러지면(경로 줄
      부재·경로 해석 실패·prunable·HEAD 줄 부재 또는 0-SHA) 그 worktree만 —
      어느 쪽도 알리지 않는다. 빠진 worktree의 HEAD 기준선은 그쪽 다음 호출이
      최초 관찰로 기록만 한다. 이 호출 자신이 관찰한 자기 HEAD tip은 열거
      결과를 덮어 담기고, 관찰된 보호 브랜치 tip은 열거와 무관하게 담긴다.
      다시 쓰기
      전까지 잃은 구간은 계속 자란다. 판정 없이 지나간 구간의 미발행 커밋은
      이후의 검사 범위에 다시 들어오면 그때 판정된다. 저장 단계에 도달한 호출은
      stderr로 알리고, 원장에는 상태를 다시 쓰는 데 성공한 호출에서만 남긴다.
      다시 쓰지 못하는 동안은 중복 제거가 불가능해 같은 줄이 쌓이므로 원장에
      쓰지 않는다 — 선언된 경계다(#119 오너 결정).
    - 상태 파일 부재는 최초 실행과 구분하지 않는다(#119). 부재로 출발하는
      호출도 위 항목의 재시작과 같은 방식으로 worktree들의 HEAD를 담는다.
      부재 후 첫 훅 호출 전에 들어온 위반은 재수립되는 기준선에 흡수되거나
      기준선 밖에 남아 그 시점에는 판정되지 않는다 — 삭제와 위반 커밋을 한
      명령에 묶든, 훅 밖 경로로 지우든 같다. 이후 검사 범위에 다시 들어오면
      그때 판정된다. 그 뒤의 위반은 앞선 호출이 상태 다시 쓰기에 성공했고 그
      worktree의 HEAD 키가 담겼을 때 판정 범위에 들어온다(그 사이 기준선이
      다시 유실되면 위 상실 항목이 적용된다) — 선언된 경계다(#124 오너 결정).
    - 평가에 실패한 구간(git 오류·timeout, 직전 tip 소멸)은 두 lane 모두 tip을
      그대로 전진시키므로 다시 검사되지 않는다. 경고만 남는다 — 붙들고 재시도하면
      영구 실패에서 매 호출 같은 실패를 반복해 훅이 세션을 마비시킨다.
    - 보고와 평가 실패 경고가 같은 실행에서 나면 stderr에는 보고만 나간다 —
      "42 = 확인된 위반" 채널에 "검사 안 됨"을 섞지 않기로 한 #52 결정 때문이다.
      그 경고는 원장의 `degraded` 줄로 보존된다.
    - 상태 저장이 계속 실패하는 동안, 그리고 override가 반복되는 동안 원장에 줄이
      쌓인다. 중복 제거는 지속성 없이 불가능하다.
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
    - git < 2.36은 전제 밖이다 — `worktree list --porcelain -z`(2.36)가 이
      모듈이 쓰는 git 기능 바닥의 최고점이다. 버전은 검사하지 않으며,
      미만에서는 기능별로 제각기 열화하고(그 양상은 주장 밖), 2.31 미만은
      훅 전체가 조용히 무동작이다.

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
    `.git`을 못 쓰게 되는 실패에서도 살아남는 흔적이다. `degraded`의 사유 어휘는
    DEGRADED_REASONS가 보유하며 두 갈래로 나뉜다 — 판정을 내고도 집행하지 못한
    경우와, 평가 자체를 수행하지 못해 판정이 없는 경우. 뒤쪽을 억제된 위반으로
    세면 관측하려는 수치가 부풀려진다.
    원장의 `block`·`override` 줄은 **Bash 명령 원문을 그대로 담는다**(위 비에코
    방침은 stderr 채널에 대한 것이다). `degraded` 줄은 명령을 싣지 않는다.

상태:
    `<git-common-dir>/atom-commit-backstop.json` —
    `{"seen": {ref: sha, ...}, "checked": [sha, ...]}`. HEAD는 worktree별
    `HEAD@<git-dir>` 키(기준선이 빈 채 출발한 호출은 열거한 다른 worktree의
    키도 함께 쓴다 — 비주장 목록 참고). 읽지 못하거나 스키마가 어긋나면 빈
    상태로 대체하고 그 사실을 알린다 — 비주장 목록 참고.

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

# degraded 이벤트의 사유 어휘. 규칙 파일의 열거와 test_reasons_sync가 결속한다 —
# 같은 열거의 사본이 다섯 곳에서 따로 어긋난 뒤 도입했다(PR #118 리뷰 루프).
REASON_STATE_UNWRITABLE = "state-unwritable"
REASON_STATE_UNREADABLE = "state-unreadable"
REASON_STATE_CORRUPT = "state-corrupt"
REASON_BRANCH_EVAL_FAILED = "branch-eval-failed"
REASON_HEAD_EVAL_FAILED = "head-eval-failed"
DEGRADED_REASONS = (
    REASON_STATE_UNWRITABLE,
    REASON_STATE_UNREADABLE,
    REASON_STATE_CORRUPT,
    REASON_BRANCH_EVAL_FAILED,
    REASON_HEAD_EVAL_FAILED,
)

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


def _worktree_heads(cwd: str | None) -> dict[str, str]:
    """저장소의 모든 worktree에 대해 `HEAD@<git-dir>` 키와 현재 tip을 조립한다.

    기준선이 빈 채 출발한 호출(상실·부재) 전용(#124): 재시작하는 호출은 자기
    worktree의 HEAD만 관찰하므로, 함께 기록하지 않으면 다른 worktree의 HEAD
    기준선이 사라져 그쪽 다음 호출이 최초 관찰로 퇴행한다. 키는 현재 tip
    수집과 같은 `_repo_dirs` realpath 정규화로 만들어 바이트 단위로 일치한다.

    `-z`(NUL 종결, git >= 2.36)는 위생이 아니라 건전성 요건이다 — 개행 종결
    porcelain은 경로·lock reason의 개행이 가짜 `HEAD` 줄을 위조할 수 있다.
    스탠자를 경계까지 모았다가 방출하므로 `HEAD` 뒤에 오는 `prunable`(등록만
    남은 worktree — 경로가 재사용되면 stale sha로 키를 오염시킨다)을 배제할
    수 있다.

    실패는 조용히 좁힌다 — 열거 자체가 실패하면 빈 dict(이 기록 이전과 같은
    동작으로 강등), 개별 스탠자가 걸러지면 그 항목만 뺀다(걸러지는 조건의
    열거는 모듈 docstring 비주장 목록이 보유한다 — 사본이 갈리면 목록 쪽이
    거짓말이 된다). bare 스탠자는 HEAD 줄이 없어 자연 제외된다.

    Args:
        cwd: hook 페이로드의 Bash 작업 디렉터리.

    Returns:
        `{f"HEAD@{git_dir}": sha}` — 걸러진 worktree는 빠진 dict.
    """
    out = _run_git(cwd, "worktree", "list", "--porcelain", "-z")
    if out is None:
        return {}
    heads: dict[str, str] = {}
    path: str | None = None
    sha: str | None = None
    prunable = False
    for item in out.split("\0"):
        if item.startswith("worktree "):
            path = item[len("worktree "):]
        elif item.startswith("HEAD "):
            sha = item[len("HEAD "):].strip()
        elif item.startswith("prunable"):
            prunable = True
        elif not item:  # 스탠자 경계 — 방출 후 리셋
            # sha.strip("0")이 falsy면 해석 불가 HEAD(미탄생·손상 admin dir).
            if path and sha and sha.strip("0") and not prunable:
                dirs = _repo_dirs(path)
                if dirs is not None:
                    heads[f"HEAD@{dirs[1]}"] = sha
            path = sha = None
            prunable = False
    return heads


def _load_state(path: str) -> tuple[dict, str | None]:
    """상태 파일을 읽는다. 부재와 기준선 상실을 갈라 돌려준다.

    부재는 정상 최초 실행(새 클론 포함)이라 알릴 것이 없고, 파일이 있는데 쓸 수 있는
    기준선을 못 내주는 것은 그 호출이 아무것도 판정하지 못했다는 뜻이라 알려야 한다.
    둘을 한 갈래로 삼키면 후자가 전자로 위장된다(#119).

    Args:
        path: 상태 파일 절대 경로.

    Returns:
        `({"seen": dict[str, str], "checked": list[str]}, 상실 사유 | None)`.
        상태는 항상 이 스키마이며, 읽지 못했으면 빈 것으로 대체된다.
    """
    fresh: dict = {"seen": {}, "checked": []}
    try:
        with open(path, encoding="utf-8") as fp:
            raw = json.load(fp)
    except FileNotFoundError:
        return fresh, None
    except OSError:
        return fresh, REASON_STATE_UNREADABLE
    except ValueError:
        return fresh, REASON_STATE_CORRUPT
    if not isinstance(raw, dict):
        return fresh, REASON_STATE_CORRUPT
    seen = raw.get("seen")
    checked = raw.get("checked")
    if not isinstance(seen, dict) or not isinstance(checked, list):
        return fresh, REASON_STATE_CORRUPT
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in seen.items()):
        return fresh, REASON_STATE_CORRUPT
    if not all(isinstance(sha, str) for sha in checked):
        return fresh, REASON_STATE_CORRUPT
    return {"seen": seen, "checked": checked}, None


def _store_state(path: str, state: dict) -> bool:
    """상태를 임시 파일 + os.replace로 쓴다. 실패는 호출자에게 알린다.

    쓰기 도중 중단으로 반쪽짜리 파일이 남으면 다음 실행이 기준선을 잃으므로,
    원자성은 그 상실을 막는 요건이다.

    원자성은 단일 프로세스 기준이다. 임시 파일 이름이 고정(`path + ".tmp"`)이라
    훅 프로세스가 병렬로 겹치면 서로의 임시 파일에 쓰고, 상태가 손상되거나 한쪽의
    워터마크 전진이 지워질 수 있다. 고치지 않기로 한 결정이며(#119), 적어 두는
    이유는 상태 손상이 기준선 상실로 관측되기 때문이다.

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
    호출부마다 각각 확인하는 테스트는 선택이 아니라 이 설계의 필수 조건이다.
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
    state, loss = _load_state(state_path)
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
                    reason=REASON_BRANCH_EVAL_FAILED,
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
                        reason=REASON_HEAD_EVAL_FAILED,
                        command=None,
                        cwd=cwd,
                        session_id=session_id,
                    )

    seen = dict(state["seen"])
    if not state["seen"]:
        # 상실(_load_state의 모든 상실 갈래는 빈 상태를 돌려준다)·부재·삭제가
        # 전부 이 분기로 온다. 재시작이 이 호출의 시야로 좁혀지면 다른
        # worktree의 HEAD 기준선이 사라진다 — 전 worktree tip을 함께
        # 기록한다(#124). moved가 뒤에서 덮어써 이 호출 자신이 판정에 쓴
        # 관찰이 이긴다.
        seen.update(_worktree_heads(cwd))
    seen.update({key: new for key, (_, new) in moved.items()})
    checked = (state["checked"] + newly_checked)[-CHECKED_CAP:]
    persisted = _store_state(state_path, {"seen": seen, "checked": checked})

    if loss is not None:
        # 채널마다 비용이 다르다. stderr는 휘발성이라 알리고, 원장은 누적되므로
        # 다시 쓰는 데 성공해 반복이 묶일 때만 남긴다.
        # 재시작 주장은 쓰기 성공에 조건화한다 — 실패한 호출에서 "restarts"를
        # 말하면 아직 자라는 구간을 닫힌 것처럼 보고하게 된다.
        outcome = (
            "the baseline restarts at this call's tips"
            if persisted
            else "the state could not be rewritten and the gap is still growing"
        )
        warnings.append(
            f"[commit-backstop] {state_path} exists but yielded no usable "
            f"baseline - nothing was judged; {outcome}"
        )
        if persisted:
            _log(
                event="degraded",
                harness="commit-backstop",
                reason=loss,
                command=None,
                cwd=cwd,
                session_id=session_id,
            )

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
            reason=REASON_STATE_UNWRITABLE,
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
        종료 코드 (main()의 반환값을 그대로 내보낸다. main()이 Exception을
        던지면 대신 1 — 비차단).
    """
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 — fail-open이 설계 요구사항
        print(f"[commit-backstop] internal error (fail-open): {exc}", file=sys.stderr)
        return 1
