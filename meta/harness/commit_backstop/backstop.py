# 실행 후 git 그래프 연산으로 보호 브랜치 위반·헤더 위반을 정확히 적발하는 PostToolUse hook
"""commit-backstop: 실행이 끝난 뒤 git 저장소의 실제 상태로 커밋 규율을 판정한다.

commit_guard(PreToolUse)는 명령 텍스트를 추론하는 best-effort 예방이고, 이
모듈은 그 뒤의 정확한 안전망이다(#52). 텍스트/reflog 추론 없이 커밋 그래프
집합 연산만 사용한다.

주장하는 것:
    - 보호 브랜치(main/master)는 원격 main/master에 이미 존재하는 커밋으로만
      전진할 수 있다. `<직전>..<현재>` 중 원격 main/master에 없는 커밋이 있으면
      경로 불문(직접 commit, 로컬 merge, cherry-pick, plumbing) 위반으로 보고한다.
    - HEAD에 새로 도달한 미공개(non-merge) 커밋의 제목은 Conventional Commits를
      따라야 한다. 위반은 수정 지시와 함께 보고한다.
    - 같은 위반은 한 번만 보고한다(평가한 tip이 곧 기록 tip).

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
    - 오래된 미push 브랜치를 checkout하면 그 브랜치에 이미 있던 비규격
      커밋이 헤더 보고를 유발한다 — 이 명령이 만든 커밋이 아니어도.
      수용한 트레이드오프이며(#52, PR #54: checked dedup으로 ts 필터 대체)
      보통은 `checked` 목록이 재방문 시 중복 제거하지만, CHECKED_CAP을
      넘겨 밀려나거나 상태 쓰기가 실패하면 다시 보고될 수 있다.
    - remote-tracking ref를 만들지 않는 `git pull <URL>`은 오탐할 수 있다.
    - 로컬 브랜치가 비표준 원격명(예: origin/trunk)을 추적하는 구성은
      고려하지 않는다.
    - merge 커밋과 git 자동 생성 제목(`Revert "`, `fixup! `, `squash! `)은
      헤더 검사에서 면제한다.

보고 채널:
    stderr는 exit 42(래퍼가 2로 되매핑)일 때만 모델 컨텍스트에 주입된다.
    보호 ref 평가 실패 경고(exit 1)는 로그 전용이며 의도된 것이다 — 그 경고가
    유의미한 시나리오(gc로 직전 SHA 은폐)는 적대 모델이라 위협 모델 밖이고,
    "42 = 확인된 위반" 계약을 흐리지 않는 쪽을 택했다(#52).
    프롬프트 주입 방지를 위해 커밋 제목 원문은 절대 에코하지 않는다
    (SHA + 위반 사유만 — answer_first_reminder의 상수 출력과 같은 근거).

상태:
    `<git-common-dir>/atom-commit-backstop.json` —
    `{"seen": {ref: sha, ...}, "checked": [sha, ...]}`. HEAD는 worktree별
    `HEAD@<git-dir>` 키. 쓰기는 임시 파일 + os.replace로 원자적. 손상·스키마
    불일치는 최초 실행으로 취급한다(기록만, 적발 없음).

종료 코드:
    0 통과 / 1 내부 경고(비차단, 로그 전용) / 42 위반(래퍼가 2로 되매핑).
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

# 위반 보고에 나열하는 SHA 상한 (stderr는 모델 컨텍스트에 주입되므로).
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


def _store_state(path: str, state: dict) -> None:
    """상태를 원자적으로 쓴다(임시 파일 + os.replace). 실패는 무시한다.

    쓰기 도중 중단으로 반쪽짜리 파일이 남으면 다음 실행이 최초 실행으로
    오인해 위반 tip을 조용히 기록하므로, 원자성은 적발 누락 방지 요건이다.
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


def _shortlist(shas: list[str]) -> str:
    """보고용 SHA 나열 (상한 REPORT_SHA_LIMIT + 총 개수)."""
    head = ", ".join(sha[:12] for sha in shas[:REPORT_SHA_LIMIT])
    if len(shas) > REPORT_SHA_LIMIT:
        return f"{head}, ... ({len(shas)} total)"
    return head


def _branch_report(branch: str, old: str, new: str, offending: list[str]) -> str:
    """보호 브랜치 위반 보고문 — 절대 SHA 기반의 순서화된 복구 지시."""
    return "\n".join(
        [
            f"[commit-backstop] {len(offending)} commit(s) not present on any "
            f"remote '{branch}' landed on local '{branch}': {_shortlist(offending)}",
            "Recover with EXACTLY these steps, in this order:",
            "  1. Preserve the work FIRST (skipping this step loses the commits):",
            f"     on '{branch}' now? -> git checkout -b <type/short-description>",
            f"     otherwise         -> git branch <type/short-description> {new}",
            f"  2. git branch -f {branch} {old}",
            f"     (fails because '{branch}' is checked out in another worktree? "
            f"run `git reset --keep {old}` in that worktree)",
            "  3. Continue on the rescue branch and merge via PR.",
            "See the commit-discipline rule (meta/rules/commit-discipline.md).",
        ]
    )


def _header_report(problems: list[tuple[str, str]]) -> str:
    """헤더 위반 보고문 — 제목 원문은 에코하지 않는다(SHA + 사유만)."""
    lines = [
        f"[commit-backstop] {len(problems)} new commit(s) violate the "
        "Conventional Commits header rule:"
    ]
    for sha, problem in problems[:REPORT_SHA_LIMIT]:
        lines.append(f"  - {sha[:12]}: {problem}")
    if len(problems) > REPORT_SHA_LIMIT:
        lines.append(f"  - ... ({len(problems)} total)")
    lines.append(
        "Commits you authored in this session: fix the tip with "
        "`git commit --amend`, rebase earlier ones before pushing. "
        "Commits that were already on the branch (surfaced by a checkout, or "
        "detected late from another directory): do NOT rewrite them — report "
        "the SHAs to the owner and continue. "
        "Unsure which is which? `git log -1 --format=%cI <sha>`. "
        "See meta/rules/commit-discipline.md."
    )
    return "\n".join(lines)


def main() -> int:
    """stdin의 PostToolUse JSON을 판정한다.

    Returns:
        종료 코드 (0 통과, 1 내부 경고 — 로그 전용, 42 위반 — 래퍼가 2로 되매핑).
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

    reports: list[str] = []
    warnings: list[str] = []
    newly_checked: list[str] = []
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
            elif offending:
                reports.append(_branch_report(branch, old, new, offending))
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
                        reports.append(_header_report(problems))

    seen = dict(state["seen"])
    seen.update({key: new for key, (_, new) in moved.items()})
    checked = (state["checked"] + newly_checked)[-CHECKED_CAP:]
    _store_state(state_path, {"seen": seen, "checked": checked})

    if reports:
        print("\n".join(reports), file=sys.stderr)
        return EXIT_BLOCK
    if warnings:
        # 로그 전용: exit 1의 stderr는 모델에 주입되지 않는다(의도됨 — #52).
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
