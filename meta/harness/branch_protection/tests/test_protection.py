# branch_protection의 요청 모양·멱등성·거부 보고·URL 파싱을 가짜 seam으로 고정하는 단위 테스트
"""branch-protection 단위 테스트.

gh·glab·git을 띄우지 않는다 — CI 이미지에 둘이 없고, 이 도구가 포지에 무엇을 보내는지가
검증 대상이기 때문이다. `protection.run_cli`를 스크립트된 가짜로 갈아끼우고, autouse
fixture가 `subprocess.run`을 막아 seam을 우회하는 호출을 시끄럽게 잡는다. 실포지 검증은
`test_protection_gitlab.py`(gitlab 마커)가 맡는다.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from harness.branch_protection import protection

_REPO_ROOT = Path(__file__).resolve().parents[4]

GH_ORIGIN = "https://github.com/o/r.git"
GL_ORIGIN = "git@gitlab.example.com:g/sub/r.git"
GL_PROJECT = "projects/g%2Fsub%2Fr"

# 2026-10-31 `gh api repos/Styleefi/atom/branches/main/protection` 실측 응답(url 필드 생략).
ATOM_PROTECTION = {
    "required_pull_request_reviews": {
        "dismiss_stale_reviews": False,
        "require_code_owner_reviews": False,
        "require_last_push_approval": False,
        "required_approving_review_count": 0,
    },
    "required_signatures": {"enabled": False},
    "enforce_admins": {"enabled": True},
    "required_linear_history": {"enabled": False},
    "allow_force_pushes": {"enabled": False},
    "allow_deletions": {"enabled": False},
    "block_creations": {"enabled": False},
    "required_conversation_resolution": {"enabled": False},
    "lock_branch": {"enabled": False},
    "allow_fork_syncing": {"enabled": False},
}

GL_IN_FORCE = {
    "id": 1,
    "name": "main",
    "push_access_levels": [{"id": 11, "access_level": 0}],
    "merge_access_levels": [{"id": 12, "access_level": 40}],
    "allow_force_push": False,
}

MARKER = "server-controlled-text-7f3a"


@pytest.fixture(autouse=True)
def _no_real_spawn(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("a test reached subprocess.run without faking the seam")

    monkeypatch.setattr(protection.subprocess, "run", _boom)


def _render(status: int, body: object) -> str:
    """gh/glab `-i` 출력 모양: status 줄, 헤더, 빈 줄, 본문."""
    text = "" if body is None else json.dumps(body)
    return f"HTTP/2.0 {status} X\r\nContent-Type: application/json\r\n\r\n{text}"


class _Forge:
    """스크립트된 응답을 돌려주고 호출을 기록하는 가짜 seam.

    `responses`는 `(cli, method, path)` → `[(status, body), ...]`. 큐에 둘 이상이면 앞에서
    소비하고 마지막 것은 반복한다.
    """

    def __init__(self, origin: str | None, responses: dict | None = None) -> None:
        self.origin = origin
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.calls: list[tuple[list[str], str | None]] = []
        self.envs: list[dict | None] = []

    def __call__(self, argv, *, stdin_text=None, extra_env=None):
        self.calls.append((list(argv), stdin_text))
        self.envs.append(extra_env)
        if argv[:3] == ["git", "remote", "get-url"]:
            return (0, self.origin + "\n") if self.origin else (2, "")
        method = argv[argv.index("--method") + 1]
        path = argv[argv.index("--method") + 2]
        queue = self.responses[(argv[0], method, path)]
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        return (0 if status < 300 else 1, _render(status, body))

    def writes(self) -> list[tuple[str, str, object]]:
        """GET이 아닌 포지 호출 전부의 (method, path, body)."""
        out = []
        for argv, stdin_text in self.calls:
            if argv[0] in ("gh", "glab") and "--method" in argv:
                method = argv[argv.index("--method") + 1]
                if method != "GET":
                    path = argv[argv.index("--method") + 2]
                    body = json.loads(stdin_text) if stdin_text is not None else None
                    out.append((method, path, body))
        return out


def _run(monkeypatch, forge: _Forge, *argv: str) -> int:
    monkeypatch.setattr(protection, "run_cli", forge)
    return protection._run(list(argv))


UNPROTECTED_BRANCH = (200, {"protected": False, "name": "main"})
PROTECTED_BRANCH = (200, {"protected": True, "name": "main"})


def _queue(branch):
    return [branch] if isinstance(branch, tuple) else list(branch)


def _github(protection_queue, repo=None, branch=UNPROTECTED_BRANCH):
    return _Forge(
        GH_ORIGIN,
        {
            ("gh", "GET", "repos/o/r"): [(200, repo or {"default_branch": "main"})],
            ("gh", "GET", "repos/o/r/branches/main"): _queue(branch),
            ("gh", "GET", "repos/o/r/branches/main/protection"): protection_queue,
            ("gh", "PUT", "repos/o/r/branches/main/protection"): [(200, {})],
        },
    )


def _gitlab(
    protection_queue, post=(201, {}), delete=(204, None), branch=UNPROTECTED_BRANCH
):
    return _Forge(
        GL_ORIGIN,
        {
            ("glab", "GET", GL_PROJECT): [(200, {"default_branch": "main"})],
            ("glab", "GET", f"{GL_PROJECT}/repository/branches/main"): _queue(branch),
            ("glab", "GET", f"{GL_PROJECT}/protected_branches/main"): protection_queue,
            ("glab", "POST", f"{GL_PROJECT}/protected_branches"): [post],
            ("glab", "DELETE", f"{GL_PROJECT}/protected_branches/main"): [delete],
        },
    )


def _protection_gets(forge: _Forge) -> list[list[str]]:
    return [
        argv
        for argv, _ in forge.calls
        if "GET" in argv
        and (argv[-1].endswith("/protection") or "/protected_branches/" in argv[-1])
    ]


GL_POST_BODY = {
    "name": "main",
    "push_access_level": 0,
    "merge_access_level": 40,
    "allow_force_push": False,
}


# ---------- URL 파싱 ----------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/o/r.git", ("github", "github.com", "o/r")),
        ("https://github.com/o/r", ("github", "github.com", "o/r")),
        ("https://github.com/o/r/", ("github", "github.com", "o/r")),
        ("git@github.com:o/r.git", ("github", "github.com", "o/r")),
        ("ssh://git@github.com/o/r.git", ("github", "github.com", "o/r")),
        ("https://user:tok@github.com/o/r.git", ("github", "github.com", "o/r")),
        ("https://gitlab.com/g/sub/r.git", ("gitlab", "gitlab.com", "g/sub/r")),
        ("git@gitlab.example.com:g/r.git", ("gitlab", "gitlab.example.com", "g/r")),
        # ssh 포트는 API 포트가 아니다 — 버린다. http·https 포트는 API 포트다.
        (
            "ssh://git@gitlab.example.com:2222/g/r.git",
            ("gitlab", "gitlab.example.com", "g/r"),
        ),
        (
            "https://gitlab.example.com:8443/g/r.git",
            ("gitlab", "gitlab.example.com:8443", "g/r"),
        ),
        (
            "git+ssh://git@gitlab.example.com:2222/g/r",
            ("gitlab", "gitlab.example.com", "g/r"),
        ),
        (
            "http://localhost:8929/root/scratch.git",
            ("gitlab", "localhost:8929", "root/scratch"),
        ),
        ("git@GitHub.com:o/r.git", ("github", "github.com", "o/r")),
        ("ssh://git@github.com:22/o/r.git", ("github", "github.com", "o/r")),
        ("https://GitLab.Example.com/g/r", ("gitlab", "gitlab.example.com", "g/r")),
    ],
)
def test_parse_origin_accepts_forge_urls(url, expected) -> None:
    assert protection.parse_origin(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/o/r/extra",
        "https://github.com/o",
        "/srv/repo.git",
        "file:///srv/repo.git",
        "../r",
        "./r:x",
        "r",
        "",
        # git은 검증 없이 저장하지만 urlsplit().port가 ValueError를 낸다 — exit 2여야 한다.
        "https://github.com:abc/o/r",
        "https://github.com:70000/o/r",
        "https://[::1/o/r",
        "git@[::1]:o/r",
        "https://[::1]/g/r",
        "ssh://[::1]:2222/g/r",
        # GitHub 경로는 인용 없이 API 경로에 들어간다 — 세그먼트 문자 집합으로 막는다.
        "git@github.com:22:o/r",
        "git@github.com:o/r?x=1",
        "git@github.com:../x",
        "https://github.com/o/r%2Fx",
        "file://github.com/o/r",
        "git@gitlab.example.com:g/r?x=1",
        "git@evil\x1b7.com:g/r",
        # `_origin()`은 첫 줄만 넘기지만, 직접 호출자(#27의 훅)는 개행을 넘길 수 있다.
        "git@gitlab.example.com:g\n/r",
        "git@gitlab.example.com\n:g/r",
    ],
)
def test_parse_origin_rejects_non_forge_urls(url) -> None:
    with pytest.raises(protection._CallerError):
        protection.parse_origin(url)


# ---------- argv·origin ----------


@pytest.mark.parametrize("argv", [[], ["apply", "check"], ["--foo"], ["Apply"]])
def test_argv_requires_exactly_one_known_subcommand(monkeypatch, capsys, argv) -> None:
    forge = _Forge(GH_ORIGIN)
    assert _run(monkeypatch, forge, *argv) == protection.EXIT_CALLER
    assert forge.calls == []
    assert "usage" in capsys.readouterr().err


def test_no_origin_is_a_caller_error_and_calls_no_forge_cli(
    monkeypatch, capsys
) -> None:
    forge = _Forge(None)
    assert _run(monkeypatch, forge, "check") == protection.EXIT_CALLER
    assert [argv[0] for argv, _ in forge.calls] == ["git"]
    assert "no remote named origin" in capsys.readouterr().err


# ---------- GitHub ----------


def test_github_apply_on_absent_puts_the_reference_body(monkeypatch, capsys) -> None:
    forge = _github([(404, {"message": MARKER}), (200, ATOM_PROTECTION)])
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_IN_FORCE
    put = [c for c in forge.calls if "PUT" in c[0]]
    assert len(put) == 1
    argv, stdin_text = put[0]
    assert argv == [
        "gh",
        "api",
        "-i",
        "--hostname",
        "github.com",
        "--method",
        "PUT",
        "repos/o/r/branches/main/protection",
        "-H",
        "Content-Type: application/json",
        "--input",
        "-",
    ]
    assert json.loads(stdin_text) == protection.GITHUB_BODY
    assert all("{owner}" not in " ".join(argv) for argv, _ in forge.calls)
    assert "written; in force" in capsys.readouterr().out


def test_github_apply_when_in_force_issues_no_write(monkeypatch, capsys) -> None:
    # 멱등성: atom의 실제 응답을 읽으면 PUT이 나가지 않는다.
    forge = _github([(200, ATOM_PROTECTION)])
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_IN_FORCE
    assert forge.writes() == []
    assert "already in force" in capsys.readouterr().out


def test_github_apply_on_mismatch_puts_once_then_rereads(monkeypatch, capsys) -> None:
    drifted = {**ATOM_PROTECTION, "enforce_admins": {"enabled": False}}
    forge = _github([(200, drifted), (200, ATOM_PROTECTION)])
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_IN_FORCE
    assert [m for m, _, _ in forge.writes()] == ["PUT"]
    assert len(_protection_gets(forge)) == 2
    # 있던 보호를 통째로 바꾼 쓰기는 "replaced"로, 무엇이 어긋나 있었는지와 함께 보고한다.
    assert (
        "protection of main replaced (was not in force: enforce_admins); in force"
        in capsys.readouterr().out
    )


def test_github_apply_refusal_reports_the_status_only(monkeypatch, capsys) -> None:
    forge = _github([(404, None)])
    forge.responses[("gh", "PUT", "repos/o/r/branches/main/protection")] = [
        (403, {"message": f"Upgrade to GitHub Pro {MARKER}"})
    ]
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_UNDECIDED
    out = capsys.readouterr()
    assert "refused the write (HTTP 403)" in out.out
    assert "landed" not in out.out  # 거부된 PUT은 반영된 단계가 아니다
    assert MARKER not in out.out + out.err
    # 거부 뒤에는 재읽기가 없다.
    assert len(_protection_gets(forge)) == 1


def test_github_apply_written_but_reread_fails_is_exit_5(monkeypatch, capsys) -> None:
    forge = _github([(404, None), (404, None)])
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_NOT_IN_FORCE
    assert "written but not in force: absent" in capsys.readouterr().out


def test_github_check_absent_is_exit_5_and_writes_nothing(monkeypatch, capsys) -> None:
    forge = _github([(404, {"message": MARKER})])
    assert _run(monkeypatch, forge, "check") == protection.EXIT_NOT_IN_FORCE
    assert forge.writes() == []
    out = capsys.readouterr().out
    assert "protection of main is absent" in out
    assert MARKER not in out


def test_github_404_on_a_protected_branch_is_undecided_not_absent(
    monkeypatch, capsys
) -> None:
    # 관리자가 아닌 토큰: 보호 GET은 404인데 브랜치는 protected: true (2026-10-31 실측 모양).
    forge = _github(
        [(404, {"message": "Not Found"})],
        branch=(200, {"protected": True, "protection": {"note": MARKER}}),
    )
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_UNDECIDED
    out = capsys.readouterr().out
    assert (
        "undecided: the branch is protected but no protection is readable under its"
        " name (HTTP 404)" in out
    )
    assert "absent" not in out
    assert MARKER not in out
    assert forge.writes() == []


def test_missing_branch_is_undecided_not_absent(monkeypatch, capsys) -> None:
    forge = _github([(404, None)], branch=(404, {"message": f"Branch {MARKER}"}))
    assert _run(monkeypatch, forge, "check") == protection.EXIT_UNDECIDED
    out = capsys.readouterr().out
    assert "undecided: the forge refused to describe the branch (HTTP 404)" in out
    assert MARKER not in out


def test_branch_without_a_protected_flag_is_undecided(monkeypatch, capsys) -> None:
    forge = _github([(404, None)], branch=(200, {"name": "main"}))
    assert _run(monkeypatch, forge, "check") == protection.EXIT_UNDECIDED
    assert "does not say whether the branch is protected" in capsys.readouterr().out


def test_branch_is_read_only_on_a_404(monkeypatch) -> None:
    forge = _github([(200, ATOM_PROTECTION)])
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_IN_FORCE
    assert not any(argv[-1] == "repos/o/r/branches/main" for argv, _ in forge.calls)


def test_reread_404_after_a_write_goes_through_the_branch_too(
    monkeypatch, capsys
) -> None:
    # PUT 뒤 재읽기가 404면 다시 브랜치를 읽는다 — protected: true면 쓰기 뒤의 판정 불가다.
    forge = _github(
        [(404, None), (404, None)], branch=[UNPROTECTED_BRANCH, PROTECTED_BRANCH]
    )
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_UNDECIDED
    assert (
        "undecided after a write was attempted (landed: PUT): the branch is protected"
        in capsys.readouterr().out
    )
    assert [
        argv[argv.index("--method") + 1 :][:2]
        for argv, _ in forge.calls
        if argv[0] == "gh"
    ] == [
        ["GET", "repos/o/r"],
        ["GET", "repos/o/r/branches/main/protection"],
        ["GET", "repos/o/r/branches/main"],
        ["PUT", "repos/o/r/branches/main/protection"],
        ["GET", "repos/o/r/branches/main/protection"],
        ["GET", "repos/o/r/branches/main"],
    ]


def test_gitlab_404_on_a_protected_branch_is_undecided(monkeypatch, capsys) -> None:
    forge = _gitlab([(404, None)], branch=PROTECTED_BRANCH)
    assert _run(monkeypatch, forge, "check") == protection.EXIT_UNDECIDED
    argv = [a for a, _ in forge.calls if a[0] == "glab"][-1]
    assert argv[-1] == f"{GL_PROJECT}/repository/branches/main"
    assert "no protection is readable under its name (HTTP 404)" in (
        capsys.readouterr().out
    )


def test_github_check_in_force_is_exit_0(monkeypatch, capsys) -> None:
    forge = _github([(200, ATOM_PROTECTION)])
    assert _run(monkeypatch, forge, "check") == protection.EXIT_IN_FORCE
    assert "github o/r: protection of main is in force" in capsys.readouterr().out


def test_github_check_names_unmet_properties(monkeypatch, capsys) -> None:
    drifted = {
        **ATOM_PROTECTION,
        "allow_force_pushes": {"enabled": True},
        "allow_deletions": {"enabled": True, "note": MARKER},
    }
    forge = _github([(200, drifted)])
    assert _run(monkeypatch, forge, "check") == protection.EXIT_NOT_IN_FORCE
    out = capsys.readouterr().out
    assert "not in force: force_push_disallowed deletion_disallowed" in out
    assert MARKER not in out


def test_github_check_treats_missing_keys_as_unmet(monkeypatch, capsys) -> None:
    forge = _github([(200, {})])
    assert _run(monkeypatch, forge, "check") == protection.EXIT_NOT_IN_FORCE
    assert (
        "pull_request_reviews_required enforce_admins force_push_disallowed "
        "deletion_disallowed" in capsys.readouterr().out
    )


def test_github_check_stricter_review_count_is_still_in_force(monkeypatch) -> None:
    stricter = {
        **ATOM_PROTECTION,
        "required_pull_request_reviews": {"required_approving_review_count": 1},
    }
    forge = _github([(200, stricter)])
    assert _run(monkeypatch, forge, "check") == protection.EXIT_IN_FORCE


@pytest.mark.parametrize("actor", ["users", "teams", "apps"])
def test_github_check_review_bypass_allowance_is_unmet(
    monkeypatch, capsys, actor
) -> None:
    # 우회 목록에 누군가 있으면 그 행위자는 PR 없이 push할 수 있다 — in force가 아니다.
    bypass = {"users": [], "teams": [], "apps": [], actor: [{"slug": MARKER}]}
    bypassed = {
        **ATOM_PROTECTION,
        "required_pull_request_reviews": {
            "required_approving_review_count": 0,
            "bypass_pull_request_allowances": bypass,
        },
    }
    forge = _github([(200, bypassed)])
    assert _run(monkeypatch, forge, "check") == protection.EXIT_NOT_IN_FORCE
    out = capsys.readouterr().out
    assert "not in force: no_review_bypass" in out
    assert MARKER not in out


@pytest.mark.parametrize("value", [None, [], [{"login": "x"}], "x"])
def test_github_check_non_object_bypass_is_unmet(monkeypatch, capsys, value) -> None:
    odd = {
        **ATOM_PROTECTION,
        "required_pull_request_reviews": {"bypass_pull_request_allowances": value},
    }
    forge = _github([(200, odd)])
    assert _run(monkeypatch, forge, "check") == protection.EXIT_NOT_IN_FORCE
    assert "not in force: no_review_bypass" in capsys.readouterr().out


def test_github_check_empty_bypass_lists_are_in_force(monkeypatch) -> None:
    empty = {
        **ATOM_PROTECTION,
        "required_pull_request_reviews": {
            "required_approving_review_count": 0,
            "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
        },
    }
    forge = _github([(200, empty)])
    assert _run(monkeypatch, forge, "check") == protection.EXIT_IN_FORCE


def test_default_branch_is_read_from_the_forge(monkeypatch, capsys) -> None:
    forge = _Forge(
        GH_ORIGIN,
        {
            ("gh", "GET", "repos/o/r"): [(200, {"default_branch": "develop"})],
            ("gh", "GET", "repos/o/r/branches/develop/protection"): [
                (200, ATOM_PROTECTION)
            ],
        },
    )
    assert _run(monkeypatch, forge, "check") == protection.EXIT_IN_FORCE
    assert "protection of develop is in force" in capsys.readouterr().out
    assert not any("main" in argv[-1] for argv, _ in forge.calls if argv[0] == "gh")


def test_unprintable_default_branch_is_replaced_in_output(monkeypatch, capsys) -> None:
    name = f"rel ease {MARKER}\x1b[0m"
    forge = _Forge(
        GH_ORIGIN,
        {
            ("gh", "GET", "repos/o/r"): [(200, {"default_branch": name})],
            (
                "gh",
                "GET",
                f"repos/o/r/branches/{protection.quote(name, safe='')}/protection",
            ): [(200, ATOM_PROTECTION)],
        },
    )
    assert _run(monkeypatch, forge, "check") == protection.EXIT_IN_FORCE
    out = capsys.readouterr().out
    assert "(unprintable name)" in out
    assert MARKER not in out


def test_printable_rejects_a_trailing_newline() -> None:
    # `$`는 끝의 개행을 허용한다 — fullmatch여야 허용 목록이 정확하다.
    assert protection._printable("main\n") == protection._UNPRINTABLE
    assert protection._printable("release/1.0") == "release/1.0"


def test_repository_describe_refusal_is_undecided(monkeypatch, capsys) -> None:
    forge = _github([], repo=None)
    forge.responses[("gh", "GET", "repos/o/r")] = [(401, {"message": MARKER})]
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_UNDECIDED
    out = capsys.readouterr().out
    assert "undecided: the forge refused to describe the repository (HTTP 401)" in out
    assert "after a write" not in out  # 쓰기 전의 판정 불가에는 표식이 없다
    assert MARKER not in out


# ---------- GitLab ----------


def test_gitlab_apply_on_absent_posts_with_the_encoded_project_path(
    monkeypatch, capsys
) -> None:
    forge = _gitlab([(404, {"message": MARKER}), (200, GL_IN_FORCE)])
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_IN_FORCE
    assert forge.writes() == [
        (
            "POST",
            f"{GL_PROJECT}/protected_branches",
            {
                "name": "main",
                "push_access_level": 0,
                "merge_access_level": 40,
                "allow_force_push": False,
            },
        )
    ]
    post_argv = next(argv for argv, _ in forge.calls if "POST" in argv)
    # glab의 --hostname은 포트 붙은 값을 거부한다 — 호스트는 GITLAB_HOST 환경변수로.
    assert post_argv[:3] == ["glab", "api", "-i"]
    assert "--hostname" not in post_argv
    assert ":id" not in " ".join(post_argv)
    glab_envs = [
        e
        for argv_env in zip(forge.calls, forge.envs)
        if argv_env[0][0][0] == "glab"
        for e in [argv_env[1]]
    ]
    assert glab_envs and all(
        e == {"GITLAB_HOST": "gitlab.example.com"} for e in glab_envs
    )
    assert (
        "gitlab gitlab.example.com g/sub/r: protection of main written; in force"
        in (capsys.readouterr().out)
    )


def test_gitlab_apply_on_existing_protection_deletes_then_posts(
    monkeypatch, capsys
) -> None:
    # push·merge 모두 Maintainer에 force push 허용인 기존 보호(테스트가 만든 상태).
    # CE는 PATCH의 allowed_to_* 를 무시하므로 지우고 참조 집합으로 다시 만든다.
    existing = {
        "push_access_levels": [{"id": 11, "access_level": 40}],
        "merge_access_levels": [{"id": 12, "access_level": 40}],
        "allow_force_push": True,
    }
    forge = _gitlab([(200, existing), (200, GL_IN_FORCE)])
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_IN_FORCE
    assert forge.writes() == [
        ("DELETE", f"{GL_PROJECT}/protected_branches/main", None),
        ("POST", f"{GL_PROJECT}/protected_branches", GL_POST_BODY),
    ]
    assert (
        "protection of main replaced (was not in force: push_no_one "
        "force_push_disallowed); in force" in capsys.readouterr().out
    )


def test_gitlab_apply_when_in_force_issues_no_write(monkeypatch, capsys) -> None:
    forge = _gitlab([(200, GL_IN_FORCE)])
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_IN_FORCE
    assert forge.writes() == []
    assert "already in force" in capsys.readouterr().out


def test_gitlab_apply_on_merge_no_one_rewrites_with_maintainer(monkeypatch) -> None:
    locked = {
        **GL_IN_FORCE,
        "merge_access_levels": [{"id": 12, "access_level": 0}],
    }
    forge = _gitlab([(200, locked), (200, GL_IN_FORCE)])
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_IN_FORCE
    assert [m for m, _, _ in forge.writes()] == ["DELETE", "POST"]
    assert forge.writes()[1][2]["merge_access_level"] == 40


def test_gitlab_post_refusal_after_delete_is_reported_without_reread(
    monkeypatch, capsys
) -> None:
    # DELETE 뒤 POST가 거부되면 보호 없는 상태로 남고 exit 3 — 줄이 DELETE가 반영됐다고 말한다.
    forge = _gitlab(
        [(200, {"allow_force_push": True})], post=(403, {"message": MARKER})
    )
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_UNDECIDED
    out = capsys.readouterr()
    assert (
        "undecided after a write was attempted (landed: DELETE): "
        "the forge refused the write (HTTP 403)" in out.out
    )
    assert MARKER not in out.out + out.err
    assert [m for m, _, _ in forge.writes()] == ["DELETE", "POST"]
    assert len(_protection_gets(forge)) == 1


def _timing_out(forge: _Forge, method: str):
    """`method` 호출만 타임아웃(rc 124)으로 바꾼 seam."""

    def _fake(argv, *, stdin_text=None, extra_env=None):
        if method in argv:
            forge.calls.append((list(argv), stdin_text))
            return 124, ""
        return forge(argv, stdin_text=stdin_text, extra_env=extra_env)

    return _fake


def test_gitlab_post_timeout_after_delete_names_the_landed_delete(
    monkeypatch, capsys
) -> None:
    forge = _gitlab([(200, {"allow_force_push": True})])
    monkeypatch.setattr(protection, "run_cli", _timing_out(forge, "POST"))
    assert protection._run(["apply"]) == protection.EXIT_UNDECIDED
    assert (
        "undecided after a write was attempted (landed: DELETE): "
        "glab did not answer within 30s" in capsys.readouterr().out
    )


def test_gitlab_delete_timeout_names_no_landed_step_and_posts_nothing(
    monkeypatch, capsys
) -> None:
    # DELETE의 답이 없으면 반영 여부를 모른다 — landed 없이 표식만, POST는 나가지 않는다.
    forge = _gitlab([(200, {"allow_force_push": True})])
    monkeypatch.setattr(protection, "run_cli", _timing_out(forge, "DELETE"))
    assert protection._run(["apply"]) == protection.EXIT_UNDECIDED
    out = capsys.readouterr().out
    assert (
        "undecided after a write was attempted: glab did not answer within 30s" in out
    )
    assert "landed" not in out
    assert [m for m, _, _ in forge.writes()] == ["DELETE"]


def test_gitlab_post_refusal_on_absent_names_no_landed_step(
    monkeypatch, capsys
) -> None:
    forge = _gitlab([(404, None)], post=(403, None))
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_UNDECIDED
    out = capsys.readouterr().out
    assert "undecided after a write was attempted: the forge refused the write" in out
    assert "landed" not in out


def test_gitlab_reread_failure_after_delete_and_post_names_both_in_order(
    monkeypatch, capsys
) -> None:
    forge = _gitlab([(200, {"allow_force_push": True}), (500, {"message": MARKER})])
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_UNDECIDED
    out = capsys.readouterr().out
    assert (
        "undecided after a write was attempted (landed: DELETE POST): "
        "the forge refused to read the protection (HTTP 500)" in out
    )
    assert MARKER not in out


def test_gitlab_check_user_push_entry_is_not_no_one(monkeypatch, capsys) -> None:
    with_user = {
        **GL_IN_FORCE,
        "push_access_levels": [
            {"id": 11, "access_level": 0},
            # 사용자 항목은 문서상 access_level이 null이다.
            {
                "id": 13,
                "access_level": None,
                "user_id": 7,
                "access_level_description": MARKER,
            },
        ],
    }
    forge = _gitlab([(200, with_user)])
    assert _run(monkeypatch, forge, "check") == protection.EXIT_NOT_IN_FORCE
    out = capsys.readouterr().out
    assert "not in force: push_no_one" in out
    assert MARKER not in out


def test_gitlab_check_empty_levels_are_unmet(monkeypatch, capsys) -> None:
    forge = _gitlab([(200, {"push_access_levels": [], "allow_force_push": False})])
    assert _run(monkeypatch, forge, "check") == protection.EXIT_NOT_IN_FORCE
    assert "push_no_one merge_maintainer" in capsys.readouterr().out


def test_gitlab_delete_refusal_reports_the_status_and_posts_nothing(
    monkeypatch, capsys
) -> None:
    forge = _gitlab(
        [(200, {"allow_force_push": True})], delete=(403, {"message": MARKER})
    )
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_UNDECIDED
    out = capsys.readouterr()
    assert "refused the delete (HTTP 403)" in out.out
    assert "landed" not in out.out
    assert MARKER not in out.out + out.err
    assert [m for m, _, _ in forge.writes()] == ["DELETE"]


def test_unauthenticated_glab_banner_is_undecided(monkeypatch, capsys) -> None:
    # 이 머신에서 실측한 미인증 glab: status 줄 없이 배너만 stdout에, rc 0.
    calls = []

    def _fake(argv, *, stdin_text=None, extra_env=None):
        calls.append(argv)
        if argv[0] == "git":
            return 0, GL_ORIGIN + "\n"
        return 0, f"   ERROR  \n  Unauthenticated. {MARKER}\n"

    monkeypatch.setattr(protection, "run_cli", _fake)
    assert protection._run(["check"]) == protection.EXIT_UNDECIDED
    out = capsys.readouterr().out
    assert "glab returned no HTTP status line" in out
    assert MARKER not in out
    assert len(calls) == 2


# ---------- seam·방어 ----------


def test_run_cli_separates_timeout_124_from_missing_binary_127(monkeypatch) -> None:
    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    def _missing(*_a, **_k):
        raise FileNotFoundError

    monkeypatch.setattr(protection.subprocess, "run", _timeout)
    assert protection.run_cli(["x"]) == (124, "")
    monkeypatch.setattr(protection.subprocess, "run", _missing)
    assert protection.run_cli(["x"]) == (127, "")


def test_run_cli_closes_stderr_and_stdin_and_disables_prompts(monkeypatch) -> None:
    seen = {}

    def _record(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(protection.subprocess, "run", _record)
    assert protection.run_cli(["x"]) == (0, "ok")
    assert seen["stderr"] is subprocess.DEVNULL
    assert seen["stdin"] is subprocess.DEVNULL
    assert "input" not in seen
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["env"]["GH_PROMPT_DISABLED"] == "1"
    assert seen["env"]["GLAB_SEND_TELEMETRY"] == "false"

    seen.clear()
    # 상속된 GIT_DIR은 `git remote get-url`을 다른 저장소로 돌린다 — 환경에서 뺀다.
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/elsewhere")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    assert protection.run_cli(["x"]) == (0, "ok")
    assert "GIT_DIR" not in seen["env"]
    assert "GIT_WORK_TREE" not in seen["env"]
    assert seen["env"]["GIT_CONFIG_COUNT"] == "0"

    seen.clear()
    assert protection.run_cli(["x"], stdin_text="{}") == (0, "ok")
    assert seen["input"] == "{}"
    assert "stdin" not in seen

    seen.clear()
    monkeypatch.setenv("GITLAB_HOST", "inherited.example")
    assert protection.run_cli(["x"], extra_env={"GITLAB_HOST": "h:1"}) == (0, "ok")
    assert seen["env"]["GITLAB_HOST"] == "h:1"


def test_undecided_when_the_cli_cannot_run_or_times_out(monkeypatch, capsys) -> None:
    for rc, phrase in ((127, "gh could not be run"), (124, "gh did not answer")):

        def _fake(argv, *, stdin_text=None, extra_env=None, rc=rc):
            return (0, GH_ORIGIN + "\n") if argv[0] == "git" else (rc, "")

        monkeypatch.setattr(protection, "run_cli", _fake)
        assert protection._run(["check"]) == protection.EXIT_UNDECIDED
        assert phrase in capsys.readouterr().out


def test_git_timeout_is_undecided_not_a_caller_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(protection, "run_cli", lambda argv, **_: (124, ""))
    assert protection._run(["check"]) == protection.EXIT_UNDECIDED
    assert "git did not answer" in capsys.readouterr().out


def test_git_exit_128_is_a_caller_error_with_its_own_reason(
    monkeypatch, capsys
) -> None:
    # 저장소 밖에서 `git remote get-url`은 128을 낸다 — "origin 없음"과 다른 사유다.
    monkeypatch.setattr(
        protection, "run_cli", lambda argv, **_: (128, f"fatal: {MARKER}")
    )
    assert protection._run(["check"]) == protection.EXIT_CALLER
    err = capsys.readouterr().err
    assert "git exited 128 reading the origin remote" in err
    assert MARKER not in err


def test_write_timeout_is_reported_as_after_a_write_was_sent(
    monkeypatch, capsys
) -> None:
    # PUT이 타임아웃하면 요청은 나갔고 결과를 모른다 — 쓰기 전 형태로 읽히면 안 된다.
    forge = _github([(404, None)])

    def _fake(argv, *, stdin_text=None, extra_env=None):
        if "PUT" in argv:
            forge.calls.append((list(argv), stdin_text))
            return 124, ""
        return forge(argv, stdin_text=stdin_text, extra_env=extra_env)

    monkeypatch.setattr(protection, "run_cli", _fake)
    assert protection._run(["apply"]) == protection.EXIT_UNDECIDED
    assert (
        "undecided after a write was attempted: gh did not answer"
        in capsys.readouterr().out
    )


def test_reread_failure_after_a_write_says_so(monkeypatch, capsys) -> None:
    # PUT은 나갔는데 재읽기가 거부되면 "쓰기가 나갔다"는 사실이 줄에 남아야 한다.
    forge = _github([(404, None), (500, {"message": MARKER})])
    assert _run(monkeypatch, forge, "apply") == protection.EXIT_UNDECIDED
    out = capsys.readouterr().out
    assert (
        "undecided after a write was attempted (landed: PUT): "
        "the forge refused to read the protection (HTTP 500)" in out
    )
    assert MARKER not in out
    assert [m for m, _, _ in forge.writes()] == ["PUT"]


def test_http_parses_status_and_body_from_include_output() -> None:
    assert protection._STATUS_LINE_RE.match("HTTP/1.1 404 Not Found\r\n")
    assert protection._STATUS_LINE_RE.match("HTTP/2.0 200 OK")
    assert not protection._STATUS_LINE_RE.match("   ERROR  ")


def test_main_returns_undecided_on_an_internal_error(monkeypatch, capsys) -> None:
    def _boom(_argv):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(protection, "_run", _boom)
    monkeypatch.setattr(sys, "argv", ["prog", "check"])
    assert protection.main() == protection.EXIT_UNDECIDED
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert MARKER not in err


def test_the_inventory_row_cites_a_command_this_module_accepts() -> None:
    # meta/README.md의 행이 인용한 호출을 argv 계약과 대조한다 — 인용이 낡아도 grep은 초록이다.
    readme = (_REPO_ROOT / "meta" / "README.md").read_text(encoding="utf-8")
    rows = [
        line for line in readme.splitlines() if line.startswith("| `branch_protection`")
    ]
    assert len(rows) == 1, "the inventory must list branch_protection exactly once"
    cells = [c.strip() for c in rows[0].strip("|").split("|")]
    command = cells[2].replace("`", "")
    tokens = command.split()
    assert tokens[:7] == [
        "uv",
        "run",
        "--directory",
        "meta",
        "python",
        "-m",
        "harness.branch_protection",
    ], tokens
    cited = set(re.split(r"[|/ ]", tokens[7])) - {""}
    assert cited == set(protection._COMMANDS)
    for name in cited:
        assert protection._parse_args([name]) == name
