import argparse
import json
import sys
import webbrowser
from pathlib import Path
from typing import Sequence

from langgraph.graph import StateGraph, START, END

from job_state import JobState
from parse_job import parse_job
from research_company import research_company
from analyze_fde_si import analyze_fde_si
from research_workplace import research_workplace
from analyze_workplace import analyze_workplace
from final_job_fit import final_job_fit
from classify_fde import (
    FDEClassificationError,
    FDEJobAssessment,
    classify_fde_jobs,
)
from comparison_report import write_comparison_reports
from html_report import write_reports
from multi_job_analysis import run_fde_job_comparison
from wanted_jobs import (
    WantedAPIError,
    WantedJob,
    find_company_jobs,
    get_job_details,
)


BASE_DIR = Path(__file__).resolve().parent

STEP_NAMES = {
    "parse_job": "채용공고 분석",
    "research_company": "회사·사업 조사",
    "analyze_fde_si": "FDE / SI 분석",
    "research_workplace": "연봉·워라밸·조직문화 조사",
    "analyze_workplace": "직장 평가",
    "final_job_fit": "최종 직무 적합도 평가",
}


builder = StateGraph(JobState)

builder.add_node("parse_job", parse_job)
builder.add_node("research_company", research_company)
builder.add_node("analyze_fde_si", analyze_fde_si)
builder.add_node("research_workplace", research_workplace)
builder.add_node("analyze_workplace", analyze_workplace)
builder.add_node("final_job_fit", final_job_fit)

builder.add_edge(START, "parse_job")
builder.add_edge("parse_job", "research_company")
builder.add_edge("parse_job", "research_workplace")
builder.add_edge("research_company", "analyze_fde_si")
builder.add_edge("research_workplace", "analyze_workplace")
builder.add_edge(
    ["analyze_fde_si", "analyze_workplace"],
    "final_job_fit"
)
builder.add_edge("final_job_fit", END)

graph = builder.compile()


def run_workflow(job_text: str, candidate_profile: dict) -> dict:
    """채용공고와 후보자 프로필로 그래프를 실행하고 누적 state를 반환합니다."""
    import time

    initial_state = {
        "job_text": job_text,
        "candidate_profile": candidate_profile
    }
    total_steps = len(STEP_NAMES)
    current_state = dict(initial_state)

    print("\n🚀 채용공고 분석을 시작합니다.\n", flush=True)

    start_time = time.perf_counter()
    last_time = start_time
    completed = 0

    for event in graph.stream(
        initial_state,
        stream_mode="updates"
    ):
        for node_name, update in event.items():

            # 각 노드가 반환한 값을 현재 state에 누적
            if update:
                current_state.update(update)

            completed += 1

            now = time.perf_counter()
            node_time = now - last_time
            last_time = now

            label = STEP_NAMES.get(node_name, node_name)

            print(
                f"✅ [{completed}/{total_steps}] "
                f"{label} 완료 "
                f"({node_time:.1f}초)",
                flush=True
            )

    total_time = time.perf_counter() - start_time

    print(
        f"\n🎉 전체 분석 완료! "
        f"({total_time:.1f}초)\n",
        flush=True
    )

    return current_state


