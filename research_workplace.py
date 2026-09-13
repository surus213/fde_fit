import json
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain_tavily import TavilySearch

from job_state import JobState

load_dotenv()


class WorkplaceEvidenceItem(BaseModel):
    dimension: Literal[
        "salary",
        "work_life_balance",
        "culture",
        "management",
        "stability_growth"
    ]

    claim: str
    source_title: str
    source_url: str

    source_type: Literal[
        "employee_review",
        "official",
        "news",
        "business_database",
        "recruitment"
    ]

    confidence: float = Field(
        ge=0,
        le=1,
        description="이 근거를 얼마나 신뢰할 수 있는지"
    )

    caveat: str = Field(
        description="표본 부족, 회사 주장, 추정치 등 주의사항"
    )


class WorkplaceEvidence(BaseModel):
    company: str
    evidence: list[WorkplaceEvidenceItem]

    conflicts: list[str] = Field(
        description="출처끼리 서로 다른 정보를 제공하는 경우"
    )

    unknowns: list[str] = Field(
        description="공개 정보만으로 확인할 수 없는 사항"
    )


search = TavilySearch(
    max_results=5,
    search_depth="advanced"
)

llm = init_chat_model(
    "openai:gpt-5-mini",
    temperature=0
)

researcher = llm.with_structured_output(WorkplaceEvidence)


def research_workplace(state: JobState) -> dict:
    """회사 관련 직장 근거를 웹에서 수집해 workplace_evidence를 만드는 LangGraph node."""
    company = state["job_info"]["company"]

    queries = [
        f"{company} 잡플래닛 리뷰 워라밸 조직문화 경영진",
        f"{company} 연봉 급여 채용",
        f"{company} 직원 수 매출 투자 기업정보",
        f"{company} 대표 인터뷰 경영 문화",
        f"{company} 복지 근무환경 재택근무 채용"
    ]

    search_results = []

    for query in queries:
        result = search.invoke({"query": query})
        search_results.append(result)

    result = researcher.invoke(
        f"""
당신은 구직자를 위한 기업 조사 분석가입니다.

다음 웹 검색 결과만 사용하여
{company}의 직장 관련 사실과 근거를 정리하세요.

평가나 추천은 아직 하지 마세요.
확인 가능한 사실만 추출하세요.

특히 다음 다섯 영역을 조사하세요.

1. salary
   연봉, 급여 수준, 보상

2. work_life_balance
   야근, 유연근무, 재택, 출장, 워라밸

3. culture
   조직문화, 협업 방식, 성장 환경

4. management
   대표 및 경영진에 대한 직원 평가,
   경영 스타일, CEO 관련 정보

5. stability_growth
   투자, 직원 수, 매출, 성장세, 회사 안정성

주의사항:

- 직원 리뷰와 회사 공식 주장을 구분하세요.
- 리뷰 수가 적으면 반드시 caveat에 기록하세요.
- 서로 다른 출처에서 숫자가 다르면 conflicts에 기록하세요.
- 연봉처럼 확인할 수 없는 정보는 추측하지 말고 unknowns에 기록하세요.
- 출처 URL을 반드시 기록하세요.

검색 결과:

{json.dumps(search_results, ensure_ascii=False)}
"""
    )

    return {
        "workplace_evidence": result.model_dump()
    }
