from __future__ import annotations

import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


WANTED_BASE_URL = "https://www.wanted.co.kr"

FDE_TITLE_PATTERNS = (
    re.compile(r"\bfde\b", re.IGNORECASE),
    re.compile(
        r"\bforward[\s-]+deploy(?:ed|ment)[\s-]+engineer(?:ing)?\b",
        re.IGNORECASE,
    ),
    re.compile(r"프론트라인\s*엔지니어", re.IGNORECASE),
)


class WantedAPIError(RuntimeError):
    """원티드 공개 API를 정상적으로 읽을 수 없을 때 발생합니다."""


class WantedCompanyNotFound(WantedAPIError):
    """검색 결과에서 요청한 회사를 결정할 수 없을 때 발생합니다."""


@dataclass(frozen=True)
class WantedCompany:
    id: int
    name: str
    active_job_count: int


@dataclass(frozen=True)
class WantedJob:
    id: int
    company_id: int
    company_name: str
    title: str
    status: str
    due_time: str | None
    location: str
    annual_from: int | None
    annual_to: int | None
    employment_type: str | None

    @property
    def source_url(self) -> str:
        return f"{WANTED_BASE_URL}/wd/{self.id}"


def _get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if params:
        path = f"{path}?{urlencode(params, doseq=True)}"

    request = Request(
        f"{WANTED_BASE_URL}{path}",
        headers={
            "Accept": "application/json",
            "User-Agent": "fde-fit/0.1 (Wanted job analysis)",
        },
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise WantedAPIError(
            f"원티드 요청이 실패했습니다 (HTTP {exc.code})."
        ) from exc
    except URLError as exc:
        raise WantedAPIError(
            f"원티드에 연결할 수 없습니다: {exc.reason}"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WantedAPIError("원티드 응답이 올바른 JSON 형식이 아닙니다.") from exc

    if not isinstance(payload, dict):
        raise WantedAPIError("원티드 응답 구조가 예상과 다릅니다.")

    return payload


def _normalize_company_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"(?:주식회사|유한회사|\(주\)|㈜)", "", normalized)
    return re.sub(r"[^0-9a-z가-힣]", "", normalized)


def search_companies(query: str, limit: int = 20) -> list[WantedCompany]:
    """원티드에서 회사명 후보를 검색합니다."""
    payload = _get_json(
        "/api/chaos/search/v2/company",
        {"query": query, "limit": limit},
    )
    items = payload.get("data")
    if not isinstance(items, list):
        raise WantedAPIError("원티드 회사 검색 응답 구조가 예상과 다릅니다.")

    companies: list[WantedCompany] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            companies.append(
                WantedCompany(
                    id=int(item["id"]),
                    name=str(item["name"]),
                    active_job_count=int(item.get("confirmed_position_count") or 0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return companies


def select_company(query: str, companies: list[WantedCompany]) -> WantedCompany:
    """검색어와 가장 가까운 회사를 결정합니다."""
    if not companies:
        raise WantedCompanyNotFound(f"원티드에서 '{query}' 회사를 찾지 못했습니다.")

    target = _normalize_company_name(query)
    if not target:
        raise WantedCompanyNotFound("검색할 회사명을 입력해주세요.")

    def score(company: WantedCompany) -> tuple[float, int]:
        candidate = _normalize_company_name(company.name)
        if candidate == target:
            similarity = 1.0
        elif target in candidate or candidate in target:
            similarity = 0.9
        else:
            similarity = SequenceMatcher(None, target, candidate).ratio()
        return similarity, company.active_job_count

    selected = max(companies, key=score)
    similarity, _ = score(selected)
    if similarity < 0.5:
        candidates = ", ".join(company.name for company in companies[:5])
        raise WantedCompanyNotFound(
            f"'{query}'와 일치하는 회사를 결정하지 못했습니다. "
            f"검색 후보: {candidates}"
        )

    return selected


def get_company_jobs(company: WantedCompany) -> list[WantedJob]:
    """회사의 현재 활성 채용공고를 가져옵니다."""
    payload = _get_json(f"/api/chaos/companies/v1/{company.id}/jobs")
    items = payload.get("jobs")
    if not isinstance(items, list):
        raise WantedAPIError("원티드 회사별 공고 응답 구조가 예상과 다릅니다.")

    jobs: list[WantedJob] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("hidden") or item.get("status") != "active":
            continue

        address = item.get("address") or {}
        location = address.get("full_location") or ""
        try:
            jobs.append(
                WantedJob(
                    id=int(item["id"]),
                    company_id=company.id,
                    company_name=company.name,
                    title=str(item["name"]),
                    status=str(item["status"]),
                    due_time=item.get("due_time"),
                    location=str(location),
                    annual_from=item.get("annual_from"),
                    annual_to=item.get("annual_to"),
                    employment_type=item.get("employment_type"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return jobs


def is_fde_job_title(title: str) -> bool:
    """공고 제목이 FDE 직무를 명시하는지 확인합니다."""
    return any(pattern.search(title) for pattern in FDE_TITLE_PATTERNS)


def find_company_jobs(
    company_query: str,
) -> tuple[WantedCompany, list[WantedJob]]:
    """회사를 찾고 현재 활성 공고를 모두 반환합니다."""
    company = select_company(company_query, search_companies(company_query))
    all_jobs = get_company_jobs(company)
    return company, all_jobs


def get_job_detail(job_id: int) -> dict[str, Any]:
    """원티드 공고 상세 데이터를 가져옵니다."""
    payload = _get_json(f"/api/v4/jobs/{job_id}")
    job = payload.get("job")
    if not isinstance(job, dict):
        raise WantedAPIError("원티드 공고 상세 응답 구조가 예상과 다릅니다.")
    return job


def get_job_details(jobs: list[WantedJob]) -> dict[int, dict[str, Any]]:
    """활성 공고 상세를 병렬로 모두 가져옵니다."""
    if not jobs:
        return {}

    details: dict[int, dict[str, Any]] = {}
    errors: list[str] = []
    worker_count = min(8, len(jobs))

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_job = {
            executor.submit(get_job_detail, job.id): job
            for job in jobs
        }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            try:
                details[job.id] = future.result()
            except WantedAPIError as exc:
                errors.append(f"{job.id} ({job.title}): {exc}")

    if errors:
        summary = "; ".join(errors[:3])
        if len(errors) > 3:
            summary += f"; 외 {len(errors) - 3}개"
        raise WantedAPIError(
            "활성 공고 전체를 분석할 수 없어 FDE 판정을 중단했습니다. " + summary
        )

    return details


def _experience_text(annual_from: Any, annual_to: Any) -> str:
    if annual_from in (None, 0) and annual_to in (None, 100):
        return "경력 무관"
    if annual_to in (None, 100):
        return f"경력 {annual_from or 0}년 이상"
    return f"경력 {annual_from or 0}~{annual_to}년"


def format_job_text(job: WantedJob, detail_payload: dict[str, Any]) -> str:
    """원티드 상세 응답을 기존 parse_job 입력용 텍스트로 변환합니다."""
    detail = detail_payload.get("detail") or {}
    address = detail_payload.get("address") or {}
    skills = [
        str(item.get("title"))
        for item in detail_payload.get("skill_tags") or []
        if isinstance(item, dict) and item.get("title")
    ]

    employment_names = {
        "regular": "정규직",
        "contract": "계약직",
        "intern": "인턴",
    }
    employment = employment_names.get(
        job.employment_type or "",
        job.employment_type or "확인 필요",
    )
    due_time = job.due_time or detail_payload.get("due_time") or "상시채용"
    location = (
        address.get("full_location")
        or job.location
        or address.get("location")
        or "확인 필요"
    )

    sections = [
        f"공고 제목: {job.title}",
        f"회사: {job.company_name}",
        f"원티드 공고 URL: {job.source_url}",
        f"고용 형태: {employment}",
        f"요구 경력: {_experience_text(job.annual_from, job.annual_to)}",
        f"마감일: {due_time}",
        f"근무지: {location}",
    ]
    if skills:
        sections.append(f"기술 스택: {', '.join(skills)}")

    content_sections = (
        ("포지션 상세", "intro"),
        ("주요 업무", "main_tasks"),
        ("자격 요건", "requirements"),
        ("우대 사항", "preferred_points"),
        ("혜택 및 복지", "benefits"),
    )
    for heading, key in content_sections:
        content = detail.get(key)
        if content:
            sections.append(f"\n[{heading}]\n{content}")

    return "\n".join(sections).strip() + "\n"
