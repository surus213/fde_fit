from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from search_jobs import WantedJob, is_fde_job_title


load_dotenv()


class FDEClassificationError(RuntimeError):
    """활성 공고 전체에 대한 FDE 분류 결과를 신뢰할 수 없을 때 발생합니다."""


class FDEJobAssessment(BaseModel):
    job_id: int
    fde_score: int = Field(
        ge=0,
        le=10,
        description="공고 업무가 FDE 역할에 가까운 정도",
    )
    confidence: float = Field(ge=0, le=1)
    is_fde_like: bool
    reasons: list[str]
    missing_signals: list[str]


class FDEAssessmentBatch(BaseModel):
    assessments: list[FDEJobAssessment]


llm = init_chat_model(
    "openai:gpt-5-mini",
    temperature=0,
)

classifier = llm.with_structured_output(FDEAssessmentBatch)


def _chunks(items: list[WantedJob], size: int) -> Iterable[list[WantedJob]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _clip(value: Any, limit: int = 6000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[이하 생략]"


def _classification_input(
    job: WantedJob,
    detail_payload: dict[str, Any],
) -> dict[str, Any]:
    detail = detail_payload.get("detail") or {}
    return {
        "job_id": job.id,
        "position": job.title,
        "intro": _clip(detail.get("intro"), 2500),
        "main_tasks": _clip(detail.get("main_tasks")),
        "requirements": _clip(detail.get("requirements")),
        "preferred_points": _clip(detail.get("preferred_points"), 3500),
    }


def classify_fde_jobs(
    jobs: list[WantedJob],
    job_details: dict[int, dict[str, Any]],
    batch_size: int = 5,
) -> list[FDEJobAssessment]:
    """모든 활성 공고의 실제 업무를 읽고 FDE 유사도를 판정합니다."""
    if not jobs:
        return []
    if batch_size < 1:
        raise ValueError("batch_size는 1 이상이어야 합니다.")

    missing_details = [job.id for job in jobs if job.id not in job_details]
    if missing_details:
        ids = ", ".join(str(job_id) for job_id in missing_details)
        raise FDEClassificationError(f"상세 내용이 없는 공고가 있습니다: {ids}")

    assessments_by_id: dict[int, FDEJobAssessment] = {}
    for batch in _chunks(jobs, batch_size):
        postings = [
            _classification_input(job, job_details[job.id])
            for job in batch
        ]
        try:
            result = classifier.invoke(
                f"""
당신은 채용공고의 실제 업무가 Forward Deployed Engineer(FDE)에 가까운지
판정하는 채용 직무 분석가입니다.

아래 모든 공고를 빠짐없이 각각 평가하세요. 직무명만 보지 말고 main_tasks와
requirements에 명시된 실제 업무를 가장 중요하게 보세요.

FDE의 핵심 신호:
- 고객 또는 고객사 현장과 직접 긴밀하게 일함
- 모호한 고객 문제를 기술 문제와 요구사항으로 직접 정의함
- 엔지니어가 코드를 작성하고 솔루션을 설계·구현함
- PoC부터 배포, 운영 안정화까지 end-to-end로 책임짐
- 현장에서 얻은 피드백을 공통 제품·플랫폼 개선으로 환원함
- 제품팀과 고객 사이의 기술적 가교 역할을 함

FDE로 판단하지 않아야 하는 경우:
- 고객 접점 없는 일반적인 내부 제품 개발 또는 백엔드/프론트엔드 개발
- 코딩과 구현 책임이 없는 영업, CSM, 컨설팅, PM, 기술지원
- 정해진 요구사항만 구현하거나 유지보수하는 일반 개발 업무
- 회사 소개에 고객·AI·B2B가 언급될 뿐 해당 포지션 업무에 FDE 신호가 없음

점수 기준:
- 0~2: FDE와 무관
- 3~5: 일부 유사하지만 FDE라고 보기 어려움
- 6~7: 직무명이 달라도 실제 업무가 FDE에 가까움
- 8~10: 명백한 FDE 역할

is_fde_like는 fde_score가 6 이상일 때만 true로 판단하세요.
reasons에는 공고에서 확인되는 구체적인 근거만 기록하고, 추측하지 마세요.
missing_signals에는 FDE 판단에 부족하거나 확인되지 않는 핵심 요소를 기록하세요.
입력된 job_id를 그대로 사용하고 모든 job_id를 정확히 한 번씩 반환하세요.

[활성 채용공고]
{json.dumps(postings, ensure_ascii=False)}
"""
            )
        except Exception as exc:
            raise FDEClassificationError(
                "활성 공고의 FDE 유사도 분석에 실패했습니다."
            ) from exc

        expected_ids = {job.id for job in batch}
        returned_ids = [assessment.job_id for assessment in result.assessments]
        if set(returned_ids) != expected_ids or len(returned_ids) != len(expected_ids):
            raise FDEClassificationError(
                "FDE 분류 결과의 공고 ID가 입력과 일치하지 않습니다."
            )

        for job in batch:
            assessment = next(
                item for item in result.assessments if item.job_id == job.id
            )
            explicit_title = is_fde_job_title(job.title)
            is_fde_like = explicit_title or assessment.fde_score >= 6
            reasons = list(assessment.reasons)
            if explicit_title and not any("직무명" in reason for reason in reasons):
                reasons.insert(0, "공고 직무명이 FDE를 명시합니다.")

            assessments_by_id[job.id] = assessment.model_copy(
                update={
                    "fde_score": max(assessment.fde_score, 8)
                    if explicit_title
                    else assessment.fde_score,
                    "is_fde_like": is_fde_like,
                    "reasons": reasons,
                }
            )

    return [assessments_by_id[job.id] for job in jobs]
