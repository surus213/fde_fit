from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from search_jobs import (
    WANTED_BASE_URL,
    WantedAPIError,
    WantedPositionSearchResult,
    position_matches_query,
    search_positions,
)


BASE_DIR = Path(__file__).resolve().parent
ProgressCallback = Callable[[str], None]


def read_job_names(path: Path) -> list[str]:
    """한 줄당 하나의 직무명을 읽고 빈 줄과 중복을 제거합니다."""
    names: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        name = raw_line.strip()
        if not name or name.startswith("#"):
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)

    if not names:
        raise ValueError(f"직무명 파일이 비어 있습니다: {path}")
    return names


def _job_record(position: WantedPositionSearchResult) -> dict[str, Any]:
    return {
        "job_id": position.id,
        "title": position.title,
        "source_url": position.source_url,
        "annual_from": position.annual_from,
        "annual_to": position.annual_to,
        "employment_type": position.employment_type,
        "is_outlink": position.is_outlink,
        "matched_job_names": [],
    }


def find_companies_for_job_names(
    job_names: list[str],
    *,
    max_results_per_query: int = 100,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """직무명별 포지션을 찾고 실제 제목이 일치하는 공고를 회사별로 묶습니다."""
    if not job_names:
        raise ValueError("검색할 직무명이 없습니다.")
    if max_results_per_query < 1:
        raise ValueError("max_results_per_query는 1 이상이어야 합니다.")

    results_by_name: dict[str, list[WantedPositionSearchResult]] = {}
    worker_count = min(4, len(job_names))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_name = {
            executor.submit(
                search_positions,
                job_name,
                max_results=max_results_per_query,
            ): job_name
            for job_name in job_names
        }
        for future in as_completed(future_to_name):
            job_name = future_to_name[future]
            results_by_name[job_name] = future.result()
            if progress:
                progress(
                    f"{job_name}: 검색 결과 "
                    f"{len(results_by_name[job_name])}개 확인"
                )

    jobs_by_id: dict[int, dict[str, Any]] = {}
    job_company_ids: dict[int, int] = {}
    company_names: dict[int, str] = {}
    search_summary: list[dict[str, Any]] = []

    for job_name in job_names:
        positions = results_by_name[job_name]
        matched_count = 0
        for position in positions:
            if not position_matches_query(job_name, position.title):
                continue

            matched_count += 1
            company_names[position.company_id] = position.company_name
            job_company_ids[position.id] = position.company_id
            job = jobs_by_id.setdefault(position.id, _job_record(position))
            job["matched_job_names"].append(job_name)

        search_summary.append(
            {
                "job_name": job_name,
                "scanned_result_count": len(positions),
                "matched_job_count": matched_count,
            }
        )

    jobs_by_company: dict[int, list[dict[str, Any]]] = {}
    for job_id, job in jobs_by_id.items():
        company_id = job_company_ids[job_id]
        jobs_by_company.setdefault(company_id, []).append(job)

    companies = []
    for company_id, jobs in jobs_by_company.items():
        jobs.sort(key=lambda item: (item["title"].casefold(), item["job_id"]))
        matched_job_names = [
            job_name
            for job_name in job_names
            if any(job_name in job["matched_job_names"] for job in jobs)
        ]
        companies.append(
            {
                "company_id": company_id,
                "company_name": company_names[company_id],
                "matched_job_count": len(jobs),
                "matched_job_names": matched_job_names,
                "jobs": jobs,
            }
        )

    companies.sort(
        key=lambda item: (
            -item["matched_job_count"],
            item["company_name"].casefold(),
            item["company_id"],
        )
    )

    return {
        "source": "Wanted",
        "source_url": WANTED_BASE_URL,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "max_results_per_query": max_results_per_query,
        "input_job_names": job_names,
        "search_summary": search_summary,
        "matched_company_count": len(companies),
        "matched_job_count": len(jobs_by_id),
        "companies": companies,
    }


def write_company_search_result(output_path: Path, result: dict[str, Any]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="직무명 목록으로 원티드 공고를 검색하고 회사를 JSON으로 저장합니다."
    )
    parser.add_argument(
        "--job-name-file",
        type=Path,
        default=BASE_DIR / "job_name.txt",
        help="한 줄당 하나의 직무명이 들어 있는 파일",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE_DIR / "wanted_job_companies.json",
        help="검색 결과 JSON 파일",
    )
    parser.add_argument(
        "--max-results-per-query",
        type=int,
        default=100,
        help="직무명 하나당 확인할 원티드 검색 결과 최대 개수 (기본값: 100)",
    )
    args = parser.parse_args(argv)
    if args.max_results_per_query < 1:
        parser.error("--max-results-per-query는 1 이상이어야 합니다.")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        job_names = read_job_names(args.job_name_file)
        print(f"직무명 {len(job_names)}개를 읽었습니다.")
        result = find_companies_for_job_names(
            job_names,
            max_results_per_query=args.max_results_per_query,
            progress=lambda message: print(f"- {message}", flush=True),
        )
        output_path = write_company_search_result(args.output, result)
    except FileNotFoundError:
        print(f"직무명 파일을 찾지 못했습니다: {args.job_name_file}", file=sys.stderr)
        return 2
    except (ValueError, WantedAPIError) as exc:
        print(f"원티드 직무 검색 실패: {exc}", file=sys.stderr)
        return 2

    print(
        f"회사 {result['matched_company_count']}개, "
        f"공고 {result['matched_job_count']}개를 찾았습니다."
    )
    print(f"JSON 저장: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
