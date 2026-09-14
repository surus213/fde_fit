"""통합 채용 검색 결과를 FDE 사전 분류 후 정밀 분석합니다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from analyze_fde_si import analyze_fde_si
from analyze_workplace import analyze_workplace
from batch_analysis_report import write_batch_analysis_report
from comparison_report import write_comparison_reports
from final_job_fit import final_job_fit
from four_factor_analysis import evaluate_four_factors
from html_report import write_reports
from parse_job import parse_job
from research_company import research_company
from research_workplace import research_workplace
from search_jobs import (
    WantedJob,
    _canonical_job_url,
    _normalize_company_name,
    format_job_text,
    get_job_detail,
    is_fde_job_title,
)


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
ProgressCallback = Callable[[str], None]


class SearchResultFDEAssessment(BaseModel):
    record_key: str
    fde_score: int = Field(ge=0, le=10)
    confidence: float = Field(ge=0, le=1)
    is_fde_like: bool
    reasons: list[str]
    missing_signals: list[str]


class SearchResultFDEBatch(BaseModel):
    assessments: list[SearchResultFDEAssessment]


classification_llm = init_chat_model(
    "openai:gpt-5-mini",
    temperature=0,
)
batch_classifier = classification_llm.with_structured_output(SearchResultFDEBatch)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _emit(progress: ProgressCallback | None, message: str) -> None:
    if progress:
        progress(message)


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[이하 생략]"


def _retry(
    label: str,
    operation: Callable[[], Any],
    *,
    attempts: int = 3,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:  # 외부 API와 SDK가 여러 예외 형식을 사용합니다.
            last_error = exc
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    assert last_error is not None
    raise RuntimeError(f"{label} 실패 ({attempts}회 시도): {last_error}") from last_error


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def _record_key(record: dict[str, Any]) -> str:
    platform = str(record.get("platform") or "unknown")
    job_id = str(record.get("job_id") or "").strip()
    if not job_id:
        source_url = str(record.get("source_url") or "")
        job_id = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:12]
    return f"{platform}:{job_id}"


def _input_signature(records: list[dict[str, Any]]) -> str:
    identity = [
        {
            "record_key": _record_key(record),
            "source_url": record.get("source_url"),
            "title": record.get("title"),
        }
        for record in records
    ]
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _profile_signature(candidate_profile: dict[str, Any]) -> str:
    encoded = json.dumps(
        candidate_profile,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_search_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("검색 결과 JSON에 jobs 배열이 없습니다.")

    records_by_key: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(payload["jobs"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"jobs[{index - 1}]가 JSON 객체가 아닙니다.")
        missing = [
            key
            for key in ("platform", "job_id", "title", "source_url")
            if not item.get(key)
        ]
        if missing:
            raise ValueError(
                f"jobs[{index - 1}]에 필수 필드가 없습니다: {', '.join(missing)}"
            )
        key = _record_key(item)
        existing = records_by_key.get(key)
        if existing is None:
            records_by_key[key] = dict(item)
            continue

        duplicate_urls = existing.setdefault("duplicate_source_urls", [])
        for url in (existing.get("source_url"), item.get("source_url")):
            if url and url != existing.get("source_url") and url not in duplicate_urls:
                duplicate_urls.append(url)
        matched_names = existing.setdefault("matched_job_names", [])
        for name in item.get("matched_job_names") or []:
            if name not in matched_names:
                matched_names.append(name)
        existing["match_score"] = max(
            float(existing.get("match_score") or 0),
            float(item.get("match_score") or 0),
        )
        if not existing.get("company_name") and item.get("company_name"):
            existing["company_name"] = item["company_name"]
    return list(records_by_key.values())


def _new_checkpoint(
    input_path: Path,
    candidate_profile_path: Path,
    records: list[dict[str, Any]],
    candidate_signature: str,
) -> dict[str, Any]:
    jobs = {
        _record_key(record): {
            "record_key": _record_key(record),
            "source": record,
            "status": "pending",
            "errors": [],
        }
        for record in records
    }
    return {
        "schema_version": "1.0",
        "input_path": str(input_path.resolve()),
        "candidate_profile_path": str(candidate_profile_path.resolve()),
        "candidate_signature": candidate_signature,
        "input_signature": _input_signature(records),
        "started_at": _now(),
        "updated_at": _now(),
        "jobs": jobs,
        "company_cache": {},
    }


def _load_checkpoint(
    checkpoint_path: Path,
    input_path: Path,
    candidate_profile_path: Path,
    records: list[dict[str, Any]],
    candidate_signature: str,
    *,
    resume: bool,
) -> dict[str, Any]:
    expected_signature = _input_signature(records)
    if resume and checkpoint_path.exists():
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if (
            payload.get("input_signature") != expected_signature
            or payload.get("candidate_signature") != candidate_signature
        ):
            raise ValueError(
                "체크포인트와 현재 입력 공고 또는 후보자 프로필이 다릅니다. "
                "--no-resume으로 새 분석을 시작하세요."
            )
        if not isinstance(payload.get("jobs"), dict):
            raise ValueError("체크포인트의 jobs 형식이 올바르지 않습니다.")
        payload.setdefault("company_cache", {})
        return payload
    return _new_checkpoint(
        input_path,
        candidate_profile_path,
        records,
        candidate_signature,
    )


def _save_checkpoint(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now()
    _atomic_write_json(path, state)


def _wanted_job_from_record(record: dict[str, Any]) -> WantedJob:
    experience = record.get("experience") or {}
    if not isinstance(experience, dict):
        experience = {}
    return WantedJob(
        id=int(record["job_id"]),
        company_id=0,
        company_name=str(record.get("company_name") or "회사 미상"),
        title=str(record["title"]),
        status="active",
        due_time=None,
        location=str(record.get("location") or ""),
        annual_from=experience.get("min_years"),
        annual_to=experience.get("max_years"),
        employment_type=record.get("employment_type"),
    )


def _format_web_job_text(record: dict[str, Any], raw_content: str) -> str:
    fields = [
        f"플랫폼: {record.get('platform_name') or record.get('platform')}",
        f"공고 ID: {record.get('job_id')}",
        f"공고 제목: {record.get('title')}",
        f"회사: {record.get('company_name') or '확인 필요'}",
        f"원본 공고 URL: {record.get('source_url')}",
    ]
    if record.get("location"):
        fields.append(f"근무지: {record['location']}")
    if record.get("employment_type"):
        fields.append(f"고용 형태: {record['employment_type']}")
    fields.append("\n[공고 페이지에서 추출한 본문]\n" + _clip(raw_content, 30_000))
    return "\n".join(fields).strip() + "\n"


def _extract_urls(urls: list[str]) -> dict[str, str]:
    from langchain_tavily import TavilyExtract

    tool = TavilyExtract(
        extract_depth="advanced",
        include_images=False,
        format="markdown",
    )
    payload = tool.invoke(
        {
            "urls": urls,
            "extract_depth": "advanced",
            "include_images": False,
        }
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Tavily Extract 응답이 JSON 객체가 아닙니다.")
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    items = payload.get("results")
    if not isinstance(items, list):
        raise RuntimeError("Tavily Extract 응답에 results 배열이 없습니다.")

    extracted: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        content = item.get("raw_content") or item.get("content")
        if not isinstance(url, str) or not isinstance(content, str):
            continue
        if content.strip():
            extracted[_canonical_job_url(url)] = content.strip()
    return extracted


def _search_url_content(record: dict[str, Any]) -> str | None:
    """Extract 실패 시 동일 URL의 검색 인덱스 본문을 보조 경로로 조회합니다."""
    from langchain_tavily import TavilySearch

    source_url = str(record.get("source_url") or "").strip()
    if not source_url:
        return None

    host = urlsplit(source_url).netloc.lower().removeprefix("www.")
    query_parts = [
        f"site:{host}" if host else "",
        f'"{record.get("title") or ""}"',
        str(record.get("company_name") or ""),
        str(record.get("job_id") or ""),
    ]
    tool = TavilySearch(
        max_results=5,
        search_depth="advanced",
        include_answer=False,
        include_raw_content=True,
    )
    payload = tool.invoke({"query": " ".join(part for part in query_parts if part)})
    items = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("Tavily Search 응답에 results 배열이 없습니다.")

    canonical_url = _canonical_job_url(source_url)
    job_id = str(record.get("job_id") or "").strip()
    for item in items:
        if not isinstance(item, dict):
            continue
        result_url = str(item.get("url") or "")
        same_page = _canonical_job_url(result_url) == canonical_url
        if not same_page and job_id:
            same_page = job_id in result_url
        if not same_page:
            continue
        content = item.get("raw_content") or item.get("content")
        if isinstance(content, str) and len(content.strip()) >= 120:
            return content.strip()
    return None


def _store_detail(
    entry: dict[str, Any],
    *,
    job_text: str,
    fetch_method: str,
) -> None:
    entry["errors"] = [
        error
        for error in entry.get("errors", [])
        if error.get("stage") != "detail"
    ]
    entry["job_text"] = job_text
    entry["detail"] = {
        "status": "ok",
        "fetch_method": fetch_method,
        "text_length": len(job_text),
        "fetched_at": _now(),
    }
    entry["status"] = "detail_ready"


def _store_error(entry: dict[str, Any], stage: str, error: Exception | str) -> None:
    message = str(error)
    entry.setdefault("errors", []).append(
        {"stage": stage, "message": message, "recorded_at": _now()}
    )
    entry["status"] = f"{stage}_error"


def collect_job_details(
    state: dict[str, Any],
    *,
    workers: int,
    extract_batch_size: int,
    progress: ProgressCallback | None = None,
) -> None:
    """원티드는 공개 상세 API, 나머지 플랫폼은 Tavily Extract로 본문을 수집합니다."""
    pending = [
        entry
        for entry in state["jobs"].values()
        if entry.get("status") in {"pending", "detail_error"}
    ]
    wanted_entries = [
        entry for entry in pending if entry["source"].get("platform") == "wanted"
    ]
    web_entries = [
        entry for entry in pending if entry["source"].get("platform") != "wanted"
    ]

    if wanted_entries:
        _emit(progress, f"원티드 공고 {len(wanted_entries)}개의 상세 내용을 수집합니다.")
        with ThreadPoolExecutor(max_workers=min(workers, len(wanted_entries))) as executor:
            future_to_entry = {
                executor.submit(
                    _retry,
                    f"원티드 {entry['record_key']} 상세 수집",
                    lambda entry=entry: get_job_detail(int(entry["source"]["job_id"])),
                ): entry
                for entry in wanted_entries
            }
            for future in as_completed(future_to_entry):
                entry = future_to_entry[future]
                try:
                    detail = future.result()
                    job = _wanted_job_from_record(entry["source"])
                    _store_detail(
                        entry,
                        job_text=format_job_text(job, detail),
                        fetch_method="wanted_public_detail_api",
                    )
                    _emit(progress, f"상세 수집 완료: {entry['record_key']}")
                except Exception as exc:
                    _store_error(entry, "detail", exc)
                    _emit(progress, f"상세 수집 실패: {entry['record_key']}")

    batches = _chunks(web_entries, extract_batch_size)
    fallback_entries: list[dict[str, Any]] = []
    if batches:
        _emit(
            progress,
            f"웹 플랫폼 공고 {len(web_entries)}개를 {len(batches)}개 묶음으로 추출합니다.",
        )
        batch_workers = min(max(1, workers // 2), 3, len(batches))
        with ThreadPoolExecutor(max_workers=batch_workers) as executor:
            future_to_batch = {
                executor.submit(
                    _retry,
                    "웹 공고 본문 추출",
                    lambda batch=batch: _extract_urls(
                        [str(entry["source"]["source_url"]) for entry in batch]
                    ),
                ): batch
                for batch in batches
            }
            for future in as_completed(future_to_batch):
                batch = future_to_batch[future]
                try:
                    extracted = future.result()
                except Exception as exc:
                    fallback_entries.extend(batch)
                    _emit(progress, f"웹 상세 묶음 {len(batch)}개 수집 실패")
                    continue

                for entry in batch:
                    record = entry["source"]
                    canonical_url = _canonical_job_url(str(record["source_url"]))
                    content = extracted.get(canonical_url)
                    if not content or len(content.strip()) < 120:
                        fallback_entries.append(entry)
                        _emit(progress, f"본문 없음: {entry['record_key']}")
                        continue
                    _store_detail(
                        entry,
                        job_text=_format_web_job_text(record, content),
                        fetch_method="tavily_extract",
                    )
                    _emit(progress, f"상세 수집 완료: {entry['record_key']}")

    if fallback_entries:
        _emit(
            progress,
            f"Extract 실패 공고 {len(fallback_entries)}개를 검색 인덱스로 재조회합니다.",
        )
        with ThreadPoolExecutor(
            max_workers=min(workers, 3, len(fallback_entries))
        ) as executor:
            future_to_entry = {
                executor.submit(
                    _retry,
                    f"{entry['record_key']} 검색 인덱스 조회",
                    lambda entry=entry: _search_url_content(entry["source"]),
                ): entry
                for entry in fallback_entries
            }
            for future in as_completed(future_to_entry):
                entry = future_to_entry[future]
                try:
                    content = future.result()
                    if not content:
                        raise RuntimeError(
                            "Extract와 검색 인덱스에서 공고 본문을 확보하지 못했습니다."
                        )
                    _store_detail(
                        entry,
                        job_text=_format_web_job_text(entry["source"], content),
                        fetch_method="tavily_search_fallback",
                    )
                    _emit(progress, f"검색 인덱스 복구 완료: {entry['record_key']}")
                except Exception as exc:
                    _store_error(entry, "detail", exc)
                    _emit(progress, f"상세 수집 최종 실패: {entry['record_key']}")


def _classification_payload(entry: dict[str, Any]) -> dict[str, Any]:
    source = entry["source"]
    return {
        "record_key": entry["record_key"],
        "platform": source.get("platform"),
        "company_name": source.get("company_name"),
        "title": source.get("title"),
        "job_posting": _clip(entry.get("job_text"), 10_000),
    }


def _classify_batch(entries: list[dict[str, Any]]) -> list[SearchResultFDEAssessment]:
    postings = [_classification_payload(entry) for entry in entries]
    result = batch_classifier.invoke(
        f"""
