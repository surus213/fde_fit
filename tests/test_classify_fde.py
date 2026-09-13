import unittest
from unittest.mock import patch

from classify_fde import (
    FDEAssessmentBatch,
    FDEClassificationError,
    FDEJobAssessment,
    classify_fde_jobs,
)
from wanted_jobs import WantedJob


def make_job(job_id: int, title: str) -> WantedJob:
    return WantedJob(
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


class ClassifyFDETest(unittest.TestCase):
    @patch("classify_fde.classifier")
    def test_all_jobs_are_classified_across_batches(self, classifier_mock) -> None:
        jobs = [make_job(job_id, f"Engineer {job_id}") for job_id in range(10, 16)]
        details = {
            job.id: {"detail": {"main_tasks": f"업무 {job.id}"}}
            for job in jobs
        }

        def batch_result(prompt: str) -> FDEAssessmentBatch:
            job_ids = [job.id for job in jobs if f'"job_id": {job.id}' in prompt]
            return FDEAssessmentBatch(
                assessments=[
                    FDEJobAssessment(
                        job_id=job_id,
                        fde_score=2,
                        confidence=0.9,
                        is_fde_like=False,
                        reasons=["일반 개발 업무입니다."],
                        missing_signals=["고객 접점"],
                    )
                    for job_id in job_ids
                ]
            )

        classifier_mock.invoke.side_effect = batch_result

        result = classify_fde_jobs(jobs, details, batch_size=5)

        self.assertEqual([assessment.job_id for assessment in result], list(range(10, 16)))
        self.assertEqual(classifier_mock.invoke.call_count, 2)

    @patch("classify_fde.classifier")
    def test_non_fde_title_can_be_classified_as_fde_like(self, classifier_mock) -> None:
        job = make_job(10, "AI Solution Engineer")
        classifier_mock.invoke.return_value = FDEAssessmentBatch(
            assessments=[
                FDEJobAssessment(
                    job_id=10,
                    fde_score=7,
                    confidence=0.9,
                    is_fde_like=True,
                    reasons=["고객 문제를 정의하고 직접 구현·배포합니다."],
                    missing_signals=["제품 환원 여부"],
                )
            ]
        )

        result = classify_fde_jobs(
            [job],
            {10: {"detail": {"main_tasks": "고객 문제 정의 및 구현"}}},
        )

        self.assertTrue(result[0].is_fde_like)
        self.assertEqual(result[0].fde_score, 7)

    @patch("classify_fde.classifier")
    def test_explicit_fde_title_is_always_included(self, classifier_mock) -> None:
        job = make_job(10, "Forward Deployed Engineer")
        classifier_mock.invoke.return_value = FDEAssessmentBatch(
            assessments=[
                FDEJobAssessment(
                    job_id=10,
                    fde_score=4,
                    confidence=0.5,
                    is_fde_like=False,
                    reasons=[],
                    missing_signals=["상세 업무 부족"],
                )
            ]
        )

        result = classify_fde_jobs([job], {10: {"detail": {}}})

        self.assertTrue(result[0].is_fde_like)
        self.assertEqual(result[0].fde_score, 8)
        self.assertIn("직무명", result[0].reasons[0])

    @patch("classify_fde.classifier")
    def test_rejects_incomplete_assessment_ids(self, classifier_mock) -> None:
        jobs = [make_job(10, "AI Engineer"), make_job(11, "Backend Engineer")]
        classifier_mock.invoke.return_value = FDEAssessmentBatch(
            assessments=[
                FDEJobAssessment(
                    job_id=10,
                    fde_score=2,
                    confidence=0.8,
                    is_fde_like=False,
                    reasons=[],
                    missing_signals=["고객 접점"],
                )
            ]
        )

        with self.assertRaises(FDEClassificationError):
            classify_fde_jobs(
                jobs,
                {10: {"detail": {}}, 11: {"detail": {}}},
            )


if __name__ == "__main__":
    unittest.main()
