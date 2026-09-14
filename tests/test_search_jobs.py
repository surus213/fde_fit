import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from search_jobs import (
    WantedCompany,
    WantedJob,
    _parse_web_search_result,
    find_company_jobs,
    format_job_text,
    get_job_details,
    is_fde_job_title,
    position_match_score,
    position_matches_query,
    search_all_job_platforms,
    search_positions,
    select_company,
    write_search_results,
)


class SearchJobsTest(unittest.TestCase):
    def test_fde_title_detection(self) -> None:
        self.assertTrue(is_fde_job_title("Forward Deployed Engineer - Seoul"))
        self.assertTrue(is_fde_job_title("FDE (3년 이상)"))
        self.assertTrue(is_fde_job_title("Forward-Deployment Engineering"))
        self.assertFalse(is_fde_job_title("Machine Learning Engineer"))

    def test_select_company_accepts_partial_name(self) -> None:
        companies = [
            WantedCompany(1, "센드버드코리아", 2),
            WantedCompany(2, "슈퍼센트", 10),
        ]
        self.assertEqual(select_company("센드버드", companies).id, 1)

    def test_position_search_match_requires_title_match(self) -> None:
        self.assertTrue(
            position_matches_query(
                "Solutions Engineer",
                "Senior AI Solution Engineer (5년 이상)",
            )
        )
        self.assertTrue(
            position_matches_query("Forward Deployed Engineer", "사내 AX 리드 / FDE")
        )
        self.assertFalse(
            position_matches_query("Forward Deployed Engineer", "Backend Engineer")
        )

    def test_position_search_match_accepts_similar_title(self) -> None:
        self.assertGreaterEqual(
            position_match_score("Applied AI Engineer", "Senior AI Engineer"),
            0.72,
        )
        self.assertTrue(
            position_matches_query(
                "Forward Deployed Engineer",
                "Forward Deploy Engineer (FDE)",
            )
        )

    @patch("search_jobs._get_json")
    def test_search_positions_reads_multiple_pages(self, get_json_mock) -> None:
        get_json_mock.side_effect = [
            {
                "total_count": 3,
                "data": [
                    {
                        "id": 10,
                        "company": {"id": 7, "name": "회사 A"},
                        "position": "Solutions Engineer",
                        "annual_from": 2,
                        "annual_to": 5,
                        "employment_type": "regular",
                        "is_outlink": False,
                    },
                    {
                        "id": 11,
                        "company": {"id": 8, "name": "회사 B"},
                        "position": "AI Solutions Engineer",
                    },
                ],
            },
            {
                "total_count": 3,
                "data": [
                    {
                        "id": 12,
                        "company": {"id": 9, "name": "회사 C"},
                        "position": "Customer Engineer",
                    }
                ],
            },
        ]

        positions = search_positions("Solutions Engineer", max_results=3, page_size=2)

        self.assertEqual([position.id for position in positions], [10, 11, 12])
        self.assertEqual(get_json_mock.call_count, 2)
        self.assertEqual(
            get_json_mock.call_args_list[1].args[1],
            {"query": "Solutions Engineer", "offset": 2, "limit": 1},
        )

    @patch("search_jobs.get_company_jobs")
    @patch("search_jobs.search_companies")
    def test_find_company_jobs_returns_all_active_jobs(
        self,
        search_companies_mock,
        get_company_jobs_mock,
    ) -> None:
        company = WantedCompany(7, "테스트", 2)
        fde = WantedJob(
            10, 7, "테스트", "FDE", "active", None, "서울", 2, 5, "regular"
        )
        backend = WantedJob(
            11,
            7,
            "테스트",
            "Backend Engineer",
            "active",
            None,
            "서울",
            2,
            5,
            "regular",
        )
        search_companies_mock.return_value = [company]
        get_company_jobs_mock.return_value = [fde, backend]

        selected, all_jobs = find_company_jobs("테스트")

        self.assertEqual(selected, company)
        self.assertEqual(all_jobs, [fde, backend])

    @patch("search_jobs.get_job_detail")
    def test_get_job_details_fetches_every_active_job(self, get_detail_mock) -> None:
        jobs = [
            WantedJob(
                job_id,
                7,
                "테스트",
                title,
                "active",
                None,
                "서울",
                2,
                5,
                "regular",
            )
            for job_id, title in ((10, "FDE"), (11, "Backend Engineer"))
        ]
        get_detail_mock.side_effect = lambda job_id: {
            "id": job_id,
            "detail": {"main_tasks": f"공고 {job_id}"},
        }

        details = get_job_details(jobs)

        self.assertEqual(set(details), {10, 11})
        self.assertEqual(get_detail_mock.call_count, 2)

    def test_format_job_text_contains_workflow_input_fields(self) -> None:
        job = WantedJob(
            10,
            7,
            "테스트",
            "Forward Deployed Engineer",
            "active",
            None,
            "서울 강남구",
            2,
            5,
            "regular",
        )
        detail = {
            "detail": {
                "intro": "회사와 포지션 소개",
                "main_tasks": "고객 문제 정의",
                "requirements": "Python 경험",
                "preferred_points": "AI 경험",
                "benefits": "유연 근무",
            },
            "skill_tags": [{"title": "Python"}],
        }

        text = format_job_text(job, detail)

        self.assertIn("회사: 테스트", text)
        self.assertIn("https://www.wanted.co.kr/wd/10", text)
        self.assertIn("[주요 업무]\n고객 문제 정의", text)
        self.assertIn("기술 스택: Python", text)

    def test_web_result_filters_non_job_and_closed_postings(self) -> None:
        active = _parse_web_search_result(
            "jobplanet",
            {
                "url": "https://www.jobplanet.co.kr/job/search?posting_ids%5B%5D=1321973",
                "title": "(주)테스트 채용공고 - Forward Deployed Engineer - 잡플래닛",
                "content": "정규직 채용공고입니다.",
            },
            "Forward Deployed Engineer",
        )
        closed = _parse_web_search_result(
            "linkedin",
            {
                "url": "https://kr.linkedin.com/jobs/view/fde-at-test-123456",
                "title": "Test Forward Deployed Engineer 채용중 | LinkedIn",
                "content": "채용공고 마감됨",
            },
            "Forward Deployed Engineer",
        )

        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["company_name"], "(주)테스트")
        self.assertEqual(active["job_id"], "1321973")
        self.assertIsNone(closed)

    def test_web_result_parses_saramin_jumpit_and_expired_deadline(self) -> None:
        active = _parse_web_search_result(
            "saramin",
            {
                "url": "https://jumpit.saramin.co.kr/position/54359875",
                "title": "점핏 | Builder / Forward Deployed Engineer (FDE) 채용",
                "content": (
                    "마감일: 2099-12-31\n"
                    "인공지능팩토리_Builder / Forward Deployed Engineer (FDE) 채용"
                ),
            },
            "Forward Deployed Engineer",
        )
        expired = _parse_web_search_result(
            "saramin",
            {
                "url": "https://jumpit.saramin.co.kr/position/12345678",
                "title": "점핏 | Forward Deployed Engineer 채용",
                "content": "마감일: 2000-01-01",
            },
            "Forward Deployed Engineer",
        )

        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["job_id"], "54359875")
        self.assertEqual(active["company_name"], "인공지능팩토리")
        self.assertEqual(active["title"], "Builder / Forward Deployed Engineer (FDE)")
        self.assertIsNone(expired)

    def test_write_search_results_preserves_unicode_json(self) -> None:
        result = {"status": "ok", "jobs": [{"company_name": "테스트 회사"}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = write_search_results(
                Path(temp_dir) / "nested" / "search_jobs_result.json",
                result,
            )

            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                result,
            )

    @patch("search_jobs.search_web_platform_job_names")
    @patch("search_jobs.search_wanted_job_names")
    def test_all_platform_search_combines_results_and_status(
        self,
        wanted_mock,
        web_mock,
    ) -> None:
        def platform_result(platform, status="ok", jobs=None):
            return {
                "platform": platform,
                "platform_name": platform,
                "status": status,
                "search_method": "test",
                "searched_query_count": 1,
                "failed_query_count": 0,
                "scanned_result_count": 1,
                "matched_job_count": len(jobs or []),
                "notes": [],
                "errors": [],
                "jobs": jobs or [],
            }

        wanted_job = {
            "platform": "wanted",
            "platform_name": "원티드",
            "job_id": "10",
            "title": "Applied AI Engineer",
            "company_name": "테스트 회사",
            "source_url": "https://www.wanted.co.kr/wd/10",
            "matched_job_names": ["Applied AI Engineer"],
            "match_score": 1.0,
        }
        wanted_mock.return_value = platform_result("wanted", jobs=[wanted_job])
        web_mock.side_effect = lambda platform, *args, **kwargs: platform_result(
            platform,
            status="skipped" if platform == "saramin" else "ok",
        )

        result = search_all_job_platforms(
            ["Applied AI Engineer"],
            platforms=("wanted", "groupby", "saramin"),
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["matched_job_count"], 1)
        self.assertEqual(result["matched_company_count"], 1)
        self.assertEqual(
            [summary["platform"] for summary in result["platform_summary"]],
            ["wanted", "groupby", "saramin"],
        )


if __name__ == "__main__":
    unittest.main()
