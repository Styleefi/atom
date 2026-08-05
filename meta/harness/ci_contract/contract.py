# 양 포지 CI 파일(.gitlab-ci.yml ↔ harness.yml)의 공유 계약 드리프트를 검사하는 순수 로직
"""양 포지 CI 계약 검사 로직 (#80).

`.gitlab-ci.yml`의 ``harness`` 잡과 `.github/workflows/harness.yml`의
``jobs.harness``가 공유해야 하는 계약 — 같은 잡 이미지, `meta/.python-version`과
일치하는 파이썬 major.minor, canonical harness 명령 목록과 문자 그대로 일치하는
명령 목록 — 을 순수 함수로 검사한다. 강제 지점은 ``tests/test_live_repo.py``의 pytest 테스트다.

두 CI 파일은 포지 관용구(트리거·캐시·interruptible 등)가 달라야 정상이므로,
전체 스키마 비교가 아니라 위 계약 키만 좁게 단언하고, 명령이 계약 밖 키로
새는 우회로(before_script, 비화이트리스트 uses:, include/extends)는
fail-closed로 막는다.
"""

import re
from pathlib import Path

import yaml

GITLAB_CI = ".gitlab-ci.yml"
GITHUB_WORKFLOW = ".github/workflows/harness.yml"
MARKER = ".dual-forge-ci"
PYTHON_VERSION_FILE = "meta/.python-version"

OPT_OUT_HINT = (
    "To opt out of this contract, delete the root .dual-forge-ci marker — "
    "deleting it withdraws the dual-forge contract entirely and the check "
    "skips itself."
)

_CHECKOUT_PREFIX = "actions/checkout@"
# 범용 (\d+\.\d+)는 uv:0.11.26-python3.14-trixie에서 uv 버전을 먼저 잡는다.
_PYTHON_IN_TAG = re.compile(r"python:?(\d+\.\d+)")

# 불변식 (4)의 canonical harness 명령 목록 — 양쪽의 정규화된 명령 목록이 이
# 목록과 문자 그대로 일치해야 한다. "이 줄이 pytest를 실행하는가"류의 해석은
# 리뷰 라운드 1~3에서 층층이 새는 게 증명됐다(파생 토큰 → 설치 명령 → exit 0
# 같은 제어 흐름 주입 — 줄의 존재가 줄의 실행을 보장하지 않는다). 목록 전체
# 고정은 추가·삭제·재배열·변형을 일괄 위반으로 만들어 해석 자체를 제거한다.
# harness 명령을 바꾸는 PR은 이 목록도 같은 PR에서 갱신해야 한다.
CANONICAL_COMMANDS = [
    "uv sync --locked --directory meta",
    "uv run --directory meta pytest",
    "uv run --directory meta python -m harness.rules_checker",
]


class UnknownTag:
    """전용 로더가 알 수 없는 YAML 태그 자리에 남기는 센티널.

    GitLab 고유 태그(``!reference`` 등)를 무시하되 ``None``으로 뭉개지 않기
    위한 표식이다. ``None``이면 harness 잡 안에서 태그가 쓰였을 때 TypeError로
    죽어 원인이 가려지고, 센티널이면 "판독 불가" 위반으로 명시된다.
    """


class _CiLoader(yaml.SafeLoader):
    """unknown 태그를 UnknownTag 센티널로 삼키는 전용 로더.

    전역 ``yaml.SafeLoader``에 생성자를 달면 같은 프로세스에서 yaml을 쓰는
    rules_checker의 파싱까지 오염되므로, 반드시 이 서브클래스에만 등록한다
    (PyYAML의 add_multi_constructor는 copy-on-write라 부모는 불변).
    """


_CiLoader.add_multi_constructor("!", lambda loader, suffix, node: UnknownTag())


def find_repo_root() -> Path:
    """이 파일의 고정 위치로부터 저장소 루트를 역산한다.

    이 모듈은 항상 <루트>/meta/harness/ci_contract/에 위치하므로 상위 3단계가
    곧 저장소 루트다. meta/가 자체 pyproject.toml을 가진 자기완결 uv
    프로젝트라서 마커 탐색 방식은 meta/를 루트로 오인할 수 있어 쓰지 않는다
    (rules_checker의 find_repo_root와 동일한 관례·근거).

    Returns:
        저장소 루트 디렉토리.
    """
    return Path(__file__).resolve().parents[3]


