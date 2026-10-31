# branch_protection의 GitLab 경로를 로컬 GitLab 테스트 환경(실인스턴스)으로 검증하는 통합 테스트
"""branch-protection GitLab 통합 테스트 (드리프트 카나리아).

meta/infra/gitlab/의 온디맨드 환경이 떠 있을 때만 실행된다 — state.env 부재 시 전체
skip이라 일반 `pytest`는 계속 green이다.
실행: `meta/infra/gitlab/run.sh uv run --directory meta pytest -m gitlab`.

단위 테스트가 믿음으로 둔 세 가지를 실인스턴스로 잰다. glab `-i`가 404에서도 status
줄을 내는지, 보호가 없는 브랜치에 POST가 통하는지, 있는 보호를 DELETE 뒤 POST로
push No one·merge Maintainer·force push 금지로 바꿀 수 있는지. 그리고 `origin`
옆에 `upstream`(atom의 GitHub URL)이 있어도 대상이 scratch 프로젝트인지 — 자식
프로젝트의 두 원격 함정.

시드 프로젝트(`root/scratch`)는 README로 초기화되어 `main`이 있고 GitLab이 기본
브랜치를 Maintainer/Maintainer로 자동 보호한 상태다. 테스트는 REST로 그 보호를 지우고
원하는 출발 상태를 만든다. 정리하지 않는다 — 환경 자체가 run.sh teardown으로 일회성이다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from harness.branch_protection import protection

# `or` 폴백: 빈 문자열 TMPDIR도 /tmp로 — 셸의 ${TMPDIR:-/tmp}(run.sh)와 동일 의미론.
_STATE_DIR = Path(os.environ.get("TMPDIR") or "/tmp") / "atom-gitlab-infra"
_STATE_FILE = _STATE_DIR / "state.env"

_SUBPROCESS_TIMEOUT = 30
_API_TIMEOUT = 10

pytestmark = [
    pytest.mark.gitlab,
    pytest.mark.skipif(
        not _STATE_FILE.exists(),
        reason="GitLab 테스트 환경 없음 — meta/infra/gitlab/run.sh로 기동",
    ),
]


def _api(state: dict, method: str, path: str, body: dict | None = None) -> object:
    """GitLab REST API를 JSON 본문으로 호출한다 (PRIVATE-TOKEN 인증).

    픽스처 단계의 실패는 응답 본문을 포함해 pytest.fail로 수렴한다 — 도구와 달리 여기서는
    시끄럽게 드러나야 한다. 404는 호출자가 다루도록 None으로 돌려준다.

    Args:
        state: gitlab_state 픽스처가 만든 세션 상태.
        method: HTTP 메서드.
        path: `/api/v4` 뒤에 붙는 경로.
        body: JSON으로 보낼 본문. 없으면 본문 없음.

    Returns:
        응답 JSON. 본문이 비면 None, 404면 None.
    """
    url = f"{state['url']}/api/v4{path}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"PRIVATE-TOKEN": state["token"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_API_TIMEOUT) as response:
            raw = response.read()
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        text = exc.read().decode(errors="replace")
        pytest.fail(f"GitLab API {method} {path} failed: HTTP {exc.code}\n{text}")
    except OSError as exc:
        # URLError도 OSError다. 연결 중 reset은 URLError로 감싸이지 않으므로 넓게 잡는다.
        pytest.fail(
            f"GitLab API {method} {path} connection failed: {getattr(exc, 'reason', exc)} "
            "(possibly stale state.env — restart via meta/infra/gitlab/run.sh)"
        )


def _project_id(state: dict) -> str:
    return urllib.parse.quote(state["project"], safe="")


def _protection_path(state: dict) -> str:
    return f"/projects/{_project_id(state)}/protected_branches/main"


def _unprotect(state: dict) -> None:
    _api(state, "DELETE", _protection_path(state))
    assert _api(state, "GET", _protection_path(state)) is None


def _levels(entry: object, key: str) -> list[int]:
    assert isinstance(entry, dict)
    return sorted(e["access_level"] for e in entry[key])


@pytest.fixture(scope="session")
def gitlab_state() -> dict:
    """state.env를 읽고 격리된 GLAB_CONFIG_DIR로 glab 로그인을 마친 세션 상태.

    issue_duplicate_guard의 통합 테스트와 같은 레시피다 — 저장소 밖에서 실행해 git
    remote 기반 호스트 추론을 차단하고, api_protocol=http로 비대화식 로그인한다.
    os.environ은 건드리지 않는다(복사본만 사용).

    Returns:
        host/url/token/project/env 키를 가진 dict. env는 도구 호출용 환경 변수 복사본.
    """
    if shutil.which("glab") is None:
        pytest.fail("glab CLI is not installed — cannot run the integration tests")
    raw = _STATE_FILE.read_text(encoding="utf-8")
    parsed = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    required = ("GITLAB_TESTENV_URL", "GITLAB_TESTENV_TOKEN", "GITLAB_TESTENV_PROJECT")
    missing = [key for key in required if not parsed.get(key)]
    if missing:
        pytest.fail(f"state.env is missing required key(s): {missing}")

    host = parsed["GITLAB_TESTENV_URL"].removeprefix("http://")
    env = {
        **os.environ,
        "GLAB_CONFIG_DIR": str(_STATE_DIR / "glab-branch-protection"),
        "GLAB_SEND_TELEMETRY": "false",
    }
    login = subprocess.run(
        [
            "glab",
            "auth",
            "login",
            "--hostname",
            host,
            "--token",
            parsed["GITLAB_TESTENV_TOKEN"],
            "--api-protocol",
            "http",
            "--git-protocol",
            "http",
        ],
        cwd=_STATE_DIR,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        check=False,
    )
    if login.returncode != 0:
        pytest.fail(
            "glab auth login 실패 (stale state.env 가능성 — "
            f"meta/infra/gitlab/run.sh로 재기동):\n{login.stderr}"
        )
    return {
        "host": host,
        "url": parsed["GITLAB_TESTENV_URL"],
        "token": parsed["GITLAB_TESTENV_TOKEN"],
        "project": parsed["GITLAB_TESTENV_PROJECT"],
        "env": env,
    }


@pytest.fixture
def child_repo(gitlab_state: dict, tmp_path: Path, monkeypatch) -> Path:
    """origin이 scratch 프로젝트이고 upstream이 atom인 로컬 저장소. cwd와 env를 맞춘다."""
    repo = tmp_path / "child"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        [
            "remote",
            "add",
            "origin",
            f"{gitlab_state['url']}/{gitlab_state['project']}.git",
        ],
        ["remote", "add", "upstream", "https://github.com/Styleefi/atom.git"],
    ):
        subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            check=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    monkeypatch.chdir(repo)
    for key in ("GLAB_CONFIG_DIR", "GLAB_SEND_TELEMETRY"):
        monkeypatch.setenv(key, gitlab_state["env"][key])
    return repo


def test_apply_from_absent_then_idempotent_then_check(
    gitlab_state: dict, child_repo: Path, monkeypatch, capsys
) -> None:
    _unprotect(gitlab_state)
    assert protection._run(["check"]) == protection.EXIT_NOT_IN_FORCE
    assert "protection of main is absent" in capsys.readouterr().out

    assert protection._run(["apply"]) == protection.EXIT_IN_FORCE
    out = capsys.readouterr().out
    assert f"gitlab {gitlab_state['host']} {gitlab_state['project']}: " in out
    assert "written; in force" in out

    entry = _api(gitlab_state, "GET", _protection_path(gitlab_state))
    assert _levels(entry, "push_access_levels") == [0]
    assert _levels(entry, "merge_access_levels") == [40]
    assert entry["allow_force_push"] is False

    real = protection.run_cli
    seen: list[list[str]] = []

    def _record(argv, *, stdin_text=None, extra_env=None):
        seen.append(list(argv))
        return real(argv, stdin_text=stdin_text, extra_env=extra_env)

    monkeypatch.setattr(protection, "run_cli", _record)
    assert protection._run(["apply"]) == protection.EXIT_IN_FORCE
    assert "already in force" in capsys.readouterr().out
    methods = [a[a.index("--method") + 1] for a in seen if a[0] == "glab"]
    assert methods == ["GET", "GET"]

    assert protection._run(["check"]) == protection.EXIT_IN_FORCE


def test_apply_converges_an_existing_maintainer_protection(
    gitlab_state: dict, child_repo: Path, capsys
) -> None:
    # REST로 만든 출발 상태: push·merge Maintainer, force push 허용.
    _unprotect(gitlab_state)
    _api(
        gitlab_state,
        "POST",
        f"/projects/{_project_id(gitlab_state)}/protected_branches",
        {
            "name": "main",
            "push_access_level": 40,
            "merge_access_level": 40,
            "allow_force_push": True,
        },
    )

    assert protection._run(["check"]) == protection.EXIT_NOT_IN_FORCE
    assert "not in force: push_no_one force_push_disallowed" in capsys.readouterr().out

    assert protection._run(["apply"]) == protection.EXIT_IN_FORCE
    assert "written; in force" in capsys.readouterr().out

    entry = _api(gitlab_state, "GET", _protection_path(gitlab_state))
    assert _levels(entry, "push_access_levels") == [0]
    assert _levels(entry, "merge_access_levels") == [40]
    assert entry["allow_force_push"] is False

    assert protection._run(["check"]) == protection.EXIT_IN_FORCE


def test_glab_include_output_is_parsed_on_404(
    gitlab_state: dict, child_repo: Path
) -> None:
    # 단위 테스트가 가정한 `-i` 출력 모양(status 줄 + 빈 줄 + 본문)이 비-2xx에서도 성립하는지.
    status, data = protection._http(
        protection.GITLAB,
        gitlab_state["host"],
        "GET",
        f"projects/{_project_id(gitlab_state)}/protected_branches/no-such-branch",
    )
    assert status == 404
    assert data is None or isinstance(data, dict)
