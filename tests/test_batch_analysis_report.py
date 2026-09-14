import tempfile
import unittest
from pathlib import Path

from batch_analysis_report import render_batch_analysis_html, write_batch_analysis_report


class BatchAnalysisReportTest(unittest.TestCase):
    def make_result(self) -> dict:
        return {
            "summary": {
                "total_input_records": 2,
                "total_search_jobs": 1,
                "detail_success_count": 1,
                "fde_like_count": 1,
                "completed_analysis_count": 1,
                "failed_count": 0,
                "status_counts": {"complete": 1},
            },
            "rankings": [
                {
                    "rank": 1,
                    "record_key": "wanted:10",
                    "platform": "wanted",
                    "company_name": "테스트 <회사>",
                    "position": "FDE",
                    "source_url": "https://example.com/?a=1&b=2",
                    "fit_score": 8,
                    "fit_confidence": 0.8,
                    "fde_score": 9,
                    "recommendation": "지원 추천",
                }
            ],
            "jobs": [
                {
                    "record_key": "wanted:10",
                    "status": "complete",
                    "source": {"title": "FDE"},
                    "four_factor_evaluation": {
                        "factors": {
                            key: {
                                "score": 7,
                                "confidence": 0.7,
                                "summary": "근거",
                            }
                            for key in (
                                "salary",
                                "work_life_balance",
                                "stability",
                                "growth",
                            )
                        },
                        "summary": {
                            "weighted_score": 7,
                            "overall_confidence": 0.7,
                        },
                    },
                    "final_job_fit": {
                        "final_conclusion": "결론",
                        "why_fit": [],
                        "concerns": [],
                        "must_verify_before_joining": [],
                    },
                }
            ],
        }

    def test_render_contains_ranking_factors_and_escaped_content(self) -> None:
        html = render_batch_analysis_html(self.make_result(), "result.json")

        self.assertIn("통합 FDE 채용공고 분석", html)
        self.assertIn("연봉", html)
        self.assertIn("워라밸", html)
        self.assertIn("안정성", html)
        self.assertIn("성장성", html)
        self.assertIn("테스트 &lt;회사&gt;", html)
        self.assertNotIn("테스트 <회사>", html)

    def test_write_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "result.html"
            written = write_batch_analysis_report(
                path,
                self.make_result(),
                json_filename="result.json",
            )

            self.assertEqual(written, path)
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