당신은 채용공고의 실제 업무가 Forward Deployed Engineer(FDE)에 가까운지
판정하는 채용 직무 분석가입니다.

아래 모든 공고를 빠짐없이 평가하세요. 직무명보다 공고 본문의 주요 업무와
자격요건을 우선하세요.

FDE 핵심 신호:
- 고객과 직접 협업하며 모호한 문제를 기술 문제로 정의함
- 엔지니어가 직접 코드를 작성하고 솔루션을 설계·구현함
- PoC, 배포, 운영 안정화를 end-to-end로 책임짐
- 현장 결과와 피드백이 공통 제품·플랫폼 개선으로 환원됨
- 제품팀과 고객 사이의 기술적 가교 역할을 함

FDE가 아닌 주요 사례:
- 고객 접점 없는 내부 제품 개발
- 구현 책임이 없는 영업, CSM, PM, 컨설팅 또는 기술지원
- 정해진 요구사항만 구현하거나 유지보수하는 일반 SI 개발
- 제목만 유사하고 실제 업무에는 FDE 신호가 없음

점수 기준:
- 0~2: FDE와 무관
- 3~5: 일부 유사하지만 FDE로 보기 어려움
- 6~7: 실제 업무가 FDE에 가까움
- 8~10: 명백한 FDE 역할

