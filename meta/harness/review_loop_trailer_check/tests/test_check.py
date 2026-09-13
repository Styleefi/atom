# 훅 본체(main)를 실 git 저장소와 PostToolUse 페이로드로 통합 검증하는 테스트
"""main() 통합 테스트.

임시 저장소에 커밋을 만들고 페이로드를 stdin으로 넣어 stdout JSON(보고)과 원장
줄, 상태 파일을 본다. 설계 불변식 — 절대 차단하지 않음(exit 0/1뿐), 루프 밖
커밋은 침묵, SHA당 한 번만 보고, 실패는 전부 통과 방향 — 를 케이스로 고정한다.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.review_loop_trailer_check import check

_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}

LOOP = "Review-loop: PR #162 round 1"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=_GIT_ENV, check=True
    )
    return result.stdout.strip()


def _make_repo(tmp_path: Path, with_remote: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    if with_remote:
        remote = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(remote)],
            capture_output=True, env=_GIT_ENV, check=True,
        )
        _git(repo, "remote", "add", "origin", str(remote))
    return repo


def _commit(repo: Path, subject: str, *trailers: str, files: dict[str, str] | None = None) -> str:
    for name, content in (files or {}).items():
        (repo / name).write_text(content, encoding="utf-8")
        _git(repo, "add", name)
    message = subject if not trailers else subject + "\n\n" + "\n".join(trailers) + "\n"
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _run(monkeypatch, repo: Path, command: str = "true", capsys=None) -> tuple[int, str]:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo), "session_id": "s1"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    rc = check.main()
    out = capsys.readouterr().out if capsys is not None else ""
    return rc, out


def _context(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""


def _ledger(tmp_path: Path) -> list[dict]:
    path = tmp_path / "state" / "atom" / "guard-blocklog.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _baseline(monkeypatch, tmp_path: Path, **kwargs) -> Path:
    """main에 초기 커밋을 push하고 feat 브랜치에서 첫 관측(기록만)까지 마친 저장소."""
    repo = _make_repo(tmp_path, **kwargs)
    _commit(repo, "chore: init")
    if kwargs.get("with_remote", True):
        _git(repo, "push", "-q", "-u", "origin", "main")
    _git(repo, "checkout", "-q", "-b", "feat/x")
    assert _run(monkeypatch, repo) == (0, "")
    return repo


def test_first_observation_records_only(monkeypatch, tmp_path, capsys) -> None:
    repo = _make_repo(tmp_path)
    _commit(repo, "chore: init")
    assert _run(monkeypatch, repo, capsys=capsys) == (0, "")
    state = json.loads((repo / ".git" / check.STATE_FILENAME).read_text())
    assert list(state["heads"].values()) == [_git(repo, "rev-parse", "HEAD")]


def test_compliant_loop_commit_is_silent(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: x", LOOP, "Prose: none")
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")
    assert _ledger(tmp_path) == []


def test_missing_prose_trailer_is_reported_by_sha_only(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    sha = _commit(repo, "feat: secret subject", LOOP)
    rc, out = _run(monkeypatch, repo, "git commit -m x", capsys)
    assert rc == 0
    context = _context(out)
    assert f"{sha[:7]}: {check.REASON_MISSING_PROSE}" in context
    assert "secret subject" not in context and "PR #162" not in context
    [entry] = _ledger(tmp_path)
    assert (entry["event"], entry["harness"], entry["reason"], entry["command"]) == (
        "report", check.HARNESS_ID, check.REASON_MISSING_PROSE, None
    )


def test_malformed_values_are_reported(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    sha = _commit(repo, "feat: x", "Review-loop: PR #? round 0", "Prose: 1 new; attacked twice")
    _, out = _run(monkeypatch, repo, "git commit -m x", capsys)
    assert f"{sha[:7]}: {check.REASON_MALFORMED_REVIEW_LOOP}+{check.REASON_MALFORMED_PROSE}" in _context(out)


def test_none_with_new_prose_is_reported(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    sha = _commit(repo, "docs: x", LOOP, "Prose: none", files={"a.md": "A brand new sentence lands here.\n"})
    _, out = _run(monkeypatch, repo, "git commit -m x", capsys)
    assert _context(out).startswith(f"{check.TAG} {sha[:7]}: {check.REASON_NONE_WITH_NEW_PROSE}")


def test_attacked_with_new_prose_is_silent(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "docs: x", LOOP, "Prose: attacked", files={"a.md": "A brand new sentence lands here.\n"})
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")


def test_missing_prose_still_runs_the_prose_check(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    sha = _commit(repo, "docs: x", LOOP, files={"a.md": "A brand new sentence lands here.\n"})
    _, out = _run(monkeypatch, repo, "git commit -m x", capsys)
    assert f"{sha[:7]}: {check.REASON_MISSING_PROSE}+{check.REASON_NONE_WITH_NEW_PROSE}" in _context(out)


def test_body_beyond_trailers_is_reported(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "feat: x\n\nWhy this change.\n\n" + LOOP + "\nProse: none\n")
    sha = _git(repo, "rev-parse", "HEAD")
    _, out = _run(monkeypatch, repo, "git commit -m x", capsys)
    assert f"{sha[:7]}: {check.REASON_BODY_BEYOND_TRAILERS}" in _context(out)


def test_commit_outside_a_loop_is_ignored(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: plain", files={"a.md": "Prose without any trailer at all.\n"})
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")


def test_missing_review_loop_on_a_loop_branch_is_reported(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: first", LOOP, "Prose: none")
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")
    sha = _commit(repo, "feat: forgot the trailers")
    _, out = _run(monkeypatch, repo, "git commit -m x", capsys)
    assert _context(out) == f"{check.TAG} {sha[:7]}: {check.REASON_MISSING_REVIEW_LOOP} — see {check.RULE_PATH}"


@pytest.mark.parametrize(
    "command",
    [
        "git -C . commit -q -m x",
        "FOO=1 git cherry-pick abc",
        "ls && git revert HEAD",
        "git add a\ngit commit -m x",
        "cd repo\ngit commit -m x",
        "git commit -m \"$(cat <<'EOF'\nfeat: x\n\nbody\nEOF\n)\"",
    ],
)
def test_authoring_verbs_are_recognised(command: str) -> None:
    assert check._authoring_count(command) == 1


@pytest.mark.parametrize("command", ["true", "git merge main", "git rebase main", "git pull", "echo 'git commit'", "git log"])
def test_non_authoring_commands_are_not(command: str) -> None:
    assert check._authoring_count(command) == 0


def test_two_commits_in_one_call_count_twice() -> None:
    assert check._authoring_count("git commit -m a && git commit -m b") == 2


def test_owner_commit_before_the_agents_commit_is_not_reported(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: first", LOOP, "Prose: none")
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")
    _commit(repo, "feat: owner made this in a gui")
    _commit(repo, "feat: agent commit", LOOP, "Prose: none")
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")


def test_head_moved_by_an_owner_commit_is_ignored(monkeypatch, tmp_path, capsys) -> None:
    # 루프 중 브랜치라도, 무관한 명령 뒤에 나타난 트레일러 없는 커밋은 오너의 것으로 본다.
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: first", LOOP, "Prose: none")
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")
    _commit(repo, "feat: owner made this in a gui")
    assert _run(monkeypatch, repo, "ls", capsys) == (0, "")


def test_upstream_commits_merged_in_are_ignored(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: first", LOOP, "Prose: none")
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "feat: upstream work", files={"u.md": "Upstream prose without trailers.\n"})
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "feat/x")
    _git(repo, "merge", "-q", "--no-edit", "main")
    assert _run(monkeypatch, repo, "git merge main", capsys) == (0, "")


def test_repo_without_main_refs_treats_the_branch_as_outside_a_loop(monkeypatch, tmp_path, capsys) -> None:
    repo = _make_repo(tmp_path, with_remote=False)
    _git(repo, "checkout", "-q", "-b", "trunk")
    _commit(repo, "chore: init")
    assert _run(monkeypatch, repo, capsys=capsys) == (0, "")
    _commit(repo, "feat: first", LOOP, "Prose: none")
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")
    _commit(repo, "feat: no trailers")
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")


def test_each_sha_is_reported_once(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    _commit(repo, "feat: x", LOOP)
    _, out = _run(monkeypatch, repo, "git commit -m x", capsys)
    assert _context(out)
    _git(repo, "checkout", "-q", "main")
    assert _run(monkeypatch, repo, capsys=capsys) == (0, "")
    _git(repo, "checkout", "-q", "feat/x")
    assert _run(monkeypatch, repo, capsys=capsys) == (0, "")


def test_commit_and_push_in_one_call_is_still_checked(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    sha = _commit(repo, "feat: x", LOOP)
    _git(repo, "push", "-q", "-u", "origin", "feat/x")
    _, out = _run(monkeypatch, repo, "git commit -m x && git push", capsys)
    assert sha[:7] in _context(out)


def test_commit_already_on_main_is_not_checked(monkeypatch, tmp_path, capsys) -> None:
    repo = _make_repo(tmp_path)
    _commit(repo, "chore: init")
    assert _run(monkeypatch, repo, capsys=capsys) == (0, "")
    _commit(repo, "feat: on main", LOOP)
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")


def test_non_bash_or_non_repo_cwd_is_silent(monkeypatch, tmp_path, capsys) -> None:
    payload = {"tool_name": "Read", "tool_input": {}, "cwd": str(tmp_path)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert check.main() == 0
    assert _run(monkeypatch, tmp_path, capsys=capsys) == (0, "")


def test_malformed_stdin_fails_open(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("{"))
    assert check.main() == 1
    assert check.TAG in capsys.readouterr().err


def test_corrupt_state_file_fails_open(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    (repo / ".git" / check.STATE_FILENAME).write_text("{not json", encoding="utf-8")
    _commit(repo, "feat: x", LOOP)
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")  # 재관측: 기록만


def test_lost_old_object_rebaselines(monkeypatch, tmp_path, capsys) -> None:
    repo = _baseline(monkeypatch, tmp_path)
    path = repo / ".git" / check.STATE_FILENAME
    state = json.loads(path.read_text())
    state["heads"] = {k: "0" * 40 for k in state["heads"]}
    path.write_text(json.dumps(state), encoding="utf-8")
    _commit(repo, "feat: x", LOOP)
    assert _run(monkeypatch, repo, "git commit -m x", capsys) == (0, "")
    assert list(json.loads(path.read_text())["heads"].values()) == [_git(repo, "rev-parse", "HEAD")]


def test_run_never_exits_two(monkeypatch, capsys) -> None:
    monkeypatch.setattr(check, "main", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert check.run() == 1
    assert check.TAG in capsys.readouterr().err
