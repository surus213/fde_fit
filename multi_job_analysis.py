from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable

from analyze_fde_si import analyze_fde_si
from analyze_workplace import analyze_workplace
from classify_fde import FDEJobAssessment
from final_job_fit import final_job_fit
from parse_job import parse_job
from research_company import research_company
from research_workplace import research_workplace
from wanted_jobs import WantedJob, format_job_text


ProgressCallback = Callable[[str], None]


def _emit(progress: ProgressCallback | None, message: str) -> None:
    if progress:
        progress(message)


def _number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def run_fde_job_comparison(
    company_name: str,
    jobs: list[WantedJob],
    job_details: dict[int, dict[str, Any]],
    assessments: list[FDEJobAssessment],
    candidate_profile: dict[str, Any],
    *,
    total_active_jobs: int,
    total_fde_like_jobs: int,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """FDE형 공고를 모두 분석하고 순위가 포함된 비교 결과를 만듭니다."""
    if not jobs:
        raise ValueError("비교 분석할 FDE형 공고가 없습니다.")

    assessment_by_id = {assessment.job_id: assessment for assessment in assessments}
    missing_assessments = [job.id for job in jobs if job.id not in assessment_by_id]
    missing_details = [job.id for job in jobs if job.id not in job_details]
    if missing_assessments or missing_details:
        raise ValueError(
            "공고 비교 입력이 완전하지 않습니다. "
            f"분류 누락={missing_assessments}, 상세 누락={missing_details}"
        )

    job_text_by_id = {
        job.id: format_job_text(job, job_details[job.id])
        for job in jobs
    }
    parsed_by_id: dict[int, dict[str, Any]] = {}
    worker_count = min(5, len(jobs))

    _emit(progress, f"FDE형 공고 {len(jobs)}개의 채용 정보를 병렬 구조화합니다.")
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_job = {
            executor.submit(
                parse_job,
                {
                    "job_text": job_text_by_id[job.id],
                    "candidate_profile": candidate_profile,
                },
            ): job
            for job in jobs
        }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            parsed = dict(future.result()["job_info"])
            parsed["company"] = company_name
            parsed["position"] = job.title
            parsed_by_id[job.id] = parsed
            _emit(progress, f"채용공고 구조화 완료: {job.title}")

    shared_state = {
        "job_info": {"company": company_name},
        "candidate_profile": candidate_profile,
    }
    _emit(progress, "회사 정보와 직장 환경을 한 번씩 병렬 조사합니다.")
    with ThreadPoolExecutor(max_workers=2) as executor:
        company_future = executor.submit(research_company, shared_state)
        workplace_future = executor.submit(research_workplace, shared_state)
        company_info = company_future.result()["company_info"]
        workplace_evidence = workplace_future.result()["workplace_evidence"]

    _emit(progress, "공고별 FDE/SI 분석과 공통 직장 평가를 병렬 실행합니다.")
    fde_si_by_id: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(jobs) + 1)) as executor:
        workplace_future = executor.submit(
            analyze_workplace,
            {"workplace_evidence": workplace_evidence},
        )
        future_to_job = {
            executor.submit(
                analyze_fde_si,
                {
                    "job_info": parsed_by_id[job.id],
                    "company_info": company_info,
                },
            ): job
            for job in jobs
        }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            fde_si_by_id[job.id] = future.result()["fde_si_analysis"]
            _emit(progress, f"FDE/SI 분석 완료: {job.title}")
        workplace_analysis = workplace_future.result()["workplace_analysis"]

    _emit(progress, f"{len(jobs)}개 공고의 최종 적합도를 병렬 평가합니다.")
    final_by_id: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_job = {
            executor.submit(
                final_job_fit,
                {
                    "job_info": parsed_by_id[job.id],
                    "company_info": company_info,
                    "fde_si_analysis": fde_si_by_id[job.id],
                    "workplace_analysis": workplace_analysis,
                    "candidate_profile": candidate_profile,
                },
            ): job
            for job in jobs
        }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            final_by_id[job.id] = future.result()["final_job_fit"]
            _emit(progress, f"최종 적합도 평가 완료: {job.title}")

    job_results = [
        {
            "job_id": job.id,
            "position": job.title,
            "source_url": job.source_url,
            "location": job.location,
            "employment_type": job.employment_type,
            "fde_classification": assessment_by_id[job.id].model_dump(),
            "job_info": parsed_by_id[job.id],
            "fde_si_analysis": fde_si_by_id[job.id],
            "final_job_fit": final_by_id[job.id],
        }
        for job in jobs
    ]
    job_results.sort(
        key=lambda item: (
            _number(item["final_job_fit"].get("fit_score")),
            _number(item["final_job_fit"].get("confidence")),
            _number(item["fde_si_analysis"].get("fde_score")),
            _number(item["fde_classification"].get("fde_score")),
        ),
        reverse=True,
    )
    ranked_jobs = [
        {"rank": rank, **item}
        for rank, item in enumerate(job_results, start=1)
    ]

    return {
        "company": company_name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total_active_jobs": total_active_jobs,
        "total_fde_like_jobs": total_fde_like_jobs,
        "analyzed_job_count": len(jobs),
        "ranking_method": (
            "fit_score 내림차순, confidence 내림차순, "
            "fde_si_analysis.fde_score 내림차순, "
            "fde_classification.fde_score 내림차순"
        ),
        "company_info": company_info,
        "workplace_evidence": workplace_evidence,
        "workplace_analysis": workplace_analysis,
        "rankings": ranked_jobs,
    }
