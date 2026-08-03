# ci_contract 로직의 픽스처 기반 단위 테스트 — 실저장소 파일은 절대 변조하지 않는다
"""contract.py의 단위 테스트.

모든 픽스처는 문자열 또는 tmp_path 안의 파일이다. 실저장소의 CI 파일·마커를
읽거나 변조하는 테스트는 여기 없다 (그건 test_live_repo.py의 몫).
"""

import pytest

from harness.ci_contract import contract

# 현재 저장소의 두 CI 파일과 같은 형태의 기준 픽스처.
GITLAB_OK = """\
workflow:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
harness:
  image: ghcr.io/astral-sh/uv:python3.14-trixie
  interruptible: true
  script:
    - uv sync --locked --directory meta
    - uv run --directory meta pytest
    - uv run --directory meta python -m harness.rules_checker
"""

GITHUB_OK = """\
name: harness
on:
  pull_request:
jobs:
  harness:
    runs-on: ubuntu-latest
    container: ghcr.io/astral-sh/uv:python3.14-trixie
    steps:
      - uses: actions/checkout@v7
      - run: uv sync --locked --directory meta
      - run: uv run --directory meta pytest
      - run: uv run --directory meta python -m harness.rules_checker
"""

PY_VERSION = "3.14\n"


def check(gitlab: str = GITLAB_OK, github: str = GITHUB_OK, py: str = PY_VERSION):
    return contract.check_contract(gitlab, github, py)


def joined(violations: list[str]) -> str:
    return "\n".join(violations)


# --- 행복 경로 ---


def test_current_repo_shape_passes():
    """현재 저장소와 같은 형태는 위반 0건이다."""
    assert check() == []


def test_mapping_image_forms_pass():
    """GitLab {name:}·GitHub {image:} 매핑 형태도 문자열과 동등하게 읽힌다."""
    gitlab = GITLAB_OK.replace(
        "image: ghcr.io/astral-sh/uv:python3.14-trixie",
        "image:\n    name: ghcr.io/astral-sh/uv:python3.14-trixie\n    entrypoint: ['']",
    )
    github = GITHUB_OK.replace(
        "container: ghcr.io/astral-sh/uv:python3.14-trixie",
        "container:\n      image: ghcr.io/astral-sh/uv:python3.14-trixie",
    )
    assert check(gitlab, github) == []


def test_multiline_run_block_normalizes():
    """GitHub의 run: | 블록 1개(주석·빈 줄 포함)와 GitLab 항목 3개는 동등하다."""
    github = """\
jobs:
  harness:
    container: ghcr.io/astral-sh/uv:python3.14-trixie
    steps:
      - uses: actions/checkout@v7
      - run: |
          # meta harness suite
          uv sync --locked --directory meta

          uv run --directory meta pytest
          uv run --directory meta python -m harness.rules_checker
"""
    assert check(github=github) == []


def test_patch_version_in_python_version_file_passes():
    """meta/.python-version에 패치 버전(3.14.2)이 있어도 major.minor로 비교한다."""
    assert check(py="3.14.2\n") == []


def test_product_job_with_unknown_tag_is_ignored():
    """harness 밖 product 잡의 !reference는 파싱을 죽이지 않고 무시된다."""
    gitlab = GITLAB_OK + """\
product:
  script: !reference [.base, script]
"""
    assert check(gitlab) == []


def test_uv_pinned_tag_extracts_python_not_uv_version():
    """uv 버전 고정 태그에서 uv 버전(0.11)이 아니라 파이썬 버전을 잡는다."""
    assert (
        contract.image_python_minor("ghcr.io/astral-sh/uv:0.11.26-python3.14-trixie")
        == "3.14"
    )


# --- 불변식 위반 ---


def test_image_tag_mismatch():
    gitlab = GITLAB_OK.replace("python3.14-trixie", "python3.13-bookworm-slim")
    violations = check(gitlab)
    assert any("job image mismatch" in v for v in violations)


def test_python_version_mismatch():
    """양쪽 이미지가 같아도 .python-version과 다르면 양쪽 모두 위반이다."""
    violations = check(py="3.13\n")
    assert sum("image Python 3.14 != meta/.python-version 3.13" in v for v in violations) == 2


def test_unreadable_python_version_fails_closed():
    gitlab = GITLAB_OK.replace(
        "ghcr.io/astral-sh/uv:python3.14-trixie", "ghcr.io/example/img:latest"
    )
    violations = check(gitlab)
    assert any("cannot read a Python version" in v for v in violations)


def test_command_flag_drift():
    """한쪽만 --locked가 빠지면 명령 목록 불일치다 (#85 유형의 드리프트)."""
    github = GITHUB_OK.replace("uv sync --locked --directory meta", "uv sync --directory meta")
    violations = check(github=github)
    assert any("command list mismatch" in v for v in violations)


def test_command_order_drift():
    gitlab = GITLAB_OK.replace(
        """\
    - uv run --directory meta pytest
    - uv run --directory meta python -m harness.rules_checker
""",
        """\
    - uv run --directory meta python -m harness.rules_checker
    - uv run --directory meta pytest
""",
    )
    violations = check(gitlab)
    assert any("command list mismatch" in v for v in violations)


