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
        self.final_result = {
            "fit_score": 8.0,
            "confidence": 0.8,
            "recommendation": "지원 추천",
            "why_fit": ["강점"],
            "concerns": ["우려"],
            "must_verify_before_joining": ["확인"],
            "final_conclusion": "결론",
        }

    @patch("workflow.run_workflow")
    @patch("workflow.classify_fde_jobs")
    @patch("workflow.get_job_details")
    @patch("workflow.find_company_jobs")
    def test_company_flow_writes_company_named_reports(
        self,
        find_jobs_mock,
        get_details_mock,
        classify_mock,
        run_workflow_mock,
    ) -> None:
        find_jobs_mock.return_value = (self.company, [self.job])
        get_details_mock.return_value = {
            self.job.id: {"detail": {"main_tasks": "고객 문제 해결"}}
        }
        classify_mock.return_value = [
            FDEJobAssessment(
                job_id=self.job.id,
                fde_score=9,
                confidence=0.9,
                is_fde_like=True,
                reasons=["고객 문제를 직접 구현합니다."],
                missing_signals=[],
            )
        ]
        run_workflow_mock.return_value = {
            "job_info": {
                "company": self.company.name,
                "position": self.job.title,
            },
            "final_job_fit": self.final_result,
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
            self.assertTrue((temp_path / "테스트_회사_final_job_fit.json").exists())
            self.assertTrue((temp_path / "테스트_회사_final_job_fit.html").exists())
            run_workflow_mock.assert_called_once()

    @patch("workflow.run_workflow")
    @patch("workflow.classify_fde_jobs")
    @patch("workflow.get_job_details")
    @patch("workflow.find_company_jobs")
    def test_no_fde_job_stops_before_analysis(
        self,
        find_jobs_mock,
        get_details_mock,
        classify_mock,
        run_workflow_mock,
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
        run_workflow_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
