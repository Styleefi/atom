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


def test_replace_ref_refuses(monkeypatch, capsys, tmp_path):
    # replace/grafts는 on 쪽으로 — 면죄 방향으로 — 거짓말한다. 중화하지 않고 거부한다.
    src, pub, local = _published(tmp_path)
    extra = _commit(src, "chore: extra")
    # extra 의 부모를 local 에서 pub 으로 바꿔 실제 그래프를 흔든다(항등 graft는 git이 거부).
    _git(src, "replace", "--graft", extra, pub)
    rc = _run(monkeypatch, src, _short(pub))
    assert rc == check.EXIT_UNDECIDED
    assert "replace refs" in capsys.readouterr().out


def test_grafts_file_refuses(monkeypatch, capsys, tmp_path):
    # --no-replace-objects 가 막지 못하는 쪽이라 반쪽 방어 대신 거부를 택했다.
    src, pub, local = _published(tmp_path)
    info = Path(_git(src, "rev-parse", "--git-common-dir"))
    if not info.is_absolute():
        info = src / info
    (info / "info").mkdir(exist_ok=True)
    (info / "info" / "grafts").write_text(f"{local} {pub}\n", encoding="utf-8")
    rc = _run(monkeypatch, src, _short(pub))
    assert rc == check.EXIT_UNDECIDED
    assert "grafts" in capsys.readouterr().out


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


def test_local_branches_and_head_are_untouched(monkeypatch, tmp_path):
    # refs/remotes/* 는 fetch가 정당하게 갱신하므로 범위에서 뺀다.
    src, pub, _ = _published(tmp_path)
    before = _git(src, "for-each-ref", "refs/heads/"), _git(src, "rev-parse", "HEAD")
    _run(monkeypatch, src, _short(pub))
    after = _git(src, "for-each-ref", "refs/heads/"), _git(src, "rev-parse", "HEAD")
    assert before == after


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

    overrides = {
        k: v for k, v in check._isolated_env().items() if os.environ.get(k) != v
    }
    assert overrides, "the isolation env sets nothing"

    src, pub, _ = _published(tmp_path)
    monkeypatch.setattr(check.subprocess, "Popen", _Spy)
    _run(monkeypatch, src, _short(pub))

    assert seen, "no git call was made"
    for call in seen:
        assert call["argv"][:3] == ["git", "-c", "credential.helper="]
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
    for token in placeholders:
        stripped = token.strip("[]<>.")
        if stripped.startswith("--"):
            assert stripped.split("=")[0] in ("--remote",), stripped
    remote, shas = check._parse_args(["--remote", "origin", "0" * 40])
    assert remote == "origin" and shas == ["0" * 40]

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