def _find_requested_job(jobs: list[WantedJob], requested_job_id: int) -> WantedJob:
    for job in jobs:
        if job.id == requested_job_id:
            return job
    available = ", ".join(str(job.id) for job in jobs)
    raise ValueError(
        f"--job-id {requested_job_id}는 검색된 FDE형 공고가 아닙니다. "
        f"선택 가능한 ID: {available}"
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="원티드 FDE 공고 또는 로컬 job.txt의 직무 적합도를 분석합니다."
    )
    parser.add_argument(
        "--company",
        help="원티드에서 검색할 회사명. 생략하면 --job-file을 분석합니다.",
    )
    parser.add_argument(
        "--job-id",
        type=int,
        help="전체 FDE형 공고 대신 하나만 분석할 원티드 공고 ID",
    )
    parser.add_argument(
        "--job-file",
        type=Path,
        default=BASE_DIR / "job.txt",
        help="--company를 생략했을 때 분석할 공고 텍스트 파일",
    )
    parser.add_argument(
        "--candidate-profile",
        type=Path,
        default=BASE_DIR / "candidate_profile.json",
        help="후보자 프로필 JSON 파일",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=BASE_DIR,
        help="회사명 기반 JSON/HTML 보고서를 저장할 디렉터리",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        dest="open_report",
        help="생성된 HTML 보고서를 기본 브라우저에서 엽니다.",
    )
    args = parser.parse_args(argv)
    if args.job_id is not None and not args.company:
        parser.error("--job-id는 --company와 함께 사용해야 합니다.")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        candidate_profile = json.loads(
            args.candidate_profile.read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        print(
            f"후보자 프로필 파일을 찾지 못했습니다: {args.candidate_profile}",
            file=sys.stderr,
        )
        return 2
    except json.JSONDecodeError as exc:
        print(f"후보자 프로필 JSON이 올바르지 않습니다: {exc}", file=sys.stderr)
        return 2

    if args.company:
        try:
            company, all_jobs = find_company_jobs(args.company)
            print(f"\n🔎 {company.name}의 활성 공고 {len(all_jobs)}개를 확인했습니다.")
            if not all_jobs:
                print("현재 활성 채용공고가 없습니다.")
                return 0

            print("모든 활성 공고의 상세 업무를 읽고 FDE 유사도를 분석합니다.")
            job_details = get_job_details(all_jobs)
            assessments = classify_fde_jobs(all_jobs, job_details)
        except (WantedAPIError, FDEClassificationError) as exc:
            print(f"공고 탐색 또는 FDE 분류 실패: {exc}", file=sys.stderr)
            return 2

        jobs_by_id = {job.id: job for job in all_jobs}
        print(f"활성 공고 {len(assessments)}개의 FDE 유사도 판정을 완료했습니다.")
        fde_assessments = sorted(
            (
                assessment
                for assessment in assessments
                if assessment.is_fde_like
            ),
            key=lambda assessment: (
                assessment.fde_score,
                assessment.confidence,
            ),
            reverse=True,
        )
        fde_jobs = [jobs_by_id[assessment.job_id] for assessment in fde_assessments]
        if not fde_assessments:
            print("실제 업무가 FDE에 가까운 공고가 없습니다.")
            return 0

        print(f"FDE형 공고 {len(fde_jobs)}개를 찾았습니다:")
        assessments_by_id: dict[int, FDEJobAssessment] = {
            assessment.job_id: assessment for assessment in fde_assessments
        }
        for job in fde_jobs:
            assessment = assessments_by_id[job.id]
            print(
                f"  - {job.title} · FDE 유사도 {assessment.fde_score}/10 "
                f"(신뢰도 {assessment.confidence:.0%})"
            )
            if assessment.reasons:
                print(f"    근거: {'; '.join(assessment.reasons)}")
            print(f"    {job.source_url}")

        jobs_to_analyze = fde_jobs
        assessments_to_analyze = fde_assessments
        if args.job_id is not None:
            try:
                requested_job = _find_requested_job(fde_jobs, args.job_id)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            jobs_to_analyze = [requested_job]
            assessments_to_analyze = [assessments_by_id[requested_job.id]]

        print(
            f"\n🚀 FDE형 공고 {len(jobs_to_analyze)}개의 비교 분석을 시작합니다.\n",
            flush=True,
        )
        comparison = run_fde_job_comparison(
            company.name,
            jobs_to_analyze,
            job_details,
            assessments_to_analyze,
            candidate_profile,
            total_active_jobs=len(all_jobs),
            total_fde_like_jobs=len(fde_jobs),
            progress=lambda message: print(f"✅ {message}", flush=True),
        )

        print("\n===== FDE형 공고 종합 순위 =====\n")
        for item in comparison["rankings"]:
            final = item["final_job_fit"]
            fde_si = item["fde_si_analysis"]
            print(
                f"{item['rank']}. {item['position']} · "
                f"적합도 {final['fit_score']}/10 · "
                f"신뢰도 {final['confidence']:.0%} · "
                f"FDE/SI {fde_si['fde_score']}/{fde_si['si_score']} · "
                f"{final['recommendation']}"
            )

        print("\n공고별 결과 파일:")
        for item in comparison["rankings"]:
            json_path, html_path = write_reports(
                args.output_dir,
                company.name,
                item["final_job_fit"],
                item["position"],
                item["source_url"],
                job_id=item["job_id"],
            )
            print(f"  - {json_path}")
            print(f"  - {html_path}")

        comparison_json, comparison_html = write_comparison_reports(
            args.output_dir,
            comparison,
        )
        print(f"\n비교 JSON 저장: {comparison_json}")
        print(f"비교 HTML 저장: {comparison_html}")

        if args.open_report:
            webbrowser.open(comparison_html.resolve().as_uri())

        return 0

    try:
        job_text = args.job_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"채용공고 파일을 찾지 못했습니다: {args.job_file}", file=sys.stderr)
        return 2

    current_state = run_workflow(job_text, candidate_profile)
    final_result = current_state["final_job_fit"]
    job_info = current_state.get("job_info", {})
    company_name = job_info.get("company") or "company"
    position = job_info.get("position") or ""

    print("===== 최종 결과 =====\n")
    print(json.dumps(final_result, ensure_ascii=False, indent=2))

    json_path, html_path = write_reports(
        args.output_dir,
        company_name,
        final_result,
        position,
        "",
    )
    print(f"\nJSON 저장: {json_path}")
    print(f"HTML 저장: {html_path}")

    if args.open_report:
        webbrowser.open(html_path.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