def decide_mode(root: Path) -> tuple[str, str]:
    """검사 모드를 판정한다.

    마커(.dual-forge-ci)의 의미는 "이 저장소는 이중 포지 계약을 선언한다"이다.
    마커 부재는 계약 전체의 철회이므로 CI 파일 존재 여부와 무관하게 skip이다 —
    위반 메시지의 탈출구("마커를 삭제하라")가 거짓 조언이 되지 않기 위한
    유일한 판독. 루트 정합성이 어긋나면 skip이 아니라 fail이다(잘못된 루트가
    조용한 skip으로 위장하는 것을 차단).

    Args:
        root: 저장소 루트로 간주할 디렉토리.

    Returns:
        (mode, reason) 튜플. mode는 ``"strict" | "skip" | "fail"``이고,
        strict일 때 reason은 빈 문자열이다.
    """
    if not (root / "meta" / "pyproject.toml").is_file():
        return (
            "fail",
            f"repo root derivation is broken — {root} has no meta/pyproject.toml",
        )
    if not (root / MARKER).is_file():
        return ("skip", f"dual-forge contract not declared (no {MARKER}) — root={root}")
    missing = [
        name for name in (GITLAB_CI, GITHUB_WORKFLOW) if not (root / name).is_file()
    ]
    if missing:
        return (
            "fail",
            f"{MARKER} declares the dual-forge contract but CI file(s) are "
            f"missing: {', '.join(missing)}. {OPT_OUT_HINT}",
        )
    return ("strict", "")


def _string_items(value: object, label: str) -> tuple[list[str] | None, list[str]]:
    """script/run 값을 문자열 목록으로 강제하고, 불가능하면 위반으로 만든다."""
    if isinstance(value, str):
        return [value], []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value), []
    if value is None:
        return None, [f"{label} is missing — cannot read the contract"]
    return None, [
        f"{label} has an unreadable entry (tags like !reference and nested "
        "structures are unsupported) — write literal strings"
    ]


def extract_gitlab(doc: object) -> tuple[str | None, list[str] | None, list[str]]:
    """GitLab CI 문서에서 harness 잡의 이미지·명령을 뽑고 우회로를 검사한다.

    Args:
        doc: ``.gitlab-ci.yml``을 파싱한 결과.

    Returns:
        (image, commands, violations). 해당 값이 판독 불가면 None이고
        violations에 사유가 담긴다.
    """
    if not isinstance(doc, dict):
        return None, None, [f"{GITLAB_CI}: top level is not a mapping — cannot read the contract"]

    violations: list[str] = []
    # 우회로 가드 — 명령은 harness.script 밖에도 실릴 수 있다 (레거시 전역 문법 포함).
    for key in ("before_script", "after_script"):
        if key in doc:
            violations.append(
                f"{GITLAB_CI}: top-level {key} bypasses the command-list check — "
                "write commands directly in the harness job's script"
            )
    default = doc.get("default")
    if isinstance(default, dict):
        for key in ("before_script", "after_script", "image"):
            if key in default:
                violations.append(
                    f"{GITLAB_CI}: default.{key} is not tracked by this checker — "
                    "write it on the harness job directly"
                )
    if "include" in doc:
        violations.append(
            f"{GITLAB_CI}: include is not merged by this checker — "
            "write the harness job literally in this file"
        )

    harness = doc.get("harness")
    if not isinstance(harness, dict):
        violations.append(f"{GITLAB_CI}: no harness job — cannot read the contract")
        return None, None, violations

    if "extends" in harness:
        violations.append(
            f"{GITLAB_CI}: harness.extends is not merged by this checker — "
            "write the job literally"
        )
    for key in ("before_script", "after_script"):
        if key in harness:
            violations.append(
                f"{GITLAB_CI}: harness.{key} bypasses the command-list check — "
                "write commands directly in script"
            )

    image = harness.get("image")
    if isinstance(image, dict):
        image = image.get("name")
    if not isinstance(image, str):
        violations.append(
            f"{GITLAB_CI}: harness.image is unreadable "
            "(expected a string or a mapping with a name key)"
        )
        image = None

    commands, item_violations = _string_items(
        harness.get("script"), f"{GITLAB_CI}: harness.script"
    )
    return image, commands, violations + item_violations


