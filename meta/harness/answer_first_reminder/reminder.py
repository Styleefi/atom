# 소유자 메시지의 질문·읽기 요청 모양을 감지해 answer-first 리마인더를 주입하는 UserPromptSubmit hook
"""answer-first 리마인더 hook (answer-first-reminder 규칙의 배포체).

Claude Code의 UserPromptSubmit hook으로 실행되어 들어오는 소유자 메시지가
질문 모양이거나 읽기 요청 모양이면 리마인더 한 줄을 stdout으로 출력한다.
UserPromptSubmit에서 exit 0의 stdout이 모델 컨텍스트에 추가되는 것은 훅 도입
당시 확인된 제품 동작으로, 이 출력이 answer-first 규칙(읽을 것을 요청하는
메시지에는 답변이 이번 턴의 산출물)을 위반 시점에 재공급한다. 규칙의 발동
조건은 "답장에서 읽을 것을 요청하는 메시지"이고, 이 hook은 그 조건을 두 가지
모양으로 근사한다.

질문 휴리스틱 (경량 — 형태소 분석 아님):

1. 코드 펜스(```...```) 블록 제거 — 붙여넣은 코드의 삼항 연산자·URL·정규식
   속 `?` 오탐 방지. 닫히지 않은 펜스(홀수 개 ```)의 본문은 남는다.
2. `?` 또는 전각 `？`가 있으면 질문.
3. 문장 끝이 한국어 의문 어미(까/까요/나요/가요/은가/는가/인가/니/냐)면 질문.
4. 문장에서 처음 나오는 영어 단어가 의문사/조동사(what, why, should 등)면
   질문 — 엄밀한 문두 검사가 아니다.

읽기 요청 휴리스틱 (2026-10-16 측정 기반, #37):

5. 코드 펜스를 제거하고 . ! 。 ！ ? ？ 와 줄바꿈 문자(\n)에서 나눈 뒤, 끝의
   공백과 ' " ’ ” ‘ “ ( ) [ ] < > … ~ 를 떼고도 남는 마지막 조각을 절로
   삼는다. 그 절이 요청 어미(해 줘/해줘/주세요/하자/보자/도록 등)로 끝나고,
   어미(와 그 앞 공백)를 뗀 나머지의 끝 5자 안에 읽기 동사 어간(설명/보고/
   확인/검토/추천/판단 등)이 있으면 읽기 요청. 어미 일치는 절 끝에 고정되어
   어간의 한 글자를 가져갈 수 있다("답해 주세요"는 포착, "답해 줘"는 미탐 —
   일치한 해 줘가 해를 가져간다). 답해는 센서스 목록 충실성 때문에 그대로
   둔다. 어미 17개와 어간 21개는 #149 센서스 분류기의 정의를 그대로
   가져왔고, 어간 5개(제시/판단/추천/찾아/받아)는 센서스 목록에 없던 것으로
   2026-10-16 측정의 읽기 요청에 각각 2회 이상 나타나 더했다(측정 표본
   554턴: 읽기 요청 86턴 중 75턴 포착, 오탐 7턴).

의도적 한계 (오탐·미탐 모두 무해가 설계 전제):

- 물음표 없는 ~야/~지는 평서문("이건 버그야.")과 구분 불가라 제외한다 —
  기록된 실제 위반 사례들은 전부 물음표가 있었다.
- 물음표 없는 -나("맞나")도 제외한다 — "하나"/"만나" 같은 평서 어절과
  충돌하고, 측정에서 물음표 없는 -나 질문은 0건이었다(같은 측정에서 어때도
  0건이라 추가하지 않았다).
- hook은 모양만 보므로 모양이 맞으면 읽기 요청이 아니어도 리마인더가 나간다 —
  질문 모양의 실행 요청("푸시할까?", 측정 554턴 중 6턴), 마지막 절의 어미
  앞 5자에 어간이 실린 실행 요청("에러 로그 보고 고쳐 줘", "PR 받아줘"),
  할게·맨 해로 끝나는 평서문("내가 확인할게", "나도 그렇게 생각해"). 실행
  요청을 answer-first 대상에서 빼는 판단은 규칙 본문을 읽는 모델의 몫이다.
  리마인더 문구가 사실 단정이 아니라 관측 서술인 이유다.
- 모양이 안 맞으면 읽기 요청이라도 미탐이다 — 마지막 절 밖의 요청("설명해
  줘. 그리고 커밋해."), 목록 밖 어간("세어 줘"), 어미(와 그 앞 공백)를 뗀 뒤
  남은 끝 5자 밖의 어간("설명을 좀 자세히 해 줘": 끝 5자는 '좀 자세히'),
  목록 밖 어미("설명해줘요", "부탁드려요", "바랍니다"), 어미 뒤에 노이즈
  제거가 걷지 않는 글자가 붙은 것("확인해 줘ㅋㅋ"). 모두 claude-md 현상
  유지로 퇴화한다.
- 줄 끝 연결형("...보니"), 해요체 평서문("먼저 가요"), 한국어 문장 속 영어
  단어("명령은 what부터"), 영어 조동사 문두("Should work now"), 닫히지 않은
  펜스 속 `?` 등의 오탐은 감수한다 — 비용은 무해한 리마인더 한 줄.
- 인라인 백틱·URL 속 `?`는 따로 처리하지 않는다.

설계 불변식:

- 어떤 경로에서도 차단 코드(가드들의 sentinel 42)를 반환하지 않는다
  (프롬프트 차단 금지). 모든 실패는 fail-open — 미탐은 claude-md 현상
  유지로 퇴화할 뿐이다. settings.json 래퍼(`|| exit 1`)도 차단(2)을 만들지
  않는 비차단형이다(계약은 rules_checker의 test_hook_command_contract.py에 있다).
- stdout은 모델 컨텍스트로 직행하는 주입 채널이므로 상수 REMINDER 외에는
  절대 쓰지 않는다(payload 내용 echo 금지 — 프롬프트 주입 증폭 방지).
- stdin은 로케일 비의존 명시적 UTF-8로 읽는다(한국어 본문을 다루는 hook).

종료 코드: 0 통과(질문·읽기 요청 모양이면 stdout으로 리마인더 한 줄), 1 내부
오류(비차단 경고).
"""

