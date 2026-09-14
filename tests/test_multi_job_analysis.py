import unittest
from unittest.mock import patch

from classify_fde import FDEJobAssessment
from multi_job_analysis import run_fde_job_comparison
from search_jobs import WantedJob


def make_job(job_id: int, title: str) -> WantedJob:
    return WantedJob(
        job_id,
        7,
        "테스트 회사",
        title,
        "active",
        None,
        "서울",
        2,
        5,
        "regular",
    )


def make_assessment(job_id: int, score: int) -> FDEJobAssessment:
    return FDEJobAssessment(
        job_id=job_id,
        fde_score=score,
        confidence=0.9,
        is_fde_like=True,
        reasons=["고객 문제를 직접 구현합니다."],
        missing_signals=[],
    )


class MultiJobAnalysisTest(unittest.TestCase):
    @patch("multi_job_analysis.final_job_fit")
    @patch("multi_job_analysis.analyze_workplace")
    @patch("multi_job_analysis.analyze_fde_si")
    @patch("multi_job_analysis.research_workplace")
    @patch("multi_job_analysis.research_company")
    @patch("multi_job_analysis.parse_job")
    @patch("multi_job_analysis.format_job_text")
    def test_shared_research_runs_once_and_all_jobs_are_ranked(
        self,
        format_job_text_mock,
        parse_job_mock,
        research_company_mock,
        research_workplace_mock,
        analyze_fde_si_mock,
        analyze_workplace_mock,
        final_job_fit_mock,
    ) -> None:
        first = make_job(10, "FDE A")
        second = make_job(11, "FDE B")
        jobs = [first, second]
        details = {10: {"detail": {}}, 11: {"detail": {}}}
        assessments = [make_assessment(10, 8), make_assessment(11, 7)]

        format_job_text_mock.side_effect = lambda job, detail: job.title
        parse_job_mock.side_effect = lambda state: {
            "job_info": {
                "company": "모델 출력 회사",
                "position": state["job_text"],
                "responsibilities": [],
                "requirements": [],
                "preferred": [],
                "tech_stack": [],
            }
        }
        research_company_mock.return_value = {"company_info": {"company": "테스트 회사"}}
        research_workplace_mock.return_value = {
            "workplace_evidence": {"company": "테스트 회사"}
        }
        analyze_workplace_mock.return_value = {
            "workplace_analysis": {"overall_score": 7}
        }
        analyze_fde_si_mock.side_effect = lambda state: {
            "fde_si_analysis": {
                "fde_score": 9 if state["job_info"]["position"] == "FDE A" else 8,
                "si_score": 2,
            }
        }
        final_job_fit_mock.side_effect = lambda state: {
            "final_job_fit": {
                "fit_score": 7.0 if state["job_info"]["position"] == "FDE A" else 9.0,
                "confidence": 0.9,
                "recommendation": "지원 추천",
                "why_fit": [],
                "concerns": [],
                "must_verify_before_joining": [],
                "final_conclusion": "결론",
            }
        }

        result = run_fde_job_comparison(
            "테스트 회사",
            jobs,
            details,
            assessments,
            {"priorities": []},
            total_active_jobs=5,
            total_fde_like_jobs=2,
        )

        self.assertEqual([item["job_id"] for item in result["rankings"]], [11, 10])
        self.assertEqual([item["rank"] for item in result["rankings"]], [1, 2])
        self.assertEqual(result["total_active_jobs"], 5)
        self.assertEqual(parse_job_mock.call_count, 2)
        self.assertEqual(analyze_fde_si_mock.call_count, 2)
        self.assertEqual(final_job_fit_mock.call_count, 2)
        research_company_mock.assert_called_once()
        research_workplace_mock.assert_called_once()
        analyze_workplace_mock.assert_called_once()

        for item in result["rankings"]:
            self.assertEqual(item["job_info"]["company"], "테스트 회사")


if __name__ == "__main__":
    unittest.main()
