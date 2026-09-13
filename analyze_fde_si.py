import json

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model

from job_state import JobState

load_dotenv()


class FDESIAnalysis(BaseModel):
    fde_score: int = Field(
        ge=0,
        le=10,
        description="FDE 특성의 강도. 0은 전혀 없음, 10은 매우 강함"
    )
    si_score: int = Field(
        ge=0,
        le=10,
        description="SI 특성의 강도. 0은 전혀 없음, 10은 매우 강함"
    )


llm = init_chat_model(
    "openai:gpt-5-mini",
    temperature=0
)

analyzer = llm.with_structured_output(FDESIAnalysis)


def analyze_fde_si(state: JobState) -> dict:
    """job_info와 company_info를 바탕으로 FDE/SI 특성을 평가하는 LangGraph node."""
    job = json.dumps(state["job_info"], ensure_ascii=False, indent=2)
    company = json.dumps(state["company_info"], ensure_ascii=False, indent=2)

    result = analyzer.invoke(
        f"""
당신은 AI 기업의 사업모델과 엔지니어링 조직을 분석합니다.

다음 채용공고와 회사 조사 결과만 근거로
이 포지션이 FDE에 가까운지 SI에 가까운지 분석하세요.

FDE 신호:
- 자체 제품 또는 플랫폼이 존재함
- 엔지니어가 고객 문제를 직접 정의함
- 고객 현장에서 빠르게 구현하고 검증함
- 고객별 프로젝트 결과가 공통 제품으로 환원됨
- PoC 이후 제품 도입 및 확장으로 이어짐
- 제품팀과 현장팀의 피드백 루프가 강함

SI 신호:
- 고객별 요구사항에 따라 별도 시스템을 구축함
- 프로젝트 납품 자체가 주요 매출원임
- RFP/RFQ 중심 사업
- 고객사 장기 상주
- 고객별 코드베이스나 커스터마이징 비중이 큼
- 구축 후 유지보수가 주요 업무임

확실한 근거가 없는 내용은 추측하지 마세요.

[채용공고]
{job}

[회사 조사]
{company}
"""
    )

    return {
        "fde_si_analysis": result.model_dump()
    }
