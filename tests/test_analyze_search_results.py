import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analyze_search_results import (
    SearchResultFDEAssessment,
    _canonical_job_url,
    _safe_report_job_id,
    _validate_search_records,
    build_public_result,
    classify_all_jobs,
    collect_job_details,
    run_batch_analysis,
)


def make_record(platform: str, job_id: str, title: str) -> dict:
    domain = {
        "wanted": f"https://www.wanted.co.kr/wd/{job_id}",
        "groupby": f"https://groupby.kr/positions/{job_id}",
    }
    return {
        "platform": platform,
        "platform_name": platform,
        "job_id": job_id,
        "title": title,
        "company_name": "테스트 회사",
        "source_url": domain[platform],
        "location": None,
        "experience": None,
        "employment_type": None,
    }


def make_entry(record: dict, status: str = "pending") -> dict:
    key = f"{record['platform']}:{record['job_id']}"
    return {
        "record_key": key,
        "source": record,
        "status": status,
        "errors": [],
    }


class AnalyzeSearchResultsTest(unittest.TestCase):
    def test_report_job_id_is_safe_for_url_shaped_ids(self) -> None:
        source = {
            "platform": "linkedin",
            "job_id": (
                "https://kr.linkedin.com/jobs/view/fde-at-company-123"
                "?position=1&pageNum=0"
            ),
        }

        safe = _safe_report_job_id(source)

        self.assertLessEqual(len(safe), 100)
        self.assertNotIn("/", safe)
        self.assertNotIn("?", safe)
        self.assertNotIn("&", safe)

    def test_duplicate_platform_job_ids_are_merged(self) -> None:
        first = make_record("groupby", "20", "Solutions Engineer")
        first["matched_job_names"] = ["Solutions Engineer"]
        second = dict(first)
        second["source_url"] = "https://groupby.kr/positions/20?ref=search"
        second["matched_job_names"] = ["Customer Engineer"]

        records = _validate_search_records({"jobs": [first, second]})

        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0]["matched_job_names"],
            ["Solutions Engineer", "Customer Engineer"],
        )
        self.assertEqual(
            records[0]["duplicate_source_urls"],
            ["https://groupby.kr/positions/20?ref=search"],
        )

    @patch("analyze_search_results._extract_urls")
    @patch("analyze_search_results.get_job_detail")
    def test_collects_wanted_and_web_details(
        self,
        get_job_detail_mock,
        extract_urls_mock,
    ) -> None:
        wanted = make_record("wanted", "10", "Forward Deployed Engineer")
        groupby = make_record("groupby", "20", "Solutions Engineer")
        state = {
            "jobs": {
                "wanted:10": make_entry(wanted),
                "groupby:20": make_entry(groupby),
            }
        }
        get_job_detail_mock.return_value = {
            "detail": {
                "main_tasks": "고객 문제를 정의하고 직접 구현합니다.",
                "requirements": "Python",
            }
        }
        extract_urls_mock.return_value = {
            _canonical_job_url(groupby["source_url"]): "상세 공고 " * 30
        }

        collect_job_details(state, workers=2, extract_batch_size=10)

        self.assertEqual(state["jobs"]["wanted:10"]["status"], "detail_ready")
        self.assertEqual(state["jobs"]["groupby:20"]["status"], "detail_ready")
        self.assertIn("고객 문제", state["jobs"]["wanted:10"]["job_text"])
        self.assertIn("상세 공고", state["jobs"]["groupby:20"]["job_text"])

    @patch("analyze_search_results._search_url_content")
    @patch("analyze_search_results._extract_urls")
    def test_detail_extract_failure_uses_search_index_fallback(
        self,
        extract_urls_mock,
        search_url_content_mock,
    ) -> None:
        groupby = make_record("groupby", "20", "Forward Deployed Engineer")
        state = {"jobs": {"groupby:20": make_entry(groupby)}}
        extract_urls_mock.return_value = {}
        search_url_content_mock.return_value = "검색 인덱스 공고 본문 " * 20

        collect_job_details(state, workers=1, extract_batch_size=10)

        entry = state["jobs"]["groupby:20"]
        self.assertEqual(entry["status"], "detail_ready")
        self.assertEqual(entry["detail"]["fetch_method"], "tavily_search_fallback")
        self.assertIn("검색 인덱스 공고 본문", entry["job_text"])

    @patch("analyze_search_results._classify_batch")
    def test_explicit_fde_title_is_adjusted_to_at_least_eight(
        self,
        classify_batch_mock,
    ) -> None:
        record = make_record("wanted", "10", "Forward Deployed Engineer")
        entry = make_entry(record, "detail_ready")
        entry["job_text"] = "고객 업무"
        state = {"jobs": {"wanted:10": entry}}
        classify_batch_mock.return_value = [
            SearchResultFDEAssessment(
                record_key="wanted:10",
                fde_score=5,
                confidence=0.7,
                is_fde_like=False,
                reasons=[],
                missing_signals=[],
            )
        ]

        classify_all_jobs(state, batch_size=5, workers=1)

        result = state["jobs"]["wanted:10"]
        self.assertEqual(result["status"], "fde_like")
        self.assertEqual(result["fde_classification"]["fde_score"], 8)
        self.assertTrue(result["fde_classification"]["is_fde_like"])

    def test_public_result_ranks_completed_jobs(self) -> None:
        first_record = make_record("wanted", "10", "FDE A")
        second_record = make_record("groupby", "20", "FDE B")
        first = make_entry(first_record, "complete")
        second = make_entry(second_record, "complete")
        for entry, fit_score in ((first, 7.0), (second, 9.0)):
            entry.update(
                {
                    "company_name": "테스트 회사",
                    "fde_classification": {"fde_score": 8, "is_fde_like": True},
                    "four_factor_evaluation": {
                        "summary": {
                            "weighted_score": 7.0,
                            "overall_confidence": 0.7,
                            "confidence_adjusted_score": 6.5,
                        }
                    },
                    "final_job_fit": {
                        "fit_score": fit_score,
                        "confidence": 0.8,
                        "recommendation": "지원 추천",
                    },
                }
            )
        state = {
            "started_at": "2026-01-01T00:00:00+09:00",
            "input_path": "input.json",
            "candidate_profile_path": "candidate.json",
            "jobs": {"wanted:10": first, "groupby:20": second},
            "company_cache": {},
        }

        result = build_public_result(state)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["summary"]["completed_analysis_count"], 2)
        self.assertEqual(
            [item["record_key"] for item in result["rankings"]],
            ["groupby:20", "wanted:10"],
        )

    @patch("analyze_search_results.analyze_all_fde_jobs")
    @patch("analyze_search_results.research_all_companies")
    @patch("analyze_search_results.parse_fde_jobs")
    @patch("analyze_search_results.classify_all_jobs")
    @patch("analyze_search_results.collect_job_details")
    def test_batch_writes_result_and_checkpoint(
        self,
        collect_mock,
        classify_mock,
        parse_mock,
        research_mock,
        analyze_mock,
    ) -> None:
        record = make_record("wanted", "10", "FDE")

        def collect(state, **kwargs):
            entry = state["jobs"]["wanted:10"]
            entry["status"] = "detail_ready"
            entry["job_text"] = "공고 본문"
            entry["detail"] = {"status": "ok"}

        def classify(state, **kwargs):
            entry = state["jobs"]["wanted:10"]
            entry["status"] = "fde_like"
            entry["fde_classification"] = {
                "fde_score": 8,
                "confidence": 0.9,
                "is_fde_like": True,
                "reasons": [],
                "missing_signals": [],
            }

        def parse(state, **kwargs):
            entry = state["jobs"]["wanted:10"]
            entry.update(
                {
                    "status": "parsed",
                    "company_name": "테스트 회사",
                    "company_key": "테스트회사",
                    "job_info": {"company": "테스트 회사", "position": "FDE"},
                }
            )

        def research(state, checkpoint_path, **kwargs):
            state["company_cache"]["테스트회사"] = {
                "company_name": "테스트 회사",
                "company_info": {},
                "workplace_evidence": {},
                "workplace_analysis": {},
                "errors": [],
            }

        def analyze(state, candidate_profile, checkpoint_path, **kwargs):
            entry = state["jobs"]["wanted:10"]
            entry.update(
                {
                    "status": "complete",
                    "fde_si_analysis": {"fde_score": 8, "si_score": 2},
                    "four_factor_evaluation": {
                        "summary": {
                            "weighted_score": 7.0,
                            "overall_confidence": 0.7,
                            "confidence_adjusted_score": 6.4,
                        }
                    },
                    "final_job_fit": {
                        "fit_score": 8.0,
                        "confidence": 0.8,
                        "recommendation": "지원 추천",
                    },
                }
            )

        collect_mock.side_effect = collect
        classify_mock.side_effect = classify
        parse_mock.side_effect = parse
        research_mock.side_effect = research
        analyze_mock.side_effect = analyze

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            input_path = base / "search.json"
            profile_path = base / "candidate.json"
            output_path = base / "result.json"
            checkpoint_path = base / "checkpoint.json"
            input_path.write_text(
                json.dumps({"jobs": [record]}, ensure_ascii=False),
                encoding="utf-8",
            )
            profile_path.write_text("{}", encoding="utf-8")

            result = run_batch_analysis(
                input_path,
                profile_path,
                output_path,
                checkpoint_path,
                base / "reports",
                write_reports_enabled=False,
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["summary"]["fde_like_count"], 1)
            self.assertEqual(result["summary"]["completed_analysis_count"], 1)
            self.assertTrue(output_path.exists())
            self.assertTrue(checkpoint_path.exists())
            self.assertNotIn("job_text", result["jobs"][0])


if __name__ == "__main__":
    unittest.main()
