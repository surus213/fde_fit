import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import workflow
from classify_fde import FDEJobAssessment
from wanted_jobs import WantedCompany, WantedJob


class WorkflowCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.company = WantedCompany(7, "테스트 회사", 1)
        self.job = WantedJob(
            10,
            7,
            "테스트 회사",
            "Forward Deployed Engineer",
            "active",
            None,
            "서울",
            2,
            5,
            "regular",
        )
        self.second_job = WantedJob(
            11,
            7,
            "테스트 회사",
            "AI Solution Engineer",
            "active",
            None,
            "서울",
            3,
            7,
            "regular",
        )
        self.final_result = {
            "fit_score": 8.0,
            "confidence": 0.8,
            "recommendation": "지원 추천",
            "why_fit": ["강점"],
            "concerns": ["우려"],
            "must_verify_before_joining": ["확인"],
            "final_conclusion": "결론",
        }

    @patch("workflow.run_fde_job_comparison")
    @patch("workflow.classify_fde_jobs")
    @patch("workflow.get_job_details")
    @patch("workflow.find_company_jobs")
    def test_company_flow_analyzes_all_fde_jobs_and_writes_comparison(
        self,
        find_jobs_mock,
        get_details_mock,
        classify_mock,
        run_comparison_mock,
    ) -> None:
        find_jobs_mock.return_value = (self.company, [self.job, self.second_job])
        get_details_mock.return_value = {
            self.job.id: {"detail": {"main_tasks": "고객 문제 해결"}},
            self.second_job.id: {
                "detail": {"main_tasks": "고객 솔루션 구현"}
            },
        }
        classify_mock.return_value = [
            FDEJobAssessment(
                job_id=self.job.id,
                fde_score=9,
                confidence=0.9,
                is_fde_like=True,
                reasons=["고객 문제를 직접 구현합니다."],
                missing_signals=[],
            ),
            FDEJobAssessment(
                job_id=self.second_job.id,
                fde_score=7,
                confidence=0.8,
                is_fde_like=True,
                reasons=["고객 솔루션을 직접 구현합니다."],
                missing_signals=["제품 환원 여부"],
            ),
        ]
        second_final = {
            **self.final_result,
            "fit_score": 7.0,
            "recommendation": "조건 확인 후 지원",
        }
        run_comparison_mock.return_value = {
            "company": self.company.name,
            "generated_at": "2026-09-13T12:00:00+09:00",
            "total_active_jobs": 2,
            "total_fde_like_jobs": 2,
            "analyzed_job_count": 2,
            "ranking_method": "fit_score 내림차순",
            "company_info": {},
            "workplace_evidence": {},
            "workplace_analysis": {},
            "rankings": [
                {
                    "rank": 1,
                    "job_id": self.job.id,
                    "position": self.job.title,
                    "source_url": self.job.source_url,
                    "location": self.job.location,
                    "employment_type": self.job.employment_type,
                    "fde_classification": classify_mock.return_value[0].model_dump(),
                    "job_info": {},
                    "fde_si_analysis": {"fde_score": 9, "si_score": 2},
                    "final_job_fit": self.final_result,
                },
                {
                    "rank": 2,
                    "job_id": self.second_job.id,
                    "position": self.second_job.title,
                    "source_url": self.second_job.source_url,
                    "location": self.second_job.location,
                    "employment_type": self.second_job.employment_type,
                    "fde_classification": classify_mock.return_value[1].model_dump(),
                    "job_info": {},
                    "fde_si_analysis": {"fde_score": 7, "si_score": 4},
                    "final_job_fit": second_final,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            profile_path = temp_path / "profile.json"
            profile_path.write_text(json.dumps({"priorities": []}), encoding="utf-8")

            with redirect_stdout(StringIO()):
                exit_code = workflow.main(
                    [
                        "--company",
                        "테스트",
                        "--candidate-profile",
                        str(profile_path),
                        "--output-dir",
                        str(temp_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue((temp_path / "테스트_회사_10_final_job_fit.json").exists())
            self.assertTrue((temp_path / "테스트_회사_11_final_job_fit.html").exists())
            self.assertTrue((temp_path / "테스트_회사_fde_comparison.json").exists())
            self.assertTrue((temp_path / "테스트_회사_fde_comparison.html").exists())

        run_comparison_mock.assert_called_once()
        call = run_comparison_mock.call_args
        self.assertEqual(call.args[1], [self.job, self.second_job])
        self.assertEqual(call.kwargs["total_active_jobs"], 2)
        self.assertEqual(call.kwargs["total_fde_like_jobs"], 2)

    @patch("workflow.run_fde_job_comparison")
    @patch("workflow.classify_fde_jobs")
    @patch("workflow.get_job_details")
    @patch("workflow.find_company_jobs")
    def test_no_fde_job_stops_before_analysis(
        self,
        find_jobs_mock,
        get_details_mock,
        classify_mock,
        run_comparison_mock,
    ) -> None:
        backend_job = WantedJob(
            11,
            7,
            "테스트 회사",
            "Backend Engineer",
            "active",
            None,
            "서울",
            2,
            5,
            "regular",
        )
        find_jobs_mock.return_value = (self.company, [backend_job])
        get_details_mock.return_value = {
            backend_job.id: {"detail": {"main_tasks": "내부 API 개발"}}
        }
        classify_mock.return_value = [
            FDEJobAssessment(
                job_id=backend_job.id,
                fde_score=2,
                confidence=0.9,
                is_fde_like=False,
                reasons=["고객 접점이 없는 내부 개발 업무입니다."],
                missing_signals=["고객 문제 정의", "현장 배포"],
            )
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "profile.json"
            profile_path.write_text("{}", encoding="utf-8")

            with redirect_stdout(StringIO()):
                exit_code = workflow.main(
                    [
                        "--company",
                        "테스트",
                        "--candidate-profile",
                        str(profile_path),
                    ]
                )

        self.assertEqual(exit_code, 0)
        classify_mock.assert_called_once_with(
            [backend_job],
            get_details_mock.return_value,
        )
        run_comparison_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
