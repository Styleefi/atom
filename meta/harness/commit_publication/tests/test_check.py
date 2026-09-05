# commit_publication의 판정·종료 코드·출력 어휘를 실 저장소로 고정하는 테스트
"""commit-publication 테스트.

git을 mock하지 않는다 — 판정이 그래프 연산이고, 이 도구가 존재하는 이유인 결함들이
전부 "git이 실제로 무엇을 내는가"에 대한 오해였기 때문이다. tmp_path에 실 저장소와
bare remote(필요하면 `file://`)를 만들어 검증한다.
"""

from __future__ import annotations

import ast
import itertools
import os
import re
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


def _published(tmp_path, branch: str = "main"):
    """원격에 발행된 커밋 하나와, 로컬 전용 커밋 하나를 가진 저장소."""
    remote = _bare(tmp_path, "remote", branch=branch)
    src = _work(tmp_path, "src", branch=branch)
    _git(src, "remote", "add", "origin", str(remote))
    pub = _commit(src, "chore: published")
    _git(src, "push", "-q", "origin", branch)
    local = _commit(src, "chore: local only")
    return src, pub, local


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


def _behind(tmp_path):
    """원격이 로컬보다 앞서 있는 저장소를 만든다 — B 는 현재 tip 객체를 갖지 않는다.

    다른 헬퍼는 같은 작업 저장소에서 원격을 만들어, 로컬 객체 저장소가 원격이 가진 것을
    **항상** 갖는다. 이 fixture를 쓰는 테스트만이 fetch가 실제로 배달하는지를 검증한다 —
    스위트를 줄일 때 지우지 말 것.

    Returns:
        `(B 저장소, base SHA, 원격 tip SHA)`.
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
    assert subprocess.run(["git", "-C", str(b), "cat-file", "-e", tip],
                          capture_output=True, env=_GIT_ENV).returncode != 0, \
        "the fixture must leave B without the remote tip"
    return b, base, tip


# --------------------------------------------------------------------------
# 불변식: 비교 대상 브랜치 집합이 훅의 상수와 같다
# --------------------------------------------------------------------------


def test_protected_branches_match_the_hook() -> None:
    # 네 번째 결함(GitFlow 면죄)은 이 두 값이 어긋나서 생겼다. 산문으로 다시 쓰는 대신
    # 결속한다 — 규약이 테스트로 옮겨갈 때에야 그 부류가 닫힌다(review-loop).
    #
    # skip은 commit_backstop 부재 한 조건으로 좁힌다. 그 훅은 자식 프로젝트가 정합하게
    # 제거할 수 있고(REMOVABLE), 그 외 어떤 조건부도 두지 않는다.
    backstop = pytest.importorskip("harness.commit_backstop.backstop")
    assert check.PROTECTED_BRANCHES == backstop.PROTECTED_BRANCHES
    # commit_guard 도 같은 집합을 하드코딩한다(set 이라 집합으로 비교).
    guard = pytest.importorskip("harness.commit_guard.guard")
    assert set(check.PROTECTED_BRANCHES) == set(guard.PROTECTED_BRANCHES)


# --------------------------------------------------------------------------
# 판정
# --------------------------------------------------------------------------


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


def test_mixed_list_reports_the_unpublished_sha(monkeypatch, capsys, tmp_path):
    # 산문의 결함 #3: 단일 SHA 판정이 미발행 커밋을 담은 보고를 통째로 기각할 수 있었다.
    src, pub, local = _published(tmp_path)
    rc = _run(monkeypatch, src, _short(pub), _short(local))
    out = capsys.readouterr().out
    assert rc == check.EXIT_SOME_NOT_ON
    assert _short(local) in out
    assert "1 of 2" in out


def test_unresolvable_sha_in_a_mixed_run_does_not_make_it_undecided(
    monkeypatch, capsys, tmp_path
):
    # on / not-on / 해석 불가가 섞이면 exit 5다. "하나 이상 not-on" 은 존재 명제라 다른
    # SHA 가 판정 불가여도 성립한다. 미해석 SHA 는 not-on 줄이 아니라 별도 줄에 이름이
    # 불려야 한다 — merge-base 의 rc 128 을 not-on 으로 읽는 구현은 여기서 not-on 줄에
    # deadbeef 를 싣는다.
    src, pub, local = _published(tmp_path)
    rc = _run(monkeypatch, src, _short(pub), _short(local), "deadbeefdead")
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert rc == check.EXIT_SOME_NOT_ON
    assert len(lines) == 2, out
    not_on_line, unjudged_line = lines
    # 분모는 판정한 수(2)다. 나열한 수(3)를 쓰면 비교된 적 없는 SHA 까지 판정에 든 것처럼
    # 읽혀, 그 SHA 가 on 이라고 오해하게 만든다.
    assert "1 of 2 judged commits" in not_on_line and _short(local) in not_on_line
    assert "deadbeefdead" not in not_on_line
    assert "could not be judged" in unjudged_line and "deadbeefdead" in unjudged_line


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
    tracking = clone / ".git" / "refs" / "remotes" / "origin" / "main"
    assert not tracking.exists()
    assert _run(monkeypatch, clone, _short(base)) == check.EXIT_ALL_ON
    # 풋프린트: 일치하는 refspec이 없으므로 fetch는 추적 ref를 만들지 않는다 — 도구가 훅의
    # 사각지대를 "치유"하지 않는다는 docstring 문장을 든다.
    assert not tracking.exists()


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
    # 풋프린트: 원격에 보호명이 아닌 브랜치도 두고 그 추적 ref를 지워 둔다. 도구는 자기가
    # 물은 main/master 만 fetch 하므로 실행 후에도 그 ref 는 생기지 않아야 한다.
    _git(src, "push", "-q", "origin", "master:refs/heads/feature")
    _git(src, "update-ref", "-d", "refs/remotes/origin/feature")
    feature = src / ".git" / "refs" / "remotes" / "origin" / "feature"
    assert not feature.exists()
    assert _run(monkeypatch, src, _short(only)) == check.EXIT_ALL_ON
    assert not feature.exists(), "the tool fetched a branch it was not asked about"


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
    # 풋프린트도 함께 든다: 비교할 tip 이 없으면 fetch 자체가 나가지 않아야 한다. `_tips`
    # 의 빈 결과 검사를 지우면 refspec 없는 `git fetch` 가 설정된 추적 ref 를 전부
    # 갱신하고, 사유가 "원격에 보호 브랜치가 없다" 에서 "SHA 를 판정할 수 없다" 로 바뀐다.
    # 등록명을 `origin` 이 아닌 것으로 두어, 사유에 담긴 이름이 하드코딩이 아니라 실제
    # remote 인자에서 온 것임을 함께 본다(단일 remote 라 기본 선택 경로도 지난다).
    remote = _bare(tmp_path, "remote", branch="trunk")
    src = _work(tmp_path, "src", branch="trunk")
    _git(src, "remote", "add", "zeta", str(remote))
    tip = _commit(src, "chore: base")
    _git(src, "push", "-q", "zeta", "trunk")
    _git(src, "update-ref", "-d", "refs/remotes/zeta/trunk")
    tracking = src / ".git" / "refs" / "remotes" / "zeta" / "trunk"
    assert not tracking.exists()
    rc = _run(monkeypatch, src, _short(tip))
    out = capsys.readouterr().out
    assert rc == check.EXIT_UNDECIDED
    assert "trunk" not in out
    assert "zeta has neither main nor master" in out
    assert not tracking.exists(), "a fetch went out with nothing to compare against"


def test_stale_tracking_ref_is_never_the_pin(monkeypatch, tmp_path):
    # 원격 main이 X 뒤로 되감겼는데 로컬 추적 ref는 아직 X를 담고 있다.
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


def test_a_remote_only_commit_is_found_which_proves_the_fetch_delivers(
    monkeypatch, tmp_path
):
    # 원격에만 있는 커밋이 on 으로 나온다 — 즉 fetch 가 실제로 배달한다.
    b, _base, tip = _behind(tmp_path)
    assert _run(monkeypatch, b, _short(tip)) == check.EXIT_ALL_ON


def test_failed_fetch_yields_no_verdict(monkeypatch, capsys, tmp_path):
    # fetch가 실패하면 아무것도 비교되지 않는다. FETCH_HEAD를 미리 심어 두어, 그 파일을
    # 읽는 구현이 판정을 만들어내지 못함을 함께 고정한다. 사유는 어느 원격에서 막혔는지도
    # 말한다 — 이 줄은 판정을 싣지 않지만 오너의 다음 행동은 그 이름에 달렸다.
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
    assert "the fetch from origin failed" in out


def test_an_unjudged_sha_never_erases_the_not_on_shas(monkeypatch, capsys, tmp_path):
    # 판정 불가 SHA 가 섞여도 이미 판정한 not-on 은 보고에서 사라지지 않고 exit 는 5다.
    # PR #152 라운드 3 실측: 판정 불가를 예외로 던지자 배치가 중단돼 조치가 필요한 미발행
    # 커밋이 출력에서 사라졌다. SHA 단위 사실은 SHA 단위 채널로 나가야 한다.
    src, _pub, local = _published(tmp_path)
    rc = _run(monkeypatch, src, _short(local), "deadbeefdead")
    lines = capsys.readouterr().out.splitlines()
    assert rc == check.EXIT_SOME_NOT_ON
    assert "1 of 1 judged commits" in lines[0]
    assert _short(local) in lines[0], "the not-on SHA vanished from the report"
    assert "deadbeefdead" not in lines[0], "an unjudged SHA was listed as not on"
    assert "deadbeefdead" in lines[1], "the unjudgeable SHA was not named"
    assert "nothing was compared" not in "\n".join(lines), "comparisons were made"


@pytest.mark.parametrize("n", (1, 2, 3))
def test_report_is_closed_world_over_verdicts(capsys, n: int):
    """`_report` 의 입력 공간 전체를 오라클과 대조한다 — SHA n 개 × 판정 3종의 모든 조합.

    PR #152 라운드 7·8 의 결함은 둘 다 이 함수의 문장이나 분기를 바꾸면서 조합 일부를
    확인하지 않아 생겼다. 순수 함수라 전수 검사가 싸다.
    """
    kinds = {"on": check.ON, "not": check.NOT_ON, "none": None}
    # 어떤 리터럴에도 우연히 들어 있지 않은 이름이라, 인자가 실제로 출력에 흘러야만
    # 아래 단언이 통과한다.
    remote = "zeta-under-test"
    for combo in itertools.product(kinds, repeat=n):
        shas = [f"{'abcdef'[i] * 6}{i:06d}" for i in range(n)]
        verdicts = {s: kinds[k] for s, k in zip(shas, combo)}
        capsys.readouterr()
        rc = check._report(remote, shas, verdicts)
        out = capsys.readouterr().out
        lines = out.rstrip("\n").split("\n")
        head = lines[0]
        on = [s for s, k in zip(shas, combo) if k == "on"]
        not_on = [s for s, k in zip(shas, combo) if k == "not"]
        unjudged = [s for s, k in zip(shas, combo) if k == "none"]
        judged = n - len(unjudged)

        # 종료 코드: not-on 은 존재 명제, 전부 on 은 전칭 명제.
        expected = (
            check.EXIT_SOME_NOT_ON if not_on
            else check.EXIT_UNDECIDED if unjudged
            else check.EXIT_ALL_ON
        )
        assert rc == expected, (combo, out)

        # 이름: on 은 싣지 않고, not-on 은 머리줄에 한 번, 미판정은 둘째 줄에 한 번.
        # 둘째 줄은 미판정이 있을 때만 있다.
        for s in on:
            assert s not in out, (combo, out)
        for s in not_on:
            assert head.count(s) == 1 and out.count(s) == 1, (combo, out)
        for s in unjudged:
            assert s not in head and lines[1].count(s) == 1, (combo, out)
        assert (len(lines) == 2) == bool(unjudged), (combo, out)
        if unjudged:
            assert lines[1].startswith(
                f"  {len(unjudged)} could not be judged: "
            ), (combo, out)

        # 어느 판정이든 무엇에 대고 물었는지 말한다 — 보고를 이슈에 붙이면 remote 이름이
        # 없는 줄은 어느 원격에 대한 답인지 구분되지 않는다.
        assert f"main/master at {remote}" in head, (combo, out)

        # 머리줄의 주장: not-on 문구는 exit 5 에만, on 주장은 exit 4 에만.
        assert ("not on main/master" in head) == bool(not_on), (combo, out)
        assert bool(re.search(r"\b(is|are) on main/master", head)) == (
            expected == check.EXIT_ALL_ON
        ), (combo, out)

        # 머리줄의 숫자: 비교한 적 없는 SHA 를 판정에 든 것처럼 세지 않는다.
        m = re.search(r"\] (\d+) of (\d+) (listed|judged) commits", head)
        assert m, (combo, out)
        numbers = (int(m[1]), int(m[2]), m[3])
        assert numbers == {
            check.EXIT_SOME_NOT_ON: (len(not_on), judged, "judged"),
            check.EXIT_UNDECIDED: (judged, n, "listed"),
            check.EXIT_ALL_ON: (n, n, "listed"),
        }[expected], (combo, out)


# --------------------------------------------------------------------------
# 출력 어휘
# --------------------------------------------------------------------------


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
    assert "are on main/master at origin as of this run" in on_out or \
           "is on main/master at origin as of this run" in on_out
    _run(monkeypatch, src, _short(local))
    assert "as of this run" in capsys.readouterr().out


# --------------------------------------------------------------------------
# argv·remote 계약
# --------------------------------------------------------------------------


def test_abbreviated_shas_are_accepted(monkeypatch, tmp_path):
    # 훅 보고문이 싣는 형태가 12자 축약이다.
    src, pub, _ = _published(tmp_path)
    assert _run(monkeypatch, src, pub[:12]) == check.EXIT_ALL_ON


# 각 행은 (argv, 인쇄돼야 할 사유)다.
_REJECTED_ARGV = [
    ([], "no commit SHAs given"),
    (["--remote"], "--remote needs a value"),
    (["--zzz", "0" * 40], "unknown option: --zzz"),
    (["zzzz"], "not hex commit SHAs: zzzz"),
]


@pytest.mark.parametrize("argv,phrase", _REJECTED_ARGV, ids=lambda v: str(v)[:40])
def test_rejected_argv_names_its_reason_and_never_calls_git(
    monkeypatch, capsys, argv: list[str], phrase: str
):
    # lane·사유·git 무접촉을 함께 고정한다. git 무접촉을 빼면 진입점 테스트가 인자 없이
    # 도구를 돌릴 때 네트워크에 닿는 회귀가 보이지 않는다.
    calls: list[list[str]] = []
    monkeypatch.setattr(
        check, "run_git", lambda args, **kw: calls.append(args) or (0, "")
    )
    assert check._check(argv) == check.EXIT_CALLER
    assert calls == [], f"{argv} reached git before being rejected"
    err = capsys.readouterr().err
    assert check.TAG in err
    assert phrase in err, f"{argv} was rejected without naming {phrase!r}"


def test_a_registered_remote_name_is_used_as_given(monkeypatch, capsys, tmp_path):
    # `--remote <등록된 이름>` 의 성공 경로 — 규칙이 인용하는 옵션의 정상 사용이다.
    # 등록명을 항상 거부하는 변이는 이 테스트만 잡는다.
    src, pub, _ = _published(tmp_path)
    assert _run(monkeypatch, src, "--remote", "origin", _short(pub)) == \
        check.EXIT_ALL_ON
    assert "on main/master at origin as of this run." in capsys.readouterr().out


def test_default_remote_selection(monkeypatch, capsys, tmp_path):
    # 단일 remote면 그것, origin이 있으면 그것, 둘 다 아니면 호출자 잘못.
    src, pub, _ = _published(tmp_path)
    _git(src, "remote", "rename", "origin", "only")
    assert _run(monkeypatch, src, _short(pub)) == check.EXIT_ALL_ON

    other = _bare(tmp_path, "other", branch="main")
    _git(src, "remote", "add", "second", str(other))
    assert _run(monkeypatch, src, _short(pub)) == check.EXIT_CALLER
    assert "name one with --remote" in capsys.readouterr().err

    _git(src, "remote", "rename", "only", "origin")
    assert _run(monkeypatch, src, _short(pub)) == check.EXIT_ALL_ON

    # remote 가 하나도 없으면 "--remote 로 고르라" 는 실행 불가능한 조언이다. 그 상태는
    # 대개 잘못된 저장소에서 실행한 것이므로 exit 2 는 맞고, 사유는 상태를 그대로 말한다.
    _git(src, "remote", "remove", "origin")
    _git(src, "remote", "remove", "second")
    capsys.readouterr()
    assert _run(monkeypatch, src, _short(pub)) == check.EXIT_CALLER
    err = capsys.readouterr().err
    assert "no remote is registered" in err
    assert "name one with --remote" not in err


def test_unregistered_remote_name_is_caller_error_not_undecided(
    monkeypatch, tmp_path
):
    # ls-remote 는 오타와 접속 불가에 똑같이 128을 낸다. git 목록과 먼저 대조하지 않으면
    # 에이전트의 오타가 "환경 문제"로 오너에게 보고된다.
    src, pub, _ = _published(tmp_path)
    assert _run(monkeypatch, src, "--remote", "orign", _short(pub)) == check.EXIT_CALLER


def test_malformed_sha_is_caller_error_and_unresolvable_is_undecided(
    monkeypatch, capsys, tmp_path
):
    # 형식 위반은 exit 2, 형식은 맞지만 이 저장소가 모르는 SHA 하나는 exit 3. not-on 이
    # 없으므로 5 가 아니다 — 미해석을 not-on 으로 읽는 구현은 여기서 5 를 낸다.
    src, pub, _ = _published(tmp_path)
    assert _run(monkeypatch, src, "zzzz") == check.EXIT_CALLER
    capsys.readouterr()
    assert _run(monkeypatch, src, "deadbeefdead") == check.EXIT_UNDECIDED
    # 판정한 것이 하나도 없으면 판정 집합에 대해 아무 주장도 하지 않는다. 공집합 위의
    # "그중 미발행은 없다" 는 참이지만 면죄로 읽히고, exit 5 줄과 같은 문구를 담아
    # 보고를 훑는 눈에 걸린다.
    out = capsys.readouterr().out
    assert "0 of 1 listed commits were judged" in out
    assert "not on main/master" not in out


def test_an_unreachable_remote_is_undecided_and_names_itself(
    monkeypatch, capsys, tmp_path
):
    # 등록은 됐는데 닿지 않는 원격. 오타(exit 2)와 달리 호출자가 고칠 수 없으므로 exit 3
    # 이고, 사유는 어느 원격이었는지 말한다. 이 경로를 도는 테스트가 없어서, 사유에서
    # 이름을 빼는 변이가 스위트를 통과했다.
    src, pub, _ = _published(tmp_path)
    _git(src, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    _git(src, "remote", "rename", "origin", "zeta")
    rc = _run(monkeypatch, src, _short(pub))
    out = capsys.readouterr().out
    assert rc == check.EXIT_UNDECIDED
    assert "could not reach zeta" in out
    assert "on main/master" not in out


def test_outside_a_repository_is_caller_error(monkeypatch, tmp_path):
    # cwd 문제는 호출자가 고칠 수 있다.
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _run(monkeypatch, plain, "deadbeefdead") == check.EXIT_CALLER


def test_local_branches_head_and_fetch_head_are_untouched(monkeypatch, tmp_path):
    # 도구가 갱신하는 ref 는 refspec 이 일치하는 추적 ref 뿐이다. FETCH_HEAD 는 읽지도 않으면서
    # 덮어쓰기만 했었고, 오너가 `git fetch origin x` 뒤 `git merge FETCH_HEAD` 를 하려던
    # 참이면 엉뚱한 커밋을 머지하게 만든다. 심어 둔 값이 그대로 남아야 한다.
    src, pub, _ = _published(tmp_path)
    fetch_head = src / ".git" / "FETCH_HEAD"
    sentinel = f"{pub}\t\tbranch 'someone-elses' of somewhere\n"
    fetch_head.write_text(sentinel, encoding="utf-8")

    before = _git(src, "for-each-ref", "refs/heads/"), _git(src, "rev-parse", "HEAD")
    # 종료 코드를 함께 고정한다 — fetch 이전에 물러나는 회귀가 생기면 아래 단언은 전부
    # 참인 채로 초록이 되고, 이 테스트가 드는 보증은 실행되지 않는다.
    assert _run(monkeypatch, src, _short(pub)) == check.EXIT_ALL_ON
    after = _git(src, "for-each-ref", "refs/heads/"), _git(src, "rev-parse", "HEAD")
    assert before == after
    assert fetch_head.read_text(encoding="utf-8") == sentinel


# --------------------------------------------------------------------------
# 러너·규칙·진입점
# --------------------------------------------------------------------------


def test_the_runner_isolates_every_git_call(monkeypatch, tmp_path):
    # 이음매는 run_git 하나다. 여기서 kwargs를 단언하지 않으면 env= 나 stdin= 이
    # 조용히 사라져도 아무도 모른다. `-c credential.helper=` 가 helper 채널을 막는다.
    seen: list[dict] = []
    real_run = subprocess.run

    def _spy(argv, **kwargs):
        seen.append({"argv": argv, **kwargs})
        return real_run(argv, **kwargs)

    src, pub, _ = _published(tmp_path)
    monkeypatch.setattr(check.subprocess, "run", _spy)
    _run(monkeypatch, src, _short(pub))

    assert seen, "no git call was made"
    for call in seen:
        argv = call["argv"]
        assert argv[:3] == ["git", "-c", "credential.helper="]
        assert call["stdin"] is subprocess.DEVNULL
        assert call["stderr"] is subprocess.DEVNULL
        assert call["env"]["GIT_TERMINAL_PROMPT"] == "0"
        # askpass 채널은 helper 와 별개다.
        assert call["env"]["GIT_ASKPASS"] == "/bin/false"
        assert call["env"]["SSH_ASKPASS"] == "/bin/false"
        assert call["env"]["SSH_ASKPASS_REQUIRE"] == "force"
        assert call["timeout"] > 0


def test_the_runner_separates_a_timeout_from_a_failure_to_run(monkeypatch):
    """`run_git` 은 타임아웃에 124, 실행 실패에 127 을 **돌려준다** — 던지지 않는다."""
    def _always_raise(exc):
        def _run(_argv, **_kwargs):
            raise exc
        return _run

    monkeypatch.setattr(
        check.subprocess, "run",
        _always_raise(subprocess.TimeoutExpired(cmd=["git"], timeout=1)),
    )
    assert check.run_git(["remote"], timeout=1) == (124, "")

    monkeypatch.setattr(
        check.subprocess, "run", _always_raise(FileNotFoundError("git"))
    )
    assert check.run_git(["remote"], timeout=1) == (127, "")


# 호출 자리 술어. `check.py` 의 `run_git` 호출 노드와 일대일이어야 한다(아래 테스트가 잰다).
_SPAWN_SITES = {
    "rev-parse": lambda a: a[:2] == ["rev-parse", "--is-shallow-repository"],
    "remote": lambda a: a == ["remote"],
    "ls-remote": lambda a: a[0] == "ls-remote",
    "fetch": lambda a: a[0] == "fetch",
    "merge-base": lambda a: a[:2] == ["merge-base", "--is-ancestor"],
}

# (주입 시작 자리, rc, 있어야 할 것, 없어야 할 것). 뒤의 두 열은 124 칸만 쓴다 — 127 칸은
# `_SPAWN_REASONS` 와 `_REMOTE_PHRASES` 로 본다.
_SPAWN_CELLS = (
    ("rev-parse", 127, (), ()),
    ("remote", 127, (), ()),
    ("ls-remote", 127, (), ()),
    ("ls-remote", 124, ("could not reach",), ("git could not be run",)),
    ("fetch", 127, (), ()),
    ("fetch", 124, ("the fetch from",), ("git could not be run",)),
    ("merge-base", 127, (), ()),
)

# 사유 경로 127 칸의 사유. 여기 없는 자리는 `_report` 경로로 본다.
_SPAWN_REASONS = {
    "rev-parse": "git did not answer when asked about this repository",
    "remote": "`git remote` failed",
    "ls-remote": "git could not be run",
    "fetch": "git could not be run",
}

# 이 모듈이 원격을 가리킬 때 쓰는 문구(`55c53dd`). 127 칸의 stdout 어디에도 없어야 한다.
_REMOTE_PHRASES = ("could not reach", "the fetch from", "has neither main nor master")


def _run_git_call_sites() -> list[tuple[str, list[str]]]:
    """소스에서 `run_git` 이라는 이름으로 호출된 노드마다 (위치, argv 리터럴 접두)를 낸다.

    argv 는 첫 위치 인자 또는 `args=` 키워드에서 읽는다. 리스트 리터럴이 아니면 접두는 빈
    리스트이고, 그 자리도 목록에 남는다.
    """
    package = Path(check.__file__).resolve().parent
    sites: list[tuple[str, list[str]]] = []
    for path in sorted(package.rglob("*.py")):
        if "tests" in path.relative_to(package).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "run_git":
                continue
            argv = node.args[0] if node.args else next(
                (kw.value for kw in node.keywords if kw.arg == "args"), None
            )
            prefix: list[str] = []
            if isinstance(argv, ast.List):
                for element in argv.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        prefix.append(element.value)
                    else:
                        break
            where = f"{path.name}:{node.lineno}:{node.col_offset}"
            sites.append((where, prefix))
    return sites


def test_every_run_git_call_site_has_exactly_one_cell_in_the_table() -> None:
    """`run_git` 호출 노드 ↔ `_SPAWN_SITES` 술어 ↔ 127 칸이 일대일이다."""
    sites = _run_git_call_sites()
    assert sites, "no run_git call site was found in the package source"

    covered: dict[str, list[str]] = {name: [] for name in _SPAWN_SITES}
    for where, prefix in sites:
        matched = [n for n, pred in _SPAWN_SITES.items() if prefix and pred(prefix)]
        assert len(matched) == 1, f"{where} argv={prefix} matched {matched}"
        covered[matched[0]].append(where)

    for name, wheres in covered.items():
        assert len(wheres) == 1, f"{name} covers {len(wheres)} call sites: {wheres}"

    cells_with_127 = {site for site, rc, _p, _a in _SPAWN_CELLS if rc == 127}
    assert cells_with_127 == set(_SPAWN_SITES), (
        f"predicates without a 127 cell: {set(_SPAWN_SITES) - cells_with_127}"
    )
    assert set(_SPAWN_REASONS) <= set(_SPAWN_SITES), (
        f"reasons for sites that do not exist: {set(_SPAWN_REASONS) - set(_SPAWN_SITES)}"
    )


@pytest.mark.parametrize("site,rc,present,absent", _SPAWN_CELLS,
                         ids=lambda v: str(v).replace(" ", "")[:28])
def test_a_local_git_failure_is_never_reported_as_a_remote_one(
    monkeypatch, capsys, tmp_path, site: str, rc: int, present, absent
):
    """127 칸의 stdout 은 원격 문구도 remote 이름도 담지 않고 기대 문자열과 같다.

    124 칸은 대조군이다 — 타임아웃은 원격 사유를 유지해야 한다.
    """
    src, _pub, local = _published(tmp_path)
    real = check.run_git
    hit = {"on": False}

    def _injected(args, **kwargs):
        if _SPAWN_SITES[site](args):
            hit["on"] = True
        return (rc, "") if hit["on"] else real(args, **kwargs)

    monkeypatch.setattr(check, "run_git", _injected)
    exit_code = _run(monkeypatch, src, _short(local))
    out, err = capsys.readouterr()

    assert hit["on"], f"the {site} call site was never reached"
    assert exit_code == check.EXIT_UNDECIDED
    if rc == 127:
        # 경로는 칸으로 정한다. 판정 줄은 remote 이름을 담는 것이 옳으므로 이름 검사는
        # 사유 경로에만 건다. (remote 이름이 사유의 낱말과 겹치면 오탐한다 — 픽스처는 `origin`.)
        remotes = _git(src, "remote").splitlines()
        if site in _SPAWN_REASONS:
            expected = f"{check.TAG} undecided: {_SPAWN_REASONS[site]}; nothing was compared\n"
            for name in remotes:
                assert not re.search(rf"\b{re.escape(name)}\b", out), (
                    f"a spawn-failure reason named the remote {name!r}: {out!r}"
                )
        else:
            expected = (
                f"{check.TAG} 0 of 1 listed commits were judged against main/master "
                f"at {remotes[0]} as of this run.\n"
                f"  1 could not be judged: {_short(local)}\n"
            )
        for phrase in _REMOTE_PHRASES:
            assert phrase not in out, f"a spawn failure blamed the remote: {out!r}"
        assert out == expected, f"spawn-failure output drifted:\n{out!r}\n!=\n{expected!r}"
        assert err == "", f"a spawn failure wrote to stderr: {err!r}"
    for text in present:
        assert text in out, f"{text!r} missing from: {out!r}"
    for text in absent:
        assert text not in out, f"{text!r} should not appear in: {out!r}"


def test_the_command_is_not_copied_into_always_loaded_context() -> None:
    # 이 도구의 명령은 규칙 파일과 meta/README.md 에만 산다. rules_checker 는
    # @meta/rules/ import 만 동기 검증하므로 CLAUDE.md·템플릿의 사본은 아무도 감시하지
    # 않는다.
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
    # 규칙이 인용한 호출 문자열을 파싱해 모듈의 argv 계약과 대조한다. grep으로 "명령이
    # 들어 있다"만 보면 인용이 낡아도 초록으로 남는다. 대체된 산문이 다시 자라지 않는지도
    # 함께 본다 — 그 절차가 결함 넷을 실었다.
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
    assert tokens[:5] == ["uv", "run", "--directory", "meta", "python"], tokens
    assert tokens[5] == "-m" and tokens[6] == "harness.commit_publication"

    placeholders = tokens[7:]
    assert placeholders, "the citation must show where the SHAs go"
    for token in placeholders:
        stripped = token.strip("[]<>.")
        if stripped.startswith("-"):
            assert stripped == "--remote", stripped
    remote, shas = check._parse_args(["--remote", "origin", "0" * 40])
    assert remote == "origin" and shas == ["0" * 40]

    for gone in ("merge-base --is-ancestor", "FETCH_HEAD", "HEAD branch"):
        assert gone not in rule, f"the replaced procedure came back: {gone}"
