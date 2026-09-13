import unittest
from unittest.mock import patch

from wanted_jobs import (
    WantedCompany,
    WantedJob,
    find_company_jobs,
    format_job_text,
    get_job_details,
    is_fde_job_title,
    select_company,
)


class WantedJobsTest(unittest.TestCase):
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

    @patch("wanted_jobs.get_company_jobs")
    @patch("wanted_jobs.search_companies")
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

    @patch("wanted_jobs.get_job_detail")
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


if __name__ == "__main__":
    unittest.main()
