# meta/ 산문이 테스트 이름으로 가리키는 대상이 실재하는지 검증하는 테스트
"""산문 테스트 이름 포인터 해석 테스트 (#101).

meta/의 주석·docstring·문서가 `test_...` 이름으로 가리키는 테스트가 실재하는지
본다. 테스트를 리네임해도 스위트는 초록이고 그 이름을 가리키던 산문만 썩기
때문이다. 설계와 근거(4라운드 리뷰, 유예, 재진입 판정)는 #101이 보유한다.

알려진 이름은 범위 안 .py의 최상위 `def test_*` 이름과 `test_*.py` 파일 스템이다.
#101 판정 스크립트는 확장자와 무관하게 스템을 세지만, 그런 비-.py 파일은 없다.

보지 않는 것: 글롭 계열·코퍼스 라벨 포인터, 두 패키지에 같은 이름으로 있는
파일·def(한쪽을 리네임해도 다른 쪽으로 해석된다), meta/ 밖의 문서(자식 프로젝트가
루트 문서를 제품 문서로 다시 쓰기 때문). 범위 안에서는 실재하지 않는 테스트 이름을
픽스처나 예시로 쓸 수 없다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from harness.rules_checker.check_rules import find_repo_root

_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.])test_[a-z0-9_]*[a-z0-9](?![A-Za-z0-9_])")
_DEF_RE = re.compile(r"^(?:async )?def (test_[a-z0-9_]+)", re.M)
_PRUNED_DIRS = {".venv", ".pytest_cache", "__pycache__", ".git"}

# 픽스처의 가짜 이름이 이 파일 자체에서 미해석 포인터가 되지 않도록 접두사를 조립한다.
_P = "test_"


def _in_scope(rel: str) -> bool:
    if rel.startswith("meta/harness/") and rel.endswith(".py"):
        return True
    return rel.startswith("meta/") and rel.endswith(".md")


def _collect(root: Path) -> dict[str, str]:
    blobs: dict[str, str] = {}
    for dirpath, dirs, files in os.walk(root / "meta"):
        dirs[:] = sorted(d for d in dirs if d not in _PRUNED_DIRS)
        for name in sorted(files):
            path = Path(dirpath, name)
            rel = path.relative_to(root).as_posix()
            if _in_scope(rel):
                blobs[rel] = path.read_text(encoding="utf-8")
    return blobs


def _unresolved(blobs: dict[str, str]) -> list[str]:
    py = {rel: text for rel, text in blobs.items() if rel.endswith(".py")}
    known = {name for text in py.values() for name in _DEF_RE.findall(text)}
    known |= {Path(rel).stem for rel in py if Path(rel).name.startswith(_P)}
    return [
        f"{rel}:{number} {match.group(0)}"
        for rel, text in blobs.items()
        for number, line in enumerate(text.splitlines(), 1)
        for match in _TOKEN_RE.finditer(line)
        if match.group(0) not in known
    ]


def test_meta_prose_pointers_name_real_tests() -> None:
    root = find_repo_root()
    blobs = _collect(root)
    # 루트를 잘못 잡으면 수집이 비어도 아래 단언이 초록이 된다(#101 라운드 2) — 자기 자신으로 묶는다.
    assert Path(__file__).resolve().relative_to(root).as_posix() in blobs
    assert _unresolved(blobs) == []


def test_resolver_reports_each_dangling_occurrence(tmp_path: Path) -> None:
    # 양성 대조: 수집·가지치기·추출·인덱스·매칭을 한 번에 관통한다.
    files = {
        f"meta/harness/x/tests/{_P}a.py": (
            f"def {_P}real():\n    pass\n# {_P}real, {_P}a, {_P}gone, {_P}gone\n"
        ),
        "meta/notes.md": f"{_P}md_gone, b_test, x.{_P}y\n",
        # 범위 규칙(meta/**.md)상 들어올 자리라 가지치기만이 막는다.
        "meta/.venv/README.md": f"{_P}venv_gone\n",
        "meta/.pytest_cache/README.md": f"{_P}cache_gone\n",
        "README.md": f"{_P}root_gone\n",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    assert _unresolved(_collect(tmp_path)) == [
        f"meta/notes.md:1 {_P}md_gone",
        f"meta/harness/x/tests/{_P}a.py:3 {_P}gone",
        f"meta/harness/x/tests/{_P}a.py:3 {_P}gone",
    ]