is_fde_like는 fde_score가 6 이상일 때만 true로 설정하세요. 명시된 근거만
reasons에 기록하고, 확인되지 않은 핵심 요소는 missing_signals에 기록하세요.
입력 record_key를 변경하지 말고 각각 정확히 한 번씩 반환하세요.

[채용공고]
{json.dumps(postings, ensure_ascii=False)}
"""
    )
    expected = {entry["record_key"] for entry in entries}
    returned = [item.record_key for item in result.assessments]
    if set(returned) != expected or len(returned) != len(expected):
        raise RuntimeError("FDE 분류 결과의 record_key가 입력과 일치하지 않습니다.")
    return result.assessments


def _adjust_assessment(
    assessment: SearchResultFDEAssessment,
    entry: dict[str, Any],
) -> SearchResultFDEAssessment:
    explicit_title = is_fde_job_title(str(entry["source"].get("title") or ""))
    reasons = list(assessment.reasons)
    if explicit_title and not any("직무명" in reason for reason in reasons):
        reasons.insert(0, "공고 직무명이 FDE를 명시합니다.")
    score = max(8, assessment.fde_score) if explicit_title else assessment.fde_score
    return assessment.model_copy(
        update={
            "fde_score": score,
            "is_fde_like": explicit_title or score >= 6,
            "reasons": reasons,
        }
    )


def classify_all_jobs(
    state: dict[str, Any],
    *,
    batch_size: int,
    workers: int,
    progress: ProgressCallback | None = None,
) -> None:
    entries = [
        entry
        for entry in state["jobs"].values()
        if entry.get("status") in {"detail_ready", "classification_error"}
        and "fde_classification" not in entry
    ]
    batches = _chunks(entries, batch_size)
    if not batches:
        return
    _emit(progress, f"상세 확보 공고 {len(entries)}개를 FDE 사전 분류합니다.")

    with ThreadPoolExecutor(max_workers=min(workers, len(batches))) as executor:
        future_to_batch = {
            executor.submit(
                _retry,
                "FDE 묶음 분류",
                lambda batch=batch: _classify_batch(batch),
            ): batch
            for batch in batches
        }
        for future in as_completed(future_to_batch):
            batch = future_to_batch[future]
            try:
                assessments = future.result()
            except Exception as batch_error:
                _emit(progress, f"FDE 묶음 분류 실패, {len(batch)}개를 개별 재시도합니다.")
                assessments = []
                for entry in batch:
                    try:
                        item = _retry(
                            f"{entry['record_key']} FDE 분류",
                            lambda entry=entry: _classify_batch([entry])[0],
                        )
                        assessments.append(item)
                    except Exception as exc:
                        _store_error(entry, "classification", exc)
                        entry["errors"].append(
                            {
                                "stage": "classification_batch",
                                "message": str(batch_error),
                                "recorded_at": _now(),
                            }
                        )
                        _emit(progress, f"FDE 분류 실패: {entry['record_key']}")

            entry_by_key = {entry["record_key"]: entry for entry in batch}
            for assessment in assessments:
                entry = entry_by_key[assessment.record_key]
                adjusted = _adjust_assessment(assessment, entry)
                entry["fde_classification"] = adjusted.model_dump()
                entry["status"] = "fde_like" if adjusted.is_fde_like else "not_fde"
                _emit(
                    progress,
                    f"FDE 분류 완료: {entry['record_key']} · {adjusted.fde_score}/10",
                )


_UNKNOWN_COMPANIES = {
    "",
    "회사 미상",
    "확인 필요",
    "unknown",
    "none",
    "null",
}


def _company_from_title(title: str) -> str | None:
    match = re.match(r"\[([^\]]{2,60})\]", title.strip())
    if match:
        return match.group(1).strip()
    return None


def _usable_company(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    company = value.strip()
    if company.casefold() in _UNKNOWN_COMPANIES:
        return None
    return company


def _resolve_company(entry: dict[str, Any], parsed: dict[str, Any]) -> str | None:
    source = entry["source"]
    return (
        _usable_company(source.get("company_name"))
        or _company_from_title(str(source.get("title") or ""))
        or _usable_company(parsed.get("company"))
    )


def parse_fde_jobs(
    state: dict[str, Any],
    *,
    workers: int,
    progress: ProgressCallback | None = None,
) -> None:
    entries = [
        entry
        for entry in state["jobs"].values()
        if entry.get("status") in {"fde_like", "parse_error"}
        and "job_info" not in entry
    ]
    if not entries:
        return
    _emit(progress, f"FDE형 공고 {len(entries)}개의 내용을 구조화합니다.")

    with ThreadPoolExecutor(max_workers=min(workers, len(entries))) as executor:
        future_to_entry = {
            executor.submit(
                _retry,
                f"{entry['record_key']} 공고 구조화",
                lambda entry=entry: parse_job(
                    {"job_text": entry["job_text"], "candidate_profile": {}}
                ),
            ): entry
            for entry in entries
        }
        for future in as_completed(future_to_entry):
            entry = future_to_entry[future]
            try:
                job_info = dict(future.result()["job_info"])
                company_name = _resolve_company(entry, job_info)
                if company_name:
                    job_info["company"] = company_name
                    entry["company_name"] = company_name
                    normalized = _normalize_company_name(company_name)
                    entry["company_key"] = normalized or entry["record_key"]
                else:
                    job_info["company"] = "회사 미상"
                    entry["company_name"] = None
                    entry["company_key"] = entry["record_key"]
                job_info["position"] = str(entry["source"].get("title") or "")
                entry["job_info"] = job_info
                entry["status"] = "parsed"
                _emit(progress, f"공고 구조화 완료: {entry['record_key']}")
            except Exception as exc:
                _store_error(entry, "parse", exc)
                _emit(progress, f"공고 구조화 실패: {entry['record_key']}")


def _empty_company_info(company_name: str) -> dict[str, Any]:
    return {
        "company": company_name,
        "products": [],
        "customers_and_cases": [],
        "business_model": "공개 정보 부족",
        "recent_activity": [],
        "evidence": [],
    }


def _empty_workplace_evidence(company_name: str) -> dict[str, Any]:
    return {
        "company": company_name,
        "evidence": [],
        "conflicts": [],
        "unknowns": ["회사명을 확인하지 못해 직장 관련 공개 자료를 조사하지 못했습니다."],
    }


def _empty_workplace_analysis(reason: str) -> dict[str, Any]:
    dimension = {"score": 5.0, "confidence": 0.0, "reason": reason}
    return {
        "salary": dict(dimension),
        "work_life_balance": dict(dimension),
        "culture": dict(dimension),
        "management": dict(dimension),
        "stability_growth": dict(dimension),
        "strengths": [],
        "risks": [reason],
        "interview_questions": ["공개 자료로 확인되지 않은 근무 조건을 면접에서 확인하세요."],
        "overall_score": 5.0,
        "conclusion": reason,
    }


def _research_company_group(company_name: str | None) -> dict[str, Any]:
    display_name = company_name or "회사 미상"
    errors: list[dict[str, str]] = []
    if company_name is None:
        return {
            "company_name": None,
            "company_info": _empty_company_info(display_name),
            "workplace_evidence": _empty_workplace_evidence(display_name),
            "workplace_analysis": _empty_workplace_analysis(
                "회사명을 확인하지 못해 회사 단위 평가의 신뢰도가 매우 낮습니다."
            ),
            "errors": [
                {
                    "stage": "company_resolution",
                    "message": "회사명을 확인하지 못했습니다.",
                }
            ],
        }

    shared_state = {
        "job_info": {"company": company_name},
        "candidate_profile": {},
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        company_future = executor.submit(
            _retry,
            f"{company_name} 회사 조사",
            lambda: research_company(shared_state)["company_info"],
        )
        workplace_future = executor.submit(
            _retry,
            f"{company_name} 직장 조사",
            lambda: research_workplace(shared_state)["workplace_evidence"],
        )
        try:
            company_info = company_future.result()
        except Exception as exc:
            company_info = _empty_company_info(company_name)
            errors.append({"stage": "research_company", "message": str(exc)})
        try:
            workplace_evidence = workplace_future.result()
        except Exception as exc:
            workplace_evidence = _empty_workplace_evidence(company_name)
            errors.append({"stage": "research_workplace", "message": str(exc)})

    try:
        workplace_analysis = _retry(
            f"{company_name} 직장 평가",
            lambda: analyze_workplace(
                {"workplace_evidence": workplace_evidence}
            )["workplace_analysis"],
        )
    except Exception as exc:
        workplace_analysis = _empty_workplace_analysis(
            "직장 환경 평가에 실패해 중립값으로 대체했습니다."
        )
        errors.append({"stage": "analyze_workplace", "message": str(exc)})

    return {
        "company_name": company_name,
        "company_info": company_info,
        "workplace_evidence": workplace_evidence,
        "workplace_analysis": workplace_analysis,
        "errors": errors,
    }


def research_all_companies(
    state: dict[str, Any],
    checkpoint_path: Path,
    *,
    company_workers: int,
    progress: ProgressCallback | None = None,
) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in state["jobs"].values():
        if entry.get("status") != "parsed":
            continue
        groups.setdefault(entry["company_key"], []).append(entry)

    missing = {
        key: entries
        for key, entries in groups.items()
        if key not in state["company_cache"]
    }
    if not missing:
        return
    _emit(progress, f"FDE형 공고의 회사 {len(missing)}곳을 공통 조사합니다.")

    with ThreadPoolExecutor(max_workers=min(company_workers, len(missing))) as executor:
        future_to_key = {
            executor.submit(
                _research_company_group,
                entries[0].get("company_name"),
            ): key
            for key, entries in missing.items()
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            entries = missing[key]
            try:
                bundle = future.result()
            except Exception as exc:
                company_name = entries[0].get("company_name")
                bundle = {
                    "company_name": company_name,
                    "company_info": _empty_company_info(company_name or "회사 미상"),
                    "workplace_evidence": _empty_workplace_evidence(
                        company_name or "회사 미상"
                    ),
                    "workplace_analysis": _empty_workplace_analysis(
                        "회사 공통 조사에 실패해 중립값으로 대체했습니다."
                    ),
                    "errors": [{"stage": "company_bundle", "message": str(exc)}],
                }
            state["company_cache"][key] = bundle
            _save_checkpoint(checkpoint_path, state)
            _emit(
                progress,
                f"회사 공통 조사 완료: {bundle.get('company_name') or key}",
            )


def _analyze_one_job(
    entry: dict[str, Any],
    company_bundle: dict[str, Any],
    candidate_profile: dict[str, Any],
) -> dict[str, Any]:
    job_info = entry["job_info"]
    company_info = company_bundle["company_info"]
    workplace_evidence = company_bundle["workplace_evidence"]
    workplace_analysis = company_bundle["workplace_analysis"]

    fde_si_analysis = _retry(
        f"{entry['record_key']} FDE/SI 분석",
        lambda: analyze_fde_si(
            {"job_info": job_info, "company_info": company_info}
        )["fde_si_analysis"],
    )
    four_factor = _retry(
        f"{entry['record_key']} 4팩터 평가",
        lambda: evaluate_four_factors(
            job_info,
            company_info,
            workplace_evidence,
        ),
    )
    final = _retry(
        f"{entry['record_key']} 최종 적합도 평가",
        lambda: final_job_fit(
            {
                "job_info": job_info,
                "company_info": company_info,
                "fde_si_analysis": fde_si_analysis,
                "workplace_analysis": workplace_analysis,
                "candidate_profile": candidate_profile,
            }
        )["final_job_fit"],
    )
    return {
        "fde_si_analysis": fde_si_analysis,
        "four_factor_evaluation": four_factor,
        "final_job_fit": final,
    }


def analyze_all_fde_jobs(
    state: dict[str, Any],
    candidate_profile: dict[str, Any],
    checkpoint_path: Path,
    *,
    workers: int,
    progress: ProgressCallback | None = None,
) -> None:
    entries = [
        entry
        for entry in state["jobs"].values()
        if entry.get("status") in {"parsed", "analysis_error"}
        and "final_job_fit" not in entry
    ]
    if not entries:
        return
    _emit(progress, f"FDE형 공고 {len(entries)}개를 모두 정밀 분석합니다.")

    with ThreadPoolExecutor(max_workers=min(workers, len(entries))) as executor:
        future_to_entry = {
            executor.submit(
                _analyze_one_job,
                entry,
                state["company_cache"][entry["company_key"]],
                candidate_profile,
            ): entry
            for entry in entries
        }
        for future in as_completed(future_to_entry):
            entry = future_to_entry[future]
            try:
                entry.update(future.result())
                entry["status"] = "complete"
                _emit(progress, f"정밀 분석 완료: {entry['record_key']}")
            except Exception as exc:
                _store_error(entry, "analysis", exc)
                _emit(progress, f"정밀 분석 실패: {entry['record_key']}")
            _save_checkpoint(checkpoint_path, state)


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _rank_completed(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed = [entry for entry in entries if entry.get("status") == "complete"]
    completed.sort(
        key=lambda entry: (
            _number(entry["final_job_fit"].get("fit_score")),
            _number(entry["final_job_fit"].get("confidence")),
            _number(
                entry["four_factor_evaluation"]["summary"].get(
                    "confidence_adjusted_score"
                )
            ),
            _number(entry["fde_classification"].get("fde_score")),
        ),
        reverse=True,
    )
    return [
        {
            "rank": rank,
            "record_key": entry["record_key"],
            "platform": entry["source"].get("platform"),
            "job_id": entry["source"].get("job_id"),
            "company_name": entry.get("company_name"),
            "position": entry["source"].get("title"),
            "source_url": entry["source"].get("source_url"),
            "fit_score": entry["final_job_fit"].get("fit_score"),
            "fit_confidence": entry["final_job_fit"].get("confidence"),
            "recommendation": entry["final_job_fit"].get("recommendation"),
            "fde_score": entry["fde_classification"].get("fde_score"),
            "four_factor_score": entry["four_factor_evaluation"]["summary"].get(
                "weighted_score"
            ),
            "four_factor_confidence": entry["four_factor_evaluation"]["summary"].get(
                "overall_confidence"
            ),
            "confidence_adjusted_four_factor_score": entry[
                "four_factor_evaluation"
            ]["summary"].get("confidence_adjusted_score"),
        }
        for rank, entry in enumerate(completed, start=1)
    ]


def build_public_result(state: dict[str, Any]) -> dict[str, Any]:
    entries = list(state["jobs"].values())
    status_counts: dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    error_statuses = {
        "detail_error",
        "classification_error",
        "parse_error",
        "analysis_error",
    }
    result_status = (
        "partial"
        if any(status_counts.get(status, 0) for status in error_statuses)
        else "ok"
    )
    public_jobs = []
    for entry in entries:
        public = {
            key: value
            for key, value in entry.items()
            if key != "job_text"
        }
        resolved_stages = set()
        if entry.get("detail", {}).get("status") == "ok":
            resolved_stages.add("detail")
        if entry.get("fde_classification"):
            resolved_stages.update({"classification", "classification_batch"})
        if entry.get("job_info"):
            resolved_stages.add("parse")
        if entry.get("final_job_fit"):
            resolved_stages.add("analysis")
        public["errors"] = [
            error
            for error in entry.get("errors", [])
            if error.get("stage") not in resolved_stages
        ]
        public_jobs.append(public)

    return {
        "schema_version": "1.0",
        "status": result_status,
        "started_at": state.get("started_at"),
        "generated_at": _now(),
        "input_path": state.get("input_path"),
        "candidate_profile_path": state.get("candidate_profile_path"),
        "summary": {
            "total_input_records": state.get("source_record_count", len(entries)),
            "duplicate_records_removed": state.get("duplicate_record_count", 0),
            "total_search_jobs": len(entries),
            "detail_success_count": sum(
                1 for entry in entries if entry.get("detail", {}).get("status") == "ok"
            ),
            "fde_like_count": sum(
                1
                for entry in entries
                if entry.get("fde_classification", {}).get("is_fde_like") is True
            ),
            "not_fde_count": status_counts.get("not_fde", 0),
            "completed_analysis_count": status_counts.get("complete", 0),
            "failed_count": sum(status_counts.get(status, 0) for status in error_statuses),
            "company_research_count": len(state.get("company_cache", {})),
            "status_counts": status_counts,
        },
        "ranking_method": (
            "fit_score 내림차순, fit_confidence 내림차순, "
            "confidence_adjusted_four_factor_score 내림차순, fde_score 내림차순"
        ),
        "rankings": _rank_completed(entries),
        "companies": state.get("company_cache", {}),
        "jobs": public_jobs,
    }


def _safe_report_job_id(source: dict[str, Any]) -> str:
    raw = f"{source.get('platform')}_{source.get('job_id')}"
    safe = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", raw).strip("._")
    if len(safe) > 100:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        safe = f"{safe[:80].rstrip('._-')}_{digest}"
    return safe or hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def write_job_reports(
    state: dict[str, Any],
    reports_dir: Path,
    progress: ProgressCallback | None = None,
) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    complete_entries = [
        entry
        for entry in state["jobs"].values()
        if entry.get("status") == "complete"
    ]
    for entry in complete_entries:
        source = entry["source"]
        report_job_id = _safe_report_job_id(source)
        write_reports(
            reports_dir,
            entry.get("company_name") or "회사_미상",
            entry["final_job_fit"],
            str(source.get("title") or ""),
            str(source.get("source_url") or ""),
            job_id=report_job_id,
        )

    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in complete_entries:
        groups.setdefault(entry["company_key"], []).append(entry)
    for company_key, entries in groups.items():
        bundle = state["company_cache"][company_key]
        report_company_name = (
            bundle.get("company_name") or f"회사 미상 ({company_key})"
        )
        ranking_items = []
        sorted_entries = sorted(
            entries,
            key=lambda entry: (
                _number(entry["final_job_fit"].get("fit_score")),
                _number(entry["final_job_fit"].get("confidence")),
            ),
            reverse=True,
        )
        for rank, entry in enumerate(sorted_entries, start=1):
            source = entry["source"]
            ranking_items.append(
                {
                    "rank": rank,
                    "job_id": f"{source.get('platform')}:{source.get('job_id')}",
                    "platform": source.get("platform"),
                    "position": source.get("title"),
                    "source_url": source.get("source_url"),
                    "location": source.get("location"),
                    "employment_type": source.get("employment_type"),
                    "fde_classification": entry["fde_classification"],
                    "job_info": entry["job_info"],
                    "fde_si_analysis": entry["fde_si_analysis"],
                    "four_factor_evaluation": entry["four_factor_evaluation"],
                    "final_job_fit": entry["final_job_fit"],
                }
            )
        comparison = {
            "company": report_company_name,
            "generated_at": _now(),
            "total_active_jobs": len(entries),
            "total_fde_like_jobs": len(entries),
            "analyzed_job_count": len(entries),
            "ranking_method": "fit_score 내림차순, confidence 내림차순",
            "company_info": bundle["company_info"],
            "workplace_evidence": bundle["workplace_evidence"],
            "workplace_analysis": bundle["workplace_analysis"],
            "rankings": ranking_items,
        }
        write_comparison_reports(reports_dir, comparison)
    _emit(
        progress,
        f"공고별·회사별 보고서를 저장했습니다: {reports_dir}",
    )


def run_batch_analysis(
    input_path: Path,
    candidate_profile_path: Path,
    output_path: Path,
    checkpoint_path: Path,
    reports_dir: Path,
    *,
    html_output_path: Path | None = None,
    workers: int = 5,
    company_workers: int = 2,
    extract_batch_size: int = 10,
    classification_batch_size: int = 5,
    resume: bool = True,
    write_reports_enabled: bool = True,
    limit: int | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    search_payload = json.loads(input_path.read_text(encoding="utf-8"))
    source_count = len(search_payload.get("jobs") or [])
    records = _validate_search_records(search_payload)
    duplicate_count = source_count - len(records)
    if limit is not None:
        if limit < 1:
            raise ValueError("limit은 1 이상이어야 합니다.")
        records = records[:limit]
        source_count = len(records)
        duplicate_count = 0
    candidate_profile = json.loads(
        candidate_profile_path.read_text(encoding="utf-8")
    )
    if not isinstance(candidate_profile, dict):
        raise ValueError("후보자 프로필은 JSON 객체여야 합니다.")

    state = _load_checkpoint(
        checkpoint_path,
        input_path,
        candidate_profile_path,
        records,
        _profile_signature(candidate_profile),
        resume=resume,
    )
    state["source_record_count"] = source_count
    state["duplicate_record_count"] = duplicate_count
    _save_checkpoint(checkpoint_path, state)

    collect_job_details(
        state,
        workers=workers,
        extract_batch_size=extract_batch_size,
        progress=progress,
    )
    _save_checkpoint(checkpoint_path, state)
    _atomic_write_json(output_path, build_public_result(state))

    classify_all_jobs(
        state,
        batch_size=classification_batch_size,
        workers=min(workers, 3),
        progress=progress,
    )
    _save_checkpoint(checkpoint_path, state)
    _atomic_write_json(output_path, build_public_result(state))

    parse_fde_jobs(state, workers=workers, progress=progress)
    _save_checkpoint(checkpoint_path, state)

    research_all_companies(
        state,
        checkpoint_path,
        company_workers=company_workers,
        progress=progress,
    )
    analyze_all_fde_jobs(
        state,
        candidate_profile,
        checkpoint_path,
        workers=workers,
        progress=progress,
    )

    state["completed_at"] = _now()
    _save_checkpoint(checkpoint_path, state)
    result = build_public_result(state)
    _atomic_write_json(output_path, result)
    html_path = html_output_path or output_path.with_suffix(".html")
    write_batch_analysis_report(
        html_path,
        result,
        json_filename=output_path.name,
    )
    if write_reports_enabled:
        write_job_reports(state, reports_dir, progress=progress)
    return result


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "search_jobs_result.json의 공고를 FDE 사전 분류한 뒤 "
            "모든 FDE형 공고를 정밀 분석합니다."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=BASE_DIR / "search_jobs_result.json",
        help="search_jobs.py가 생성한 통합 검색 결과 JSON",
    )
    parser.add_argument(
        "--candidate-profile",
        type=Path,
        default=BASE_DIR / "candidate_profile.json",
        help="후보자 프로필 JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE_DIR / "search_jobs_analysis_result.json",
        help="전체 분석 결과 JSON",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=BASE_DIR / ".search_jobs_analysis_checkpoint.json",
        help="중단 후 재개에 사용할 체크포인트 JSON",
    )
    parser.add_argument(
        "--html-output",
        type=Path,
        help="통합 HTML 결과. 생략하면 --output의 확장자를 .html로 바꿉니다.",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=BASE_DIR / "search_jobs_reports",
        help="공고별·회사별 JSON/HTML 보고서 디렉터리",
    )
    parser.add_argument("--workers", type=int, default=5, help="공고 분석 병렬 작업 수")
    parser.add_argument(
        "--company-workers",
        type=int,
        default=2,
        help="회사 공통 조사 병렬 작업 수",
    )
    parser.add_argument(
        "--extract-batch-size",
        type=int,
        default=10,
        help="한 번의 Tavily Extract 요청에 포함할 공고 URL 수",
    )
    parser.add_argument(
        "--classification-batch-size",
        type=int,
        default=5,
        help="한 번의 LLM FDE 판정에 포함할 공고 수",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="앞에서부터 지정한 수의 공고만 처리합니다. 점검용 옵션입니다.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_false",
        dest="resume",
        help="기존 체크포인트를 무시하고 새로 시작합니다.",
    )
    parser.add_argument(
        "--no-reports",
        action="store_false",
        dest="write_reports",
        help="공고별·회사별 JSON/HTML 생성을 생략합니다.",
    )
    parser.set_defaults(resume=True, write_reports=True)
    args = parser.parse_args(argv)
    for name in (
        "workers",
        "company_workers",
        "extract_batch_size",
        "classification_batch_size",
    ):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')}는 1 이상이어야 합니다.")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY가 필요합니다.", file=sys.stderr)
        return 2
    if not os.getenv("TAVILY_API_KEY"):
        print("TAVILY_API_KEY가 필요합니다.", file=sys.stderr)
        return 2

    def progress(message: str) -> None:
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}", flush=True)

    try:
        result = run_batch_analysis(
            args.input,
            args.candidate_profile,
            args.output,
            args.checkpoint,
            args.reports_dir,
            html_output_path=args.html_output,
            workers=args.workers,
            company_workers=args.company_workers,
            extract_batch_size=args.extract_batch_size,
            classification_batch_size=args.classification_batch_size,
            resume=args.resume,
            write_reports_enabled=args.write_reports,
            limit=args.limit,
            progress=progress,
        )
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"배치 분석 실패: {exc}", file=sys.stderr)
        return 2

    summary = result["summary"]
    print("\n===== 통합 분석 완료 =====", flush=True)
    print(f"검색 공고: {summary['total_search_jobs']}개", flush=True)
    print(f"FDE형 공고: {summary['fde_like_count']}개", flush=True)
    print(f"정밀 분석 완료: {summary['completed_analysis_count']}개", flush=True)
    print(f"실패: {summary['failed_count']}개", flush=True)
    print(f"결과 JSON: {args.output}", flush=True)
    print(
        f"결과 HTML: {args.html_output or args.output.with_suffix('.html')}",
        flush=True,
    )
    print(f"체크포인트: {args.checkpoint}", flush=True)
    if args.write_reports:
        print(f"보고서: {args.reports_dir}", flush=True)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
