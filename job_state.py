from typing import TypedDict, NotRequired


class JobState(TypedDict):
    # 최초 입력
    job_text: str
    candidate_profile: dict

    # 각 LangGraph node가 차례로 채우는 값
    job_info: NotRequired[dict]
    company_info: NotRequired[dict]
    fde_si_analysis: NotRequired[dict]
    workplace_evidence: NotRequired[dict]
    workplace_analysis: NotRequired[dict]
    final_job_fit: NotRequired[dict]
