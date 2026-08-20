# commit_backstop의 그래프 판정·헤더 검사·상태 관리·fail-open 경로를 실 git 저장소로 검증하는 테스트
"""backstop 모듈 테스트.

commit_guard 테스트와 달리 git을 mock하지 않는다 — 판정이 그래프 연산이므로
tmp_path에 실제 저장소(+bare remote)를 만들어 결정성 있게 검증한다. 설계
불변식 — 오탐 금지(정당한 동기화·merge/자동생성 제목), 실패는 전부 통과
방향, 통상 1회 보고(재보고 경로는 모듈 비주장 목록), 절대 SHA 사실 보고와
오너 라우팅(복구 절차는 규칙 파일 소관) — 를 케이스로 고정한다.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import pytest

from harness.commit_backstop import backstop

# 전역/시스템 git 설정과 격리된 결정적 환경.
_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(repo, *args: str) -> str:
    """저장소에서 git 명령을 실행하고 stdout을 돌려준다 (실패는 테스트 실패)."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=_GIT_ENV,
        check=True,
    )
    return result.stdout.strip()


def _commit(repo, subject: str) -> str:
    """빈 커밋을 만들고 SHA를 돌려준다."""
    _git(repo, "commit", "--allow-empty", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def _make_repo(tmp_path, name="repo", branch="main", with_remote=True):
    """실 git 저장소를 만든다 (기본: bare remote 'origin' 연결)."""
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", branch)
    if with_remote:
        remote = tmp_path / f"{name}-remote.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", branch, str(remote)],
            capture_output=True,
            env=_GIT_ENV,
            check=True,
        )
        _git(repo, "remote", "add", "origin", str(remote))
    return repo