def test_pytest_removed_from_both_sides():
    """양쪽에서 pytest가 동시에 사라지면 목록은 일치해도 불변식 (4)가 잡는다."""
    gitlab = GITLAB_OK.replace("    - uv run --directory meta pytest\n", "")
    github = GITHUB_OK.replace("      - run: uv run --directory meta pytest\n", "")
    violations = check(gitlab, github)
    assert sum("no pytest command" in v for v in violations) == 2


def test_pytest_word_boundary():
    """pytest_config 같은 파생 토큰은 pytest 실행으로 인정되지 않는다."""
    assert contract._PYTEST_WORD.search("cat pytest_config.ini") is None
    assert contract._PYTEST_WORD.search("uv run --directory meta pytest") is not None


# --- 우회로 차단 가드 ---


@pytest.mark.parametrize(
    "snippet",
    [
        "before_script:\n  - pip install foo\n",
        "after_script:\n  - echo done\n",
        "default:\n  before_script:\n    - pip install foo\n",
        "default:\n  image: other:latest\n",
        "include: product-ci.yml\n",
    ],
)
def test_gitlab_top_level_bypass_routes_fail(snippet):
    """최상위 레거시 전역 문법·default·include는 fail-closed다."""
    violations = check(snippet + GITLAB_OK)
    assert violations, snippet


def test_gitlab_job_level_bypass_routes_fail():
    gitlab = GITLAB_OK.replace(
        "  script:", "  before_script:\n    - pip install foo\n  script:"
    )
    violations = check(gitlab)
    assert any("harness.before_script bypasses" in v for v in violations)


def test_gitlab_extends_fails():
    gitlab = GITLAB_OK.replace("  script:", "  extends: .base\n  script:")
    violations = check(gitlab)
    assert any("harness.extends" in v for v in violations)


def test_non_checkout_uses_fails():
    github = GITHUB_OK.replace(
        "      - uses: actions/checkout@v7\n",
        "      - uses: actions/checkout@v7\n      - uses: astral-sh/setup-uv@v8\n",
    )
    violations = check(github=github)
    assert any("disallowed uses step" in v for v in violations)


def test_checkout_lookalike_uses_fails():
    """actions/checkout-anything 같은 유사 이름은 화이트리스트를 통과하지 못한다."""
    github = GITHUB_OK.replace(
        "uses: actions/checkout@v7", "uses: actions/checkout-evil@v1"
    )
    violations = check(github=github)
    assert any("disallowed uses step" in v for v in violations)


# --- 판독 불가 (H2 포함) ---


def test_unknown_tag_inside_harness_script_fails_explicitly():
    """harness.script의 !reference는 TypeError가 아니라 명시적 위반이다."""
    gitlab = """\
harness:
  image: ghcr.io/astral-sh/uv:python3.14-trixie
  script: !reference [.base, script]
"""
    violations = check(gitlab)
    assert any("unreadable entry" in v for v in violations)


def test_missing_harness_job_fails():
    violations = check("other_job:\n  script: [echo hi]\n")
    assert any("no harness job" in v for v in violations)


def test_missing_github_harness_job_fails():
    violations = check(github="jobs:\n  checks:\n    steps: []\n")
    assert any("no jobs.harness job" in v for v in violations)


# --- decide_mode (tmp_path — 실저장소 무접촉) ---


def make_repo(tmp_path, *, marker=True, gitlab=True, github=True, meta=True):
    """decide_mode 검증용 최소 저장소 형태를 tmp_path에 구성한다."""
    if meta:
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "pyproject.toml").write_text("[project]\n")
    if marker:
        (tmp_path / contract.MARKER).write_text("")
    if gitlab:
        (tmp_path / contract.GITLAB_CI).write_text(GITLAB_OK)
    if github:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "harness.yml").write_text(GITHUB_OK)
    return tmp_path


def test_decide_mode_strict(tmp_path):
    assert contract.decide_mode(make_repo(tmp_path)) == ("strict", "")


def test_decide_mode_broken_root_fails_not_skips(tmp_path):
    """루트 정합성 실패는 조용한 skip이 아니라 fail이다 (O2)."""
    mode, reason = contract.decide_mode(make_repo(tmp_path, meta=False))
    assert mode == "fail"
    assert str(tmp_path) in reason


def test_decide_mode_no_marker_skips_even_with_both_files(tmp_path):
    """마커 부재 = 계약 철회 — 파일이 다 있어도 전면 skip이다 (F1)."""
    mode, reason = contract.decide_mode(make_repo(tmp_path, marker=False))
    assert mode == "skip"
    assert str(tmp_path) in reason


def test_decide_mode_no_marker_skips_single_forge(tmp_path):
    mode, _ = contract.decide_mode(make_repo(tmp_path, marker=False, gitlab=False))
    assert mode == "skip"


def test_decide_mode_marker_with_missing_file_fails(tmp_path):
    """마커가 계약을 선언하는데 CI 파일이 없으면 시끄러운 실패다."""
    mode, reason = contract.decide_mode(make_repo(tmp_path, gitlab=False))
    assert mode == "fail"
    assert contract.GITLAB_CI in reason
    assert ".dual-forge-ci" in reason  # 탈출구 안내 (O7)
