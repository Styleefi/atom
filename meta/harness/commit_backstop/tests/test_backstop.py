# commit_backstop의 그래프 판정·헤더 검사·상태 관리·fail-open 경로를 실 git 저장소로 검증하는 테스트
"""backstop 모듈 테스트.

commit_guard 테스트와 달리 git을 mock하지 않는다 — 판정이 그래프 연산이므로
tmp_path에 실제 저장소(+bare remote)를 만들어 결정성 있게 검증한다. 설계
불변식 — 오탐 금지(정당한 동기화·merge/자동생성 제목), 실패는 전부 통과
방향, 1회 보고(dedup), 절대 SHA 복구 지시 — 를 케이스로 고정한다.
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


def test_direct_commit_on_main_blocks_with_absolute_sha_recovery(
    monkeypatch, capsys, tmp_path
):
    repo = _baseline(monkeypatch, tmp_path)
    old = _git(repo, "rev-parse", "main")
    _commit(repo, "feat: direct on main")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert old in err  # 절대 SHA 복구 지시
    assert "HEAD~" not in err
    assert "Preserve the work FIRST" in err
    assert "git branch -f main" in err


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
    err = capsys.readouterr().err
    assert "1." in err and "2." in err and "3." in err
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
    assert "git branch -f master" in err
    assert "git branch -f main" not in err


def test_multiple_commits_reported_once_with_count(monkeypatch, capsys, tmp_path):
    repo = _baseline(monkeypatch, tmp_path)
    for i in range(3):
        _commit(repo, f"feat: bulk {i}")
    assert _run(monkeypatch, repo) == backstop.EXIT_BLOCK
    err = capsys.readouterr().err
    assert "3 commit(s)" in err
    assert err.count("Recover with EXACTLY") == 1


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
    # 지시의 하중은 rewrite 금지문과 HEAD 동일성 앵커에 걸린다 — 이 둘이 빠지면
    # "전부 amend하라"는 #64 이전 의미로 되돌아간다 (#64, PR #114 round 1).
    assert "do NOT rewrite" in err
    assert "git rev-parse HEAD" in err
    assert "git rev-list " in err  # 평가 구간 열거 명령
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
    assert "Recover with EXACTLY" in err  # 브랜치 위반
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


def test_shortlist_caps_report(monkeypatch):
    shas = [f"{i:040d}" for i in range(8)]
    text = backstop._shortlist(shas)
    assert "(8 total)" in text
    assert text.count(",") == backstop.REPORT_SHA_LIMIT  # 5개 + 총계 표기