from __future__ import annotations

import re
import sys

from harness.hookio.payload import read_payload

# 컨텍스트에 주입되는 유일한 출력. 재공급 대상인 answer-first 규칙 본문과
# 같은 언어(영어)·같은 용어를 써서 로드된 규칙과 즉시 연결되게 한다.
# hook이 아는 것은 모양뿐이므로 "질문이 있다"고 단정하지 않고, 규칙의 발동
# 조건("asks for something to read in the reply")을 조건문으로 넘긴다 —
# 오탐 턴에서도 거짓 사실을 주입하지 않는다.
REMINDER = (
    "[answer-first-reminder] The owner's message looks like a question or a "
    "read-request — if it asks for something to read in the reply, this turn's "
    "deliverable is the answer, in prose, first; execution (commits, writes, "
    "plan-approval dialogs) resumes after the owner responds."
)

_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

_SENTENCE_SPLIT_RE = re.compile(r"[.!。！?？\n]")

# 조각 끝에서 떼는 노이즈: 공백(str.isspace)과 아래 문자들. `[...]+$` 정규식은 긴
# 공백 열의 시작 위치마다 끝까지 훑고 `$`에서 실패하기를 반복해 2차 시간이
# 되므로(#195) 뒤에서 한 글자씩 보는 선형 루프로 뗀다.
_TRAILING_NOISE_CHARS = frozenset("'\"’”‘“()[]<>…~.")


def _strip_trailing_noise(text: str) -> str:
    """조각 끝의 공백과 노이즈 문자를 뗀다.

    Args:
        text: 분리된 조각 하나.

    Returns:
        끝의 공백·노이즈 문자를 뗀 조각.
    """
    end = len(text)
    while end and (text[end - 1].isspace() or text[end - 1] in _TRAILING_NOISE_CHARS):
        end -= 1
    return text[:end]


_FIRST_ENGLISH_WORD_RE = re.compile(r"[a-zA-Z]+")

