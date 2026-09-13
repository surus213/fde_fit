from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model

from job_state import JobState

load_dotenv()


class JobPosting(BaseModel):
    company: str = Field(description="회사명")
    position: str = Field(description="채용 포지션")
    responsibilities: list[str] = Field(description="주요 업무")
    requirements: list[str] = Field(description="자격 요건")
    preferred: list[str] = Field(description="우대 사항")
    tech_stack: list[str] = Field(description="기술 스택")


llm = init_chat_model(
    "openai:gpt-5-mini",
    temperature=0
)

structured_llm = llm.with_structured_output(JobPosting)


def parse_job(state: JobState) -> dict:
    """채용공고 원문을 구조화된 job_info로 변환하는 LangGraph node."""
    job_text = state["job_text"]

    result = structured_llm.invoke(
        f"""
        다음 채용공고를 분석하세요.

        {job_text}
        """
    )

    return {
        "job_info": result.model_dump()
    }
