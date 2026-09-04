# commit_publication의 판정·종료 코드·출력 어휘를 실 저장소로 고정하는 테스트
"""commit-publication 테스트.

git을 mock하지 않는다 — 판정이 그래프 연산이고, 이 도구가 존재하는 이유인 결함들이
전부 "git이 실제로 무엇을 내는가"에 대한 오해였기 때문이다. tmp_path에 실 저장소와
bare remote(필요하면 `file://`)를 만들어 검증한다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.commit_publication import check

_REPO_ROOT = Path(__file__).resolve().parents[4]

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


def _git(cwd, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env=_GIT_ENV,
        check=True,
    )
    return result.stdout.strip()


def _commit(repo, subject: str) -> str:
    _git(repo, "commit", "--allow-empty", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def _bare(tmp_path, name: str, branch: str = "main"):
    remote = tmp_path / f"{name}.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", branch, str(remote)],
        capture_output=True,
        env=_GIT_ENV,
        check=True,
    )
    return remote


def _work(tmp_path, name: str, branch: str = "main"):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", branch)
    return repo


def _run(monkeypatch, repo, *argv: str) -> int:
    """도구를 repo에서 실행하고 종료 코드를 돌려준다."""
    monkeypatch.chdir(repo)
    return check._check(list(argv))


def _short(sha: str) -> str:
    return sha[:12]


@pytest.fixture(autouse=True)
def _reset_repo_dir():
    """모든 테스트를 `-C` 잔여물 없는 상태에서 시작시킨다.

    `_repo_dir` 은 모듈 전역이고 지우는 것은 `_check` 뿐이다. `judge` 나 `_report` 를 직접
    부르는 테스트는 앞 테스트가 남긴 — 이미 사라진 — tmp_path 를 가리킨 채 돌 수 있다.
    한 테스트가 손으로 지우고 있었지만, 손으로 지우는 방어는 다음에 **추가되는** 테스트를
    덮지 않는다. 잊는 것이 가능한 자리를 없앤다.
    """
    check._repo_dir = None
    yield
    check._repo_dir = None


# --------------------------------------------------------------------------
# T0 — 불변식: 비교 대상 브랜치 집합이 훅의 상수와 같다
# --------------------------------------------------------------------------


def test_protected_branches_match_the_hook() -> None:
    # 네 번째 결함(GitFlow 면죄)은 이 두 값이 어긋나서 생겼다. 산문으로 다시 쓰는 대신
    # 결속한다 — 규약이 테스트로 옮겨갈 때에야 그 부류가 닫힌다(review-loop).
    #
    # skip은 commit_backstop 부재 한 조건으로 좁힌다. 그 훅은 자식 프로젝트가 정합하게
    # 제거할 수 있고(REMOVABLE), 그 외 어떤 조건부도 두지 않는다.
    backstop = pytest.importorskip("harness.commit_backstop.backstop")
    assert check.PROTECTED_BRANCHES == backstop.PROTECTED_BRANCHES


# --------------------------------------------------------------------------
# 결함 고정
# --------------------------------------------------------------------------


def _gitflow(tmp_path):
    """기본 브랜치 develop, main도 존재. 커밋 X는 develop에만 있고 로컬 main에 머지됨."""
    remote = _bare(tmp_path, "remote", branch="develop")
    src = _work(tmp_path, "src", branch="develop")
    _git(src, "remote", "add", "origin", str(remote))
    base = _commit(src, "chore: base")
    _git(src, "push", "-q", "origin", "develop")
    _git(src, "branch", "main", base)
    _git(src, "push", "-q", "origin", "main")
    x = _commit(src, "feat: on develop only")
    _git(src, "push", "-q", "origin", "develop")
    _git(src, "checkout", "-q", "main")
    _git(src, "merge", "-q", "--ff-only", "develop")
    return src, x, base


def test_gitflow_commit_on_develop_only_is_not_on_main(monkeypatch, capsys, tmp_path):
    # 규칙이 원격 HEAD 브랜치(develop)를 fetch하던 시절 이 경우가 "사각지대"로
    # 면죄됐다. 훅이 옳고, 도구는 not on을 내야 한다.
    src, x, _ = _gitflow(tmp_path)
    assert _run(monkeypatch, src, _short(x)) == check.EXIT_SOME_NOT_ON
    assert _short(x) in capsys.readouterr().out


def test_gitflow_commit_on_main_is_on(monkeypatch, capsys, tmp_path):
    # 짝 테스트. 아무것도 fetch하지 않고 늘 not-on을 답하는 스텁은 위 테스트를 통과하므로,
    # 같은 저장소에서 원격 main에 있는 커밋이 on으로 나오는지도 함께 고정한다.
    src, _, base = _gitflow(tmp_path)
    assert _run(monkeypatch, src, _short(base)) == check.EXIT_ALL_ON


def _published(tmp_path, branch: str = "main"):
    """원격에 발행된 커밋 하나와, 로컬 전용 커밋 하나를 가진 저장소."""
    remote = _bare(tmp_path, "remote", branch=branch)
    src = _work(tmp_path, "src", branch=branch)
    _git(src, "remote", "add", "origin", str(remote))
    pub = _commit(src, "chore: published")
    _git(src, "push", "-q", "origin", branch)
    local = _commit(src, "chore: local only")
    return src, pub, local


def test_mixed_list_reports_the_unpublished_sha(monkeypatch, capsys, tmp_path):
    # 결함 #3: 단일 SHA 판정이 미발행 커밋을 담은 보고를 통째로 기각할 수 있었다.
    src, pub, local = _published(tmp_path)
    rc = _run(monkeypatch, src, _short(pub), _short(local))
    out = capsys.readouterr().out
    assert rc == check.EXIT_SOME_NOT_ON
    assert _short(local) in out
    assert "1 of 2" in out


def test_unresolvable_sha_in_a_mixed_run_makes_the_whole_run_undecided(
    monkeypatch, capsys, tmp_path
):
    # on / not-on / 해석 불가가 섞이면 exit 5가 아니라 exit 3이다. `N of M` 형식은
    # "M 중 k개만 판정했다"를 표현할 수 없어, 그대로 두면 판정된 적 없는 SHA를 면죄하는
    # 인쇄된 거짓이 된다 — 네 번째 결함과 같은 부류.
    src, pub, local = _published(tmp_path)
    rc = _run(monkeypatch, src, _short(pub), _short(local), "deadbeefdead")
    out = capsys.readouterr().out
    assert rc == check.EXIT_UNDECIDED
    assert "2 of 3 listed commits were judged" in out
    assert _short(local) in out          # not-on 줄은 사라지지 않는다
    assert "deadbeefdead" in out         # 미해석 SHA도 이름이 불린다


def test_single_branch_clone_of_another_branch_still_answers(
    monkeypatch, tmp_path
):
    # 훅의 사각지대 그 자체: 로컬에 origin/main이 없다. main 클론이면 순수 로컬 구현도
    # 통과하므로 develop을 클론한다.
    remote = _bare(tmp_path, "remote", branch="develop")
    src = _work(tmp_path, "src", branch="develop")
    _git(src, "remote", "add", "origin", str(remote))
    base = _commit(src, "chore: base")
    _git(src, "push", "-q", "origin", "develop")
    _git(src, "branch", "main", base)
    _git(src, "push", "-q", "origin", "main")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--single-branch", "--branch", "develop",
         str(remote), str(clone)],
        capture_output=True, env=_GIT_ENV, check=True,
    )
    assert not (clone / ".git" / "refs" / "remotes" / "origin" / "main").exists()
    assert _run(monkeypatch, clone, _short(base)) == check.EXIT_ALL_ON


def test_commit_on_master_only_is_found(monkeypatch, tmp_path):
    # main과 master가 둘 다 있고 검사 SHA가 master에만 있다. FETCH_HEAD를 읽는 구현은
    # 첫 줄(main)을 집어 not-on을 내므로 여기서 빨개진다.
    remote = _bare(tmp_path, "remote", branch="main")
    src = _work(tmp_path, "src", branch="main")
    _git(src, "remote", "add", "origin", str(remote))
    _commit(src, "chore: base")
    _git(src, "push", "-q", "origin", "main")
    _git(src, "checkout", "-q", "-b", "master")
    only = _commit(src, "chore: on master only")
    _git(src, "push", "-q", "origin", "master")
    assert _run(monkeypatch, src, _short(only)) == check.EXIT_ALL_ON


def test_shallow_clone_refuses_before_the_network(monkeypatch, capsys, tmp_path):
    # shallow는 not-on 쪽으로 거짓말하고 그 "없다"가 오너를 보호 브랜치 되감기로 보낸다.
    # 다른 조건은 전부 성공하도록 구성해, shallow 검사를 지우면 다른 이유로 초록이 되지
    # 못하게 한다. 로컬 경로 clone은 --depth를 무시하므로 file:// 이 필수다.
    remote = _bare(tmp_path, "remote", branch="main")
    src = _work(tmp_path, "src", branch="main")
    _git(src, "remote", "add", "origin", str(remote))
    _commit(src, "chore: one")
    pub = _commit(src, "chore: two")
    _git(src, "push", "-q", "origin", "main")

    clone = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{remote}", str(clone)],
        capture_output=True, env=_GIT_ENV, check=True,
    )
    assert _git(clone, "rev-parse", "--is-shallow-repository") == "true"
    rc = _run(monkeypatch, clone, _short(pub))
    assert rc == check.EXIT_UNDECIDED
    assert "shallow" in capsys.readouterr().out




def test_no_main_or_master_on_the_remote_hides_the_remote_branch_name(
    monkeypatch, capsys, tmp_path
):
    # 규칙 밖 배치다. 추측하지 않고 물러나며, 원격이 보고한 이름은 인쇄하지 않는다.
    remote = _bare(tmp_path, "remote", branch="trunk")
    src = _work(tmp_path, "src", branch="trunk")
    _git(src, "remote", "add", "origin", str(remote))
    tip = _commit(src, "chore: base")
    _git(src, "push", "-q", "origin", "trunk")
    rc = _run(monkeypatch, src, _short(tip))
    out = capsys.readouterr().out
    assert rc == check.EXIT_UNDECIDED
    assert "trunk" not in out


def test_tail_glob_collision_pins_the_real_branch(monkeypatch, tmp_path):
    # ls-remote 의 ref 인자는 정확 일치가 아니라 꼬리 글롭이라, 원격이
    # refs/heads/refs/heads/main 을 두면 같은 패턴에 걸린다. 바이트 정확 비교가 없으면
    # 원격이 고른 SHA가 pin이 된다.
    remote = _bare(tmp_path, "remote", branch="main")
    src = _work(tmp_path, "src", branch="main")
    _git(src, "remote", "add", "origin", str(remote))
    real = _commit(src, "chore: real main")
    _git(src, "push", "-q", "origin", "main")
    _git(src, "checkout", "-q", "-b", "decoy")
    decoy = _commit(src, "chore: decoy")
    _git(src, "push", "-q", "origin", "decoy:refs/heads/refs/heads/main")
    _git(src, "checkout", "-q", "main")

    # 진짜 main tip은 on, 미끼 쪽만 있는 커밋은 not on 이어야 한다.
    assert _run(monkeypatch, src, _short(real)) == check.EXIT_ALL_ON
    assert _run(monkeypatch, src, _short(decoy)) == check.EXIT_SOME_NOT_ON


def test_stale_tracking_ref_is_never_the_pin(monkeypatch, tmp_path):
    # 원격 main이 X 뒤로 되감겼는데 로컬 추적 ref는 아직 X를 담고 있다. 로컬 ref에서
    # pin을 뽑는 구현은 여기서 on을 내므로 빨개진다.
    remote = _bare(tmp_path, "remote", branch="main")
    src = _work(tmp_path, "src", branch="main")
    _git(src, "remote", "add", "origin", str(remote))
    base = _commit(src, "chore: base")
    x = _commit(src, "chore: x")
    _git(src, "push", "-q", "origin", "main")
    _git(src, "fetch", "-q", "origin")
    assert _git(src, "rev-parse", "refs/remotes/origin/main") == x
    _git(src, "push", "-q", "-f", "origin", f"{base}:refs/heads/main")
    assert _run(monkeypatch, src, _short(x)) == check.EXIT_SOME_NOT_ON


def test_output_carries_no_instruction_vocabulary(monkeypatch, capsys, tmp_path):
    # 원래 산문의 첫 결함이 에이전트용 탈출구였다. 도구는 저장소 이력에 대해 아무것도
    # 지시하지 않고, "사각지대였다"고도 말하지 않는다.
    src, pub, local = _published(tmp_path)
    for argv in ([_short(pub)], [_short(local)], ["deadbeefdead"]):
        _run(monkeypatch, src, *argv)
        text = capsys.readouterr().out.lower()
        for banned in ("blind spot", "override", "you may", "continue"):
            assert banned not in text


def test_exit_four_states_a_fact_and_exit_five_names_the_shas(
    monkeypatch, capsys, tmp_path
):
    # 비대칭. 양쪽 다 시제 한정어를 달고, exit 4는 보고를 해소한다고 말하지 않는다.
    src, pub, local = _published(tmp_path)
    _run(monkeypatch, src, _short(pub))
    on_out = capsys.readouterr().out
    assert "are on main/master at origin as of this fetch" in on_out or \
           "is on main/master at origin as of this fetch" in on_out
    _run(monkeypatch, src, _short(local))
    assert "as of this fetch" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 계약
# --------------------------------------------------------------------------


def test_url_form_works_and_never_prints_the_url(monkeypatch, capsys, tmp_path):
    # `git pull <URL>` 사각지대에 닿는 유일한 경로다. URL은 자격 증명을 담을 수 있고
    # 출력은 에이전트 컨텍스트로 들어가므로 고정 문구로 대체한다.
    remote = _bare(tmp_path, "remote", branch="main")
    src = _work(tmp_path, "src", branch="main")
    tip = _commit(src, "chore: base")
    _git(src, "push", "-q", str(remote), "main")

    rc = _run(monkeypatch, src, "--remote", str(remote), _short(tip))
    out = capsys.readouterr().out
    assert rc == check.EXIT_ALL_ON
    assert str(remote) not in out
    assert check.URL_TARGET_LABEL in out
    assert not (src / ".git" / "refs" / "remotes").exists()


def test_abbreviated_shas_are_accepted(monkeypatch, tmp_path):
    # 훅 보고문이 싣는 형태가 12자 축약이다.
    src, pub, _ = _published(tmp_path)
    assert _run(monkeypatch, src, pub[:12]) == check.EXIT_ALL_ON


def test_no_arguments_exits_caller_error_without_touching_git(monkeypatch, capsys):
    # 진입점 테스트가 인자 없이 빈 stdin으로 실행하므로, 네트워크에 닿기 전에 끝나야 한다.
    calls: list[list[str]] = []
    monkeypatch.setattr(
        check, "run_git", lambda args, **kw: calls.append(args) or (0, "")
    )
    assert check._check([]) == check.EXIT_CALLER
    assert calls == []
    assert check.TAG in capsys.readouterr().err


def test_local_branches_head_and_fetch_head_are_untouched(monkeypatch, tmp_path):
    """도구가 남기는 것은 추적 ref 하나뿐이다.

    refs/remotes/* 는 fetch 가 refspec 에 따라 정당하게 갱신하므로 범위에서 뺀다.
    FETCH_HEAD 는 다르다 — 도구는 그걸 읽지도 않으면서 덮어쓰기만 했고, 오너가
    `git fetch origin some-branch` 뒤 `git merge FETCH_HEAD` 를 하려던 참이면 엉뚱한
    커밋을 머지하게 만든다.
    """
    src, pub, _ = _published(tmp_path)
    fetch_head = src / ".git" / "FETCH_HEAD"
    sentinel = f"{pub}\t\tbranch 'someone-elses' of somewhere\n"
    fetch_head.write_text(sentinel, encoding="utf-8")

    before = _git(src, "for-each-ref", "refs/heads/"), _git(src, "rev-parse", "HEAD")
    _run(monkeypatch, src, _short(pub))
    after = _git(src, "for-each-ref", "refs/heads/"), _git(src, "rev-parse", "HEAD")
    assert before == after
    assert fetch_head.read_text(encoding="utf-8") == sentinel


def test_a_timed_out_call_leaves_no_lock_behind(monkeypatch, tmp_path):
    """타임아웃은 SIGTERM 유예를 거쳐야 git 이 lock 을 치울 수 있다.

    SIGKILL 만 보내면 git 의 정리 훅이 돌지 못해 `*.lock` 이 남고, 오너의 다음 git
    명령이 이유 없는 "Unable to create '...lock'" 으로 죽는다 — 이 도구는 git 의
    stderr 를 지워 놓으므로 원인을 아무도 설명해 주지 않는다.

    실제 git 을 느리게 만들 수 없으므로, 신호가 SIGTERM → (유예) → SIGKILL 순서로
    나가는지를 신호 자체로 확인한다.
    """
    import signal as _signal

    sent: list[int] = []
    real_signal_group = check._signal_group

    def _record(proc, sig):
        sent.append(sig)
        real_signal_group(proc, sig)  # 실제로도 보낸다 — 안 그러면 자식이 끝까지 잔다.

    monkeypatch.setattr(check, "_signal_group", _record)
    monkeypatch.setattr(check, "TERM_GRACE_SECONDS", 0.2)

    # 진짜 Popen 을 먼저 붙잡는다 — check.subprocess 는 subprocess 모듈 자체라,
    # 그 안에서 subprocess.Popen 을 부르면 패치된 자기 자신을 부르게 된다.
    real_popen = subprocess.Popen
    script = "import time; time.sleep(30)"
    monkeypatch.setattr(
        check.subprocess, "Popen",
        lambda argv, **kw: real_popen(
            [sys.executable, "-c", script],
            **{k: v for k, v in kw.items() if k != "env"},
        ),
    )
    rc, _ = check.run_git(["ls-remote", "nowhere"], timeout=0.2)
    assert rc == 124
    assert sent and sent[0] == _signal.SIGTERM, "SIGTERM must come first"


def test_default_remote_selection(monkeypatch, tmp_path):
    # 단일 remote면 그것, origin이 있으면 그것, 둘 다 아니면 호출자 잘못.
    src, pub, _ = _published(tmp_path)
    _git(src, "remote", "rename", "origin", "only")
    assert _run(monkeypatch, src, _short(pub)) == check.EXIT_ALL_ON

    other = _bare(tmp_path, "other", branch="main")
    _git(src, "remote", "add", "second", str(other))
    assert _run(monkeypatch, src, _short(pub)) == check.EXIT_CALLER

    _git(src, "remote", "rename", "only", "origin")
    assert _run(monkeypatch, src, _short(pub)) == check.EXIT_ALL_ON


def test_unregistered_remote_name_is_caller_error_not_undecided(
    monkeypatch, tmp_path
):
    # ls-remote 는 오타와 접속 불가에 똑같이 128을 낸다. git 목록과 먼저 대조하지 않으면
    # 에이전트의 오타가 "환경 문제"로 오너에게 보고된다.
    src, pub, _ = _published(tmp_path)
    assert _run(monkeypatch, src, "--remote", "orign", _short(pub)) == check.EXIT_CALLER


def test_malformed_sha_is_caller_error_and_unresolvable_is_undecided(
    monkeypatch, tmp_path
):
    src, pub, _ = _published(tmp_path)
    assert _run(monkeypatch, src, "zzzz") == check.EXIT_CALLER
    assert _run(monkeypatch, src, "deadbeefdead") == check.EXIT_UNDECIDED


def test_outside_a_repository_is_caller_error(monkeypatch, tmp_path):
    # cwd 문제는 호출자가 고칠 수 있다.
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _run(monkeypatch, plain, "deadbeefdead") == check.EXIT_CALLER


def test_failed_fetch_yields_no_verdict(monkeypatch, capsys, tmp_path):
    # fetch가 실패하면 아무것도 비교되지 않는다. FETCH_HEAD를 미리 심어 두어, 그 파일을
    # 읽는 구현이 판정을 만들어내지 못함을 함께 고정한다.
    src, pub, _ = _published(tmp_path)
    (src / ".git" / "FETCH_HEAD").write_text(
        f"{pub}\t\tbranch 'main' of somewhere\n", encoding="utf-8"
    )
    real = check.run_git

    def _fail_fetch(args, **kw):
        if args and args[0] == "fetch":
            return 128, ""
        return real(args, **kw)

    monkeypatch.setattr(check, "run_git", _fail_fetch)
    rc = _run(monkeypatch, src, _short(pub))
    out = capsys.readouterr().out
    assert rc == check.EXIT_UNDECIDED
    assert "on main/master" not in out


def test_the_runner_isolates_every_git_call(monkeypatch, tmp_path):
    # 이음매는 run_git 하나다. 여기서 kwargs를 단언하지 않으면 env= 나 stdin= 이
    # 조용히 사라져도 아무도 모른다.
    seen: list[dict] = []
    real_popen = subprocess.Popen

    class _Spy:
        def __init__(self, argv, **kwargs):
            seen.append({"argv": argv, **kwargs})
            self._p = real_popen(argv, **kwargs)

        def communicate(self, timeout=None):
            return self._p.communicate(timeout=timeout)

        @property
        def returncode(self):
            return self._p.returncode

        @property
        def pid(self):
            return self._p.pid

        def kill(self):
            self._p.kill()

        def wait(self):
            return self._p.wait()

    # 주변 환경과의 차분으로 뽑지 않는다 — 테스트 프로세스가 이미 같은 값을 export 하고
    # 있으면 그 키가 조용히 검증에서 빠지고, 넷 다 export 하면 단언 자체가 헛돈다.
    expected = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "SSH_ASKPASS_REQUIRE": "force",
    }
    assert check._isolated_env() | expected == check._isolated_env(), (
        "the isolation env no longer sets what this test pins"
    )
    overrides = expected

    src, pub, _ = _published(tmp_path)
    monkeypatch.setattr(check.subprocess, "Popen", _Spy)
    _run(monkeypatch, src, _short(pub))

    assert seen, "no git call was made"
    for call in seen:
        argv = call["argv"]
        assert argv[0] == "git"
        # 위치 고정 대신 존재로 본다 — -C 와 --no-replace-objects 가 앞에 붙을 수 있고,
        # 위치를 박아 두면 그 경로들이 이 단언에서 조용히 빠진다(라운드 2 지적).
        assert "credential.helper=" in argv
        assert argv[argv.index("credential.helper=") - 1] == "-c"
        assert call["stdin"] is subprocess.DEVNULL
        assert call["stderr"] is subprocess.DEVNULL
        assert call["start_new_session"] is True
        # env 단언은 _isolated_env() 에서 파생시킨다 — 손으로 열거하면 그 함수에 항목이
        # 늘어도 테스트가 따라가지 못한다. 실제로 러너가 env= 를 넘기지 않아 격리가 통째로
        # 꺼져 있던 것을 이 단언의 부재가 놓쳤다(라운드 1).
        for key, value in overrides.items():
            assert call["env"][key] == value


def test_credential_helpers_do_not_run(monkeypatch, tmp_path):
    # -c credential.helper= 만이 이 채널을 막는다. 나머지 변수로는 helper가 그대로
    # 실행된다(실측). HTTP 서버 없이 마커 파일로 확인해 오프라인을 유지한다.
    marker = tmp_path / "helper-ran"
    helper = tmp_path / "helper.sh"
    helper.write_text(f'#!/bin/sh\ntouch "{marker}"\n', encoding="utf-8")
    helper.chmod(0o755)
    cfg = tmp_path / "gitconfig"
    cfg.write_text(f"[credential]\n\thelper = {helper}\n", encoding="utf-8")

    src, pub, _ = _published(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    _run(monkeypatch, src, _short(pub))
    assert not marker.exists()


def test_the_command_is_not_copied_into_always_loaded_context() -> None:
    # 이 도구의 명령은 규칙 파일과 meta/README.md 에만 산다. rules_checker 는
    # @meta/rules/ import 만 동기 검증하므로 CLAUDE.md·템플릿의 사본은 아무도 감시하지
    # 않는다 — 사각지대 목록을 지우면서 감시 없는 사본을 늘릴 수는 없다.
    for rel in ("CLAUDE.md", "meta/templates/CLAUDE.template.md"):
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "commit_publication" not in text


def test_main_returns_undecided_on_an_internal_error(monkeypatch, capsys) -> None:
    # 잡히지 않은 예외는 파이썬 exit 1로 나가는데, 1은 uv 실패와도 겹치고 실질 판정과
    # 구분되지 않는다. 타입명만 싣는다 — 예외 문구는 서브프로세스 유래 텍스트를 담을 수 있다.
    def _boom(_argv):
        raise RuntimeError("secret from a subprocess")

    monkeypatch.setattr(check, "_check", _boom)
    monkeypatch.setattr(sys, "argv", ["prog", "deadbeefdead"])
    assert check.main() == check.EXIT_UNDECIDED
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "secret" not in err


def test_the_rule_cites_a_command_this_module_accepts() -> None:
    """규칙이 인용한 호출 문자열을 파싱해 모듈의 argv 계약과 대조한다.

    grep으로 "명령이 들어 있다"만 보면 인용이 낡아도 초록으로 남는다. 실제로 뜯어서
    모듈 경로와 옵션이 `_parse_args`가 받는 것인지 확인한다. 그리고 대체된 산문이
    다시 자라지 않는지도 함께 본다 — 그 절차가 결함 넷을 실었다.
    """
    rule = (_REPO_ROOT / "meta" / "rules" / "commit-backstop.md").read_text(
        encoding="utf-8"
    )
    cited = [
        line.strip()
        for line in rule.splitlines()
        if "harness.commit_publication" in line
    ]
    assert len(cited) == 1, "the rule must cite the command exactly once"

    tokens = cited[0].split()
    assert tokens[:5] == [
        "uv", "run", "--directory", "meta", "python",
    ], tokens
    assert tokens[5] == "-m" and tokens[6] == "harness.commit_publication"

    # 인용된 자리표시자를 실제 인자 파서에 통과시킨다.
    placeholders = tokens[7:]
    assert placeholders, "the citation must show where the SHAs go"
    # -C 는 허용이 아니라 **요구**한다. docstring 이 서브모듈 보고에 -C 를 쓰라고 하는데
    # 인용에 없으면 에이전트가 읽는 자리에서 그 경로가 보이지 않는다(라운드 2 지적).
    assert any(tok.strip("[]<>.").startswith("-C") for tok in placeholders), tokens
    for token in placeholders:
        stripped = token.strip("[]<>.")
        if stripped.startswith("-"):
            assert stripped.split("=")[0] in ("-C", "--remote"), stripped
    repo, remote, shas = check._parse_args(["--remote", "origin", "0" * 40])
    assert repo is None and remote == "origin" and shas == ["0" * 40]

    # 대체된 절차가 되살아나지 않았는지 확인한다.
    for gone in ("merge-base --is-ancestor", "FETCH_HEAD", "HEAD branch"):
        assert gone not in rule, f"the replaced procedure came back: {gone}"


def test_a_hex_named_ref_cannot_shadow_a_reported_sha(monkeypatch, tmp_path):
    """hex 이름 브랜치가 같은 접두사의 커밋을 가리지 못한다.

    rev-parse 는 ref 를 객체 이름보다 먼저 해석한다. 미발행 커밋 B 를 보고받았는데
    B 의 축약형과 똑같은 이름의 브랜치가 발행된 커밋 A 를 가리키면, 방어가 없을 때
    도구는 A 를 판정해 exit 4 — 거짓 면죄 — 를 낸다. 설계 전체가 피하려는 방향이다.
    """
    src, pub, local = _published(tmp_path)
    _git(src, "branch", _short(local), pub)
    assert _git(src, "rev-parse", f"{_short(local)}^{{commit}}") == pub

    rc = _run(monkeypatch, src, _short(local))
    assert rc == check.EXIT_UNDECIDED, "a shadowing ref must not produce a verdict"



def test_option_shaped_remote_values_are_rejected(monkeypatch, tmp_path):
    """`--remote` 값이 옵션 꼴이면 git 에 닿기 전에 거부한다.

    `--upload-pack=/tmp/x.sh` 는 URL 휴리스틱(`/:@` 포함)을 통과해 bare argv 항목으로
    `git ls-remote` 에 넘어가고, git 은 그걸 옵션으로 읽어 지정된 프로그램을 실행한다.
    SHA 는 hex 검증이 이미 막고 있었고 remote 값만 뚫려 있었다.
    """
    src, pub, _ = _published(tmp_path)
    for hostile in ("--upload-pack=/tmp/x.sh", "-o", "--exec=/bin/sh"):
        assert _run(monkeypatch, src, "--remote", hostile, _short(pub)) == \
            check.EXIT_CALLER
    # 옵션 꼴 토큰이 SHA 자리에 오는 경우도 같은 lane 이다.
    assert _run(monkeypatch, src, "--upload-pack=/tmp/x.sh") == check.EXIT_CALLER


def test_dash_c_judges_the_repository_it_points_at(monkeypatch, tmp_path):
    """`-C` 가 가리키는 저장소를 판정한다 — cwd 가 다른 곳이어도.

    규칙이 인용하는 `uv run --directory meta ...` 는 cwd 를 `meta/` 로 바꾼다(실측).
    그래서 "cwd 의 저장소를 판정한다"는 주장은 거짓이었고, 훅이 서브모듈이나 다른
    worktree 기준으로 낸 보고는 확인할 방법이 없었다. 주장을 좁히는 대신 코드가
    주장과 맞도록 경로를 받는다.
    """
    src, pub, local = _published(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    # 다른 디렉터리에서 실행해도 -C 로 가리킨 저장소에 대해 답한다.
    assert _run(monkeypatch, elsewhere, "-C", str(src), _short(pub)) == \
        check.EXIT_ALL_ON
    assert _run(monkeypatch, elsewhere, "-C", str(src), _short(local)) == \
        check.EXIT_SOME_NOT_ON
    # -C 없이 같은 자리에서 돌리면 저장소가 아니므로 호출자 잘못이다.
    assert _run(monkeypatch, elsewhere, _short(pub)) == check.EXIT_CALLER
    # 존재하지 않는 경로도 호출자 잘못이다.
    assert _run(monkeypatch, src, "-C", str(tmp_path / "nope"), _short(pub)) == \
        check.EXIT_CALLER


# core.graftsFile 은 뺐다 — git 2.53 은 그 설정을 조상 판정에 **쓰지 않는다**(실측:
# 같은 위조 파일이 GIT_GRAFT_FILE 과 .git/info/grafts 로는 rc 0 을 만들고 core.graftsFile
# 로는 rc 1 이다). 라운드 1 은 경로 해석 동작만 보고 그 설정에 방어를 붙였는데, 효과를
# 재는 이 방식은 애초에 그런 유령을 만들지 않는다.
@pytest.mark.parametrize(
    "mechanism", ["info/grafts", "GIT_GRAFT_FILE", "replace ref"]
)
def test_forged_ancestry_is_refused_however_it_was_forged(
    monkeypatch, capsys, tmp_path, mechanism: str
):
    """조상 관계가 위조돼 있으면 판정을 거부한다 — 위조 수단을 열거하지 않고.

    무관한 orphan 커밋을 발행된 커밋의 부모로 위조하면, 방어가 없을 때 도구는 그 orphan 을
    "on" 으로 — 거짓 면죄로 — 보고한다. 메커니즘 목록을 stat 하는 대신 같은 질문을 재작성을
    끈 채 한 번 더 던져 답이 갈리는지를 본다. 경로를 해석하지 않으므로 `-C` 와도 어긋나지
    않고, 앞으로 생길 재작성 수단까지 함께 덮인다.
    """
    src, pub, _ = _published(tmp_path)
    _git(src, "checkout", "-q", "--orphan", "island")
    island = _commit(src, "chore: unrelated island")
    _git(src, "checkout", "-q", "main")

    forgery = f"{pub} {island}\n"
    if mechanism == "info/grafts":
        info = src / ".git" / "info"
        info.mkdir(exist_ok=True)
        (info / "grafts").write_text(forgery, encoding="utf-8")
    elif mechanism == "GIT_GRAFT_FILE":
        elsewhere = tmp_path / "grafts-env"
        elsewhere.write_text(forgery, encoding="utf-8")
        monkeypatch.setenv("GIT_GRAFT_FILE", str(elsewhere))
    else:
        _git(src, "replace", "--graft", pub, island)

    # 위조가 실제로 답을 뒤집는지 먼저 확인한다 — 아니면 이 테스트는 아무것도 지키지 않는다.
    forged_rc = subprocess.run(
        ["git", "-C", str(src), "merge-base", "--is-ancestor", island, pub],
        capture_output=True, env={**_GIT_ENV, **os.environ}, check=False,
    ).returncode
    assert forged_rc == 0, "the fixture did not actually forge the ancestry"

    rc = _run(monkeypatch, src, _short(island))
    assert rc == check.EXIT_UNDECIDED
    assert "replace refs or a grafts file" in capsys.readouterr().out


def test_an_irrelevant_forgery_does_not_block_the_answer(monkeypatch, tmp_path):
    """조작이 있어도 이 SHA들의 조상 관계와 무관하면 그대로 답한다.

    메커니즘을 stat 하던 옛 게이트는 무관한 graft 에도 판정을 포기했다. 효과를 재면
    거짓 거부가 사라진다 — 같은 안전성에 더 많은 답.
    """
    src, pub, local = _published(tmp_path)
    _git(src, "checkout", "-q", "--orphan", "island")
    island = _commit(src, "chore: unrelated island")
    _git(src, "checkout", "-q", "main")
    # local 의 부모를 island 로 위조한다 — pub 의 조상 관계와는 무관하다.
    _git(src, "replace", "--graft", local, island)

    assert _run(monkeypatch, src, _short(pub)) == check.EXIT_ALL_ON


def test_a_rewind_is_answered_against_the_current_tip(monkeypatch, tmp_path):
    """읽는 사이 원격이 되감기면, 거부하지 말고 현재 tip 기준으로 답한다.

    그 창이 닫혀 있지 않으면 로컬에 이미 있던 옛 tip 이 pin 이 되어 `cat-file` 을 통과하고
    "on" 으로 — **면죄 방향으로** — 보고된다. 재고정하면 되감긴 커밋이 올바르게 not-on 이
    되고, 전진 push 였다면 답을 버리지 않는다.
    """
    src, pub, _ = _published(tmp_path)
    x = _commit(src, "chore: also published")
    _git(src, "push", "-q", "origin", "main")

    fetches = {"n": 0}
    real_run = check.run_git

    def _rewind_once_after_fetch(args, **kw):
        result = real_run(args, **kw)
        if args and args[0] == "fetch":
            fetches["n"] += 1
            if fetches["n"] == 1:
                _git(src, "push", "-q", "-f", "origin", f"{pub}:refs/heads/main")
        return result

    monkeypatch.setattr(check, "run_git", _rewind_once_after_fetch)
    rc = _run(monkeypatch, src, _short(x))
    assert fetches["n"] >= 2, "the fixture never triggered a re-pin"
    assert rc == check.EXIT_SOME_NOT_ON, "a settled rewind must be answered, not refused"


def test_a_remote_that_keeps_moving_yields_no_verdict(monkeypatch, capsys, tmp_path):
    """원격이 연속으로 움직이면 판정하지 않는다 — 재시도는 유계다."""
    src, pub, _ = _published(tmp_path)
    x = _commit(src, "chore: also published")
    _git(src, "push", "-q", "origin", "main")

    real_run = check.run_git

    def _move_after_every_fetch(args, **kw):
        result = real_run(args, **kw)
        if args and args[0] == "fetch":
            _commit(src, "chore: another")
            _git(src, "push", "-q", "origin", "main")
        return result

    monkeypatch.setattr(check, "run_git", _move_after_every_fetch)
    rc = _run(monkeypatch, src, _short(x))
    assert rc == check.EXIT_UNDECIDED
    assert "kept moving" in capsys.readouterr().out


def test_a_forged_sha_never_erases_the_not_on_shas(monkeypatch, capsys, tmp_path):
    """판정 불가 SHA 가 섞여도 이미 판정한 not-on 은 보고에서 사라지지 않는다.

    라운드 3 실측: judge 가 판정 불가를 **예외로** 던지자 배치 전체가 중단돼, 조치가
    필요한 미발행 커밋이 출력에서 사라지고 "nothing was compared" 라는 거짓 문장이 나갔다.
    SHA 단위 사실은 SHA 단위 채널로 나가야 한다.
    """
    src, pub, local = _published(tmp_path)
    _git(src, "checkout", "-q", "--orphan", "island")
    island = _commit(src, "chore: unrelated island")
    _git(src, "checkout", "-q", "main")
    _git(src, "replace", "--graft", pub, island)

    rc = _run(monkeypatch, src, _short(local), _short(island))
    out = capsys.readouterr().out
    assert rc == check.EXIT_UNDECIDED
    assert _short(local) in out, "the not-on SHA vanished from the report"
    assert _short(island) in out, "the unjudgeable SHA was not named"
    assert "nothing was compared" not in out, "comparisons were made"


@pytest.mark.parametrize("n", range(1, 4))
def test_report_is_closed_world_over_verdicts(capsys, n: int):
    """**출력 불변식.** 판정값 조합 전수에 대해 이름과 종료 코드가 모두 정직해야 한다.

    이 불변식은 `_report` 의 docstring 문장으로만 존재했고 라운드 2 의 변경이 조용히
    어겼다(라운드 3 적발). 그리고 라운드 4 검토에서, 상태를 하나 더 들이면 사유별 목록으로
    분기하던 `_report` 가 그 값을 "전부 on" 가지로 흘려 **exit 4 — 거짓 면죄** 를 낸다는
    것이 실측됐다. 그래서 이름뿐 아니라 **종료 코드**까지, 그리고 목록에 **없는** 값까지
    함께 고정한다 — 나중에 판정값이 늘어도 이 테스트가 먼저 빨개진다.
    """
    import itertools

    states = list(check.VERDICTS) + ["a-state-nobody-declared"]
    for combo in itertools.product(states, repeat=n):
        shas = [f"{i:012x}" for i in range(n)]
        verdicts = dict(zip(shas, combo))
        rc = check._report("origin", shas, verdicts)
        out = capsys.readouterr().out

        judged = [s for s in shas if verdicts[s] in (check.ON, check.NOT_ON)]
        not_on = [s for s in judged if verdicts[s] == check.NOT_ON]
        unjudged = [s for s in shas if verdicts[s] not in (check.ON, check.NOT_ON)]

        # 종료 코드는 세 갈래뿐이고, 미판정이 하나라도 있으면 절대 4·5 가 아니다.
        if unjudged:
            assert rc == check.EXIT_UNDECIDED, f"{combo}: unjudged yielded {rc}"
            # 면죄 문장은 **어디에도** 없어야 한다. 앞선 형태는 `endswith` 였고 not_on 이
            # 있으면 통째로 vacuous 였다 — 미판정과 not-on 이 섞인 조합에서 아무것도
            # 지키지 않았다. 함께 있던 `"of the listed commits are on"` 은 어떤 출력에도
            # 나오지 않는 문구였다(실측). 부분 문자열 부재가 그 두 구멍을 닫는다.
            assert "on main/master at origin as of this fetch." not in out, (
                f"{combo}: an all-on sentence with unjudged SHAs"
            )
        elif not_on:
            assert rc == check.EXIT_SOME_NOT_ON, f"{combo}: {rc}"
        else:
            assert rc == check.EXIT_ALL_ON, f"{combo}: {rc}"

        # 이름이 불리지 않고 사라지는 SHA 가 없어야 한다.
        for sha, verdict in verdicts.items():
            if verdict != check.ON:
                assert sha in out, f"{combo}: {verdict} SHA vanished"



def test_a_runner_failure_is_reported_as_unavailable(monkeypatch, tmp_path):
    """한쪽 호출만 실패하면 "조작됐다" 도 "해석 불가" 도 아니라 "git 이 답하지 않음" 이다.

    `run_git` 은 타임아웃에 124, OSError 에 127 을 낸다. 두 rc 를 무조건 비교하면 그런
    전이적 실패가 위조로 오진되고, `None` 에 넣으면 오너가 멀쩡한 SHA 를 잘못된 것으로
    읽고 엉뚱한 곳을 뒤진다 — 사유가 틀리면 다음 행동도 틀린다.
    """
    src, pub, _ = _published(tmp_path)
    real_run = check.run_git

    def _neutralized_times_out(args, **kw):
        if args and args[0] == "merge-base" and kw.get("neutralized"):
            return 124, ""
        return real_run(args, **kw)

    monkeypatch.setattr(check, "run_git", _neutralized_times_out)
    monkeypatch.chdir(src)
    verdict = check.judge(_short(pub), {"main": pub})
    assert verdict == check.UNAVAILABLE, (
        "a runner failure must be neither a forgery nor an unknown SHA"
    )


def _behind(tmp_path):
    """원격이 로컬보다 앞서 있는 저장소를 만든다 — B 는 현재 tip 객체를 갖지 않는다.

    기존 헬퍼(`_make_repo`/`_published`)는 같은 작업 저장소에서 원격을 만들어, 로컬 객체
    저장소가 원격이 가진 것을 **항상** 갖는다. 그래서 "로컬에 원격 것이 없다" 부류 전체가
    스위트에 보이지 않았고, 라운드 4 의 결함이 그 사각지대로 들어왔다.

    Returns:
        `(B 저장소, A 저장소, base SHA, 원격 tip SHA, B 의 로컬 전용 SHA)`.
    """
    remote = _bare(tmp_path, "remote", branch="main")
    a = tmp_path / "A"
    subprocess.run(["git", "clone", "-q", str(remote), str(a)],
                   capture_output=True, env=_GIT_ENV, check=True)
    base = _commit(a, "chore: base")
    _git(a, "push", "-q", "origin", "main")

    b = tmp_path / "B"
    subprocess.run(["git", "clone", "-q", str(remote), str(b)],
                   capture_output=True, env=_GIT_ENV, check=True)   # B 는 base 까지만 안다

    tip = _commit(a, "chore: pushed by someone else")
    _git(a, "push", "-q", "origin", "main")
    local = _commit(b, "chore: local only")
    assert subprocess.run(["git", "-C", str(b), "cat-file", "-e", tip],
                          capture_output=True, env=_GIT_ENV).returncode != 0, \
        "the fixture must leave B without the remote tip"
    return b, a, base, tip, local


def test_a_remote_only_commit_is_found_which_proves_the_fetch_delivers(
    monkeypatch, tmp_path
):
    """원격에만 있는 커밋이 on 으로 나온다 — 즉 fetch 가 실제로 배달한다.

    이 성질을 고정하는 테스트가 없었다. 실측: fetch 대상을 `.` 으로 바꿔 아무것도 배달하지
    않게 만들어도 스위트 전체가 초록이었다. 변이를 커밋 전에 손으로 넣는 의례 대신, 배달
    자체를 단언한다.
    """
    b, _a, _base, tip, _local = _behind(tmp_path)
    assert _run(monkeypatch, b, _short(tip)) == check.EXIT_ALL_ON


def test_a_fetch_that_delivers_nothing_yields_no_verdict(monkeypatch, capsys, tmp_path):
    """fetch 가 rc 0 을 내면서 아무것도 배달하지 않으면 판정하지 않는다."""
    b, _a, _base, tip, _local = _behind(tmp_path)
    real_run = check.run_git

    def _hollow_fetch(args, **kw):
        if args and args[0] == "fetch":
            return 0, ""          # 성공했다고 하고 아무것도 가져오지 않는다
        return real_run(args, **kw)

    monkeypatch.setattr(check, "run_git", _hollow_fetch)
    rc = _run(monkeypatch, b, _short(tip))
    assert rc == check.EXIT_UNDECIDED
    assert "did not arrive" in capsys.readouterr().out


def test_a_rewind_between_pin_and_fetch_is_answered(monkeypatch, tmp_path):
    """고정과 fetch 사이의 되감기에서도 답한다 — 로컬에 옛 tip 이 없어도.

    라운드 4 의 above-bar. 재시도 루프가 존재 이유인 경우인데, `_usable_pins` 가 루프
    **밖으로** 던지는 바람에 두 번째 시도가 오지 않았다.
    """
    b, a, base, _tip, local = _behind(tmp_path)
    real_run = check.run_git
    fired = {"n": 0}

    def _rewind_after_ls_remote(args, **kw):
        out = real_run(args, **kw)
        if args and args[0] == "ls-remote" and fired["n"] == 0:
            fired["n"] = 1
            _git(a, "push", "-q", "-f", "origin", f"{base}:refs/heads/main")
        return out

    monkeypatch.setattr(check, "run_git", _rewind_after_ls_remote)
    assert _run(monkeypatch, b, _short(local)) == check.EXIT_SOME_NOT_ON


def test_a_branch_deleted_between_pin_and_fetch_is_answered(monkeypatch, tmp_path):
    """ref 하나가 사라져도 남은 것으로 답한다.

    `git fetch <remote> refs/heads/main refs/heads/master` 는 **둘 중 하나만 없어도**
    통째로 rc 128 이다(실측). 그 실패가 재시도 루프 밖으로 던져지면, 기본 브랜치 이름
    변경 같은 평범한 사건이 판정을 통째로 잃게 만든다.
    """
    b, a, _base, _tip, local = _behind(tmp_path)
    _git(a, "branch", "master")
    _git(a, "push", "-q", "origin", "master")
    real_run = check.run_git
    fired = {"n": 0}

    def _delete_master_after_ls_remote(args, **kw):
        out = real_run(args, **kw)
        if args and args[0] == "ls-remote" and fired["n"] == 0:
            fired["n"] = 1
            _git(a, "push", "-q", "origin", "--delete", "master")
        return out

    monkeypatch.setattr(check, "run_git", _delete_master_after_ls_remote)
    assert _run(monkeypatch, b, _short(local)) == check.EXIT_SOME_NOT_ON


def test_a_stable_but_failing_attempt_does_not_claim_the_remote_moved(
    monkeypatch, capsys, tmp_path
):
    """움직이지 않았는데 실패하면, 그 시도의 사유를 그대로 낸다.

    성패를 재시도 기준에 섞으면 "kept moving" 이라는 거짓 사유가 나온다 — tip 은 움직이지
    않았는데도.
    """
    b, _a, _base, tip, _local = _behind(tmp_path)
    real_run = check.run_git

    def _fetch_always_fails(args, **kw):
        if args and args[0] == "fetch":
            return 128, ""
        return real_run(args, **kw)

    monkeypatch.setattr(check, "run_git", _fetch_always_fails)
    rc = _run(monkeypatch, b, _short(tip))
    out = capsys.readouterr().out
    assert rc == check.EXIT_UNDECIDED
    assert "the fetch failed" in out
    assert "kept moving" not in out
    assert out.count("nothing was compared") == 1, "the phrase is doubled"


@pytest.mark.parametrize(
    "step", ["rev-parse", "remote", "ls-remote", "fetch", "cat-file", "merge-base"]
)
def test_every_git_call_site_fails_into_undecided(
    monkeypatch, capsys, tmp_path, step: str
):
    """어느 git 호출이 타임아웃해도 판정이 아니라 판정 불가로 떨어진다.

    ledger 의 부류 B bound("git 실패가 exit 5 로 렌더돼 오너를 `branch -f` 로 보내면 안
    된다")를 **선언에서 기계로** 옮긴 것이다. 커밋 전에 호출 지점을 하나씩 124 로 강제하는
    의례 대신, 그 성질을 파라미터로 돈다 — 의례는 기억에 기대고 테스트는 기대지 않는다.
    """
    b, _a, _base, tip, _local = _behind(tmp_path)
    real_run = check.run_git
    hit = {"n": 0}

    def _step_times_out(args, **kw):
        if args and args[0] == step:
            hit["n"] += 1
            return 124, ""
        return real_run(args, **kw)

    monkeypatch.setattr(check, "run_git", _step_times_out)
    rc = _run(monkeypatch, b, _short(tip))
    assert hit["n"] > 0, f"{step} was never called; the parametrisation is stale"
    assert rc in (check.EXIT_UNDECIDED, check.EXIT_CALLER), (
        f"a timeout at {step} produced {rc}, not a refusal"
    )
    assert rc != check.EXIT_ALL_ON and rc != check.EXIT_SOME_NOT_ON
    captured = capsys.readouterr()
    assert check.TAG in captured.out + captured.err
