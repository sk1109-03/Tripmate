"""규칙 결과를 LLM으로 요약한다. 판단은 바꾸지 않고 설명 문장만 만든다."""

from llm_client import LLMError, ask
from planner import v

SYSTEM = (
    "너는 여행 일정 점검 결과를 요약하는 도우미다. 주어진 규칙 기반 결과의 순위와 판단을 바꾸지 말고, "
    "새로운 사실이나 수치를 만들지 마라. 한국어 3~5문장으로 1순위 안, 유지할 일정, 핵심 불확실성, "
    "사용자가 확인할 항목을 요약하라. 각 문장 끝에 근거 문서 번호(D01~D08)를 괄호로 붙여라. "
    "강수·풍속·이동/지연시간·데크 폐쇄는 예보·추정이므로 '가능성', '예보상', '추정'처럼 표현하고 "
    "확정 표현을 쓰지 마라. 공연 입장 마감과 예약 규칙은 확정 조건으로 표현해도 된다."
)


def summarize(data, report):
    banned = v(data["llm"]["banned_phrases"])
    try:
        text = ask(f"다음 규칙 기반 점검 결과를 요약해줘.\n\n{report}", system=SYSTEM, max_tokens=1500)
    except LLMError as e:
        return f"- LLM 호출 실패({e}). 위 규칙 결과만 사용하세요."
    hits = [w for w in banned if w in text]
    if hits:
        return f"- LLM 요약에 확정 표현({', '.join(hits)})이 포함되어 제외함 [D08]. 위 규칙 결과만 사용하세요."
    return "(LLM 생성 요약: 판단은 위 규칙 결과 기준)\n" + text.strip()