def extract_github(doc: object) -> tuple[str | None, list[str] | None, list[str]]:
    """GitHub 워크플로 문서에서 harness 잡의 이미지·명령을 뽑고 우회로를 검사한다.

    PyYAML은 YAML 1.1 규칙으로 최상위 ``on:`` 키를 불리언 ``True``로 파싱한다.
    이 함수는 ``jobs``만 읽으므로 영향이 없지만, 트리거 검사로 범위를 넓힐
    때는 반드시 이 함정을 처리해야 한다.

    Args:
        doc: ``.github/workflows/harness.yml``을 파싱한 결과.

    Returns:
        (image, commands, violations). 해당 값이 판독 불가면 None이고
        violations에 사유가 담긴다.
    """
    if not isinstance(doc, dict):
        return None, None, [
            f"{GITHUB_WORKFLOW}: top level is not a mapping — cannot read the contract"
        ]

    jobs = doc.get("jobs")
    harness = jobs.get("harness") if isinstance(jobs, dict) else None
    if not isinstance(harness, dict):
        return None, None, [
            f"{GITHUB_WORKFLOW}: no jobs.harness job — cannot read the contract"
        ]

    violations: list[str] = []
    image = harness.get("container")
    if isinstance(image, dict):
        image = image.get("image")
    if not isinstance(image, str):
        violations.append(
            f"{GITHUB_WORKFLOW}: jobs.harness.container is unreadable "
            "(expected a string or a mapping with an image key)"
        )
        image = None

    steps = harness.get("steps")
    if not isinstance(steps, list):
        violations.append(
            f"{GITHUB_WORKFLOW}: jobs.harness.steps is missing — cannot read the contract"
        )
        return image, None, violations

    commands: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            violations.append(
                f"{GITHUB_WORKFLOW}: unreadable step ({step!r}) — write a literal mapping"
            )
            continue
        uses = step.get("uses")
        if uses is not None:
            # composite action은 임의 명령을 실행할 수 있어 명령 목록 검사를 우회한다.
            if not (isinstance(uses, str) and uses.startswith(_CHECKOUT_PREFIX)):
                violations.append(
                    f"{GITHUB_WORKFLOW}: disallowed uses step ({uses!r}) — "
                    f"uses other than {_CHECKOUT_PREFIX}* bypasses the "
                    "command-list check; write a run step instead"
                )
            continue
        run = step.get("run")
        if isinstance(run, str):
            commands.append(run)
        elif run is not None:
            violations.append(
                f"{GITHUB_WORKFLOW}: unreadable run value ({run!r}) — write a literal string"
            )
    return image, commands, violations


def normalize_commands(blocks: list[str]) -> list[str]:
    """run/script 블록들을 비교 가능한 명령 시퀀스로 정규화한다.

    멀티라인 블록은 개행으로 분할하고 각 행을 strip하며, 빈 행과 셸 주석
    행(``#`` 시작)은 버린다 — GitLab의 항목 3개와 GitHub의 ``run: |`` 블록
    1개가 같은 파이프라인으로 인정된다. 행 내부 공백은 건드리지 않는다.
    인용부호 안 공백 차이는 실제 의미 차이이기 때문이다.

    Args:
        blocks: script 항목 또는 run 값들의 목록.

    Returns:
        정규화된 명령 문자열 목록 (순서 보존).
    """
    commands: list[str] = []
    for block in blocks:
        for line in block.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                commands.append(line)
    return commands


def python_minor(version: str) -> str:
    """버전 문자열에서 major.minor만 취한다 (예: ``"3.14.2"`` → ``"3.14"``)."""
    return ".".join(version.strip().split(".")[:2])


