"""여러 채용 플랫폼의 공고 검색과 원티드 상세 조회 기능을 제공합니다."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from dotenv import load_dotenv


WANTED_BASE_URL = "https://www.wanted.co.kr"
BASE_DIR = Path(__file__).resolve().parent

PLATFORM_ORDER = ("wanted", "groupby", "saramin", "linkedin", "jobplanet")
PLATFORM_NAMES = {
    "wanted": "원티드",
    "groupby": "그룹바이",
    "saramin": "사람인",
    "linkedin": "링크드인",
    "jobplanet": "잡플래닛",
}
WEB_PLATFORM_DOMAINS = {
    "groupby": "groupby.kr",
    "saramin": "saramin.co.kr",
    "linkedin": "linkedin.com",
    "jobplanet": "jobplanet.co.kr",
}

ProgressCallback = Callable[[str], None]

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


class JobPlatformSearchError(RuntimeError):
    """채용 플랫폼 또는 도메인 제한 검색이 실패했을 때 발생합니다."""


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


@dataclass(frozen=True)
class WantedPositionSearchResult:
    id: int
    company_id: int
    company_name: str
    title: str
    annual_from: int | None
    annual_to: int | None
    employment_type: str | None
    is_outlink: bool

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


def _normalize_position_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    words = re.sub(r"[^0-9a-z가-힣]+", " ", normalized).split()
    aliases = {
        "solutions": "solution",
        "developers": "developer",
        "engineers": "engineer",
    }
    return " ".join(aliases.get(word, word) for word in words)


def position_match_score(query: str, title: str) -> float:
    """직무명과 공고 제목의 유사도를 0~1 사이 값으로 계산합니다."""
    normalized_query = _normalize_position_name(query)
    normalized_title = _normalize_position_name(title)
    if not normalized_query or not normalized_title:
        return 0.0

    if normalized_query in normalized_title:
        return 1.0

    if normalized_query == "forward deployed engineer" and re.search(
        r"\bfde\b", title, re.IGNORECASE
    ):
        return 0.98
    if normalized_query == "forward deployed software engineer" and re.search(
        r"\bfdse\b", title, re.IGNORECASE
    ):
        return 0.98

    query_words = normalized_query.split()
    title_words = set(normalized_title.split())
    coverage = sum(word in title_words for word in query_words) / len(query_words)
    if len(query_words) >= 3 and coverage >= 2 / 3:
        return round(0.65 + 0.2 * coverage, 4)
    if len(query_words) == 2 and coverage == 1:
        return 0.9

    query_length = len(query_words)
    title_tokens = normalized_title.split()
    window_scores = [
        SequenceMatcher(
            None,
            normalized_query,
            " ".join(title_tokens[index : index + query_length]),
        ).ratio()
        for index in range(max(1, len(title_tokens) - query_length + 1))
    ]
    return round(max(window_scores, default=0.0), 4)


def position_matches_query(query: str, title: str) -> bool:
    """넓은 검색 결과에서 입력 직무와 유사한 공고 제목인지 확인합니다."""
    return position_match_score(query, title) >= 0.72


def _parse_position_search_item(
    item: Any,
) -> WantedPositionSearchResult | None:
    if not isinstance(item, dict):
        return None
    company = item.get("company") or {}
    if not isinstance(company, dict):
        return None

    try:
        return WantedPositionSearchResult(
            id=int(item["id"]),
            company_id=int(company["id"]),
            company_name=str(company["name"]),
            title=str(item["position"]),
            annual_from=item.get("annual_from"),
            annual_to=item.get("annual_to"),
            employment_type=item.get("employment_type"),
            is_outlink=bool(item.get("is_outlink")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def search_positions(
    query: str,
    *,
    max_results: int = 100,
    page_size: int = 20,
) -> list[WantedPositionSearchResult]:
    """직무명으로 포지션을 검색하고 최대 검색 건수까지 페이지네이션합니다."""
    query = query.strip()
    if not query:
        raise ValueError("검색할 직무명을 입력해주세요.")
    if max_results < 1:
        raise ValueError("max_results는 1 이상이어야 합니다.")
    if page_size < 1:
        raise ValueError("page_size는 1 이상이어야 합니다.")

    positions: list[WantedPositionSearchResult] = []
    seen_job_ids: set[int] = set()
    offset = 0

    while offset < max_results:
        request_limit = min(page_size, max_results - offset)
        payload = _get_json(
            "/api/chaos/search/v1/position",
            {
                "query": query,
                "offset": offset,
                "limit": request_limit,
            },
        )
        items = payload.get("data")
        if not isinstance(items, list):
            raise WantedAPIError("원티드 포지션 검색 응답 구조가 예상과 다릅니다.")
        if not items:
            break

        for item in items:
            position = _parse_position_search_item(item)
            if position is None or position.id in seen_job_ids:
                continue
            seen_job_ids.add(position.id)
            positions.append(position)

        offset += len(items)
        total_count = payload.get("total_count")
        if isinstance(total_count, int) and offset >= total_count:
            break
        if len(items) < request_limit:
            break

    return positions


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


def read_job_names(path: Path) -> list[str]:
    """한 줄당 하나의 직무명을 읽고 빈 줄, 주석과 중복을 제거합니다."""
    names: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        name = raw_line.strip()
        if not name or name.startswith("#"):
            continue
        key = unicodedata.normalize("NFKC", name).casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)

    if not names:
        raise ValueError(f"직무명 파일이 비어 있습니다: {path}")
    return names


def _new_platform_result(
    platform: str,
    search_method: str,
    *,
    status: str = "ok",
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "platform": platform,
        "platform_name": PLATFORM_NAMES[platform],
        "status": status,
        "search_method": search_method,
        "searched_query_count": 0,
        "failed_query_count": 0,
        "scanned_result_count": 0,
        "matched_job_count": 0,
        "notes": notes or [],
        "errors": [],
        "jobs": [],
    }


def _finish_platform_result(result: dict[str, Any]) -> dict[str, Any]:
    result["matched_job_count"] = len(result["jobs"])
    failed_count = result["failed_query_count"]
    searched_count = result["searched_query_count"]
    if result["status"] == "skipped":
        return result
    if failed_count and searched_count:
        result["status"] = "partial"
    elif failed_count and not searched_count:
        result["status"] = "error"
    else:
        result["status"] = "ok"
    return result


def _record_match(
    records: dict[str, dict[str, Any]],
    record: dict[str, Any],
    job_name: str,
    score: float,
) -> None:
    key = str(record.get("source_url") or record.get("job_id"))
    existing = records.get(key)
    if existing is None:
        record["matched_job_names"] = [job_name]
        record["match_score"] = round(score, 4)
        records[key] = record
        return

    if job_name not in existing["matched_job_names"]:
        existing["matched_job_names"].append(job_name)
    existing["match_score"] = max(existing["match_score"], round(score, 4))


def _wanted_search_record(position: WantedPositionSearchResult) -> dict[str, Any]:
    return {
        "platform": "wanted",
        "platform_name": PLATFORM_NAMES["wanted"],
        "job_id": str(position.id),
        "title": position.title,
        "company_name": position.company_name,
        "source_url": position.source_url,
        "location": None,
        "experience": {
            "min_years": position.annual_from,
            "max_years": position.annual_to,
        },
        "employment_type": position.employment_type,
        "published_at": None,
        "search_method": "wanted_public_search",
    }


def search_wanted_job_names(
    job_names: Sequence[str],
    *,
    max_results_per_query: int = 100,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """원티드에서 모든 직무명을 검색하고 유사 제목의 공고를 반환합니다."""
    result = _new_platform_result("wanted", "wanted_public_search")
    records: dict[str, dict[str, Any]] = {}

    for job_name in job_names:
        try:
            positions = search_positions(
                job_name,
                max_results=max_results_per_query,
            )
        except (WantedAPIError, ValueError) as exc:
            result["failed_query_count"] += 1
            result["errors"].append({"job_name": job_name, "message": str(exc)})
            if progress:
                progress(f"원티드 / {job_name}: 실패")
            continue

        result["searched_query_count"] += 1
        result["scanned_result_count"] += len(positions)
        matched_count = 0
        for position in positions:
            score = position_match_score(job_name, position.title)
            if score < 0.72:
                continue
            matched_count += 1
            _record_match(
                records,
                _wanted_search_record(position),
                job_name,
                score,
            )
        if progress:
            progress(f"원티드 / {job_name}: 유사 공고 {matched_count}개")

    result["jobs"] = list(records.values())
    return _finish_platform_result(result)


def _canonical_job_url(url: str) -> str:
    parts = urlsplit(url)
    kept_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in {"trk", "trackingid", "refid"}
    ]
    return urlunsplit(
        (
            parts.scheme or "https",
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            urlencode(kept_query, doseq=True),
            "",
        )
    )


def _web_platform_for_url(url: str) -> str | None:
    host = urlsplit(url).netloc.casefold().split(":", 1)[0]
    for platform, domain in WEB_PLATFORM_DOMAINS.items():
        if host == domain or host.endswith(f".{domain}"):
            return platform
    return None


def _is_job_detail_url(platform: str, url: str) -> bool:
    parts = urlsplit(url)
    if platform == "groupby":
        return bool(re.search(r"/positions/\d+/?$", parts.path))
    if platform == "saramin":
        if parts.netloc.casefold().startswith("jumpit."):
            return bool(re.search(r"/position/\d+/?$", parts.path))
        return "/zf_user/jobs/relay/" in parts.path and bool(
            re.search(r"(?:rec|recruit)_idx=\d+", parts.query)
        )
    if platform == "linkedin":
        return "/jobs/view/" in parts.path and bool(re.search(r"\d+/?$", parts.path))
    if platform == "jobplanet":
        return "/job/" in parts.path and bool(
            re.search(r"posting_ids(?:%5B%5D|\[\])?=\d+", url, re.IGNORECASE)
            or re.search(r"/posting/\d+", parts.path)
        )
    return False


def _job_id_from_url(platform: str, url: str) -> str:
    patterns = {
        "groupby": r"/positions/(\d+)",
        "saramin": r"(?:/position/|(?:rec|recruit)_idx=)(\d+)",
        "linkedin": r"-(\d+)/?$",
        "jobplanet": r"(?:posting_ids(?:%5B%5D|\[\])?=|/posting/)(\d+)",
    }
    match = re.search(patterns[platform], url, re.IGNORECASE)
    return match.group(1) if match else url


def _query_title_pattern(job_name: str) -> re.Pattern[str]:
    tokens = _normalize_position_name(job_name).split()
    patterns = []
    for token in tokens:
        if token == "solution":
            patterns.append(r"solutions?")
        elif token == "deployed":
            patterns.append(r"deploy(?:ed|ment)?")
        else:
            patterns.append(re.escape(token))
    return re.compile(r"\b" + r"[\s\-/]+".join(patterns) + r"\b", re.IGNORECASE)


def _clean_web_title(
    platform: str,
    page_title: str,
    content: str,
    job_name: str,
    url: str,
) -> tuple[str, str | None]:
    title = re.sub(r"\s+", " ", page_title).strip()
    company: str | None = None

    if platform == "jobplanet":
        match = re.match(r"(.+?)\s+채용공고\s*-\s*(.+)", title)
        if match:
            company = match.group(1).strip()
            title = match.group(2).strip()
        title = re.split(r",\s*(?:서울|경기|인천|부산|대전|대구|광주|울산|제주)\b", title)[0]
        title = re.sub(r"\s*-\s*잡플래닛.*$", "", title).strip(" -…")
    elif platform == "saramin":
        title = re.sub(r"^점핏\s*\|\s*", "", title).strip()
        title = re.sub(r"\s*(?:-|\|)\s*사람인.*$", "", title).strip()
        title = re.sub(r"\s+채용(?:공고)?\s*$", "", title).strip(" -…")

        company_match = re.search(
            r"(?:^|\s)([가-힣A-Za-z0-9㈜()·._-]{2,40})_"
            + re.escape(title[: min(20, len(title))]),
            content,
            re.IGNORECASE,
        )
        if company_match:
            company = company_match.group(1).strip()
    elif platform == "groupby":
        title = re.sub(r"^(?:무관|신입|인턴|경력(?:\s*\d+년[^ ]*)?)\s+", "", title)
        match = re.match(r"(.+?)\s+채용\s*\|\s*(.+)$", title)
        if match:
            title = match.group(1).strip()
            candidate = match.group(2).strip(" -…")
            company = (
                candidate
                if len(candidate) >= 2 and "그룹바이" not in candidate
                else None
            )
        title = re.sub(r"\s+채용\s*(?:-|\|)\s*그룹바이.*$", "", title).strip()
        title = re.sub(r"\s*(?:-|\|)\s*그룹바이.*$", "", title).strip()
    elif platform == "linkedin":
        title = re.sub(r"\s*(?:-|\|)\s*LinkedIn.*$", "", title, flags=re.IGNORECASE)
        hiring = re.match(r"(.+?)\s+hiring\s+(.+?)(?:\s+in\s+.+)?$", title, re.IGNORECASE)
        if hiring:
            company = hiring.group(1).strip()
            title = hiring.group(2).strip()
        else:
            query_match = _query_title_pattern(job_name).search(title)
            if query_match and query_match.start() > 0:
                candidate = title[: query_match.start()].strip(" []-|")
                if candidate and len(candidate) <= 80:
                    company = candidate
                title = title[query_match.start() :].strip()
            title = re.sub(
                r"\((?:대한민국|서울|경기|인천)[^)]*(?:\)|$)",
                "",
                title,
            ).strip()
            title = re.sub(r"\s+채용중.*$", "", title).strip()

        if not company:
            slug_match = re.search(r"-at-(.+)-(?:\d+)/?$", urlsplit(url).path)
            if slug_match:
                company = slug_match.group(1).replace("-", " ").title()

    if not title:
        title = page_title.strip()
    return title, company


def _web_result_is_closed(content: str) -> bool:
    closed_markers = (
        "채용공고 마감됨",
        "접수가 마감되었습니다",
        "no longer accepting applications",
        "this job is no longer available",
    )
    normalized = content.casefold()
    if any(marker.casefold() in normalized for marker in closed_markers):
        return True

    deadline_match = re.search(
        r"마감일\s*:?\s*(\d{4}-\d{2}-\d{2})",
        content,
        re.IGNORECASE,
    )
    if deadline_match:
        deadline = datetime.fromisoformat(deadline_match.group(1)).date()
        return deadline < datetime.now().astimezone().date()
    return False


def _parse_web_search_result(
    platform: str,
    item: Any,
    job_name: str,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    page_title = item.get("title")
    if not isinstance(url, str) or not isinstance(page_title, str):
        return None
    if _web_platform_for_url(url) != platform or not _is_job_detail_url(platform, url):
        return None

    content = str(item.get("content") or "")
    if _web_result_is_closed(content):
        return None
    canonical_url = _canonical_job_url(url)
    title, company = _clean_web_title(
        platform,
        page_title,
        content,
        job_name,
        canonical_url,
    )
    return {
        "platform": platform,
        "platform_name": PLATFORM_NAMES[platform],
        "job_id": _job_id_from_url(platform, canonical_url),
        "title": title,
        "company_name": company,
        "source_url": canonical_url,
        "location": None,
        "experience": None,
        "employment_type": None,
        "published_at": None,
        "search_method": "tavily_domain_search",
    }


def search_web_platform_job_names(
    platform: str,
    job_names: Sequence[str],
    *,
    tavily_api_key: str | None,
    max_results_per_query: int = 20,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """공식 검색 API가 없는 플랫폼을 Tavily 도메인 제한 검색으로 조회합니다."""
    if platform not in WEB_PLATFORM_DOMAINS:
        raise ValueError(f"지원하지 않는 웹 검색 플랫폼입니다: {platform}")

    result = _new_platform_result(
        platform,
        "tavily_domain_search",
        notes=[
            "공식 채용 검색 API가 없어 Tavily의 도메인 제한 웹 검색 결과를 사용합니다.",
            "검색 엔진 인덱스 상태에 따라 사이트의 모든 공고가 포함되지 않을 수 있습니다.",
        ],
    )
    if not tavily_api_key:
        result["status"] = "skipped"
        result["notes"].append("TAVILY_API_KEY가 없어 검색을 건너뛰었습니다.")
        return result

    try:
        from langchain_tavily import TavilySearch
    except ImportError:
        result["status"] = "skipped"
        result["notes"].append("langchain-tavily 패키지가 없어 검색을 건너뛰었습니다.")
        return result

    domain = WEB_PLATFORM_DOMAINS[platform]
    records: dict[str, dict[str, Any]] = {}
    tool = TavilySearch(
        tavily_api_key=tavily_api_key,
        include_domains=[domain],
        max_results=min(20, max_results_per_query),
        search_depth="basic",
        include_answer=False,
        include_raw_content=False,
    )

    for job_name in job_names:
        query = f'"{job_name}" 채용 대한민국'
        try:
            payload = tool.invoke({"query": query})
            items = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise JobPlatformSearchError("웹 검색 응답 구조가 예상과 다릅니다.")
        except Exception as exc:  # 외부 SDK는 여러 종류의 예외를 전달합니다.
            if "No search results found" in str(exc):
                items = []
            else:
                result["failed_query_count"] += 1
                result["errors"].append(
                    {"job_name": job_name, "message": str(exc)}
                )
                if progress:
                    progress(f"{PLATFORM_NAMES[platform]} / {job_name}: 실패")
                continue

        result["searched_query_count"] += 1
        result["scanned_result_count"] += len(items)
        matched_count = 0
        for item in items:
            job = _parse_web_search_result(platform, item, job_name)
            if job is None:
                continue
            score = max(
                position_match_score(job_name, job["title"]),
                position_match_score(job_name, str(item.get("title") or "")),
            )
            if score < 0.72:
                continue
            matched_count += 1
            _record_match(records, job, job_name, score)
        if progress:
            progress(
                f"{PLATFORM_NAMES[platform]} / {job_name}: "
                f"유사 공고 {matched_count}개"
            )

    result["jobs"] = list(records.values())
    return _finish_platform_result(result)


def _company_summary(jobs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    companies: dict[str, dict[str, Any]] = {}
    for job in jobs:
        company_name = job.get("company_name")
        if not company_name:
            continue
        key = _normalize_company_name(str(company_name)) or str(company_name).casefold()
        company = companies.setdefault(
            key,
            {
                "company_name": company_name,
                "matched_job_count": 0,
                "platforms": [],
                "jobs": [],
            },
        )
        company["matched_job_count"] += 1
        if job["platform"] not in company["platforms"]:
            company["platforms"].append(job["platform"])
        company["jobs"].append(
            {
                "platform": job["platform"],
                "job_id": job["job_id"],
                "title": job["title"],
                "source_url": job["source_url"],
                "matched_job_names": job["matched_job_names"],
            }
        )

    result = list(companies.values())
    for company in result:
        company["platforms"].sort(key=PLATFORM_ORDER.index)
        company["jobs"].sort(
            key=lambda item: (
                PLATFORM_ORDER.index(item["platform"]),
                item["title"].casefold(),
            )
        )
    result.sort(
        key=lambda item: (-item["matched_job_count"], item["company_name"].casefold())
    )
    return result


def search_all_job_platforms(
    job_names: Sequence[str],
    *,
    max_results_per_query: int = 100,
    web_results_per_query: int = 20,
    platforms: Sequence[str] = PLATFORM_ORDER,
    tavily_api_key: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """선택한 플랫폼을 병렬 검색하고 공통 형식의 결과로 통합합니다."""
    if not job_names:
        raise ValueError("검색할 직무명이 없습니다.")
    if max_results_per_query < 1 or web_results_per_query < 1:
        raise ValueError("검색 결과 최대 개수는 1 이상이어야 합니다.")

    requested = list(dict.fromkeys(platforms))
    unknown = [platform for platform in requested if platform not in PLATFORM_ORDER]
    if unknown:
        raise ValueError(f"지원하지 않는 플랫폼입니다: {', '.join(unknown)}")
    if not requested:
        raise ValueError("검색할 플랫폼을 하나 이상 지정해주세요.")

    providers: dict[str, Callable[[], dict[str, Any]]] = {}
    if "wanted" in requested:
        providers["wanted"] = lambda: search_wanted_job_names(
            job_names,
            max_results_per_query=max_results_per_query,
            progress=progress,
        )
    for platform in WEB_PLATFORM_DOMAINS:
        if platform in requested:
            providers[platform] = lambda platform=platform: search_web_platform_job_names(
                platform,
                job_names,
                tavily_api_key=tavily_api_key,
                max_results_per_query=web_results_per_query,
                progress=progress,
            )

    results_by_platform: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(providers)) as executor:
        futures = {
            executor.submit(provider): platform
            for platform, provider in providers.items()
        }
        for future in as_completed(futures):
            platform = futures[future]
            try:
                results_by_platform[platform] = future.result()
            except Exception as exc:
                failed = _new_platform_result(
                    platform,
                    "unknown",
                    status="error",
                )
                failed["failed_query_count"] = len(job_names)
                failed["errors"].append({"job_name": None, "message": str(exc)})
                results_by_platform[platform] = failed
            if progress:
                platform_result = results_by_platform[platform]
                progress(
                    f"{PLATFORM_NAMES[platform]} 완료: "
                    f"{platform_result['matched_job_count']}개"
                )

    platform_results = [results_by_platform[name] for name in PLATFORM_ORDER if name in requested]
    jobs = [job for result in platform_results for job in result["jobs"]]
    jobs.sort(
        key=lambda item: (
            PLATFORM_ORDER.index(item["platform"]),
            -(item.get("match_score") or 0),
            item["title"].casefold(),
        )
    )
    companies = _company_summary(jobs)
    statuses = {result["status"] for result in platform_results}
    if statuses == {"ok"}:
        overall_status = "ok"
    elif statuses <= {"error", "skipped"}:
        overall_status = "error"
    else:
        overall_status = "partial"

    summaries = []
    for result in platform_results:
        summaries.append({key: value for key, value in result.items() if key != "jobs"})

    return {
        "status": overall_status,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "input_job_names": list(job_names),
        "requested_platforms": requested,
        "max_results_per_query": max_results_per_query,
        "web_results_per_query": min(20, web_results_per_query),
        "matched_job_count": len(jobs),
        "matched_company_count": len(companies),
        "platform_summary": summaries,
        "jobs": jobs,
        "companies": companies,
    }


def write_search_results(output_path: Path, result: dict[str, Any]) -> Path:
    """통합 검색 결과를 UTF-8 JSON 파일로 저장합니다."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "job_name.txt의 직무명으로 원티드, 그룹바이, 사람인, 링크드인, "
            "잡플래닛 공고를 검색해 JSON으로 저장합니다."
        )
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
        default=BASE_DIR / "search_jobs_result.json",
        help="통합 검색 결과 JSON 파일",
    )
    parser.add_argument(
        "--platform",
        action="append",
        choices=PLATFORM_ORDER,
        dest="platforms",
        help="검색할 플랫폼(여러 번 지정 가능, 생략 시 전체)",
    )
    parser.add_argument(
        "--max-results-per-query",
        type=int,
        default=100,
        help="직무명 하나당 원티드에서 확인할 최대 결과 수",
    )
    parser.add_argument(
        "--web-results-per-query",
        type=int,
        default=20,
        help="직무명 하나당 웹 검색 결과 수(최대 20)",
    )
    args = parser.parse_args(argv)
    if args.max_results_per_query < 1:
        parser.error("--max-results-per-query는 1 이상이어야 합니다.")
    if not 1 <= args.web_results_per_query <= 20:
        parser.error("--web-results-per-query는 1~20이어야 합니다.")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    load_dotenv(BASE_DIR / ".env")
    try:
        job_names = read_job_names(args.job_name_file)
        print(f"직무명 {len(job_names)}개를 읽었습니다.")
        result = search_all_job_platforms(
            job_names,
            max_results_per_query=args.max_results_per_query,
            web_results_per_query=args.web_results_per_query,
            platforms=args.platforms or PLATFORM_ORDER,
            tavily_api_key=os.getenv("TAVILY_API_KEY"),
            progress=lambda message: print(f"- {message}", flush=True),
        )
        output_path = write_search_results(args.output, result)
    except FileNotFoundError:
        print(f"직무명 파일을 찾지 못했습니다: {args.job_name_file}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"채용공고 검색 실패: {exc}", file=sys.stderr)
        return 2

    print(
        f"공고 {result['matched_job_count']}개, "
        f"회사 {result['matched_company_count']}개를 찾았습니다."
    )
    for summary in result["platform_summary"]:
        print(
            f"- {summary['platform_name']}: {summary['status']} / "
            f"공고 {summary['matched_job_count']}개"
        )
    print(f"JSON 저장: {output_path}")
    return 0 if result["matched_job_count"] or result["status"] != "error" else 2


if __name__ == "__main__":
    raise SystemExit(main())
