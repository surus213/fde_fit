import json

from dotenv import load_dotenv
from pydantic import BaseModel
from langchain.chat_models import init_chat_model
from langchain_tavily import TavilySearch

from job_state import JobState

load_dotenv()


class Evidence(BaseModel):
    claim: str
    source_title: str
    source_url: str


class CompanyInfo(BaseModel):
    company: str
    products: list[str]
    customers_and_cases: list[str]
    business_model: str
    recent_activity: list[str]
    evidence: list[Evidence]


search = TavilySearch(
    max_results=5,
    search_depth="advanced"
)

llm = init_chat_model(
    "openai:gpt-5-mini",
    temperature=0
)

structured_llm = llm.with_structured_output(CompanyInfo)


def research_company(state: JobState) -> dict:
    """job_info의 회사명을 이용해 웹 조사 후 company_info를 만드는 LangGraph node."""
    company = state["job_info"]["company"]

    queries = [
        f"{company} 회사 제품 서비스 사업 모델",
        f"{company} 고객 사례 AI AX",
        f"{company} 최근 뉴스 투자 사업"
    ]

    search_results = []

    for query in queries:
        result = search.invoke({"query": query})
        search_results.append(result)

    company_info = structured_llm.invoke(
        f"""
        다음은 {company}에 대한 웹 검색 결과입니다.

        검색 결과에 명시된 사실만 사용하세요.
        추측하지 마세요.

        각 주요 주장에는 반드시 출처 URL을 evidence에 기록하세요.

        검색 결과:
        {json.dumps(search_results, ensure_ascii=False)}
        """
    )

    return {
        "company_info": company_info.model_dump()
    }
