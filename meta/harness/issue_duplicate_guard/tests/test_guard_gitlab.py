# glab 어댑터를 로컬 GitLab 테스트 환경(실인스턴스)으로 검증하는 통합 테스트
"""issue-duplicate-guard glab 어댑터 통합 테스트 (드리프트 카나리아).

meta/infra/gitlab/의 온디맨드 환경이 떠 있을 때만 실행된다 — state.env
부재 시 전체 skip이라 일반 `pytest`는 계속 green이고, 환경이 떠 있으면
plain `pytest`에서도 함께 실행되는 것은 의도된 동작이다.
실행: `meta/infra/gitlab/run.sh uv run --directory meta pytest -m gitlab`.

검증 대상은 guard가 실행하는 `glab issue list --all --search` 호출의 실제
출력이 보수 파싱 정규식(_GLAB_ISSUE_LINE_RE)과 맞는지다 — 어댑터는
fail-open 설계라 형식이 어긋나면 GitLab 쪽 중복 차단이 조용히 무력화된다
(issue #9). 시드 이슈는 정리하지 않는다 — 환경 자체가 run.sh teardown으로
일회성이다.

검증 불가 경계: Claude Code가 훅을 실제 호출하는 구간과 settings.json의
`uv run` 래퍼. 프로세스 경계까지는 test_module_entrypoint_blocks_duplicate가
커버한다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pytest

from harness.issue_duplicate_guard import guard
from harness.issue_duplicate_guard.tests.test_guard import _bash_payload, _run_main

# `or` 폴백: 빈 문자열 TMPDIR도 /tmp로 — 셸의 ${TMPDIR:-/tmp}(run.sh)와 동일 의미론.
_STATE_DIR = Path(os.environ.get("TMPDIR") or "/tmp") / "atom-gitlab-infra"
_STATE_FILE = _STATE_DIR / "state.env"
_META_DIR = Path(__file__).resolve().parents[3]

_SUBPROCESS_TIMEOUT = 30
_API_TIMEOUT = 10
_POLL_ATTEMPTS = 15
_POLL_INTERVAL = 2.0

pytestmark = [
    pytest.mark.gitlab,
    pytest.mark.skipif(
        not _STATE_FILE.exists(),
        reason="GitLab 테스트 환경 없음 — meta/infra/gitlab/run.sh로 기동",
    ),
]


# ---------- 헬퍼 ----------

def _api(state: dict, method: str, path: str, form: dict | None = None) -> dict:
    """GitLab REST API를 호출한다 (PRIVATE-TOKEN 인증).

    실패는 응답 본문/원인을 포함해 pytest.fail로 수렴한다 — guard의
    fail-open과 달리 픽스처 단계의 실패는 시끄럽게 드러나야 한다.

    Args:
        state: gitlab_state 픽스처가 만든 세션 상태.
        method: HTTP 메서드 ("POST", "PUT" 등).
        path: `/api/v4` 뒤에 붙는 경로.
        form: 폼 인코딩으로 보낼 필드. 없으면 본문 없음.

    Returns:
        응답 JSON을 파싱한 dict.
    """
    url = f"{state['url']}/api/v4{path}"
    data = urllib.parse.urlencode(form).encode() if form else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"PRIVATE-TOKEN": state["token"]},
    )
    try:
        with urllib.request.urlopen(request, timeout=_API_TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        pytest.fail(f"GitLab API {method} {path} 실패: HTTP {exc.code}\n{body}")
    except urllib.error.URLError as exc:
        pytest.fail(
            f"GitLab API {method} {path} 연결 실패: {exc.reason} "
            "(stale state.env 가능성 — meta/infra/gitlab/run.sh로 재기동)"
        )


def _adapter_search(state: dict, title: str) -> subprocess.CompletedProcess[str]:
    """guard.search_duplicates의 glab 분기와 동일한 argv로 검색을 실행한다."""
    argv = [
        "glab", "issue", "list",
        "--all",
        "--search", title,
        "--per-page", str(guard.MAX_CANDIDATES),
        "--repo", state["project"],
    ]
    return subprocess.run(
        argv, cwd=_STATE_DIR, env=state["env"], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
    )


def _wait_until_searchable(state: dict, title: str) -> None:
    """시드 제목이 어댑터 검색에 나타날 때까지 바운디드 폴링한다 (인덱싱 지연 흡수)."""
    last = None
    for _ in range(_POLL_ATTEMPTS):
        last = _adapter_search(state, title)
        if title in last.stdout:
            return
        time.sleep(_POLL_INTERVAL)
    pytest.fail(
        f"시드 이슈가 {_POLL_ATTEMPTS * _POLL_INTERVAL:.0f}s 내에 검색되지 않음: {title!r}\n"
        f"stdout:\n{last.stdout}\nstderr:\n{last.stderr}"
    )


def _create_payload(state: dict, title: str, override: bool = False) -> dict:
    """`glab issue create` PreToolUse payload를 만든다.

    --repo는 필수 — cwd가 state_dir(비 git 저장소)라 repo 없이는 glab이
    대상 저장소를 추론하지 못해 검색 실패 → fail-open으로 오진된다.
    """
    prefix = f"{guard.OVERRIDE_TOKEN} " if override else ""
    command = f'{prefix}glab issue create --repo {state["project"]} --title "{title}"'
    return _bash_payload(command)


# ---------- 픽스처 ----------

@pytest.fixture(scope="session")
def gitlab_state() -> dict:
    """state.env를 읽고 격리된 GLAB_CONFIG_DIR로 glab 로그인을 마친 세션 상태.

    smoke.sh:16-27의 실측 레시피를 그대로 따른다 — 저장소 밖(state_dir)에서
    실행해 git remote 기반 호스트 추론을 차단하고, api_protocol=http로
    비대화식 로그인한다. os.environ은 건드리지 않는다(복사본만 사용).

    Returns:
        host/url/token/project/env 키를 가진 dict. env는 glab 호출용
        환경 변수 복사본.
    """
    if shutil.which("glab") is None:
        pytest.fail("glab CLI가 설치되어 있지 않다 — 통합 테스트 실행 불가")
    raw = _STATE_FILE.read_text(encoding="utf-8")
    parsed = dict(
        line.split("=", 1) for line in raw.splitlines() if "=" in line
    )
    required = ("GITLAB_TESTENV_URL", "GITLAB_TESTENV_TOKEN", "GITLAB_TESTENV_PROJECT")
    missing = [key for key in required if not parsed.get(key)]
    if missing:
        pytest.fail(f"state.env에 필수 키 누락: {missing}")

    host = parsed["GITLAB_TESTENV_URL"].removeprefix("http://")
    env = {
        **os.environ,
        "GLAB_CONFIG_DIR": str(_STATE_DIR / "glab-pytest"),
        "GLAB_SEND_TELEMETRY": "false",
        "GITLAB_HOST": host,
    }
    login = subprocess.run(
        [
            "glab", "auth", "login",
            "--hostname", host,
            "--token", parsed["GITLAB_TESTENV_TOKEN"],
            "--api-protocol", "http",
            "--git-protocol", "http",
        ],
        cwd=_STATE_DIR, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
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


@pytest.fixture(scope="session")
def seeded_issues(gitlab_state: dict) -> dict[str, str]:
    """스크래치 프로젝트에 open/closed 시드 이슈를 REST API로 만든다.

    glab create 계열의 대화형 프롬프트 리스크를 피해 API로 시드한다 —
    guard가 실행하는 glab 명령은 `issue list`뿐이라 커버리지 손실이 없다.

    Returns:
        {"open": <열린 이슈 제목>, "closed": <닫힌 이슈 제목>}.
    """
    project = urllib.parse.quote(gitlab_state["project"], safe="")
    run_id = uuid.uuid4().hex[:12]
    titles = {
        "open": f"dup-guard-it-open {run_id}",
        "closed": f"dup-guard-it-closed {run_id}",
    }
    _api(gitlab_state, "POST", f"/projects/{project}/issues", {"title": titles["open"]})
    closed = _api(
        gitlab_state, "POST", f"/projects/{project}/issues", {"title": titles["closed"]}
    )
    _api(
        gitlab_state, "PUT",
        f"/projects/{project}/issues/{closed['iid']}", {"state_event": "close"},
    )
    for title in titles.values():
        _wait_until_searchable(gitlab_state, title)
    return titles


@pytest.fixture
def guard_glab_env(gitlab_state: dict, monkeypatch: pytest.MonkeyPatch) -> dict:
    """guard의 _run_search subprocess가 상속할 환경을 준비한다.

    GLAB_CONFIG_DIR는 gitlab_state가 로그인한 경로와 동일해야 한다 —
    다른 경로면 미인증 config로 검색이 실패한다. chdir는 smoke.sh 실측대로
    git remote 추론을 차단하는 belt-and-suspenders.
    """
    monkeypatch.setenv("GLAB_CONFIG_DIR", gitlab_state["env"]["GLAB_CONFIG_DIR"])
    monkeypatch.setenv("GLAB_SEND_TELEMETRY", "false")
    monkeypatch.setenv("GITLAB_HOST", gitlab_state["host"])
    monkeypatch.chdir(_STATE_DIR)
    return gitlab_state


# ---------- 어댑터 검색 표면 (드리프트 카나리아) ----------

def test_real_search_output_matches_conservative_regex(
    gitlab_state: dict, seeded_issues: dict[str, str]
) -> None:
    result = _adapter_search(gitlab_state, seeded_issues["open"])
    matching = [
        line.strip()
        for line in result.stdout.splitlines()
        if seeded_issues["open"] in line
    ]
    assert matching, (
        f"시드 이슈 라인이 출력에 없음.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert any(guard._GLAB_ISSUE_LINE_RE.match(line) for line in matching), (
        "실제 glab 출력이 보수 파싱 정규식과 불일치 — 어댑터가 조용히 fail-open된다.\n"
        f"시드 라인: {matching!r}\n전체 stdout:\n{result.stdout}"
    )


def test_all_flag_includes_closed_issues(
    gitlab_state: dict, seeded_issues: dict[str, str]
) -> None:
    result = _adapter_search(gitlab_state, seeded_issues["closed"])
    assert seeded_issues["closed"] in result.stdout, (
        f"--all이 닫힌 이슈를 반환하지 않음.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_search_duplicates_finds_seeded_issue(
    guard_glab_env: dict, seeded_issues: dict[str, str]
) -> None:
    invocation = guard.CreateInvocation(
        cli="glab", title=seeded_issues["open"],
        repo=guard_glab_env["project"], override=False,
    )
    candidates = guard.search_duplicates(invocation)
    assert candidates is not None, "검색이 실패(fail-open)로 수렴 — glab 인증/환경 확인"
    assert any(seeded_issues["open"] in candidate for candidate in candidates), candidates


# ---------- 판정 흐름 e2e ----------

def test_duplicate_create_blocked_end_to_end(
    guard_glab_env: dict, seeded_issues: dict[str, str],
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    code = _run_main(monkeypatch, _create_payload(guard_glab_env, seeded_issues["open"]))
    err = capsys.readouterr().err
    assert code == 2, f"차단(exit 2)이어야 하는데 {code} — stderr:\n{err}"
    assert any(line.strip().startswith("#") for line in err.splitlines()), err
    assert guard.OVERRIDE_TOKEN in err


def test_module_entrypoint_blocks_duplicate(
    guard_glab_env: dict, seeded_issues: dict[str, str]
) -> None:
    # cwd는 meta가 아니라 state_dir — meta면 guard의 glab subprocess가 저장소
    # 안(GitHub remote)에서 돌아 outside-repo 레시피가 깨진다. 임포트는 PYTHONPATH로.
    payload = json.dumps(_create_payload(guard_glab_env, seeded_issues["open"]))
    result = subprocess.run(
        [sys.executable, "-m", "harness.issue_duplicate_guard"],
        input=payload, cwd=_STATE_DIR,
        env={**os.environ, "PYTHONPATH": str(_META_DIR)},
        capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
    )
    assert result.returncode == 2, (
        f"exit 2여야 하는데 {result.returncode} — stderr:\n{result.stderr}"
    )
    assert guard.OVERRIDE_TOKEN in result.stderr


def test_unique_title_passes_end_to_end(
    guard_glab_env: dict,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # 단일 토큰 제목 — 검색이 단어를 OR 매칭하는 버전이어도 타 이슈와 안 겹친다.
    title = f"nodup{uuid.uuid4().hex}"
    code = _run_main(monkeypatch, _create_payload(guard_glab_env, title))
    err = capsys.readouterr().err
    assert code == 0, f"통과(exit 0)여야 하는데 {code} — stderr:\n{err}"
    # fail-open 경유 가짜 통과 차단: 검색이 실제 실행되어 빈 결과였음을 구분한다.
    assert "duplicate check skipped" not in err, f"검색이 실행되지 않고 fail-open됨:\n{err}"


def test_override_prefix_passes_end_to_end(
    guard_glab_env: dict, seeded_issues: dict[str, str],
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    code = _run_main(
        monkeypatch,
        _create_payload(guard_glab_env, seeded_issues["open"], override=True),
    )
    err = capsys.readouterr().err
    assert code == 0
    # override 경로는 검색 자체를 건너뛰므로 출력이 전혀 없어야 한다.
    assert err == "", f"override 경로는 출력이 없어야 함:\n{err}"