def _run(monkeypatch, repo, command="true", session_id=None) -> int:
    """PostToolUse payload를 stdin으로 넣고 main()을 실행한다."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(repo),
    }
    if session_id is not None:
        payload["session_id"] = session_id
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return backstop.main()


def _ledger_entries(tmp_path) -> list[dict]:
    """격리된 원장(conftest가 tmp_path로 돌린다)의 줄을 파싱한다."""
    path = tmp_path / "state" / "atom" / "guard-blocklog.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]


def _baseline(monkeypatch, tmp_path, **kwargs):
    """초기 커밋을 push까지 마친 저장소 + 최초 관찰 기록이 끝난 상태를 만든다."""
    repo = _make_repo(tmp_path, **kwargs)
    branch = kwargs.get("branch", "main")
    _commit(repo, "chore: init")
    _git(repo, "push", "-q", "-u", "origin", branch)
    assert _run(monkeypatch, repo) == 0  # 최초 관찰: 기록만
    return repo


def _state_path(repo) -> str:
    return os.path.join(str(repo), ".git", backstop.STATE_FILENAME)


def _read_state(repo) -> dict:
    """상태 파일을 읽어 돌려준다."""
    with open(_state_path(repo), encoding="utf-8") as fp:
        return json.load(fp)


def _write_state(repo, state: dict) -> None:
    """상태 파일을 덮어쓴다 (평가 실패 유도용)."""
    with open(_state_path(repo), "w", encoding="utf-8") as fp:
        json.dump(state, fp)


class _BreakReplace:
    """`os.replace`를 토글로 실패시킨다 — 지속 실패와 복구를 한 테스트에 담는다.

    `monkeypatch.undo()`는 그 시점까지의 모든 setattr를 함께 되돌리므로 중간
    복구에 쓸 수 없다. `_store_state`는 `OSError`만 잡으므로 그 하위 타입을
    던진다 — 다른 예외면 `main()` 밖으로 새어 의도한 경로를 태우지 못한다.
    """

    def __init__(self) -> None:
        self.broken = True
        self._real = os.replace

    def __call__(self, src, dst, **kwargs):
        if self.broken:
            raise PermissionError(13, "state write blocked by test")
        return self._real(src, dst, **kwargs)


# --- 보호 브랜치 판정 --------------------------------------------------------


def test_direct_commit_on_main_blocks_with_absolute_sha_facts(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    old = _git(repo, "rev-parse", "main")
    _commit(repo, "feat: direct on main")
    new = _git(repo, "rev-parse", "main")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert old in err and new in err  # 절대 SHA — 오너가 복구에 쓸 사실
    assert "HEAD~" not in err
    assert "Do NOT rewrite or move 'main' yourself" in err
    assert "do not push until they decide" in err
    # 절차는 규칙 파일이 보유한다 — 근거는 _branch_report docstring.
    assert "git branch -f" not in err
    assert "meta/rules/commit-backstop.md" in err


def test_unpushed_branch_report_states_facts_without_surgery(
    monkeypatch, capsys, tmp_path
):
    # 원격 main ref가 없으면 보고가 난다. 보고는 사실만 싣고 절차는 싣지
    # 않는다 — 근거는 모듈 비주장 목록.
    repo = _make_repo(tmp_path)
    _commit(repo, "chore: init")
    assert _run(monkeypatch, repo) == 0  # 최초 관찰
    _commit(repo, "feat: still unpushed")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert "git branch -f" not in err
    assert "No remote 'main' exists here" in err  # 사실 전달 — 판정 아님
    assert "do not push until they decide" in err


def test_at_most_one_history_rewrite_instruction_per_report(
    monkeypatch, capsys, tmp_path
):
    # 동반 발화 시 헤더 레인은 라우팅만 한다 — 근거는 _header_report
    # docstring. 합성 규칙은 코드가 강제한다.
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "totally wrong subject on main")  # 두 lane 동시 발화
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert err.count("[commit-backstop]") == 2
    assert "--amend" not in err  # 헤더 lane은 라우팅만
    assert "git branch -f" not in err
    assert "already routes this to the owner" in err


def test_same_violation_reported_once(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: direct on main")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    capsys.readouterr()
    assert _run(monkeypatch, repo) == 0  # dedup
    assert capsys.readouterr().err == ""


def test_new_violation_after_report_blocks_again(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: first")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    _commit(repo, "feat: second")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK


def test_amend_on_main_blocks(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    _git(repo, "commit", "--amend", "--allow-empty", "-m", "feat: amended")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK


def test_fast_forward_pull_on_main_passes(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "-q", str(tmp_path / "repo-remote.git"), str(other)],
        capture_output=True,
        env=_GIT_ENV,
        check=True,
    )
    _commit(other, "feat: upstream work")
    _git(other, "push", "-q", "origin", "main")
    _git(repo, "pull", "-q", "origin", "main")
    assert _run(monkeypatch, repo) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("merge_args", [["merge", "-q", "feat/x"], ["merge", "-q", "--no-ff", "-m", "merge feat", "feat/x"]])
def test_merging_unpushed_branch_into_main_blocks(
    monkeypatch, capsys, tmp_path, merge_args
):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "feat: on branch")
    _git(repo, "checkout", "-q", "main")
    assert _run(monkeypatch, repo) == 0  # checkout 자체는 무해
    _git(repo, *merge_args)
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK


def test_merging_pushed_branch_into_main_blocks(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "feat: pushed work")
    _git(repo, "push", "-q", "origin", "feat/x")
    _git(repo, "checkout", "-q", "main")
    assert _run(monkeypatch, repo) == 0
    _git(repo, "merge", "-q", "feat/x")
    # 커밋이 origin/feat/x에는 있어도 원격 main에는 없다 — 제외 집합이 원격
    # main/master만임을 검증하는 핵심 케이스.
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK


def test_pr_merge_then_pull_passes(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "-q", str(tmp_path / "repo-remote.git"), str(other)],
        capture_output=True,
        env=_GIT_ENV,
        check=True,
    )
    _git(other, "checkout", "-q", "-b", "feat/pr")
    _commit(other, "feat: via pr")
    _git(other, "checkout", "-q", "main")
    _git(other, "merge", "-q", "--no-ff", "-m", "Merge pull request #1", "feat/pr")
    _git(other, "push", "-q", "origin", "main")
    _git(repo, "pull", "-q", "origin", "main")
    assert _run(monkeypatch, repo) == 0
    assert capsys.readouterr().err == ""


def test_recovery_flow_silences_backstop(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    old = _git(repo, "rev-parse", "main")
    _commit(repo, "feat: direct on main")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    assert old in capsys.readouterr().err  # 오너가 되돌릴 지점을 보고문에서 얻는다
    _git(repo, "checkout", "-q", "-b", "feat/rescue")
    _git(repo, "branch", "-f", "main", old)
    assert _run(monkeypatch, repo) == 0
    assert capsys.readouterr().err == ""


def test_preexisting_history_is_never_flagged_on_first_run(
    monkeypatch, capsys, tmp_path
):
    repo = _make_repo(tmp_path)
    _commit(repo, "bad subject straight onto main.")
    _commit(repo, "another bad one")
    assert _run(monkeypatch, repo) == 0  # 최초 관찰: 기록만
    assert capsys.readouterr().err == ""


def test_commit_in_secondary_worktree_blocks(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/w")
    assert _run(monkeypatch, repo) == 0
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(wt), "main")
    _git(wt, "commit", "--allow-empty", "-m", "feat: in worktree")
    # cwd는 primary인데 공유 브랜치 ref가 움직였다 (#47).
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK


def test_branch_delete_recreate_laundering_blocks(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "feat: work")
    assert _run(monkeypatch, repo) == 0
    _git(repo, "branch", "-D", "main")
    _git(repo, "branch", "main", "HEAD")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK


def test_pushed_branch_named_like_main_does_not_widen_exclusion(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "backup/main")
    _commit(repo, "feat: sneaky")
    _git(repo, "push", "-q", "origin", "backup/main")
    _git(repo, "checkout", "-q", "main")
    assert _run(monkeypatch, repo) == 0
    _git(repo, "merge", "-q", "backup/main")
    # refs/remotes/origin/backup/main이 제외 집합에 끼면 통과해 버린다 (H-2).
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK


def test_master_repo_uses_parameterized_branch_name(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path, name="mrepo", branch="master")
    _commit(repo, "feat: direct on master")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert "'master' moved" in err
    assert "'main'" not in err


def test_multiple_commits_reported_once_with_count(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    for i in range(3):
        _commit(repo, f"feat: bulk {i}")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert "3 commit(s)" in err
    assert err.count("[commit-backstop]") == 1  # 한 브랜치 = 한 보고문


def test_commit_and_push_in_one_command_is_documented_limitation(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: slipped through")
    _git(repo, "push", "-q", "origin", "main")
    # push가 refs/remotes/origin/main을 먼저 갱신해 제외된다 — 문서화된 한계
    # (서버 브랜치 보호 계층의 몫). 이 테스트는 그 동작을 명시적으로 고정한다.
    assert _run(monkeypatch, repo) == 0


# --- 메시지(헤더) 검사 -------------------------------------------------------


def test_feature_branch_header_check(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "feat: good header")
    assert _run(monkeypatch, repo) == 0
    bad_subject = "Bad Subject With Uppercase."
    sha = _commit(repo, bad_subject)
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert "--amend" in err
    # 지시의 하중은 rewrite 금지문과, 호출자가 확실히 아는 앵커("방금 실행된
    # 명령")에 걸린다 — 둘이 빠지면 "전부 amend하라"는 #64 이전 의미로
    # 되돌아간다 (#64, PR #114 rounds 1-2).
    assert "do NOT rewrite" in err
    assert "the Bash command that just ran" in err
    assert sha[:12] in err
    assert bad_subject not in err  # 제목 원문 에코 금지 (프롬프트 주입 방지)
    _git(repo, "commit", "--amend", "--allow-empty", "-m", "fix: repaired header")
    assert _run(monkeypatch, repo) == 0


def test_detached_head_header_check(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "--detach")
    _commit(repo, "no type prefix here")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    _commit(repo, "feat: proper header")
    assert _run(monkeypatch, repo) == 0


def test_checked_commits_not_rereported_after_branch_roundtrip(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "bad header once")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    _git(repo, "checkout", "-q", "main")
    assert _run(monkeypatch, repo) == 0
    _git(repo, "checkout", "-q", "feat/x")
    assert _run(monkeypatch, repo) == 0  # checked dedup — 재보고 없음


def test_merge_commit_subject_is_exempt(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "-q", str(tmp_path / "repo-remote.git"), str(other)],
        capture_output=True,
        env=_GIT_ENV,
        check=True,
    )
    _commit(other, "feat: upstream advance")
    _git(other, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "feat: local work")
    assert _run(monkeypatch, repo) == 0
    _git(repo, "fetch", "-q", "origin")
    # feature 브랜치에서 main을 당겨오는 일상 동기화 — merge 커밋 제목
    # ("Merge ...")이 헤더 검사에 걸리면 안 된다 (--no-merges).
    _git(repo, "merge", "-q", "--no-ff", "origin/main")
    assert _run(monkeypatch, repo) == 0
    assert capsys.readouterr().err == ""


def test_two_bad_commits_from_one_command_route_the_non_head_one(
    monkeypatch, capsys, tmp_path
):
    # 한 명령이 비규격 커밋을 둘 만들면 HEAD가 아닌 쪽이 두 분기 사이로
    # 빠져나가 고쳐지지도 라우팅되지도 않았다 — `checked`에 들어가 다시는
    # 호명되지 않으므로 조용히 push된다 (PR #114 round 5).
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    first = _commit(repo, "Bad one.")
    second = _commit(repo, "Bad two.")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert first[:12] in err and second[:12] in err
    assert "no longer HEAD" in err  # 비-HEAD 분기가 명시적으로 라우팅된다


def test_violations_past_the_reason_limit_still_list_every_sha(
    monkeypatch, capsys, tmp_path
):
    # 사유는 REPORT_SHA_LIMIT개까지만 붙이되 SHA는 전부 실어야 한다 — 판정된
    # 커밋은 모두 `checked`에 들어가 다시는 호명되지 않으므로, 잘라내면 오너
    # 보고가 영구히 불완전해진다 (PR #114 round 2).
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    shas = [_commit(repo, f"Bad number {i}.") for i in range(backstop.REPORT_SHA_LIMIT + 2)]
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    for sha in shas:
        assert sha[:12] in err
    assert f"({len(shas)} total)" in err


def test_custom_subject_merge_commit_is_exempt(monkeypatch, capsys, tmp_path):
    # merge 면제는 제목 접두사가 아니라 부모 수(--no-merges)로 이뤄진다. 기본
    # 제목("Merge ...")만 검증하면 접두사 구현으로 바꿔도 통과하므로, 규격을
    # 어긴 커스텀 제목으로 메커니즘을 고정한다 (#64 finding 1, PR #114 round 1).
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/side")
    _commit(repo, "feat: side work")
    _git(repo, "checkout", "-q", "-b", "feat/x", "main")
    _commit(repo, "feat: local work")
    assert _run(monkeypatch, repo) == 0
    _git(repo, "merge", "-q", "--no-ff", "-m", "THIS Merge subject breaks.", "feat/side")
    assert _run(monkeypatch, repo) == 0
    assert capsys.readouterr().err == ""


def test_merge_tip_is_not_the_advised_amend_target(monkeypatch, capsys, tmp_path):
    # tip이 merge면 보고 목록에 없다 — 위치("the tip") 대신 동일성을 앵커로
    # 삼아야 하는 이유이자, 그 지시가 엉뚱한 커밋을 가리키지 않는다는 고정.
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/side")
    _commit(repo, "feat: side work")
    _git(repo, "checkout", "-q", "-b", "feat/x", "main")
    assert _run(monkeypatch, repo) == 0
    bad = _commit(repo, "Bad header here.")
    _git(repo, "merge", "-q", "--no-ff", "-m", "feat: merge side", "feat/side")
    merge_sha = _git(repo, "rev-parse", "HEAD")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert bad[:12] in err
    assert merge_sha[:12] not in err  # HEAD는 위반 목록 밖 — amend 대상이 아니다


def test_checkout_of_unobserved_branch_reports_preexisting_commits(
    monkeypatch, capsys, tmp_path
):
    # 문서화된 한계: hook이 한 번도 보지 못한 오래된 브랜치를 checkout하면 그
    # 브랜치의 기존 비규격 커밋이 보고된다 — 이 명령이 만들지 않았어도.
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "old/feat")
    stale = _commit(repo, "Legacy header.")  # hook 미실행 구간
    # HEAD가 old/feat에서 갈라진 다른 브랜치에 있도록 만든다 — 되돌아오는
    # 이동이 fast-forward가 아니어야 "재부상"이 진짜로 검증된다.
    _git(repo, "checkout", "-q", "-b", "feat/current", "main")
    _commit(repo, "feat: current work")
    assert _run(monkeypatch, repo) == 0
    capsys.readouterr()
    _git(repo, "checkout", "-q", "old/feat")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert stale[:12] in err
    assert "do NOT rewrite" in err  # 남의 커밋 재작성 금지가 이 경로의 하중
    # 되돌아오는 이동을 훅이 관찰해야 `seen`이 옮겨가고, 다음 복귀가 실제로
    # `checked` dedup 경로를 탄다. 관찰을 빼면 `moved`가 비어 조기 반환하므로
    # 아래 단언이 dedup을 전혀 검증하지 못한다 (PR #114 round 2).
    _git(repo, "checkout", "-q", "feat/current")
    assert _run(monkeypatch, repo) == 0
    _git(repo, "checkout", "-q", "old/feat")
    assert _run(monkeypatch, repo) == 0  # checked dedup


def test_amend_to_another_bad_header_is_reported_again(monkeypatch, capsys, tmp_path):
    # dedup 키가 SHA이므로 amend·rebase가 새 SHA를 만들면 다시 보고된다 —
    # "한 번만 보고한다"가 논리적 커밋 단위로는 성립하지 않는 세 번째 경로.
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    first = _commit(repo, "Still bad.")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    assert first[:12] in capsys.readouterr().err
    _git(repo, "commit", "--amend", "--allow-empty", "-m", "Also bad.")
    second = _git(repo, "rev-parse", "HEAD")
    assert second != first
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    assert second[:12] in capsys.readouterr().err


def test_git_generated_subjects_are_exempt(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    (tmp_path / "repo" / "r.txt").write_text("payload")
    _git(repo, "add", "r.txt")
    _git(repo, "commit", "-q", "-m", "feat: base")
    base = _git(repo, "rev-parse", "HEAD")
    assert _run(monkeypatch, repo) == 0
    _git(repo, "revert", "--no-edit", base)  # 'Revert "feat: base"'
    assert _run(monkeypatch, repo) == 0
    _git(repo, "commit", "--allow-empty", "--fixup", base)  # 'fixup! feat: base'
    assert _run(monkeypatch, repo) == 0
    _commit(repo, "squash! feat: base")
    assert _run(monkeypatch, repo) == 0
    assert capsys.readouterr().err == ""


def test_subject_starting_with_commit_is_parsed_correctly(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    bad = _commit(repo, "commit guard fix")  # 'commit '으로 시작하는 제목 (M-ii)
    good = _commit(repo, "feat: fine")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert bad[:12] in err
    assert good[:12] not in err


def test_dual_violation_reports_once_with_both_reasons(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "totally wrong subject on main")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert "landed on local 'main'" in err  # 브랜치 위반
    assert "Conventional Commits" in err  # 헤더 위반
    assert err.count("[commit-backstop]") == 2  # 보고문 2절, 출력·exit는 1회


def test_unicode_subject_does_not_crash(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "잘못된 제목 🚀.")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    _git(repo, "commit", "--amend", "--allow-empty", "-m", "feat: 🚀 emoji is fine")
    assert _run(monkeypatch, repo) == 0


def test_stash_does_not_trigger_header_check(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    (tmp_path / "repo" / "f.txt").write_text("one")
    _git(repo, "add", "f.txt")
    _commit_tracked = _git(repo, "commit", "-q", "-m", "feat: add file")
    assert _run(monkeypatch, repo) == 0
    (tmp_path / "repo" / "f.txt").write_text("two")
    _git(repo, "stash", "-q")
    assert _run(monkeypatch, repo) == 0  # 'WIP on ...' 커밋 객체는 HEAD 비도달
    _git(repo, "stash", "pop", "-q")
    assert _run(monkeypatch, repo) == 0
    assert capsys.readouterr().err == ""


def test_import_fallback_skips_header_check_only(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    monkeypatch.setattr(backstop, "validate_subject", None)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "definitely not conventional")
    assert _run(monkeypatch, repo) == 0  # 헤더 검사만 생략
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "feat: on main")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK  # 브랜치 검사는 동작


# --- 스킵·override·상태 ------------------------------------------------------


def test_repo_without_remote_is_fully_skipped(monkeypatch, capsys, tmp_path):
    repo = _make_repo(tmp_path, with_remote=False)
    _commit(repo, "whatever, scratch repo.")
    assert _run(monkeypatch, repo) == 0
    assert not os.path.exists(_state_path(repo))  # 상태도 기록하지 않는다
    _commit(repo, "still fine")
    assert _run(monkeypatch, repo) == 0


def test_override_records_tip_and_stays_silent(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: deliberate exception")
    assert _run(monkeypatch, repo, command="ATOM_COMMIT_OVERRIDE=1 git commit") == 0
    assert capsys.readouterr().err == ""
    assert _run(monkeypatch, repo) == 0  # tip이 기록되어 재적발 없음


def test_state_file_lands_in_target_repo_git_dir(monkeypatch, capsys, tmp_path):
    # 훅 프로세스 cwd(프로젝트 디렉터리)와 payload cwd(tmp 저장소)가 다른
    # 상태에서 실행된다 — 상태 파일은 반드시 대상 저장소 쪽에 생겨야 한다 (H-1).
    assert os.getcwd() != str(tmp_path / "repo")
    repo = _baseline(monkeypatch, tmp_path)
    assert os.path.exists(_state_path(repo))
    assert not os.path.exists(os.path.join(os.getcwd(), ".git", backstop.STATE_FILENAME))


def test_state_write_is_atomic_and_capped(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    monkeypatch.setattr(backstop, "CHECKED_CAP", 3)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    for i in range(5):
        _commit(repo, f"feat: c{i}")
        _run(monkeypatch, repo)
    assert not os.path.exists(_state_path(repo) + ".tmp")
    with open(_state_path(repo), encoding="utf-8") as fp:
        state = json.load(fp)
    assert len(state["checked"]) <= 3  # FIFO cap
    assert isinstance(state["seen"], dict)


def test_corrupt_state_reports_the_lost_baseline_and_stops_repeating(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    with open(_state_path(repo), "w", encoding="utf-8") as fp:
        fp.write("{ not json")
    assert _run(monkeypatch, repo) == 1  # 기준선 상실: 판정 없이 알리기만
    err = capsys.readouterr().err
    assert "no usable baseline" in err
    # 다시 쓰기에 성공한 호출이므로 재시작을 말해도 된다.
    assert "restarts at the current tips" in err
    entries = _ledger_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["event"] == "degraded"
    assert entries[0]["reason"] == "state-corrupt"
    assert entries[0]["command"] is None
    assert _run(monkeypatch, repo) == 0  # 재구축됐으므로 반복하지 않는다
    assert capsys.readouterr().err == ""
    assert len(_ledger_entries(tmp_path)) == 1
    _commit(repo, "feat: next one")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK  # 적발 재개
    with open(_state_path(repo), "w", encoding="utf-8") as fp:
        json.dump({"seen": [], "checked": {}}, fp)  # 유효 JSON, 스키마 불일치
    _commit(repo, "feat: after schema break")
    assert _run(monkeypatch, repo) == 1
    assert _ledger_entries(tmp_path)[-1]["reason"] == "state-corrupt"
    _commit(repo, "feat: caught again")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK


def test_unreadable_state_reports_the_lost_baseline(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    path = _state_path(repo)
    os.remove(path)
    try:
        os.symlink(path, path)  # 자기 참조 — open()이 ELOOP를 낸다
    except OSError:  # 심링크를 만들 수 없는 파일시스템(Windows 마운트 등)
        pytest.skip("이 파일시스템에서는 심링크를 만들 수 없다")
    _commit(repo, "feat: on main")
    assert _run(monkeypatch, repo) == 1
    assert "no usable baseline" in capsys.readouterr().err
    assert _ledger_entries(tmp_path)[0]["reason"] == "state-unreadable"
    # os.replace는 심링크를 따라가지 않으므로 그 자리가 정규 파일로 치유된다.
    assert _run(monkeypatch, repo) == 0
    assert len(_ledger_entries(tmp_path)) == 1


def test_absent_state_file_is_not_a_lost_baseline(monkeypatch, capsys, tmp_path):
    # 부재를 상실로 기록하면 모든 새 클론이 첫 호출에서 퇴화를 보고한다.
    repo = _make_repo(tmp_path)
    _commit(repo, "chore: init")
    _git(repo, "push", "-q", "-u", "origin", "main")
    assert not os.path.exists(_state_path(repo))  # 전제를 헬퍼에 맡기지 않는다
    assert _run(monkeypatch, repo) == 0
    assert capsys.readouterr().err == ""
    assert _ledger_entries(tmp_path) == []


def test_state_that_cannot_be_rebuilt_warns_without_a_ledger_line(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    path = _state_path(repo)
    os.remove(path)
    os.mkdir(path)  # 읽기도 os.replace도 EISDIR — 다시 쓸 수 없다
    _commit(repo, "feat: on main")
    for _ in range(2):
        assert _run(monkeypatch, repo) == 1
        err = capsys.readouterr().err
        assert "no usable baseline" in err
        # 다시 쓰지 못한 호출이 "restarts"를 말하면 자라는 구간을 닫힌 것처럼
        # 보고하게 된다 — 실패 변형을 고정한다.
        assert "the gap is still growing" in err
    assert _ledger_entries(tmp_path) == []  # 선언된 경계: 원장에는 쌓지 않는다


def test_override_does_not_suppress_the_lost_baseline(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    with open(_state_path(repo), "w", encoding="utf-8") as fp:
        fp.write("{ not json")
    assert _run(monkeypatch, repo, command="ATOM_COMMIT_OVERRIDE=1 git commit") == 1
    assert "no usable baseline" in capsys.readouterr().err
    assert {e["event"] for e in _ledger_entries(tmp_path)} == {"override", "degraded"}


def test_unreachable_previous_tip_warns_log_only(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    with open(_state_path(repo), encoding="utf-8") as fp:
        state = json.load(fp)
    state["seen"]["refs/heads/main"] = "0" * 40  # 도달 불가 sha
    with open(_state_path(repo), "w", encoding="utf-8") as fp:
        json.dump(state, fp)
    _commit(repo, "feat: moves main")
    assert _run(monkeypatch, repo) == 1
    assert "NOT checked" in capsys.readouterr().err
    assert _run(monkeypatch, repo) == 0  # tip은 기록되어 다음 호출은 침묵


def test_rev_list_timeout_warns_log_only(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    real_run = subprocess.run

    def flaky(argv, **kwargs):
        if "rev-list" in argv:
            raise subprocess.TimeoutExpired(argv, backstop.GIT_TIMEOUT_SECONDS)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(backstop.subprocess, "run", flaky)
    assert _run(monkeypatch, repo) == 1
    assert "NOT checked" in capsys.readouterr().err


def test_head_evaluation_failure_warns_log_only(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "Bad header.")
    real_run = subprocess.run

    def flaky(argv, **kwargs):
        # `--no-merges`는 _new_head_commits에만 있다 — 헤더 레인만 실패시킨다.
        if "--no-merges" in argv:
            raise subprocess.TimeoutExpired(argv, backstop.GIT_TIMEOUT_SECONDS)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(backstop.subprocess, "run", flaky)
    assert _run(monkeypatch, repo) == 1
    assert "header check was NOT run" in capsys.readouterr().err


def test_git_remote_failure_fails_open(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    monkeypatch.setattr(backstop, "_remote_names", lambda cwd: None)
    assert _run(monkeypatch, repo) == 1  # 스킵 (비차단)


# --- 지속 실패 시 강등 (#115) -------------------------------------------------


def test_failed_state_write_downgrades_and_never_repeats_into_the_model(
    monkeypatch, capsys, tmp_path
):
    # #115가 재현한 것은 단발이 아니라 반복이다 — run 1/2/3이 전부 42였다.
    repo = _baseline(monkeypatch, tmp_path)
    before = _read_state(repo)
    _commit(repo, "feat: on main")
    monkeypatch.setattr(backstop.os, "replace", _BreakReplace())
    outputs = []
    for _ in range(3):
        assert _run(monkeypatch, repo) == 1  # 42가 아니다 — 집행하지 않는다
        outputs.append(capsys.readouterr().err)
    assert all("state could not be persisted" in err for err in outputs)
    assert all("not present on any remote 'main'" in err for err in outputs)
    assert len(set(outputs)) == 1  # 판정은 그대로, 채널만 강등
    assert _read_state(repo)["seen"] == before["seen"]  # 워터마크 불변


def test_verdict_fires_at_exit_block_once_persistence_recovers(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    breaker = _BreakReplace()
    monkeypatch.setattr(backstop.os, "replace", breaker)
    sha = _commit(repo, "feat: on main")
    assert _run(monkeypatch, repo) == 1
    capsys.readouterr()
    breaker.broken = False
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK  # 계류하던 판정이 발화
    err = capsys.readouterr().err
    assert sha[:12] in err
    assert "state could not be persisted" not in err
    assert _run(monkeypatch, repo) == 0  # 이제 1회 보고가 보증된다


def test_degraded_output_carries_both_the_report_and_the_warning(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "branch", "master")  # 두 번째 보호 브랜치
    assert _run(monkeypatch, repo) == 0  # master 최초 관찰
    state = _read_state(repo)
    state["seen"]["refs/heads/master"] = "0" * 40  # 평가 실패를 유도
    _write_state(repo, state)
    _git(repo, "checkout", "-q", "master")
    _commit(repo, "feat: moves master")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "feat: on main")
    monkeypatch.setattr(backstop.os, "replace", _BreakReplace())
    assert _run(monkeypatch, repo) == 1
    err = capsys.readouterr().err
    assert "state could not be persisted" in err
    assert "not present on any remote 'main'" in err  # 보고
    assert "NOT checked" in err  # 경고가 보고에 삼켜지지 않는다


def test_failed_state_write_without_a_verdict_stays_silent(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "feat: fine everywhere")
    monkeypatch.setattr(backstop.os, "replace", _BreakReplace())
    assert _run(monkeypatch, repo) == 0  # 매 호출 알리면 항시 노이즈가 된다
    assert capsys.readouterr().err == ""


def test_failed_state_write_never_swallows_a_standalone_warning(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    state = _read_state(repo)
    state["seen"]["refs/heads/main"] = "0" * 40
    _write_state(repo, state)
    _commit(repo, "feat: moves main")
    monkeypatch.setattr(backstop.os, "replace", _BreakReplace())
    assert _run(monkeypatch, repo) == 1
    err = capsys.readouterr().err
    assert "NOT checked" in err  # 강등이 현행 경고를 삼키면 안 된다
    assert "state could not be persisted" not in err  # 보고가 없으면 고지도 없다


def test_override_under_a_failed_state_write_stays_silent(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: deliberate exception")
    monkeypatch.setattr(backstop.os, "replace", _BreakReplace())
    assert _run(monkeypatch, repo, command="ATOM_COMMIT_OVERRIDE=1 git commit") == 0
    assert capsys.readouterr().err == ""


# --- 원장 기록 (#76 확장) -----------------------------------------------------
#
# `_log`는 예외를 삼키므로 호출부 키워드 오타가 조용한 무기록이 된다. 호출부를
# 각각 태우는 아래 테스트들은 선택이 아니라 그 설계의 필수 조건이다.


def test_ledger_records_a_protected_branch_block(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    entries = _ledger_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["event"] == "block"
    assert entries[0]["harness"] == "commit-backstop"
    assert entries[0]["reason"] == "protected-branch"


def test_ledger_records_a_header_block(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "Bad header.")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    assert _ledger_entries(tmp_path)[0]["reason"] == "header"


def test_ledger_marks_a_block_that_fired_both_lanes(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "totally wrong subject on main")  # 두 lane 동시 발화
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    assert _ledger_entries(tmp_path)[0]["reason"] == "protected-branch+header"


def test_ledger_records_an_override(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: deliberate exception")
    assert _run(monkeypatch, repo, command="ATOM_COMMIT_OVERRIDE=1 git commit") == 0
    entry = _ledger_entries(tmp_path)[0]
    assert entry["event"] == "override"
    assert entry["reason"] is None


def test_ledger_records_a_suppressed_verdict_without_the_command(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    monkeypatch.setattr(backstop.os, "replace", _BreakReplace())
    assert _run(monkeypatch, repo, command="cat <<'EOF' > big.py") == 1
    entry = _ledger_entries(tmp_path)[0]
    assert entry["event"] == "degraded"
    assert entry["reason"] == "state-unwritable"
    assert entry["command"] is None  # 훅 자신의 상태를 서술하는 이벤트다


def test_ledger_records_evaluation_failures_in_both_lanes(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    real_run = subprocess.run

    def flaky(argv, **kwargs):
        if "rev-list" in argv:  # 두 lane의 평가를 함께 실패시킨다
            raise subprocess.TimeoutExpired(argv, backstop.GIT_TIMEOUT_SECONDS)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(backstop.subprocess, "run", flaky)
    assert _run(monkeypatch, repo) == 1
    entries = _ledger_entries(tmp_path)
    assert len(entries) == 2  # 한 호출이 한 줄이라는 뜻이 아니다
    assert {e["reason"] for e in entries} == {
        "branch-eval-failed",
        "head-eval-failed",
    }
    assert all(e["event"] == "degraded" for e in entries)
    assert all(e["command"] is None for e in entries)


def test_ledger_keeps_the_warning_that_stderr_drops(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "branch", "master")
    assert _run(monkeypatch, repo) == 0  # master 최초 관찰
    state = _read_state(repo)
    state["seen"]["refs/heads/master"] = "0" * 40
    _write_state(repo, state)
    _git(repo, "checkout", "-q", "master")
    _commit(repo, "feat: moves master")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "feat: on main")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    assert "NOT checked" not in capsys.readouterr().err  # stderr에서는 버려진다
    events = [(e["event"], e["reason"]) for e in _ledger_entries(tmp_path)]
    assert ("block", "protected-branch") in events
    assert ("degraded", "branch-eval-failed") in events  # 흔적은 원장에 남는다


def test_ledger_stays_empty_on_pass_and_fail_open_paths(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/x")
    _commit(repo, "feat: nothing wrong here")
    assert _run(monkeypatch, repo) == 0
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _run(monkeypatch, empty) == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert backstop.main() == 1
    assert _ledger_entries(tmp_path) == []


def test_ledger_carries_session_id_and_cwd(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    assert _run(monkeypatch, repo, session_id="sess-42") == backstop.EXIT_BLOCK
    entry = _ledger_entries(tmp_path)[0]
    assert entry["session_id"] == "sess-42"
    assert entry["cwd"] == str(repo)


def test_ledger_session_id_is_null_when_absent(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    assert _ledger_entries(tmp_path)[0]["session_id"] is None


def test_ledger_failure_never_downgrades_a_block(monkeypatch, capsys, tmp_path):
    # _log의 존재 이유. record_block이 던지면(시그니처 드리프트 등) 예외가
    # main() 밖으로 나가 run()이 1을 반환하고 차단이 통과로 뒤집힌다.
    from harness.blocklog import blocklog as blocklog_module

    def boom(**kwargs):
        raise TypeError("signature drift")

    monkeypatch.setattr(blocklog_module, "record_block", boom)
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    assert _ledger_entries(tmp_path) == []


# --- 프롤로그·환경 fail-open --------------------------------------------------


def test_non_repo_cwd_passes(monkeypatch, capsys, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _run(monkeypatch, empty) == 0


def test_git_binary_failure_fails_open(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")

    def boom(argv, **kwargs):
        raise OSError("no git")

    monkeypatch.setattr(backstop.subprocess, "run", boom)
    assert _run(monkeypatch, repo) == 0  # 저장소 해석 실패 → 통과


def test_malformed_payloads_never_block(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert backstop.main() == 1
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_name": "Write"})))
    assert backstop.main() == 0
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {}}))
    )
    assert backstop.main() == 0


def test_run_catchall_fails_open(monkeypatch, capsys):
    def explode():
        raise RuntimeError("boom")

    monkeypatch.setattr(backstop, "main", explode)
    assert backstop.run() == 1
    assert "fail-open" in capsys.readouterr().err


# --- 순수 함수 단위 테스트 ----------------------------------------------------


def test_moved_refs_pure_logic():
    current = {"refs/heads/main": "b", "HEAD@x": "c", "refs/heads/master": "d"}
    seen = {"refs/heads/main": "a", "HEAD@x": "c"}
    moved = backstop._moved_refs(current, seen)
    assert moved == {
        "refs/heads/main": ("a", "b"),  # 이동
        "refs/heads/master": (None, "d"),  # 최초 관찰
    }


def test_branch_report_lists_every_offending_sha(monkeypatch):
    # 앞 12자가 서로 달라야 절단을 실제로 감지한다.
    shas = [f"{i:x}" * 40 for i in range(1, 9)]
    text = backstop._branch_report("main", "a" * 40, "b" * 40, shas, True)
    assert "8 commit(s)" in text
    for sha in shas:
        assert sha[:12] in text  # 절단 없음 — 잘린 SHA는 다시 호명되지 않는다
    assert "git branch -f" not in text  # 절차는 규칙 파일 소관
    # 원격 ref 부재는 사실로만 알린다 — 정당성도, 무엇이 제외됐는지도 주장하지
    # 않는다. 근거는 _branch_report docstring (PR #114 rounds 5, 7, 8).
    assert "No remote 'main' exists here" not in text  # ref가 있으면 싣지 않는다
    absent = backstop._branch_report("main", "a" * 40, "b" * 40, shas, False)
    assert "No remote 'main' exists here" in absent
    assert "legitimate" not in absent  # 정당성 주장 금지
    assert "excluded" not in absent  # 제외 여부 주장 금지
