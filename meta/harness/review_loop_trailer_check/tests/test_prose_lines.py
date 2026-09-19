# 산문 감지(prose_lines_added)를 실측 커밋에서 잘라 온 헝크와 합성 사례로 고정하는 테스트
"""산문 감지 픽스처.

실측 출처(`e8cd23e`, `f82cd72`, `eddade6`, `71d8847`,
`4081fb9`, `0e70c56` 등)는 atom 히스토리의 커밋이지만 여기서는 내용을 복사해 재구성하므로
자식 프로젝트에서도 SHA 없이 돈다. 통과해야 하는 쪽(`Prose: none`이 정당한 편집)이
주 표본이다 — 오탐이 이 훅의 신뢰를 깎는다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from harness.review_loop_trailer_check.check import (
    _is_noise,
    _parse_diff,
    _prose_lines_md,
    _prose_lines_py,
    _tokens,
    prose_lines_added,
)

_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=_GIT_ENV,
        check=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


def _commit_files(repo: Path, files: dict[str, str | None], subject: str = "x: step") -> str:
    """파일 내용을 쓰고(None이면 삭제) 커밋한 SHA를 돌려준다."""
    for name, content in files.items():
        path = repo / name
        if content is None:
            _git(repo, "rm", "-q", name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "--allow-empty", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def _judge(tmp_path: Path, before: dict[str, str], after: dict[str, str | None]) -> bool:
    repo = _repo(tmp_path)
    _commit_files(repo, before, "x: before")
    sha = _commit_files(repo, after, "x: after")
    return prose_lines_added(str(repo), sha)


# ── 통과해야 하는 쪽 ─────────────────────────────────────────────────────────

REFLOW_CASES = {
    # e8cd23e
    "e8cd23e": (
        "- A fix stays within every declared bound, each per its own wording;\n"
        "  a fix that cannot is remade, or the bound is relaxed with owner approval.\n"
        "  (Origin: #113.)\n",
        "- A fix stays within every declared bound, each per its own wording; a fix\n"
        "  that cannot is remade, or the bound is relaxed with owner approval. (Origin:\n"
        "  #113.)\n",
    ),
    # 417039a
    "417039a": (
        "   edit to the title or body, invalidates the observation (not the pass)\n"
        "   and requires a new pass. Below-bar findings\n"
        "   from this pass are filed or recorded in the ledger per Triage lanes,\n"
        "   then the loop ends.\n",
        "   edit to the title or body, invalidates the observation (not the pass) and\n"
        "   requires a new pass. Below-bar findings from this pass are filed or\n"
        "   recorded in the ledger per Triage lanes, then the loop ends.\n",
    ),
    # f5a8d0e
    "f5a8d0e": (
        "  and a sentence the new diff leaves untouched and makes false.\n"
        "  Whether a fix commit actually removed its target finding is always in\n"
        "  scope for the next pass. Defects unrelated to the new diff do not block\n"
        "  this PR and are never fixed in it: one worth fixing per the Triage lanes'\n"
        "  first question is filed as an issue immediately, even an above-bar-grade\n"
        "  one.\n",
        "  and a sentence the new diff leaves untouched and makes false. Whether a\n"
        "  fix commit actually removed its target finding is always in scope for the\n"
        "  next pass. Defects unrelated to the new diff do not block this PR and are\n"
        "  never fixed in it: one worth fixing per the Triage lanes' first question\n"
        "  is filed as an issue immediately, even an above-bar-grade one.\n",
    ),
    # f82cd72 — 문단 안 절 삭제 + 재래핑 + `rewritten` 뒤 마침표 재부착
    "f82cd72": (
        "  deleted, not rewritten — two attacks per fix at most. Text the agent writes\n"
        "  into the PR title or body in this loop passes the same attack before it is\n"
        "  posted. A PR title falsified on its second attack with no clause left\n"
        "  standing stops the loop before its next fix and returns to the owner.\n"
        "  (Origin: #125; #139.)\n",
        "  deleted, not rewritten. Text the agent writes into the PR title or body in\n"
        "  this loop passes the same attack before it is posted. A PR title falsified\n"
        "  on its second attack with no clause left standing stops the loop before its\n"
        "  next fix and returns to the owner. (Origin: #125; #139.)\n",
    ),
}


@pytest.mark.parametrize("name", sorted(REFLOW_CASES))
def test_reflow_and_clause_deletion_pass(tmp_path: Path, name: str) -> None:
    before, after = REFLOW_CASES[name]
    assert _judge(tmp_path, {"rule.md": before}, {"rule.md": after}) is False


def test_paragraph_moved_earlier_passes(tmp_path: Path) -> None:
    first = "First paragraph stays exactly as it was written before.\n"
    second = "Second paragraph moves above the first one in this commit.\n"
    assert _judge(
        tmp_path, {"a.md": first + "\n" + second}, {"a.md": second + "\n" + first}
    ) is False


def test_rename_only_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    text = "A sentence that survives the rename without any edit at all.\n"
    _commit_files(repo, {"old.md": text}, "x: before")
    _git(repo, "mv", "old.md", "new.md")
    _git(repo, "commit", "-q", "-m", "x: rename")
    assert prose_lines_added(str(repo), _git(repo, "rev-parse", "HEAD")) is False


@pytest.mark.parametrize(
    "before,after",
    [
        ("Call `old_name` before the check runs here.\n", "Call `new_name` before the check runs here.\n"),
        ("이 함수는 `old_name`을 불러서 결과를 적어 둔다.\n", "이 함수는 `new_name`을 불러서 결과를 적어 둔다.\n"),
        ("결과를 표(`_SPAWN_CELLS`)에 적어 둔다.\n", "결과를 표에 적어 둔다.\n"),
    ],
)
def test_backticked_identifier_rename_passes(tmp_path: Path, before: str, after: str) -> None:
    assert _judge(tmp_path, {"a.md": before}, {"a.md": after}) is False


EDDADE6_BEFORE = '''"""모듈.

    - 불변식: 표(`_SPAWN_CELLS`)에 든 호출 자리에서 그 실패가 사유를 낼 때, 사유는
      원격의 이름을 담지 않는다.
    - 불변식: 그렇게 호출된 자리는 모두 표에 rc 127 칸이 있고, 그 칸의 stdout에는 그 문구도
      그 이름도 없으며 stderr는 비어 있다.
