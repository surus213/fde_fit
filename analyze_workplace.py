import json

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model

from job_state import JobState

load_dotenv()


class DimensionScore(BaseModel):
    score: float = Field(ge=0, le=10)
    confidence: float = Field(ge=0, le=1)
    reason: str


class WorkplaceAnalysis(BaseModel):
    salary: DimensionScore
    work_life_balance: DimensionScore
    culture: DimensionScore
    management: DimensionScore
    stability_growth: DimensionScore

    strengths: list[str]
    risks: list[str]
    interview_questions: list[str]

    overall_score: float = Field(ge=0, le=10)
    conclusion: str


llm = init_chat_model(
    "openai:gpt-5-mini",
    temperature=0
)

analyzer = llm.with_structured_output(WorkplaceAnalysis)


def analyze_workplace(state: JobState) -> dict:
    """workplace_evidence를 점수와 리스크로 평가하는 LangGraph node."""
    evidence = json.dumps(
        state["workplace_evidence"],
        ensure_ascii=False,
        indent=2
    )

    result = analyzer.invoke(
        f"""
당신은 구직자를 위한 기업 평가 분석가입니다.

아래 조사 결과만 사용하여 직장으로서의 매력을 평가하세요.

각 영역을 0~10점으로 평가하고,
근거가 부족하면 점수를 억지로 정하지 말고
confidence를 낮추세요.

평가 영역:

1. salary
2. work_life_balance
3. culture
4. management
5. stability_growth

중요한 규칙:

- 회사 평균연봉과 특정 포지션 연봉을 혼동하지 마세요.
- 표본 2~7건짜리 리뷰는 강한 근거로 취급하지 마세요.
- 회사 공식 자료는 조직문화의 '주장'이지 검증된 사실이 아닙니다.
- 투자유치는 성장 신호이지만 회사 안정성을 보장하지 않습니다.
- 출처가 충돌하면 그 사실을 평가에 반영하세요.
- 모르는 것은 모른다고 판단하세요.
- 스타트업 특유의 성장 가능성과 안정성 위험을 동시에 평가하세요.

또한 면접에서 반드시 확인해야 할 질문을 만들어주세요.

[조사 결과]

{evidence}
"""
    )

    return {
        "workplace_analysis": result.model_dump()
    }