_KO_QUESTION_ENDINGS = ("까", "까요", "나요", "가요", "은가", "는가", "인가", "니", "냐")

_EN_QUESTION_STARTERS = frozenset({
    "what", "why", "how", "when", "where", "who", "which",
    "should", "can", "could", "would", "is", "are", "do", "does", "did",
})

# 요청 어미: 절 끝에서만 본다($ 고정). #149 센서스 분류기의 목록 그대로.
_REQUEST_ENDING_RE = re.compile(
    r"(해\s*줘|해줘|주세요|줄래|해\s*봐|해봐|해라|하자|보자|가자|합시다|할게|해|줘|바람|바라|도록)$"
)

# 읽기 동사 어간: 요청 어미 직전 5자 안에서 찾는다. 앞 21개는 #149 센서스
# 분류기의 목록, 뒤 5개는 그 목록에 없던 어간으로 측정(2026-10-16)의 읽기
# 요청에 각각 2회 이상 나타난 것 — 단발 어간은 표본에 과적합하므로 넣지 않는다.
_KO_READ_STEMS = (
    "설명", "보고", "확인", "검토", "알려", "정리", "비교", "분석", "리뷰", "평가",
    "조사", "보여", "요약", "살펴", "읽어", "답해", "답변", "의견", "생각", "짚어",
    "소개", "제시", "판단", "추천", "찾아", "받아",
)


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
        sentence = _strip_trailing_noise(raw_sentence)
        if sentence.endswith(_KO_QUESTION_ENDINGS):
            return True
        first_word = _FIRST_ENGLISH_WORD_RE.search(sentence)
        if first_word and first_word.group(0).lower() in _EN_QUESTION_STARTERS:
            return True
    return False


def contains_read_request(text: str) -> bool:
    """텍스트의 마지막 절이 읽기 요청 모양인지 경량 휴리스틱으로 판정한다.

    코드 펜스를 제거하고 . ! 。 ！ ? ？ 와 줄바꿈 문자(\n)에서 나눈 조각마다
    끝 노이즈(_strip_trailing_noise)를 뗀 뒤, 비어 있지 않은 마지막 조각을 절로
    삼는다. 그 절이 요청 어미로 끝나고, 어미(와 그 앞 공백)를 뗀 나머지의 끝
    5자 안에 읽기 동사 어간이 있으면 True.

    Args:
        text: 소유자 메시지 원문.

    Returns:
        읽기 요청 모양으로 보이면 True.
    """
    stripped = _CODE_FENCE_RE.sub("", text)
    # 조각마다 노이즈를 먼저 떼야 문장 부호 뒤의 닫는 따옴표·괄호만 남은 조각이
    # 마지막 절로 뽑히지 않는다 — contains_question과 같은 순서.
    clauses = [
        c for c in (_strip_trailing_noise(piece).strip() for piece in _SENTENCE_SPLIT_RE.split(stripped)) if c
    ]
    if not clauses:
        return False
    last = clauses[-1]
    if not _REQUEST_ENDING_RE.search(last):
        return False
    core = _REQUEST_ENDING_RE.sub("", last).strip()
    return any(stem in core[-5:] for stem in _KO_READ_STEMS)


def main() -> int:
    """stdin의 UserPromptSubmit JSON을 판정한다.

    Returns:
        종료 코드 (0 통과 — 질문·읽기 요청 모양이면 stdout으로 리마인더, 1 내부 경고).
    """
    try:
        payload = read_payload(strict_utf8=True)
    except ValueError:
        print("[answer-first-reminder] malformed hook input (fail-open)", file=sys.stderr)
        return 1
    if payload is None:
        return 0
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return 0
    if contains_question(prompt) or contains_read_request(prompt):
        print(REMINDER)
    return 0


def run() -> int:
    """최상위 방어 실행기: 어떤 내부 오류도 차단으로 새지 않게 한다.

    Returns:
        종료 코드 (main()의 반환값을 그대로 내보낸다. main()이 Exception을
        던지면 대신 1 — 비차단).
    """
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 — fail-open이 설계 요구사항
        print(f"[answer-first-reminder] internal error (fail-open): {exc}", file=sys.stderr)
        return 1