"""
'''
EDDADE6_AFTER = '''"""모듈.

    - 불변식: 표에 든 호출 자리에서 그 실패가 사유를 낼 때, 사유는
      원격의 이름을 담지 않는다.
    - 불변식: 그렇게 호출된 자리는 모두 표에 rc 127 칸이 있고, 그 칸의 stdout에는 그 문구가
      없으며 stderr는 비어 있다.
"""
'''


def test_korean_clause_deletion_passes(tmp_path: Path) -> None:
    # eddade6: 절을 지우자 살아남은 낱말의 조사가 바뀐다(`문구도` → `문구가`).
    assert _judge(tmp_path, {"check.py": EDDADE6_BEFORE}, {"check.py": EDDADE6_AFTER}) is False


def test_code_only_change_passes(tmp_path: Path) -> None:
    before = "def f(x):\n    return x + 1\n"
    after = "def f(x):\n    y = x * 2\n    return y + 1\n"
    assert _judge(tmp_path, {"m.py": before}, {"m.py": after}) is False


def test_short_korean_tail_line_is_a_known_miss(tmp_path: Path) -> None:
    # 4자 미만 꼬리 줄은 잡음 규칙에 걸려 놓친다 — 알려진 한계로 고정.
    before = 'def f():\n    """긴 설명이 여기까지 이어지다가 줄이 바뀔\n    """\n'
    after = 'def f():\n    """긴 설명이 여기까지 이어지다가 줄이 바뀔\n    수 있다.\n    """\n'
    assert _judge(tmp_path, {"m.py": before}, {"m.py": after}) is False


def test_files_other_than_py_and_md_are_ignored(tmp_path: Path) -> None:
    assert _judge(tmp_path, {"notes.txt": "Old.\n"}, {"notes.txt": "A brand new sentence lands here.\n"}) is False


@pytest.mark.parametrize(
    "before,after",
    [
        ("TIMEOUT = 10  # seconds to wait for git\n", "TIMEOUT = 30  # seconds to wait for git\n"),
        (
            "try:\n    pass\nexcept Exception:  # noqa: BLE001\n    pass\n",
            "try:\n    pass\nexcept (OSError, ValueError):  # noqa: BLE001\n    pass\n",
        ),
    ],
)
def test_code_change_on_a_commented_line_passes(tmp_path: Path, before: str, after: str) -> None:
    assert _judge(tmp_path, {"m.py": before}, {"m.py": after}) is False


def test_pseudo_header_lines_in_a_fence_do_not_break_the_parser(tmp_path: Path) -> None:
    fence = "```\n++ b/x\ndiff --git a/x b/x\n@@ -1 +1 @@\n```\n"
    assert _judge(tmp_path, {"a.md": "Intro line kept.\n"}, {"a.md": "Intro line kept.\n" + fence}) is False


def test_verbatim_relocation_across_files_passes(tmp_path: Path) -> None:
    block = (
        "    불변식: 그렇게 호출된 자리는 모두 표에 rc 127 칸이 있다.\n"
        "    실패 방향: 그 이름 없이 git을 부르는 자리는 검사되지 않는다.\n"
    )
    moved = (
        "불변식: 그렇게 호출된 자리는 모두 표에 rc 127 칸이 있다.\n"
        "실패 방향: 그 이름 없이 git을 부르는 자리는 검사되지 않는다.\n"
    )
    before = {"check.py": '"""모듈.\n\n' + block + '"""\n', "check_test.py": '"""테스트.\n"""\n'}
    after = {"check.py": '"""모듈.\n"""\n', "check_test.py": '"""테스트.\n\n' + moved + '"""\n'}
    assert _judge(tmp_path, before, after) is False


# ── 걸려야 하는 쪽 ───────────────────────────────────────────────────────────


def test_new_sentence_next_to_a_deleted_one_is_flagged(tmp_path: Path) -> None:
    # 5146ccf
    before = (
        "  deleted, not rewritten — two attacks per commit at most. (Origin: #125;\n"
        "  #139.)\n"
    )
    after = (
        "  deleted, not rewritten — two attacks per commit at most. Text the agent\n"
        "  writes into the PR title or body in this loop passes the same attack\n"
        "  before it is posted. (Origin: #125; #139.)\n"
    )
    assert _judge(tmp_path, {"rule.md": before}, {"rule.md": after}) is True


def test_one_new_sentence_is_flagged(tmp_path: Path) -> None:
    # d0d301f
    before = "links to filed issues.\n"
    after = "links to filed issues. An edit the agent makes to the PR title or body is\nrecorded like a commit.\n"
    assert _judge(tmp_path, {"rule.md": before}, {"rule.md": after}) is True


def test_non_ascii_path_is_still_attributed(tmp_path: Path) -> None:
    assert _judge(tmp_path, {"한글.md": "첫 문장은 그대로 남아 있다.\n"}, {"한글.md": "첫 문장은 그대로 남아 있다.\n새로 쓴 두 번째 문장이 여기 붙는다.\n"}) is True


def test_path_with_a_space_is_still_attributed(tmp_path: Path) -> None:
    assert _judge(tmp_path, {"my notes.md": "Old.\n"}, {"my notes.md": "Old.\nA brand new sentence lands here.\n"}) is True


def test_a_large_deletion_elsewhere_does_not_mask_a_new_sentence(tmp_path: Path) -> None:
    big = "\n".join(f"The agent records finding {i} in the ledger before the next pass ends." for i in range(60)) + "\n"
    before = {"big.md": big, "a.md": "Keep.\n"}
    after = {"big.md": None, "a.md": "Keep.\nBefore the next pass ends the ledger records every finding the agent found.\n"}
    assert _judge(tmp_path, before, after) is True


def test_diff_prefix_config_does_not_hide_a_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "config", "diff.dstPrefix", "X/")
    _git(repo, "config", "diff.srcPrefix", "Y/")
    _commit_files(repo, {"a.md": "Old.\n"}, "x: before")
    sha = _commit_files(repo, {"a.md": "Old.\nA brand new sentence lands here.\n"}, "x: after")
    assert prose_lines_added(str(repo), sha) is True


def test_root_commit_is_inspected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    sha = _commit_files(repo, {"a.md": "A brand new sentence in the very first commit.\n"}, "x: root")
    assert prose_lines_added(str(repo), sha) is True


def test_docstring_authoring_is_flagged(tmp_path: Path) -> None:
    # 4081fb9
    before = '''"""모듈.

    - `subprocess.run`이 내는 `OSError`를 전부 "git을 실행하지 못했다"로 읽는다.
