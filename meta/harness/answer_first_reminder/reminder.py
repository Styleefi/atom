# 소유자 메시지의 질문을 감지해 answer-first 리마인더를 주입하는 UserPromptSubmit hook
"""answer-first 리마인더 hook (answer-first-reminder 규칙의 배포체).

Claude Code의 UserPromptSubmit hook으로 실행되어 들어오는 소유자 메시지가
질문 형태이면 리마인더 한 줄을 stdout으로 출력한다. UserPromptSubmit에서
exit 0의 stdout은 모델 컨텍스트에 추가되므로, 이 출력이 answer-first 규칙
(질문에는 답변이 이번 턴의 산출물)을 위반 시점에 결정론적으로 재공급한다.

질문 휴리스틱 (경량 — 형태소 분석 아님):

1. 코드 펜스(```...```) 블록 제거 — 붙여넣은 코드의 삼항 연산자·URL·정규식
   속 `?` 오탐 방지. 닫히지 않은 펜스(홀수 개 ```)의 본문은 남는다.
2. `?` 또는 전각 `？`가 있으면 질문.
3. 문장 끝이 한국어 의문 어미(까/까요/나요/가요/은가/는가/인가/니/냐)면 질문.
4. 문장에서 처음 나오는 영어 단어가 의문사/조동사(what, why, should 등)면
   질문 — 엄밀한 문두 검사가 아니다.

의도적 한계 (오탐·미탐 모두 무해가 설계 전제):

- 물음표 없는 ~야/~지는 평서문("이건 버그야.")과 구분 불가라 제외한다 —
  기록된 실제 위반 사례들은 전부 물음표가 있었다.
- 줄 끝 연결형("...보니"), 해요체 평서문("먼저 가요"), 한국어 문장 속 영어
  단어("명령은 what부터"), 영어 조동사 문두("Should work now"), 닫히지 않은
  펜스 속 `?` 등의 오탐은 감수한다 — 비용은 무해한 리마인더 한 줄.
- 인라인 백틱·URL 속 `?`는 따로 처리하지 않는다.

설계 불변식:

- 어떤 경로에서도 exit 2를 반환하지 않는다(프롬프트 차단 금지). 모든 실패는
  fail-open — 미탐은 claude-md 현상 유지로 퇴화할 뿐이다.
- stdout은 모델 컨텍스트로 직행하는 주입 채널이므로 상수 REMINDER 외에는
  절대 쓰지 않는다(payload 내용 echo 금지 — 프롬프트 주입 증폭 방지).
- stdin은 로케일 비의존 명시적 UTF-8로 읽는다(한국어 본문을 다루는 hook).

종료 코드: 0 통과(질문이면 stdout으로 리마인더 한 줄), 1 내부 오류(비차단 경고).
"""

from __future__ import annotations

import json
import re
import sys

# 컨텍스트에 주입되는 유일한 출력. 재공급 대상인 answer-first 규칙 본문과
# 같은 언어(영어)·같은 용어를 써서 로드된 규칙과 즉시 연결되게 한다.
REMINDER = (
    "[answer-first-reminder] The owner's message contains a question — "
    "this turn's deliverable is the answer, in prose, first; execution "
    "(commits, writes, plan-approval dialogs) resumes after the owner responds."
)

_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# 물음표 검사(2단계)가 먼저 True로 빠지므로 ?/？ 분리는 죽은 가지지만,
# 단계 순서가 바뀌어도 분리가 깨지지 않게 방어적으로 유지한다.
_SENTENCE_SPLIT_RE = re.compile(r"[.!。！?？\n]")

# 문장 끝의 공백·따옴표(전각 포함)·괄호류·말줄임·물결 등 어미 검사를 가리는 노이즈.
_TRAILING_NOISE_RE = re.compile(r"[\s'\"’”‘“()\[\]<>…~.]+$")

_FIRST_ENGLISH_WORD_RE = re.compile(r"[a-zA-Z]+")

_KO_QUESTION_ENDINGS = ("까", "까요", "나요", "가요", "은가", "는가", "인가", "니", "냐")

_EN_QUESTION_STARTERS = frozenset({
    "what", "why", "how", "when", "where", "who", "which",
    "should", "can", "could", "would", "is", "are", "do", "does", "did",
})


def contains_question(text: str) -> bool:
    """텍스트가 질문 형태인지 경량 휴리스틱으로 판정한다.

    Args:
        text: 소유자 메시지 원문.

    Returns:
        질문 형태로 보이면 True.
    """
    if not text.strip():
        return False
    stripped = _CODE_FENCE_RE.sub("", text)
    if "?" in stripped or "？" in stripped:
        return True
    for raw_sentence in _SENTENCE_SPLIT_RE.split(stripped):
        sentence = _TRAILING_NOISE_RE.sub("", raw_sentence)
        if sentence.endswith(_KO_QUESTION_ENDINGS):
            return True
        first_word = _FIRST_ENGLISH_WORD_RE.search(sentence)
        if first_word and first_word.group(0).lower() in _EN_QUESTION_STARTERS:
            return True
    return False


def main() -> int:
    """stdin의 UserPromptSubmit JSON을 판정한다.

    Returns:
        종료 코드 (0 통과 — 질문이면 stdout으로 리마인더, 1 내부 경고).
    """
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError:
        # UnicodeDecodeError도 ValueError의 하위 클래스라 여기서 잡힌다.
        print("[answer-first-reminder] malformed hook input (fail-open)", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        return 0
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return 0
    if contains_question(prompt):
        print(REMINDER)
    return 0


def run() -> int:
    """최상위 방어 실행기: 어떤 내부 오류도 차단으로 새지 않게 한다.

    Returns:
        종료 코드 (내부 오류 시 1 — 비차단).
    """
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 — fail-open이 설계 요구사항
        print(f"[answer-first-reminder] internal error (fail-open): {exc}", file=sys.stderr)
        return 1
