# origin 포지의 기본 브랜치 보호를 API로 걸고(apply) 읽어 판정하는(check) 오너 실행 도구
"""branch-protection: 자식 프로젝트의 기본 브랜치 보호를 포지 API로 걸고 확인한다 (#221).

자식 프로젝트는 atom을 클론해 훅은 상속받지만 포지의 브랜치 보호는 저장소별 설정이라
상속되지 않는다. commit-backstop 규칙이 push 층을 서버 보호에 맡기므로, 그 보호를 거는
절차를 README 산문이 아니라 실행 파일이 수행하게 한다 (PR #220의 기록).

주장하는 것:
    - 대상은 `git remote get-url origin`이 돌려준 URL이 가리키는 저장소 하나다. host가
      `github.com`이면 `gh api`, 그 외 host는 전부 `glab api`로 부른다. API 경로는 그
      URL에서 뽑은 owner/repo(GitLab: 프로젝트 경로)로 **전부 풀어 쓴다** — gh의
      `{owner}/{repo}` placeholder는 `gh repo set-default`를 쓰지 않은 저장소에서
      `upstream` 원격을 `origin`보다 먼저 고르므로(2026-10-31 실측), 자식 프로젝트에서는
      atom 저장소를 가리킨다. 호스트도 매 호출 명시한다 — gh에는
      `--hostname github.com`, glab에는 `GITLAB_HOST` 환경변수(glab의 `--hostname`은
      포트 붙은 값을 거부하고, 환경변수는 git 디렉터리 추론보다 우선한다 — 2026-10-31
      실측). 상속된 `GH_REPO`·`GH_HOST`·`GITLAB_HOST`는 그 둘에 덮여 대상을 고르지
      못하고, 상속된 `GIT_DIR`·`GIT_WORK_TREE`는 환경에서 빼므로 origin을 읽는
      저장소를 바꾸지 못한다. 그 밖의 호스트 변수(예: `GITLAB_API_HOST`)와 그 밖의
      `GIT_*`(insteadOf를 싣는 `GIT_CONFIG_*` 등)는 중화하지 않는다.
    - 보호 대상 브랜치는 포지가 답한 `default_branch`다. `main`도 `master`도 가정하지
      않는다.
    - `check`는 읽기만 한다. 필요한 속성이 전부 켜져 있으면 exit 0, 보호가 없거나
      하나라도 꺼져 있으면 exit 5.
    - `apply`는 먼저 읽어서 필요한 속성이 전부 켜져 있으면 **쓰지 않고** exit 0으로
      끝난다(멱등). 아니면 한 번 쓰고 다시 읽어 판정한다. 쓰기는 GitHub에서 보호 전체를
      참조 집합(`GITHUB_BODY`)으로 교체하는 PUT 하나, GitLab에서 보호가 없으면 POST 하나,
      있으면 DELETE 뒤 같은 POST다 — GitLab CE는 PATCH의 `allowed_to_push`·
      `allowed_to_merge`를 무시한다(문서상 Premium·Ultimate 전용, CE 19.2.0 실측
      2026-10-31). 오너 결정 2026-10-31: 1안(DELETE 뒤 POST).
    - 필요한 속성. GitHub: PR 리뷰 필수(승인 수는 비교하지 않는다 — 더 엄격한 값도
      통과), 리뷰 우회 허용 목록(`bypass_pull_request_allowances`) 비어 있음, 관리자에게도
      적용, force-push 금지, 삭제 금지. GitLab: push 권한 No one만,
      merge 권한 Maintainer만, force-push 금지. merge를 No one으로 두면 MR이 전부
      막히므로 Maintainer를 요구한다.
    - 보호 읽기의 404는 브랜치를 한 번 더 읽어 가른다 — GitHub는 관리자가 아닌 토큰에도
      404를 내기 때문이다(2026-10-31 실측). 브랜치가 `protected: false`면 보호 없음,
      `true`면 그 이름으로 읽히는 보호가 없는 것이므로 exit 3, 브랜치 읽기가 200이
      아니면(없는 브랜치 포함) exit 3이다.
    - 그 밖에 포지가 읽기·쓰기를 거부하면 HTTP status 정수만 싣고 exit 3이다.
      원인은 진단하지 않는다 — #221이 든 사례(GitHub Free 요금제의 비공개 저장소)도
      여기서는 status 하나로만 보인다. 그 사례를 push 없이 알아내는 것이 `check`의
      존재 이유다.

주장하지 않는 것:
    - `github.com` 밖의 GitHub 호스트(Enterprise, SSH over 443의 `ssh.github.com`, 호스트
      문자 집합에 맞는 ssh 설정 별칭)는 GitLab으로 오감지된다. 지원하지 않는다.
    - origin URL의 scheme을 glab에 넘기지 않는다. glab은 `glab auth login`이 그 host에
      기록한 api_protocol을 쓴다. http·https URL의 기본값 아닌 포트만 host에 붙이고(API
      포트 — 테스트 스택의 `localhost:8929`가 그 경우다) 그 밖의 포트는 버린다.
    - `git remote get-url`은 `url.<base>.insteadOf`를 적용한 결과를 돌려준다. 파싱 대상은
      그 결과다.
    - 인증은 gh·glab 자신의 것이다. 미로그인·권한 부족은 거부(exit 3)로만 보인다. glab은
      미인증이면 status 줄 없이 배너만 낸다(rc는 호스트에 따라 0 또는 1) — exit 3이다.
    - GitHub PUT은 보호 설정 전체를 `GITHUB_BODY`로 교체한다. 본문에 있는 값은 본문
      값이 된다 — 더 엄격했던 승인 수 2도 0이 되고, 본문이 null로 두는 status check·
      restrictions는 꺼진다 — 그리고 본문에 없는 설정(linear history 등)이 남는다고
      보장하지 않는다. 쓰기는 보호가 없거나 필요한 속성 중 하나라도 미충족일 때만
      나가고, "replaced" 줄은 쓰기 전에 미충족이던 속성 이름을 싣는다. 통째 교체는
      선언된 경계다(오너 결정 2026-10-31, atom PR #224 원장의 결정 댓글): 이 도구가 쓰는
      것은 참조 집합 하나이고, 참조 집합 밖의 설정은 지워지는 쪽으로 틀린다.
    - GitLab의 DELETE와 POST 사이에는 보호가 없는 짧은 구간이 있다(CE 19.2.0 로컬
      스택 실측 0.12초, 대부분 glab 기동). POST가 거부되거나 답이 없으면 보호 없는
      상태로 남을 수 있고, 도구는 재읽기 없이 "(landed: DELETE)"를 단 exit 3을 낸다.
      참조 집합 밖의 항목(유료판의 사용자·그룹 허용 등)은 DELETE로 사라진다 — GitHub의
      PUT 전체 교체와 같은 성질이다.
    - 쓰기 단계에 들어간 뒤의 판정 불가(거부·타임아웃·CLI 미기동·재읽기 실패)는
      "undecided after a write was attempted"로 보고하고, 성공 status가 돌아온 쓰기
      단계가 있으면 "(landed: DELETE)"처럼 그 이름을 순서대로 덧붙인다. 한 번의
      Ctrl-C(SIGINT)는 traceback 대신 "interrupted"에 같은 표식을 달아 exit 3이다. 쓰기
      전의 판정 불가에는 이 표식이 없다.
    - IPv6 리터럴 호스트는 지원하지 않는다. 호스트는 `[A-Za-z0-9.-]`(+`:포트`), 경로
      세그먼트는 `[A-Za-z0-9._-]`로만 된 것만 포지 URL로 읽는다. 그 밖은 exit 2다.
    - 판정은 기본 브랜치와 정확히 같은 이름의 규칙(GitLab)·classic protection(GitHub)
      만 읽는다(그 읽기가 404면 브랜치의 `protected` 플래그도). 같은 브랜치에 적용되는
      다른 규칙은 읽지 않는다. GitLab: 정확한 이름의 규칙이 참조 집합이면 더 느슨한
      와일드카드 규칙이 있어도 exit 0이다 — 덜 잡는 쪽으로 틀린다. GitHub: ruleset은
      classic protection을 더 엄격하게만 만들므로(문서: 가장 엄격한 쪽이 적용) 판정이
      약해지지 않고, ruleset만으로 보호된 브랜치는 classic protection이 404라 exit 3이다.
      오너 결정 2026-10-31(atom PR #224 원장의 결정 댓글).
    - 읽기와 쓰기 사이의 동시 변경은 감지하지 않는다. 재읽기가 보여 주는 것이 판정이다.
    - `GITHUB_BODY`는 `bypass_pull_request_allowances`를 보내지 않는다. 생략이 그 목록을
      비우는지는 실포지에서 검증하지 않았다 — 비우지 않으면 `apply`는 돌릴 때마다 쓰고
      exit 5다.
    - 쓰기 뒤 재읽기가 exit 5면 그대로 보고한다. 재시도하지 않는다.
    - CI에서는 돌지 않는다. CI 이미지에 gh·glab이 없고, 단위 테스트는 어떤 바이너리도
      띄우지 않는다. 실포지 검증은 GitLab은 `gitlab` 마커 테스트(meta/infra/gitlab)로,
      GitHub는 atom 자신에 대한 `check` 실행으로만 했다 — GitHub `apply`의 쓰기 경로는
      실포지에서 검증하지 않았다(오너 결정 2026-10-31).

출력 채널:
    gh·glab·git의 stderr는 **어떤 경로로도 에코하지 않는다.** 응답 본문의 `message`·
    설명 문자열·id도 인쇄하지 않는다 — 서버가 제어하는 텍스트다. 인쇄하는 변수 내용은
    forge·host·프로젝트 경로(로컬 origin URL 유래), HTTP status 정수, 미충족 속성의
    상수 이름, 그리고 기본 브랜치 이름이다. 브랜치 이름은 `[A-Za-z0-9._/-]`만으로 된
    것만 그대로 찍고, 아니면 `(unprintable name)`으로 바꿔 찍는다(작업은 계속한다).

종료 코드:
    0 in force / 5 not in force / 3 판정 불가(포지 거부·CLI 부재·타임아웃·응답 형식·Ctrl-C) /
    2 호출자 잘못 / 1 도구가 돌지 못함(uv·import 실패 — 의도적으로 내지 않는다).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from urllib.parse import quote, urlsplit

TAG = "[branch-protection]"

EXIT_IN_FORCE = 0
EXIT_CALLER = 2
EXIT_UNDECIDED = 3
EXIT_NOT_IN_FORCE = 5

TIMEOUT_SECONDS = 30

GITHUB = "github"
GITLAB = "gitlab"
GITHUB_HOST = "github.com"

# atom 자신의 보호 설정과 같은 값이다(2026-10-31 `gh api .../protection` 실측).
GITHUB_BODY = {
    "required_status_checks": None,
    "enforce_admins": True,
    "required_pull_request_reviews": {"required_approving_review_count": 0},
    "restrictions": None,
    "allow_force_pushes": False,
    "allow_deletions": False,
}

GITLAB_NO_ONE = 0
GITLAB_MAINTAINER = 40

_COMMANDS = ("apply", "check")
_USAGE = f"{TAG} usage: python -m harness.branch_protection apply|check"

_STATUS_LINE_RE = re.compile(r"^HTTP/\S+ (\d{3})\b")
_FORGE_SCHEMES = frozenset({"http", "https", "ssh", "git", "git+ssh", "ssh+git"})
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_PRINTABLE_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_UNPRINTABLE = "(unprintable name)"


class _CallerError(Exception):
    """호출자 잘못 (exit 2)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Undecided(Exception):
    """판정을 내릴 수 없는 상태 (exit 3)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def run_cli(
    argv: list[str],
    *,
    stdin_text: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """git·gh·glab을 실행하고 (종료 코드, stdout)을 돌려준다. 모든 외부 호출의 유일한 이음매다.

    stderr는 돌려주지 않는다(모듈 docstring의 출력 채널 항목). 환경은 상속한다 — gh·glab이
    자기 설정 디렉터리에서 인증을 읽어야 하기 때문이다 — 다만 저장소 위치를 바꾸는
    `GIT_DIR`·`GIT_WORK_TREE`는 빼고, 그 위에 프롬프트를 끄는 변수와 `extra_env`를
    얹는다(상속된 같은 이름의 값은 덮인다).

    Args:
        argv: 실행할 명령 전체.
        stdin_text: 표준 입력으로 넘길 본문. None이면 stdin을 닫는다.
        extra_env: 이 호출에만 얹을 환경 변수.

    Returns:
        `(rc, stdout)`. 타임아웃은 rc 124, 실행 실패(바이너리 부재 등)는 rc 127 — 둘 다
        CLI가 내지 않는 값이고 서로 달라야 한다.
    """
    stdin_kwargs = (
        {"input": stdin_text}
        if stdin_text is not None
        else {"stdin": subprocess.DEVNULL}
    )
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
            env={
                **{
                    k: v
                    for k, v in os.environ.items()
                    if k not in ("GIT_DIR", "GIT_WORK_TREE")
                },
                "GIT_TERMINAL_PROMPT": "0",
                "GH_PROMPT_DISABLED": "1",
                "GLAB_SEND_TELEMETRY": "false",
                **(extra_env or {}),
            },
            timeout=TIMEOUT_SECONDS,
            check=False,
            **stdin_kwargs,
        )
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""
    return proc.returncode, proc.stdout


def _parse_args(argv: list[str]) -> str:
    if len(argv) != 1 or argv[0] not in _COMMANDS:
        raise _CallerError("expected exactly one of: apply, check")
    return argv[0]


def parse_origin(url: str) -> tuple[str, str, str]:
    """origin URL을 (forge, host, 프로젝트 경로)로 가른다.

    `://`가 있으면 URL(scheme은 http·https·ssh·git 계열만)로, 없으면 scp 형식
    (`[user@]host:path`)으로 읽는다. userinfo는
    버린다(출력에도 나가지 않는다). 호스트 이름은 소문자로 비교한다. 이름이
    `github.com`이면 포트와 무관하게 GitHub이고 경로는 정확히 `owner/repo`여야 한다.
    그 외는 전부 GitLab이고, http·https URL에 기본값이 아닌 포트가 있으면 `host:port`를
    host로 보며(API 포트; 그 밖의 scheme의 포트와 그 scheme의 기본 포트 — http 80,
    https 443 — 는 버린다), 경로의 세그먼트 수는 제한하지 않는다(서브그룹). 호스트는 `[A-Za-z0-9.-]`, 경로 세그먼트는
    `[A-Za-z0-9._-]`로만 된 것을 받는다 — GitHub 경로는 인용 없이 API 경로에 들어가므로
    `?`·`:`·`..` 같은 문자가 요청을 바꾸지 못하게 한다. 포트 자리가 숫자가 아니거나
    범위 밖이면, 그리고 IPv6 호스트는 포지 URL로 읽지 않는다.

    Args:
        url: `git remote get-url origin`의 한 줄.

    Returns:
        `(forge, host, path)`. host는 http·https의 기본값 아닌 포트가 있으면 `host:port`.

    Raises:
        _CallerError: 포지 URL로 읽을 수 없을 때.
    """
    url = url.strip()
    port = None
    if "://" in url:
        try:
            parts = urlsplit(url)
            hostname, port = parts.hostname, parts.port
        except ValueError:
            raise _CallerError("origin is not a forge URL") from None
        if parts.scheme not in _FORGE_SCHEMES:
            raise _CallerError("origin is not a forge URL")
        if parts.scheme not in ("http", "https"):
            port = None  # http·https 밖의 포트는 API 포트가 아니다
        if port == {"http": 80, "https": 443}.get(parts.scheme):
            port = None  # 명시된 기본 포트는 생략과 같은 URL이다
        path = parts.path
    else:
        head, sep, path = url.partition(":")
        if not sep or not head or head.startswith(("/", ".")) or "/" in head:
            raise _CallerError("origin is not a forge URL")
        hostname = head.rpartition("@")[2]
    hostname = (hostname or "").lower()
    path = path.strip("/").removesuffix(".git").strip("/")
    segments = path.split("/")
    if (
        not _HOST_RE.fullmatch(hostname)
        or not path
        or not all(
            _SEGMENT_RE.fullmatch(seg) and seg not in (".", "..") for seg in segments
        )
    ):
        raise _CallerError("origin is not a forge URL")
    if hostname == GITHUB_HOST:
        if len(segments) != 2:
            raise _CallerError("a github.com origin must be owner/repo")
        return GITHUB, hostname, path
    host = hostname if port is None else f"{hostname}:{port}"
    return GITLAB, host, path


def _origin() -> tuple[str, str, str]:
    rc, out = run_cli(["git", "remote", "get-url", "origin"])
    if rc == 127:
        raise _Undecided("git could not be run")
    if rc == 124:
        raise _Undecided(f"git did not answer within {TIMEOUT_SECONDS}s")
    if rc == 2 or (rc == 0 and not out.strip()):
        raise _CallerError("no remote named origin")
    if rc != 0:
        raise _CallerError(f"git exited {rc} reading the origin remote")
    return parse_origin(out.splitlines()[0])


def _http(
    forge: str, host: str, method: str, path: str, body: object = None
) -> tuple[int, object]:
    """포지 API를 CLI로 한 번 부르고 (HTTP status, 본문 JSON)을 돌려준다.

    `-i`로 받은 헤더 블록의 첫 줄에서 status를 읽는다. 본문이 JSON이 아니면 None이다.

    Args:
        forge: `GITHUB` 또는 `GITLAB`.
        host: 호출할 호스트. GitHub는 `--hostname github.com`, GitLab은 `GITLAB_HOST`로
            넘긴다(포트 포함 가능).
        method: HTTP 메서드.
        path: API 경로(placeholder 없이 전부 풀어 쓴 것).
        body: JSON으로 직렬화해 stdin으로 넘길 본문. None이면 본문 없음.

    Returns:
        `(status, json 또는 None)`.

    Raises:
        _Undecided: CLI가 돌지 않았거나, 답하지 않았거나, status 줄이 없을 때.
    """
    if forge == GITHUB:
        cli, hostname_args, extra_env = "gh", ["--hostname", GITHUB_HOST], None
    else:
        cli, hostname_args, extra_env = "glab", [], {"GITLAB_HOST": host}
    argv = [cli, "api", "-i", *hostname_args, "--method", method, path]
    stdin_text = None
    if body is not None:
        # glab의 --input은 Content-Type을 붙이지 않아 GitLab이 415를 낸다(2026-10-31 실측).
        argv += ["-H", "Content-Type: application/json", "--input", "-"]
        stdin_text = json.dumps(body)
    rc, out = run_cli(argv, stdin_text=stdin_text, extra_env=extra_env)
    if rc == 127:
        raise _Undecided(f"{cli} could not be run")
    if rc == 124:
        raise _Undecided(f"{cli} did not answer within {TIMEOUT_SECONDS}s")
    out = out.replace("\r\n", "\n")
    match = _STATUS_LINE_RE.match(out)
    if match is None:
        raise _Undecided(f"{cli} returned no HTTP status line")
    text = out.partition("\n\n")[2]
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:
        data = None
    return int(match.group(1)), data


def _repo_path(forge: str, path: str) -> str:
    return f"repos/{path}" if forge == GITHUB else f"projects/{quote(path, safe='')}"


def _protection_path(forge: str, path: str, branch: str) -> str:
    branch_q = quote(branch, safe="")
    if forge == GITHUB:
        return f"repos/{path}/branches/{branch_q}/protection"
    return f"projects/{quote(path, safe='')}/protected_branches/{branch_q}"


def _branch_path(forge: str, path: str, branch: str) -> str:
    branch_q = quote(branch, safe="")
    if forge == GITHUB:
        return f"repos/{path}/branches/{branch_q}"
    return f"projects/{quote(path, safe='')}/repository/branches/{branch_q}"


def _default_branch(forge: str, host: str, path: str) -> str:
    status, data = _http(forge, host, "GET", _repo_path(forge, path))
    if status != 200:
        raise _Undecided(
            f"the forge refused to describe the repository (HTTP {status})"
        )
    name = data.get("default_branch") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name:
        raise _Undecided("the forge reports no default branch")
    return name


def _read(forge: str, host: str, path: str, branch: str) -> dict | None:
    """보호 설정을 읽는다. 없으면 None.

    404는 브랜치의 `protected` 플래그로 가른다 — GitHub는 관리자가 아닌 토큰에도 보호를
    404로 숨기므로, 404만으로는 "없음"을 단정할 수 없다(모듈 docstring).
    """
    status, data = _http(forge, host, "GET", _protection_path(forge, path, branch))
    if status == 404:
        status, data = _http(forge, host, "GET", _branch_path(forge, path, branch))
        if status != 200:
            raise _Undecided(
                f"the forge refused to describe the branch (HTTP {status})"
            )
        flag = data.get("protected") if isinstance(data, dict) else None
        if flag is False:
            return None
        if flag is True:
            raise _Undecided(
                "the branch is protected but no protection is readable under its name"
                " (HTTP 404)"
            )
        raise _Undecided("the forge does not say whether the branch is protected")
    if status != 200:
        raise _Undecided(f"the forge refused to read the protection (HTTP {status})")
    if not isinstance(data, dict):
        raise _Undecided("the forge answered with something other than a JSON object")
    return data


def _enabled(state: dict, key: str) -> object:
    value = state.get(key)
    return value.get("enabled") if isinstance(value, dict) else None


def _levels(state: dict, key: str) -> list:
    value = state.get(key)
    return value if isinstance(value, list) else []


def _level(entry: object) -> object:
    return entry.get("access_level") if isinstance(entry, dict) else None


def _unmet(forge: str, state: dict) -> list[str]:
    """켜져 있어야 하는데 꺼진(또는 없는) 속성의 상수 이름들. 비어 있으면 in force."""
    unmet: list[str] = []
    if forge == GITHUB:
        reviews = state.get("required_pull_request_reviews")
        if not isinstance(reviews, dict):
            unmet.append("pull_request_reviews_required")
        else:
            bypass = reviews.get("bypass_pull_request_allowances", {})
            if not isinstance(bypass, dict) or any(
                bypass.get(k) for k in ("users", "teams", "apps")
            ):
                unmet.append("no_review_bypass")
        if _enabled(state, "enforce_admins") is not True:
            unmet.append("enforce_admins")
        if _enabled(state, "allow_force_pushes") is not False:
            unmet.append("force_push_disallowed")
        if _enabled(state, "allow_deletions") is not False:
            unmet.append("deletion_disallowed")
        return unmet
    push = _levels(state, "push_access_levels")
    if not push or any(_level(e) != GITLAB_NO_ONE for e in push):
        unmet.append("push_no_one")
    merge = _levels(state, "merge_access_levels")
    if not merge or any(_level(e) != GITLAB_MAINTAINER for e in merge):
        unmet.append("merge_maintainer")
    if state.get("allow_force_push") is not False:
        unmet.append("force_push_disallowed")
    return unmet


def _write(
    forge: str,
    host: str,
    path: str,
    branch: str,
    state: dict | None,
    landed: list[str],
) -> None:
    """보호를 참조 집합으로 쓴다. 성공 status가 아니면 판정 불가로 올린다.

    GitLab에 보호가 이미 있으면 먼저 DELETE한다(모듈 docstring의 DELETE+POST 항목).
    성공 status가 돌아온 쓰기 단계의 이름을 `landed`에 차례로 넣는다 — 중간에 판정
    불가로 끝나도 호출자가 어디까지 반영됐는지 보고할 수 있게.

    Args:
        landed: 성공한 쓰기 단계 이름(`PUT`·`DELETE`·`POST`)이 쌓이는 리스트.
    """
    if forge == GITHUB:
        status, _ = _http(
            forge, host, "PUT", _protection_path(forge, path, branch), GITHUB_BODY
        )
        if status != 200:
            raise _Undecided(f"the forge refused the write (HTTP {status})")
        landed.append("PUT")
        return
    if state is not None:
        status, _ = _http(forge, host, "DELETE", _protection_path(forge, path, branch))
        if status != 204:
            raise _Undecided(f"the forge refused the delete (HTTP {status})")
        landed.append("DELETE")
    status, _ = _http(
        forge,
        host,
        "POST",
        f"projects/{quote(path, safe='')}/protected_branches",
        {
            "name": branch,
            "push_access_level": GITLAB_NO_ONE,
            "merge_access_level": GITLAB_MAINTAINER,
            "allow_force_push": False,
        },
    )
    if status != 201:
        raise _Undecided(f"the forge refused the write (HTTP {status})")
    landed.append("POST")


def _label(forge: str, host: str, path: str) -> str:
    return f"{forge} {path}" if forge == GITHUB else f"{forge} {host} {path}"


def _printable(branch: str) -> str:
    return branch if _PRINTABLE_BRANCH_RE.fullmatch(branch) else _UNPRINTABLE


def _run(argv: list[str]) -> int:
    """절차 전체를 순서대로 수행한다.

    Args:
        argv: 프로그램 이름을 제외한 인자 목록.

    Returns:
        종료 코드.
    """
    label = None
    wrote = False
    landed: list[str] = []
    try:
        command = _parse_args(argv)
        forge, host, path = _origin()
        label = _label(forge, host, path)
        branch = _default_branch(forge, host, path)
        shown = _printable(branch)
        state = _read(forge, host, path, branch)
        unmet = _unmet(forge, state) if state is not None else None
        if command == "check":
            if state is None:
                print(f"{TAG} {label}: protection of {shown} is absent")
                return EXIT_NOT_IN_FORCE
            if unmet:
                print(
                    f"{TAG} {label}: protection of {shown} is not in force: "
                    + " ".join(unmet)
                )
                return EXIT_NOT_IN_FORCE
            print(f"{TAG} {label}: protection of {shown} is in force")
            return EXIT_IN_FORCE
        if state is not None and not unmet:
            print(f"{TAG} {label}: protection of {shown} is already in force")
            return EXIT_IN_FORCE
        wrote = True
        verb = (
            "written"
            if state is None
            else "replaced (was not in force: " + " ".join(unmet) + ")"
        )
        _write(forge, host, path, branch, state, landed)
        state = _read(forge, host, path, branch)
        unmet = _unmet(forge, state) if state is not None else ["absent"]
        if unmet:
            print(
                f"{TAG} {label}: protection of {shown} {verb} but not in force: "
                + " ".join(unmet)
            )
            return EXIT_NOT_IN_FORCE
        print(f"{TAG} {label}: protection of {shown} {verb}; in force")
        return EXIT_IN_FORCE
    except _CallerError as exc:
        print(f"{TAG} {exc.reason}", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return EXIT_CALLER
    except (_Undecided, KeyboardInterrupt) as exc:
        where = f" {label}:" if label else ""
        when = " after a write was attempted" if wrote else ""
        if landed:
            when += f" (landed: {' '.join(landed)})"
        kind = "interrupted" if isinstance(exc, KeyboardInterrupt) else "undecided"
        reason = "" if isinstance(exc, KeyboardInterrupt) else f": {exc.reason}"
        print(f"{TAG}{where} {kind}{when}{reason}")
        return EXIT_UNDECIDED


def main() -> int:
    """최상위 실행기 — 내부 오류가 판정으로 새지 않게 한다.

    잡히지 않은 예외는 파이썬이 exit 1로 내보내는데, 그건 uv 실패와 겹치고 날것의
    traceback이 에이전트 컨텍스트로 쏟아진다. 예외 문구는 서브프로세스 유래 텍스트를
    담을 수 있으므로 **타입명만** 싣는다.

    Returns:
        종료 코드. 암묵적 None(→ exit 0) 경로가 없다.
    """
    try:
        return _run(sys.argv[1:])
    except Exception as exc:  # noqa: BLE001 — 사망 부류 방어가 설계 요구사항
        print(f"{TAG} internal error ({type(exc).__name__})", file=sys.stderr)
        return EXIT_UNDECIDED