"""
'''
    after = '''"""모듈.

    - `subprocess.run`이 내는 `OSError`를 하위 타입으로 가르지 않고 전부 "git을 실행하지
      못했다"로 읽는다 — 선언된 경계. 불변식: 표에 든 호출 자리에서 그 실패가 사유를 낼 때,
      사유는 원격의 이름을 담지 않는다. 인용: 오너 결정 2026-09-05, PR #154.
"""
'''
    assert _judge(tmp_path, {"check.py": before}, {"check.py": after}) is True


def test_comment_rewrite_is_flagged(tmp_path: Path) -> None:
    # 71d8847
    before = (
        "def f():\n"
        "    # remote 가 관련된 사유는 그 이름을 사유 안에 담는다 — 이 줄은 판정을 싣지 않지만,\n"
        "    # 사유(얕은 클론 등)는 로컬 저장소에 대한 것이라 담을 이름이 없다.\n"
        "    return 0\n"
    )
    after = (
        "def f():\n"
        "    # 원격에 대한 사유는 그 이름을 사유 안에 담는다 — 이 줄은 판정을 싣지 않지만,\n"
        "    # 사유는 로컬에 대한 것이다 — 원격 대면 자리에서 난 spawn 실패도 그렇다(#153).\n"
        "    return 0\n"
    )
    assert _judge(tmp_path, {"check.py": before}, {"check.py": after}) is True


def test_relocated_block_with_two_reworded_sentences_is_flagged(tmp_path: Path) -> None:
    # 71d8847: docstring 블록을 테스트 파일로 옮기며 4문장 중 2문장을 고쳐 썼다.
    block = (
        "    - 위 불변식을 지키는 테스트는 소스에 `run_git`이라는 이름이 나타나는 호출 자리만 세고,\n"
        "      원격 탓은 이 모듈의 원격 문구 셋과 등록된 remote 이름으로만 알아본다.\n"
        "      불변식: 그렇게 호출된 자리는 모두 표에 rc 127 칸이 있다.\n"
        "      실패 방향: 그 이름 없이 git을 부르는 자리는 검사되지 않는다.\n"
    )
    moved = (
        "spawn 실패 검사는 소스에 `run_git`이라는 이름이 나타나는 호출 자리만 세고,\n"
        "원격 탓은 `check.py`의 원격 사유 문구 셋과 등록된 remote 이름으로만 알아본다.\n"
        "불변식: 그렇게 호출된 자리는 모두 표에 rc 127 칸이 있다.\n"
        "실패 방향: 그 이름 없이 git을 부르는 자리는 검사되지 않는다.\n"
    )
    before = {"check.py": '"""모듈.\n\n' + block + '"""\n', "check_test.py": '"""테스트.\n"""\n'}
    after = {"check.py": '"""모듈.\n"""\n', "check_test.py": '"""테스트.\n\n' + moved + '"""\n'}
    assert _judge(tmp_path, before, after) is True


def test_test_comment_sentences_are_flagged(tmp_path: Path) -> None:
    # 0e70c56
    before = 'def case_x():\n    assert "refs/remotes/origin/main" in left  # --not --remotes는 여전히 실동작한다\n'
    after = (
        "def case_x():\n"
        '    # 원격 ref 집합이 비지 않았다 — 아래 블록이 "제외할 ref가 하나도 없어서"\n'
        "    # 생긴 퇴화가 아님을 배제한다.\n"
        '    assert "refs/remotes/origin/main" in left\n'
    )
    assert _judge(tmp_path, {"b_test.py": before}, {"b_test.py": after}) is True


def test_comment_change_on_a_code_line_is_flagged(tmp_path: Path) -> None:
    before = "TIMEOUT = 10  # seconds to wait for git\n"
    after = "TIMEOUT = 10  # seconds before the hook gives up entirely\n"
    assert _judge(tmp_path, {"m.py": before}, {"m.py": after}) is True


def test_korean_tail_line_of_four_letters_is_flagged(tmp_path: Path) -> None:
    before = 'def f():\n    """긴 설명이 여기까지 이어지다가 줄이 바뀌면 그 다음은\n    """\n'
    after = 'def f():\n    """긴 설명이 여기까지 이어지다가 줄이 바뀌면 그 다음은\n    한 번이다.\n    """\n'
    assert _judge(tmp_path, {"m.py": before}, {"m.py": after}) is True


def test_recombining_deleted_words_into_a_new_paragraph_is_flagged(tmp_path: Path) -> None:
    deleted = "the hook reports every commit it checks once\n"
    recombined = "every commit reports once the hook it checks\n"
    assert _judge(tmp_path, {"a.md": deleted, "b.md": "Keep.\n"}, {"a.md": "", "b.md": "Keep.\n" + recombined}) is True


def test_new_sentence_after_pseudo_header_lines_is_still_attributed(tmp_path: Path) -> None:
    # 헤더처럼 생긴 내용 줄 뒤의 새 문장 — 순진한 파서는 경로를 잃고 놓친다.
    fence = "```\n++ b/x\ndiff --git a/x b/x\n@@ -1 +1 @@\n```\n"
    before = "Intro line kept.\n\nTail line kept as well.\n"
    after = "Intro line kept.\n" + fence + "\nTail line kept as well.\nA new sentence lands after the fence.\n"
    assert _judge(tmp_path, {"a.md": before}, {"a.md": after}) is True


# ── 단위 ─────────────────────────────────────────────────────────────────────


def test_parse_diff_counts_context_lines_inside_a_hunk() -> None:
    diff = (
        "diff --git a/a.md b/a.md\n--- a/a.md\n+++ b/a.md\n"
        "@@ -1,3 +1,4 @@\n-old\n+new\n kept\n kept too\n+added\n"
    )
    added, removed = _parse_diff(diff)
    assert added == {"a.md": [(1, "new"), (4, "added")]}
    assert removed == ["old"]


def test_tokens_drop_backtick_spans_and_punctuation() -> None:
    # 백틱 구간은 통째로 빠지고 그 뒤의 조사는 남는다 — 개명 전후로 같은 토큰이다.
    assert _tokens("표(`_SPAWN_CELLS`)에 든 `run_git`을, 그리고 rewritten.") == [
        "표에", "든", "을", "그리고", "rewritten"
    ]


def test_markdown_prose_lines_skip_frontmatter_fences_and_table_rules() -> None:
    text = "---\nid: x\n---\n# Title\n\n```\ncode\n```\n| a | b |\n|---|---|\n| c | d |\n"
    assert _prose_lines_md(text) == {4, 9, 11}


def test_python_prose_lines_survive_a_syntax_error_for_comments() -> None:
    text = "def f(:\n    # a comment survives tokenize\n    x = (\n"
    assert 2 in _prose_lines_py(text)


@pytest.mark.parametrize(
    "line",
    ["# noqa: E501", "# noqa: BLE001, E501", "    Args:", '    """', "## 검증", "# type: ignore[attr]", "# pragma: no cover", "# fmt: off"],
)
def test_noise_lines(line: str) -> None:
    assert _is_noise(line) is True


def test_prose_after_a_directive_is_not_noise() -> None:
    assert _is_noise("# noqa: BLE001 — fail-open이 설계 요구사항") is False


def test_new_justification_after_noqa_is_flagged(tmp_path: Path) -> None:
    before = "try:\n    pass\nexcept OSError:\n    pass\n"
    after = "try:\n    pass\nexcept Exception:  # noqa: BLE001 — a brand new justification sentence nobody attacked\n    pass\n"
    assert _judge(tmp_path, {"m.py": before}, {"m.py": after}) is True


@pytest.mark.parametrize("line", ["    한 번이다.", "# 실패는 전부 통과 방향이다.", "A line."])
def test_prose_lines_are_not_noise(line: str) -> None:
    assert _is_noise(line) is False
