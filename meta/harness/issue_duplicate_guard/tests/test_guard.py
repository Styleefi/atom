# issue_duplicate_guard의 감지·판정·fail-open 경로를 검증하는 테스트
"""guard 모듈 테스트.

외부 CLI(gh/glab)는 전부 mock: 감지 로직은 detect_invocations를 직접,
판정 흐름은 stdin JSON + _run_search monkeypatch로 검증한다.
설계 불변식 — 오차단 금지(문자열 내부 언급), 실패는 전부 통과 방향 —
을 케이스로 고정한다.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys

from harness.issue_duplicate_guard import guard


def _run_main(monkeypatch, payload) -> int:
    """payload(dict 또는 원시 문자열)를 stdin으로 넣고 main()을 실행한다."""
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    return guard.main()


def _bash_payload(command: str, cwd: str | None = None) -> dict:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


# ---------- 감지: 오탐 방지 ----------

def test_quoted_mention_with_operator_is_not_detected() -> None:
    # 따옴표 안 연산자 포함 — 이번 작업 커밋 메시지 같은 형태가 절대 걸리면 안 됨
    assert guard.detect_invocations('git commit -m "fix && gh issue create guard"') == []


def test_argument_position_literal_is_not_detected() -> None:
    assert guard.detect_invocations("echo gh issue create") == []


def test_bash_dash_c_inner_command_is_not_detected() -> None:
    # 문서화된 한계: 놓치는(통과) 방향이므로 안전
    assert guard.detect_invocations("bash -c 'gh issue create -t x'") == []


def test_heredoc_body_mention_is_not_detected() -> None:
    # 본문 라인은 명령이 아니다 — 무고한 파일 쓰기가 차단되면 안 됨 (#32 괴리 1)
    cmd = 'cat > notes.sh <<EOF\ngh issue create --title "T"\nEOF'
    assert guard.detect_invocations(cmd) == []


def test_heredoc_file_write_passes_without_search(monkeypatch) -> None:
    def _fail(argv, cwd=None):
        raise AssertionError("search must not run for a heredoc body mention")

    monkeypatch.setattr(guard, "_run_search", _fail)
    cmd = 'cat > notes.sh <<EOF\ngh issue create --title "T"\nEOF'
    assert _run_main(monkeypatch, _bash_payload(cmd)) == 0


def test_arithmetic_shift_with_real_heredoc_still_strips() -> None:
    # $((...)) 마스킹 검증: 산술 <<가 마커 큐를 오염시켜 스트립을 무효화하면 안 됨
    cmd = 'echo $(( (1<<2) + 3 ))\ncat > x.sh <<EOF\ngh issue create --title "T"\nEOF'
    assert guard.detect_invocations(cmd) == []


def test_bare_arithmetic_with_real_heredoc_still_strips() -> None:
    # bash의 산술 명령 (( ... ))는 $ 없이도 산술이다 — 마스킹 대상 (라운드 1 C3)
    cmd = '(( 1<<2 ))\ncat > x.sh <<EOF\ngh issue create --title "T"\nEOF'
    assert guard.detect_invocations(cmd) == []


def test_exotic_delimiter_heredoc_body_not_detected() -> None:
    # bash는 +@! 등이 든 구분자도 허용한다 — 문자군이 좁으면 종결자를 못 찾아
    # all-or-nothing이 원문을 유지하고 본문 오차단이 되살아난다 (라운드 1 C1)
    cmd = 'cat > x.sh <<MY+DELIM\ngh issue create --title "T"\nMY+DELIM'
    assert guard.detect_invocations(cmd) == []


def test_quoted_paren_prose_does_not_hide_real_heredoc() -> None:
    # 라운드 2 divergence 재현: 따옴표 산문 속 (( )) 조각을 가로지른 마스킹이
    # 실제 마커를 가리면 본문이 명령으로 파싱된다 (C1 재발 방지)
    cmd = 'echo "shift is ((" ; cat > n.md <<EOF ; echo "))"\ngh issue create --title "X"\nEOF'
    assert guard.detect_invocations(cmd) == []


def test_quoted_dollar_arith_prose_is_real_arithmetic() -> None:
    # 라운드 2에서 산문 조각으로 오판했던 형태의 기대값 정정 — bash 실측:
    # 큰따옴표 안 $((는 진짜 산술 시작이라 ))까지 삼키고(런타임 산술 오류가
    # 나도) 다음 줄은 본문이 아니라 실행되는 명령이다(create 실행 확인).
    # 스팬이 마스킹되고 heredoc은 등록되지 않으며 create가 감지돼야 한다.
    cmd = 'echo "cost $((" ; cat > n.md <<EOF ; echo "))"\ngh issue create --title "X"\nEOF'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "X"


def test_parameter_expansion_paren_injection_does_not_hide_marker() -> None:
    # ${var:-((}는 따옴표·백슬래시 없이 괄호 리터럴을 주입한다 (제미나이 F1)
    cmd = 'echo ${var:-((} ; cat > t.md <<EOF ; echo ${var:-))}\ngh issue create --title "X"\nEOF'
    assert guard.detect_invocations(cmd) == []


def test_real_heredoc_inside_command_substitution_in_arith() -> None:
    # 산술 → 명령 치환 → 진짜 heredoc (Amendment A): 마커가 마스킹으로
    # 지워지면 본문이 복구 불능 형태로 오차단된다
    cmd = 'echo $(( $(cat <<XX) + 1 ))\ngh issue create --title "X"\nXX'
    assert guard.detect_invocations(cmd) == []


def test_protected_flags_marks_escaped_operator() -> None:
    # 이스케이프된 연산자는 진짜 구분자가 아니다 — 표식이 붙어야 경계 판정에서 빠진다
    text = 'echo \\; gh issue create'
    flags = guard._protected_flags(text, len(guard._tokenize(text)))
    assert flags is not None
    assert flags[1] is True          # `;` 자리
    assert flags[0] is False         # `echo`


def test_protected_flags_gives_up_when_input_holds_sentinel() -> None:
    # 센티널이 입력에 있으면 정규화 결과를 신뢰할 수 없다 — 판별 포기
    text = f"echo {guard._ESCAPE_SENTINEL} gh issue create"
    assert guard._protected_flags(text, len(guard._tokenize(text))) is None


def test_protected_flags_ignores_apostrophe_inside_double_quotes() -> None:
    # bash는 큰따옴표 안 작은따옴표를 리터럴로 둔다 — 인용 상태가 뒤집히면
    # 뒤따르는 이스케이프가 접히지 않아 정렬이 깨지고 오차단이 되살아난다
    text = 'echo "it\'s" a\\ b && gh issue create'
    flags = guard._protected_flags(text, len(guard._tokenize(text)))
    assert flags is not None         # 상태가 뒤집혔다면 정렬 실패로 None이 된다
    assert flags[-1] is False        # 진짜 && 는 보호 대상이 아니다


def test_strip_heredocs_reports_completion() -> None:
    # 완결 플래그는 줄 연속 결합을 켜는 스위치다 — 코퍼스는 최종 감지 결과만
    # 보므로 플래그 의미가 뒤집혀도 우연히 같은 결과가 나오면 안 잡힌다
    text, complete = guard._strip_heredocs('cat <<EOF\nbody\nEOF')
    assert complete is True
    assert "body" not in text


def test_strip_heredocs_reports_rollback_on_unterminated() -> None:
    # 미종결 heredoc은 all-or-nothing 롤백 — 원문 그대로, 완결 아님
    cmd = 'cat <<EOF\nbody'
    text, complete = guard._strip_heredocs(cmd)
    assert complete is False
    assert text == cmd


# ---------- 감지: 잡아야 하는 형태 ----------

def test_heredoc_fed_create_is_still_detected() -> None:
    # 마커 라인 자체는 보존된다 — heredoc으로 body를 먹이는 실제 생성은 감지
    cmd = 'gh issue create -t "T" --body-file - <<EOF\nbody text\nEOF'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_arithmetic_shift_does_not_break_detection() -> None:
    # 산술 시프트가 heredoc 마커로 오인돼 뒤 명령 감지를 지우면 안 됨
    cmd = 'echo $((1<<2))\ngh issue create -t "T"'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_bare_arithmetic_does_not_erase_real_create() -> None:
    # (( x << y ))의 시프트가 마커로 오인되면 실감지가 통째로 지워진다 (라운드 1 C3)
    cmd = '(( total << shift ))\ngh issue create --title "T"\nshift'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_double_nested_arithmetic_does_not_expose_shift() -> None:
    # $(( ((a)) << 2 ))의 안쪽 ))에서 마스킹이 조기 종료되면 노출된 << 2가
    # 우연한 종결자 줄로 닫혀 실감지를 지운다 (제미나이 F3)
    cmd = 'echo $(( ((a)) << 2 ))\ngh issue create --title "T"\n2'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_nested_arith_expansion_stays_masked() -> None:
    # 산술 내부의 인접 $((는 명령 치환이 아니라 중첩 산술 (Amendment A-1)
    cmd = 'echo $(( $((1<<2)) + 1 ))\ngh issue create --title "T"\n2'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_quoted_value_arithmetic_stays_masked() -> None:
    # 진짜 산술에는 따옴표가 올 수 있다 — (( x = "1" << 2 ))는 유효 bash
    cmd = '(( x = "1" << 2 ))\ngh issue create --title "T"\n2'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_keyword_context_arithmetic_stays_masked() -> None:
    # if (( x << y ))처럼 키워드 뒤 산술도 마스킹돼야 한다 — 명령 위치
    # 앵커 방식이었다면 놓쳤을 형태의 잠금
    cmd = 'if (( x << y )); then\ngh issue create --title "T"\ny\nfi'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_escaped_backtick_does_not_flip_quote_state() -> None:
    # bash는 백틱 안에서 백슬래시+백틱으로 백틱을 이스케이프한다(중첩 치환의
    # 고전 문법) — 상태가 역전되면 이후 산술이 면제 구역으로 새어 실감지가
    # 삼켜진다 (라운드 3 H1, C3)
    cmd = 'echo `a\\`b` $((1<<2))\ngh issue create --title "T"\n2'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_arith_inside_cmdsub_inside_arith_stays_masked() -> None:
    # 3중 중첩(산술→명령치환→산술)의 안쪽 산술은 어떤 깊이에서든 산술이다 —
    # 면제 판정은 최근접 비-plain 프레임 기준 (라운드 3 H2)
    cmd = 'echo $(( $(echo $((1<<2))) + 1 ))\ngh issue create --title "T"\n2'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_close_paren_inside_brace_expansion_does_not_pop() -> None:
    # ${...} 내부의 닫는 괄호는 brace 분기가 소비해 스택 pop이 없다 —
    # 스팬 조기 종료로 시프트가 노출되면 안 된다 (제미나이 3차 F2 방어 잠금)
    cmd = 'echo $(( ${var:-)} + 1 << 2 ))\ngh issue create --title "T"\n2'
    invs = guard.detect_invocations(cmd)
    assert len(invs) == 1 and invs[0].title == "T"


def test_operator_without_spaces_is_detected() -> None:
    invs = guard.detect_invocations('cd x&&gh issue create -t "T"')
    assert len(invs) == 1 and invs[0].title == "T"
    assert invs[0].cwd_unsafe is True


def test_multiline_command_is_detected() -> None:
    invs = guard.detect_invocations('git add .\ngh issue create --title "T"')
    assert len(invs) == 1 and invs[0].title == "T"


def test_title_flag_forms() -> None:
    for cmd in (
        'gh issue create --title "X"',
        "gh issue create --title=X",
        "gh issue create -t X",
    ):
        invs = guard.detect_invocations(cmd)
        assert len(invs) == 1 and invs[0].title == "X", cmd


def test_attached_title_flag_with_equals_is_parsed() -> None:
    # pflag는 -t=X에서 =를 벗긴다 — 그대로 두면 검색이 --search "=X"로 돌아
    # 항상 무결과가 되어 중복 검사가 조용히 통과한다 (#67)
    invs = guard.detect_invocations("gh issue create -t=X")
    assert len(invs) == 1 and invs[0].title == "X"


def test_attached_title_flag_with_empty_value_has_no_title() -> None:
    # -t= 는 제목이 비어 검색할 것이 없다 — 제목 없는 create로 눕혀 차단시킨다
    invs = guard.detect_invocations("gh issue create -t=")
    assert len(invs) == 1 and invs[0].title is None


def test_glab_and_path_prefixed_cli_are_detected() -> None:
    assert guard.detect_invocations("glab issue create -t x")[0].cli == "glab"
    assert guard.detect_invocations("/usr/bin/gh issue create -t x")[0].cli == "gh"


def test_repo_flag_is_parsed() -> None:
    invs = guard.detect_invocations("gh issue create -t x -R owner/repo")
    assert invs[0].repo == "owner/repo"


def test_attached_repo_flag_is_parsed() -> None:
    # -t 결합형(-tX)과 대칭 — 놓치면 cd 래치·검색 대상 판정이 둘 다 틀어진다
    invs = guard.detect_invocations("gh issue create -t x -Rowner/repo")
    assert invs[0].repo == "owner/repo"


def test_attached_repo_flag_with_equals_is_parsed() -> None:
    # pflag는 -R=owner/repo에서 =를 벗긴다 — 그대로 두면 검색 --repo가
    # "=owner/repo"로 실패해 중복 검사가 조용히 스킵된다 (리뷰 라운드 1 C3)
    invs = guard.detect_invocations("gh issue create -t x -R=owner/repo")
    assert invs[0].repo == "owner/repo"


def test_cd_on_earlier_line_marks_cwd_unsafe() -> None:
    invs = guard.detect_invocations('cd /elsewhere\ngh issue create -t "T"')
    assert len(invs) == 1 and invs[0].cwd_unsafe is True


def test_cd_after_invocation_does_not_mark() -> None:
    invs = guard.detect_invocations('gh issue create -t "T" && cd /elsewhere')
    assert len(invs) == 1 and invs[0].cwd_unsafe is False


def test_override_prefix_alone_and_in_compound() -> None:
    assert guard.detect_invocations(f"{guard.OVERRIDE_TOKEN} gh issue create -t x")[0].override
    invs = guard.detect_invocations(f"cd y && {guard.OVERRIDE_TOKEN} gh issue create -t x")
    assert invs[0].override


def test_unparseable_command_fails_open() -> None:
    # shlex 이중 실패(따옴표 불균형)는 전면 fail-open — 규칙 파일의 계약
    # "unparseable command fails open"을 코드가 그대로 이행한다 (#32 괴리 2)
    assert guard.detect_invocations('gh issue create --title "T" --body "unclosed') == []


def test_quoted_mention_in_unparseable_command_is_not_detected() -> None:
    # 구 폴백의 오차단 재현 케이스: 따옴표 속 언급이 절대 걸리면 안 됨
    assert guard.detect_invocations('echo "todo; gh issue create for tracker later') == []


def test_comment_with_unbalanced_quote_fails_open(monkeypatch) -> None:
    # 수용 한계 잠금: 주석 뒤 불균형 따옴표는 유효 bash지만 shlex가 이중
    # 실패한다(commenters=""). 감지가 아니라 통과가 의도된 행동이다.
    def _fail(argv, cwd=None):
        raise AssertionError("no search may run for an undetected command")

    monkeypatch.setattr(guard, "_run_search", _fail)
    cmd = 'gh issue create -t "T" # comment with " unclosed quote'
    assert _run_main(monkeypatch, _bash_payload(cmd)) == 0


# ---------- bash 오라클 코퍼스 (리뷰 루프 프로브 승격) ----------
# 각 항목의 기대값은 작성 시점에 실제 bash로 "create 줄이 실행되는가"를
# 실측해 결정했다(주석에 근거 병기). 회고 교훈: 임시 프로브에서 검증된
# 성질은 메커니즘 교체 때 조용히 뒤집힌다 — 전부 여기로 승격해 게이트로
# 만든다. 형식: (설명, 명령, 기대 감지 제목 튜플).

_ORACLE_CORPUS = [
    ("톱레벨 cmdsub 안 bare 산술 명령 — bash: create 실행됨",
     'echo $( echo foo ; (( x = 1 << 2 )) )\ngh issue create --title "T"\n2',
     ("T",)),
    ("cmdsub 안 인용된 닫는 괄호는 프레임을 닫지 않음 — bash: create 실행됨",
     'echo $(( $(echo ")") + 1 << 2 ))\ngh issue create --title "T"\n2',
     ("T",)),
    ("이스케이프된 닫는 중괄호는 확장을 닫지 않음 — bash: create 실행됨",
     'echo ${var:-\\}} ; (( 1<<2 ))\ngh issue create --title "T"\n2',
     ("T",)),
    ("brace 확장 안 unquoted 닫는 괄호는 리터럴 — bash: 출력 ')', create 실행됨",
     'echo ${x:- ) } $(( 1 << 2 ))\ngh issue create --title "T"\n2',
     ("T",)),
    # --- 이하 수정 라운드 4 red 승격분 (bash 오라클 실측 병기) ---
    ("K2: 큰따옴표 안 산술 확장은 활성 — bash: create 실행됨 (관용 입력)",
     'echo "count: $((1<<2))"\ngh issue create --title "T"\n2',
     ("T",)),
    ("K1b: brace 안 cmdsub의 진짜 heredoc — bash: create 미실행 (본문)",
     'echo $(( ${x:-$(cat <<EOF)} + 1 ))\ngh issue create --title "T"\nEOF',
     ()),
    ("K1a: 4중 중첩형 동형 — bash: create 미실행 (본문)",
     'echo $(( $(echo $(( ${x:-$(cat <<EOF)} + 1 ))) + 1 ))\ngh issue create --title "T"\nEOF',
     ()),
    ("K3: cmdsub 직속 bare 산술 명령 — bash: create 실행됨",
     'echo $(( $( ((y=1<<2)); echo $y ) ))\ngh issue create --title "T"\n2',
     ("T",)),
    ("F3in: plain 개재 cmdsub 안 bare 산술 — bash: create 실행됨",
     'echo $(( $( ( ((y=1<<2)) ) ) ))\ngh issue create --title "T"\n2',
     ("T",)),
    ("F4q: 큰따옴표 안 cmdsub의 명령 문맥 리셋 — bash: create 실행됨",
     'echo "$( (( x = 1 << 2 )); echo $x )"\ngh issue create --title "T"\n2',
     ("T",)),
    ("F4b: brace 안 cmdsub의 명령 문맥 리셋 — bash: create 실행됨",
     'echo ${var:-$( ((1<<2)) )}\ngh issue create --title "T"\n2',
     ("T",)),
    ("brace 안 산술 확장은 활성 — bash: create 실행됨",
     'echo ${x:-$((1<<2))}\ngh issue create --title "T"\n2',
     ("T",)),
    ("P1: 백틱 안 중첩 산술 마스킹 (uniform cmdsub) — bash: 출력 5, create 실행됨",
     'echo $(( `echo $((1<<2))` + 1 ))\ngh issue create --title "T"\n2',
     ("T",)),
    ("SL1: cmdsub pop 후 바깥 큰따옴표 복원 — bash: 출력 'A B ...', create 실행됨",
     'echo "A $(echo "B") C $(( 1 << 2 ))"\ngh issue create --title "T"\n2',
     ("T",)),
    ("SL2: brace 안 중첩 큰따옴표의 독립 인용+복원 — bash: create 실행됨",
     'echo "${x:-" ) "} $(( 1 << 2 ))"\ngh issue create --title "T"\n2',
     ("T",)),
    # --- 이하 수정 라운드 5 (마지막) red 승격분 ---
    ("R5-1: arith 안 brace 내부도 마스킹 관통 — bash: 출력 4, create 실행됨",
     'echo $(( ${SHIFT:-1<<2 } ))\ngh issue create --title "T"\n2',
     ("T",)),
    ("R5-2: dq 안 brace 안 작은따옴표는 리터럴, 산술은 활성 — bash: 출력 '4', "
     "다음 heredoc 본문은 미실행",
     'echo "${u:-\'$((1<<2))\'}"\ncat > /dev/null <<EOF\ntrue && gh issue create --title "T"\nEOF',
     ()),
    # --- 이하 라운드 6 Critical 수정분 ---
    ("C1: dq 안 brace가 정상 닫혀 이후 마스킹 유지 — bash: create 실행됨",
     'echo "${u:-x}" ; (( 1 << 2 ))\ngh issue create --title "T"\n2',
     ("T",)),
    ("C1 오차단 방향: brace 누수로 뒤 heredoc 본문이 파싱되면 안 됨 — "
     "bash: create 미실행 (본문)",
     'echo "${x:-y}"; (( 1<<2 )); cat > /dev/null <<EOF\ngh issue create --title "T"\nEOF',
     ()),
    ("C2: 백틱 안 작은따옴표 안 이스케이프 백틱은 닫지 않음 — bash: 출력 a`b, "
     "create 실행됨",
     'echo `echo \'a\\`b\'` ; (( 1 << 2 ))\ngh issue create --title "T"\n2',
     ("T",)),
    # --- 이하 라운드 7 Critical 수정분 ---
    ("R7-A: 큰따옴표 문맥은 중첩 brace로 상속 — bash: 출력 '4', create 실행됨",
     'echo "${u:-${v:-\'$((1<<2))\'}}"\ngh issue create --title "T"\n2',
     ("T",)),
    ("R7-A 오차단 방향: 중첩 brace의 산술이 마스킹돼야 뒤 heredoc 본문이 "
     "보존됨 — bash: create 미실행 (본문)",
     'echo "${u:-${v:-\'$((1<<2))\'}}"\ncat > /dev/null <<EOF\ngh issue create --title "T"\nEOF',
     ()),
    ("R7-B: 백틱 렉서 이스케이프 집합 밖의 백슬래시-작은따옴표는 인용을 "
     "닫음 — bash: 출력 a\\ 4, create 실행됨",
     "echo `echo 'a\\' ; echo $((1<<2))`\ngh issue create --title \"T\"\n2",
     ("T",)),
    ("R7-B 오차단 방향 — bash: create 미실행 (본문)",
     "echo `echo 'a\\' ; echo $((1<<2))`\ncat > /dev/null <<EOF\ngh issue create --title \"T\"\nEOF",
     ()),
]


def test_oracle_corpus() -> None:
    for description, cmd, expected_titles in _ORACLE_CORPUS:
        titles = tuple(inv.title for inv in guard.detect_invocations(cmd))
        assert titles == expected_titles, f"{description}: got {titles!r}"


# ---------- 세그먼트 판정 코퍼스 ----------
# _ORACLE_CORPUS가 동결된 마스킹 스캐너의 계약이라면, 이 테이블은 세그먼트 경계
# 판정(명령 위치 예약어·연산자 런·줄 연속)의 계약이다. 섞지 않는 이유는 동결
# 선언이 지목한 계약의 의미를 흐리지 않기 위해서다. 기대값은 전부 "bash가 그
# 자리의 create를 **명령으로 취급하는가**"를 실측해 정했다. 실행 여부와 같지
# 않다는 점에 주의 — 조건문·단축 평가 때문에 명령 위치에 있어도 그 실행에서는
# 안 불릴 수 있다(`if ((x)); then gh …` 는 x가 참일 때만 실행되지만 gh는 명령
# 위치에 있다). 이 가드는 정적 판정기라 명령 위치를 본다. 설명에 "실행됨"이라고
# 적을 때는 무조건 실행되는 경우이고, 조건부면 조건을 함께 적는다.
#
# 두 표의 실행 주장 49건 전부를 gh 스텁으로 bash에 돌려 대조했다(PR #75 라운드 2).
# 재현법: 각 항목의 명령을 `bash -c 'gh() { echo __GH__; }; <명령>'` 로 임시
# 디렉터리에서 실행해 `__GH__` 출력 여부를 본다. 주의 두 가지 — `while true; do
# ... done` 항목은 무한 루프라 타임아웃이 필요하고(gh는 즉시 호출된다), 조건문·
# 단축 평가 항목은 조건이 참일 때만 호출되므로 "미호출"을 오차단으로 읽으면 안 된다.
# 감지 방향과 **오차단 방향 잠금**을 함께 담는다 — 이 층의 진짜 위험은
# 실행되지 않는 텍스트를 차단하는 쪽이다.
# 형식: (설명, 명령, 기대 감지 제목 튜플).

_SEGMENT_CORPUS = [
    ("then 뒤 create — bash: gh는 명령 위치, 실행은 파일 x가 있을 때",
     'if [ -f x ]; then gh issue create -t "T"; fi', ("T",)),
    ("do 뒤 create (for) — bash: 실행됨",
     'for i in 1; do gh issue create -t "T"; done', ("T",)),
    ("do 뒤 create (while) — bash: 실행됨",
     'while true; do gh issue create -t "T"; done', ("T",)),
    ("그룹 명령 중괄호 뒤 create — bash: 실행됨",
     '{ gh issue create -t "T"; }', ("T",)),
    ("time 뒤 create — bash: 실행됨",
     'time gh issue create -t "T"', ("T",)),
    ("부정 ! 뒤 create — bash: 실행됨",
     '! gh issue create -t "T"', ("T",)),
    ("case 분기 뒤 create — bash: 실행됨",
     'case x in x) gh issue create -t "T" ;; esac', ("T",)),
    ("오차단 방향: 인자 위치의 then은 예약어가 아니다 — bash: 미실행(echo 인자)",
     'echo then gh issue create --title "T"', ()),
    # --- 결합 연산자 토큰 (shlex가 인접 문장부호를 한 토큰으로 묶는다) ---
    ("산술 확장 뒤 ; 로 이어진 create — `));` 한 토큰 — bash: 실행됨",
     'echo $((1<<2)); gh issue create -t "T"', ("T",)),
    ("#68 재현 케이스 — `));` 경계와 then 스킵이 둘 다 있어야 감지 — bash: gh는 명령 위치, 실행은 x가 참일 때",
     'if ((x)); then gh issue create -t "T"; fi', ("T",)),
    ("오차단 방향: `))` 단독은 경계가 아니다 — bash: 미실행(echo 인자, 출력 `4 gh ...`)",
     'echo $((1<<2)) gh issue create -t "T"', ()),
    ("오차단 방향: &> 는 리다이렉션이다 — bash: 미실행(gh라는 파일로 리다이렉션)",
     'echo a &> gh issue create -t "T"', ()),
    ("오차단 방향: >& 도 리다이렉션 — bash: 미실행",
     'echo a >& gh issue create -t "T"', ()),
    ("오차단 방향: >| 도 리다이렉션 — bash: 미실행",
     'echo a >| gh issue create -t "T"', ()),
    ("오차단 방향: &>> 도 리다이렉션 — bash: 미실행",
     'echo a &>> gh issue create -t "T"', ()),
    ("서브셸 닫기 뒤 create — `);` 한 토큰 — bash: 실행됨",
     '(cd x; ls); gh issue create -t "T"', ("T",)),
    # 인용된 연산자 리터럴 — posix 토큰화가 따옴표를 벗기므로 non-posix 재토큰화로
    # 표식을 만들어 걸러낸다. 아래 `;`·`&&`는 이 판정 도입 이전에도 오차단이었다.
    ("오차단 방향: 인용된 연산자 리터럴은 명령 구분자가 아니다 — bash: 미실행",
     'echo "|&" gh issue create -t "T"', ()),
    ("오차단 방향: 괄호+구분자 인용 리터럴 — bash: 미실행(echo 인자)",
     'echo "(;" gh issue create -t "T"', ()),
    ("오차단 방향: `));` 인용 리터럴 — bash: 미실행",
     'echo "));" gh issue create -t "T"', ()),
    ("오차단 방향: 작은따옴표 리터럴도 동일 — bash: 미실행",
     "echo '));' gh issue create -t \"T\"", ()),
    ("오차단 방향: 단독 구분자 인용 리터럴(도입 이전부터의 오차단) — bash: 미실행",
     'echo ";" gh issue create -t "T"', ()),
    ("오차단 방향: `&&` 인용 리터럴(도입 이전부터의 오차단) — bash: 미실행",
     'echo "&&" gh issue create -t "T"', ()),
    ("오차단 방향: 인용된 예약어는 예약어가 아니다 — bash: 미실행"
     "(`then: command not found`)",
     '"then" gh issue create -t "T"', ()),
    ("오차단 방향: 작은따옴표 예약어도 동일 — bash: 미실행",
     "'time' gh issue create -t \"T\"", ()),
    ("오차단 방향: 따옴표가 낱말에 붙어 정렬이 깨져도 오차단이 되면 안 됨 — "
     "bash: 미실행(`();;)` 를 명령으로 취급)",
     'echo x && "();;"\')\' gh issue create -t "T"', ()),
    ("이스케이프가 섞여도 정확 일치 연산자는 경계로 남는다 — bash: 실행됨",
     'echo a\\ b && gh issue create -t "T"', ("T",)),
    ("이스케이프 정규화로 정렬이 복원돼 새 판정이 살아난다 — bash: gh는 명령 위치, 실행은 x가 참일 때",
     'echo a\\ b; if ((x)); then gh issue create -t "T"; fi', ("T",)),
    # --- 이스케이프된 연산자 리터럴 (#72) ---
    ("오차단 방향: 이스케이프된 구분자는 명령 구분자가 아니다 — bash: 미실행",
     'echo \\; gh issue create -t "T"', ()),
    ("오차단 방향: 같은 줄의 이스케이프가 있어도 인용된 구분자를 지킨다 — bash: 미실행",
     'echo a\\ b ";" gh issue create -t "T"', ()),
    ("오차단 방향: 작은따옴표 안 백슬래시는 리터럴이라 인용을 닫는다 — bash: 미실행",
     "echo 'a\\' ';' gh issue create -t \"T\"", ()),
    ("오차단 방향: 큰따옴표 안 작은따옴표는 인용을 열지 않는다 — bash: 미실행",
     'echo "\'" \\; gh issue create -t "T"', ()),
    ("오차단 방향: 위 둘의 결합(작은따옴표만 추적하던 중간안의 회귀) — bash: 미실행",
     'echo "\'" \'a\\\' \';\' gh issue create -t "T"', ()),
    ("감지 유지: 큰따옴표 안 아포스트로피가 진짜 && 경계를 삼키면 안 된다 — bash: 실행됨",
     'echo "it\'s fine" && gh issue create -t "T"', ("T",)),
    ("감지 유지: 작은따옴표 안 큰따옴표도 마찬가지 — bash: 실행됨",
     "echo 'say \"hi\"' ; gh issue create -t \"T\"", ("T",)),
    # --- 백슬래시 줄 연속 ---
    ("줄 연속 뒤 create — bash: 실행됨",
     'echo a && \\\ngh issue create --title "T"', ("T",)),
    ("오차단 방향: 작은따옴표 안 줄 연속은 문자열 내용일 뿐 — bash: 미실행",
     "echo 'text \\\ngh issue create -t T'", ()),
    ("오차단 방향: 결합돼야 마커가 되는 heredoc(`E\\`+`OF`) — bash: 본문, 미실행",
     'cat << E\\\nOF\ngh issue create -t "T"\nEOF', ()),
    ("오차단 방향: 미종결 heredoc 본문의 쪼개진 토큰(`g\\`+`h`)을 결합해 "
     "gh를 조립하면 안 됨 — bash: 경고 후 본문 출력, 미실행",
     'cat << "EOF"\ng\\\nh issue create -t "T"', ()),
    # 교환 관계 잠금: bash는 본문 안 `E\`+`OF`도 결합해 heredoc을 조기 종결하므로
    # 이 create를 **실제로 실행한다**(실측: 뒤 명령 실행됨, 마지막 EOF는 command
    # not found). 그럼에도 기대값은 미감지다 — 롤백 구역에서 결합을 포기하는 대가로
    # 정탐 하나를 잃고 위 오차단을 막는 교환이다.
    ("교환 관계: 롤백 구역이라 결합하지 않아 미감지 — bash: 실행됨",
     'cat << E\\\nOF\necho "body"\nE\\\nOF\ngh issue create -t "T"\nEOF', ()),
]


def test_segment_corpus() -> None:
    for description, cmd, expected_titles in _SEGMENT_CORPUS:
        titles = tuple(inv.title for inv in guard.detect_invocations(cmd))
        assert titles == expected_titles, f"{description}: got {titles!r}"


# ---------- 알려진 오차단 (사전 존재) ----------
# 이 표의 항목은 **버그를 잠근 것**이다. bash가 create를 실행하지 않는데도
# 가드가 감지해 차단하는 입력이며, 전부 main에도 있는 사전 존재 결함이다.
# 여기 두는 이유는 green 스위트가 "오차단이 없다"로 읽히지 않게 하기 위해서다.
#
# **이 테스트가 깨졌다면 버그가 고쳐진 것이다.** 기대값을 되돌리지 말고 해당
# 항목을 지워라.
#
# 남은 항목은 owner가 수용한 한계다(#72 종결 시 결정). 실사용에서 한 번이라도
# 관측되면 수용을 철회하고 수리 대상으로 올린다.
#
# **이 표가 망라적이라는 증명은 없다.** 여기 있는 것은 실측으로 확인된 것들뿐이다.
# 셋 다 정확 일치 OPERATORS 경로를 지나지만 도달 이유는 다르다 — 1·3번은 인용·
# 이스케이프 때문에 표식 정렬이 깨져 폴백으로 떨어지고, 2번은 표식이 정상인데도
# `;;`가 정확 일치 연산자라서 그 경로가 곧바로 경계를 만든다(case 밖에서는 문법
# 오류라는 bash 문맥 민감성은 이 가드가 보지 않는다).
#
# 형식: (설명, 명령, 현재 감지되는 제목 튜플, 출처).

_KNOWN_FALSE_BLOCKS = [
    ("이스케이프된 큰따옴표가 낱말 경계를 가로질러 인용 표식 정렬이 깨지고, "
     "폴백의 정확 일치 경로가 인용된 구분자를 경계로 읽는다 — bash: 미실행",
     'echo "a\\" "a\\" ";" gh issue create -t "T"', ("T",), "PR #72 수용 한계"),
    ("`;;`는 case 밖에서 문법 오류라 bash가 명령 전체를 거부하는데, 가드는 "
     "정확 일치로 경계를 만든다 — bash: 미실행(syntax error). 인용과 무관한 "
     "사전 존재 계열이며 main도 동일",
     'echo ;; gh issue create -t "T"', ("T",), "PR #75 라운드 1 발견"),
    ("인용된 연산자가 명령 이름 자리에 오면 bash는 그 이름을 찾다 실패한다 — "
     "bash: 미실행(`&: command not found`). 사전 존재 계열이며 main도 동일",
     'echo ;"&"\'\' gh issue create -t "T"', ("T",), "PR #75 라운드 1 발견"),
]


def test_known_false_blocks() -> None:
    for description, cmd, expected_titles, origin in _KNOWN_FALSE_BLOCKS:
        titles = tuple(inv.title for inv in guard.detect_invocations(cmd))
        assert titles == expected_titles, (
            f"{description} ({origin}): got {titles!r} — 오차단이 사라졌다면 "
            "이 항목을 지워라. 기대값을 되돌리지 마라"
        )


# ---------- 판정 흐름 (main) ----------

def test_non_bash_tool_passes(monkeypatch) -> None:
    assert _run_main(monkeypatch, {"tool_name": "Write", "tool_input": {}}) == 0


def test_malformed_stdin_warns_without_blocking(monkeypatch, capsys) -> None:
    assert _run_main(monkeypatch, "not json{") == 1
    assert "fail-open" in capsys.readouterr().err


def test_no_duplicates_passes_silently(monkeypatch) -> None:
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: "[]")
    assert _run_main(monkeypatch, _bash_payload("gh issue create -t brand-new")) == 0


def test_duplicates_block_with_candidates_and_override_hint(monkeypatch, capsys) -> None:
    issues = json.dumps([{"number": 12, "state": "OPEN", "title": "같은 작업"}])
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: issues)
    assert _run_main(monkeypatch, _bash_payload('gh issue create -t "같은 작업"')) == 42
    err = capsys.readouterr().err
    assert "#12" in err and guard.OVERRIDE_TOKEN in err


def test_override_skips_search_entirely(monkeypatch) -> None:
    def _fail(argv, cwd=None):
        raise AssertionError("search was invoked despite override")

    monkeypatch.setattr(guard, "_run_search", _fail)
    cmd = f"{guard.OVERRIDE_TOKEN} gh issue create -t anything"
    assert _run_main(monkeypatch, _bash_payload(cmd)) == 0


def test_missing_title_blocks(monkeypatch, capsys) -> None:
    assert _run_main(monkeypatch, _bash_payload("gh issue create --body x")) == 42
    err = capsys.readouterr().err
    # 모든 차단 메시지는 override 복구 경로를 안내해야 한다 (#32 괴리 3)
    assert "--title" in err and guard.OVERRIDE_TOKEN in err


def test_whole_command_prefix_does_not_override_later_segment(monkeypatch) -> None:
    # 의미론 잠금(#32 괴리 4): 마커는 gh/glab이 있는 세그먼트 선두에서만
    # 유효하다 — 셸 의미론과 동일. 이 테스트는 문서 정합 커밋에서 기존
    # 동작을 고정하며, 수정 전에도 통과한다(선실패 원칙의 명시적 예외).
    issues = json.dumps([{"number": 12, "state": "OPEN", "title": "T"}])
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: issues)
    cmd = f'{guard.OVERRIDE_TOKEN} git add . && gh issue create -t "T"'
    assert _run_main(monkeypatch, _bash_payload(cmd)) == 42


def test_empty_title_blocks(monkeypatch, capsys) -> None:
    assert _run_main(monkeypatch, _bash_payload('gh issue create --title ""')) == 42
    assert "--title" in capsys.readouterr().err


def test_search_failure_fails_open(monkeypatch, capsys) -> None:
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: None)
    assert _run_main(monkeypatch, _bash_payload("gh issue create -t x")) == 0
    assert "skipped" in capsys.readouterr().err


def test_repo_is_forwarded_to_search(monkeypatch) -> None:
    seen: list[list[str]] = []

    def _capture(argv, cwd=None):
        seen.append(argv)
        return "[]"

    monkeypatch.setattr(guard, "_run_search", _capture)
    _run_main(monkeypatch, _bash_payload("gh issue create -t x -R owner/repo"))
    assert ["--repo", "owner/repo"] == seen[0][-2:]


def test_payload_cwd_is_forwarded_to_search(monkeypatch) -> None:
    # #33 회귀: 훅 프로세스 cwd가 아니라 Bash 도구의 작업 디렉터리에서 검색해야 한다
    seen: list[str | None] = []

    def _capture(argv, cwd=None):
        seen.append(cwd)
        return "[]"

    monkeypatch.setattr(guard, "_run_search", _capture)
    payload = _bash_payload("gh issue create -t x", cwd="/divergent/dir")
    assert _run_main(monkeypatch, payload) == 0
    assert seen == ["/divergent/dir"]


def test_cd_makes_search_unsafe_and_fails_open(monkeypatch, capsys) -> None:
    # 선행 cd 뒤에는 검색 대상 저장소가 불명 → 검색 없이 통과 (fail-open)
    def _fail(argv, cwd=None):
        raise AssertionError("search must not run after cd")

    monkeypatch.setattr(guard, "_run_search", _fail)
    payload = _bash_payload("cd /elsewhere && gh issue create -t x", cwd="/orig")
    assert _run_main(monkeypatch, payload) == 0
    assert "skipped" in capsys.readouterr().err


def test_cd_with_explicit_repo_still_searches(monkeypatch) -> None:
    # 명시적 --repo는 디렉터리와 무관하므로 cd가 있어도 검색은 수행돼야 한다
    seen: list[list[str]] = []

    def _capture(argv, cwd=None):
        seen.append(argv)
        return "[]"

    monkeypatch.setattr(guard, "_run_search", _capture)
    payload = _bash_payload("cd /elsewhere && gh issue create -t x -R owner/repo")
    assert _run_main(monkeypatch, payload) == 0
    assert len(seen) == 1 and ["--repo", "owner/repo"] == seen[0][-2:]


# ---------- glab 어댑터 (보수 파싱) ----------
# 픽스처 문자열은 실측 출력 그대로 (glab 1.108.0 / GitLab CE 19.2.0, 2026-07-22).
# 실인스턴스 드리프트 카나리아는 test_guard_gitlab.py (-m gitlab).

def test_glab_confident_lines_block(monkeypatch, capsys) -> None:
    out = (
        "Showing 1 issue in root/scratch that match your search. (Page 1)\n"
        "\n"
        "ID\tTitle\tLabels\tCreated at\n"
        "#3\tcapture sample issue\t\tless than a minute ago\n"
        "\n"
    )
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: out)
    assert _run_main(monkeypatch, _bash_payload("glab issue create -t x")) == 42
    assert "#3" in capsys.readouterr().err


def test_glab_ambiguous_output_fails_open(monkeypatch) -> None:
    out = "No issues match your search in root/scratch.\n\n\n"
    monkeypatch.setattr(guard, "_run_search", lambda argv, cwd=None: out)
    assert _run_main(monkeypatch, _bash_payload("glab issue create -t x")) == 0


# ---------- 하위 실행기 ----------

def test_run_search_timeout_returns_none(monkeypatch) -> None:
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=guard.SEARCH_TIMEOUT_SECONDS)

    monkeypatch.setattr(subprocess, "run", _timeout)
    assert guard._run_search(["gh", "issue", "list"]) is None


def test_run_search_passes_cwd_to_subprocess(monkeypatch) -> None:
    captured: dict = {}

    def _record(argv, **kwargs):
        captured.update(kwargs)

        class Result:
            returncode = 0
            stdout = "[]"

        return Result()

    monkeypatch.setattr(subprocess, "run", _record)
    guard._run_search(["gh", "issue", "list"], cwd="/somewhere")
    assert captured["cwd"] == "/somewhere"


def test_run_wrapper_converts_crash_to_nonblocking(monkeypatch, capsys) -> None:
    def _boom() -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(guard, "main", _boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert guard.run() == 1
    assert "fail-open" in capsys.readouterr().err
