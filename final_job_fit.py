import json

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model

from job_state import JobState

load_dotenv()


class FinalJobFit(BaseModel):
    fit_score: float = Field(ge=0, le=10)
    confidence: float = Field(ge=0, le=1)

    recommendation: str

    why_fit: list[str]
    concerns: list[str]

    must_verify_before_joining: list[str]

    final_conclusion: str


llm = init_chat_model(
    "openai:gpt-5-mini",
    temperature=0
)

judge = llm.with_structured_output(FinalJobFit)


def final_job_fit(state: JobState) -> dict:
    """모든 분석 결과와 후보자 우선순위를 합쳐 최종 적합도를 판단하는 LangGraph node."""
    job = json.dumps(state["job_info"], ensure_ascii=False, indent=2)
    company = json.dumps(state["company_info"], ensure_ascii=False, indent=2)
    fde = json.dumps(state["fde_si_analysis"], ensure_ascii=False, indent=2)
    workplace = json.dumps(state["workplace_analysis"], ensure_ascii=False, indent=2)
    candidate = json.dumps(state["candidate_profile"], ensure_ascii=False, indent=2)

    result = judge.invoke(
        f"""
당신은 경력직 AI 엔지니어의 채용 의사결정을 돕는 분석가입니다.

아래 자료를 종합하여 이 포지션이 후보자에게 얼마나 적합한지 평가하세요.

중요 규칙:

- 후보자의 priorities를 중요도 가중치로 사용하세요.
- FDE 적합성을 특히 중요하게 평가하세요.
- 단순 SI인지 반드시 고려하세요.
- workplace 점수만 단순 평균하지 마세요.
- confidence가 낮은 정보는 최종 판단에 적게 반영하세요.
- 회사 전체 평균연봉을 해당 직무의 실제 연봉으로 간주하지 마세요.
- 확인되지 않은 사실은 단정하지 마세요.
- 좋은 점뿐 아니라 입사 전에 반드시 검증해야 할 위험도 명시하세요.

추천은 다음 중 하나로 작성하세요.

- 적극 지원
- 지원 추천
- 조건 확인 후 지원
- 보류
- 비추천


[채용공고]
{job}

[회사 조사]
{company}

[FDE / SI 분석]
{fde}

[직장 평가]
{workplace}

[후보자 기준]
{candidate}
"""
    )

    return {
        "final_job_fit": result.model_dump()
    }
