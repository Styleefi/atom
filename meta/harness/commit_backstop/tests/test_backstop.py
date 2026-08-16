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


def _run(monkeypatch, repo, command="true") -> int:
    """PostToolUse payload를 stdin으로 넣고 main()을 실행한다."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(repo),
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return backstop.main()


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
    # 절차는 규칙 파일이 보유한다 — stderr에 이력 수술 명령이 실리면 그 문장이
    # 저장소 상태 전제를 지고, 문서화된 오탐에서 정당한 전진을 되감는다
    # (PR #114 rounds 1-4에서 반복 실측).
    assert "git branch -f" not in err
    assert "meta/rules/commit-backstop.md" in err


def test_unpushed_branch_report_states_facts_without_surgery(
    monkeypatch, capsys, tmp_path
):
    # 원격 main ref가 없으면 제외 집합이 비어 모든 전진이 위반으로 보인다
    # (문서화된 오탐). 보고문이 절차를 싣지 않으므로 최악의 결과는 불필요한
    # 오너 보고 1회이며, 정당한 전진이 되감기지 않는다.
    repo = _make_repo(tmp_path)
    _commit(repo, "chore: init")
    assert _run(monkeypatch, repo) == 0  # 최초 관찰
    _commit(repo, "feat: still unpushed")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert "git branch -f" not in err
    assert "No remote 'main' exists here" in err  # 오탐 가능성을 오너에게 전달
    assert "do not push until they decide" in err


def test_at_most_one_history_rewrite_instruction_per_report(
    monkeypatch, capsys, tmp_path
):
    # 한 stderr 블록에 이력을 고치라는 지시가 둘이면 순서를 산문으로 정해야
    # 하고, 그 문장이 다시 결함이 된다 (라운드 3의 순서 지침 → 라운드 4의
    # 최상위 발견). 합성 규칙은 코드가 강제한다.
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


def test_corrupt_or_invalid_state_is_treated_as_first_run(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    with open(_state_path(repo), "w", encoding="utf-8") as fp:
        fp.write("{ not json")
    assert _run(monkeypatch, repo) == 0  # 손상 → 최초 실행 취급 (기록만)
    _commit(repo, "feat: next one")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK  # 이후는 정상 적발
    with open(_state_path(repo), "w", encoding="utf-8") as fp:
        json.dump({"seen": [], "checked": {}}, fp)  # 유효 JSON, 스키마 불일치
    _commit(repo, "feat: after schema break")
    assert _run(monkeypatch, repo) == 0  # 최초 실행 취급
    _commit(repo, "feat: caught again")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK


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


def test_git_remote_failure_fails_open(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: on main")
    monkeypatch.setattr(backstop, "_remote_names", lambda cwd: None)
    assert _run(monkeypatch, repo) == 1  # 스킵 (비차단)


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
    # 원격 ref가 있으면 오탐 변명을 싣지 않는다 — 진짜 위반을 올리는
    # 에이전트에게 빠져나갈 구실이 된다 (PR #114 round 5).
    assert "may be legitimate" not in text
    assert "may be legitimate" in backstop._branch_report(
        "main", "a" * 40, "b" * 40, shas, False
    )