def image_python_minor(image: str) -> str | None:
    """이미지 참조에서 파이썬 major.minor를 추출한다.

    ``python3.14`` / ``python:3.14`` 형식만 인식한다. 태그+다이제스트 병기
    (``uv:python3.14-trixie@sha256:…``)는 그대로 잡힌다.

    Args:
        image: 컨테이너 이미지 참조 문자열.

    Returns:
        major.minor 문자열, 판독 불가면 None (호출자가 fail-closed 처리).
    """
    match = _PYTHON_IN_TAG.search(image)
    return match.group(1) if match else None


def check_contract(
    gitlab_text: str, github_text: str, python_version: str
) -> list[str]:
    """두 CI 파일 본문에 네 불변식과 우회로 가드를 적용한다.

    불변식: (1) harness 잡 이미지 일치, (2) 이미지 파이썬 ↔
    ``meta/.python-version`` major.minor 일치, (3) 정규화된 명령 목록의 순서
    포함 일치, (4) 양쪽 각각 canonical 명령 목록(``CANONICAL_COMMANDS``)과
    문자 그대로 일치. (4)가 (3)을 함의하지만, (3)은 포지 간 비대칭 드리프트를
    더 읽기 좋은 메시지로 보여 유지한다.

    Args:
        gitlab_text: ``.gitlab-ci.yml`` 본문.
        github_text: ``.github/workflows/harness.yml`` 본문.
        python_version: ``meta/.python-version`` 본문.

    Returns:
        위반 메시지 목록. 비어 있으면 계약 준수. 위반이 하나라도 있으면
        마지막 원소는 위반이 아니라 옵트아웃 힌트(``OPT_OUT_HINT``)다.
    """
    gl_image, gl_raw, violations = extract_gitlab(
        yaml.load(gitlab_text, Loader=_CiLoader)
    )
    gh_image, gh_raw, gh_violations = extract_github(
        yaml.load(github_text, Loader=_CiLoader)
    )
    violations = violations + gh_violations

    if gl_image is not None and gh_image is not None and gl_image != gh_image:
        violations.append(
            f"job image mismatch — GitLab {gl_image!r} != GitHub {gh_image!r}"
        )

    expected = python_minor(python_version)
    for label, image in ((GITLAB_CI, gl_image), (GITHUB_WORKFLOW, gh_image)):
        if image is None:
            continue
        actual = image_python_minor(image)
        if actual is None:
            violations.append(
                f"{label}: cannot read a Python version from image {image!r} — "
                "keep python<major.minor> visible in the tag (for digest pins, "
                "keep the tag alongside: uv:python3.14-trixie@sha256:…)"
            )
        elif actual != expected:
            violations.append(
                f"{label}: image Python {actual} != {PYTHON_VERSION_FILE} {expected}"
            )

    if gl_raw is not None and gh_raw is not None:
        gl_commands = normalize_commands(gl_raw)
        gh_commands = normalize_commands(gh_raw)
        if gl_commands != gh_commands:
            violations.append(
                "command list mismatch —\n"
                f"  GitLab : {gl_commands}\n"
                f"  GitHub : {gh_commands}\n"
                "  (this checker does not track GitHub defaults.run and does not "
                "support > folded blocks — write commands explicitly per script "
                "entry / run step, using | or list form)"
            )
        for label, commands in ((GITLAB_CI, gl_commands), (GITHUB_WORKFLOW, gh_commands)):
            if commands != CANONICAL_COMMANDS:
                violations.append(
                    f"{label}: command list deviates from the canonical harness "
                    "commands —\n"
                    f"  expected: {CANONICAL_COMMANDS}\n"
                    f"  actual  : {commands}\n"
                    "  (the contract pins the inherited harness commands "
                    "literally — no extra, missing, reordered, or modified "
                    "lines; changing them requires updating CANONICAL_COMMANDS "
                    "in the same PR)"
                )
    # O7 — 위반이 있으면 반환 목록의 마지막 원소로 옵트아웃 탈출구를 싣는다.
    if violations:
        violations.append(OPT_OUT_HINT)
    return violations
