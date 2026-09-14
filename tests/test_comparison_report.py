import json
import tempfile
import unittest
from pathlib import Path

from comparison_report import render_comparison_html, write_comparison_reports


class ComparisonReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.comparison = {
            "company": "테스트 & 회사",
            "generated_at": "2026-09-13T12:00:00+09:00",
            "total_active_jobs": 3,
            "total_fde_like_jobs": 2,
            "analyzed_job_count": 2,
            "ranking_method": "fit_score 내림차순",
            "company_info": {},
            "workplace_evidence": {},
            "workplace_analysis": {
                "salary": {"score": 7, "confidence": 0.5, "reason": "근거"},
                "overall_score": 7,
                "conclusion": "직장 결론",
            },
            "rankings": [
                {
                    "rank": 1,
                    "job_id": 10,
                    "position": "FDE <Lead>",
                    "source_url": "https://www.wanted.co.kr/wd/10",
                    "location": "서울",
                    "employment_type": "regular",
                    "fde_classification": {
                        "fde_score": 9,
                        "confidence": 0.9,
                        "reasons": ["고객 문제 정의"],
                        "missing_signals": [],
                    },
                    "job_info": {},
                    "fde_si_analysis": {"fde_score": 9, "si_score": 2},
                    "final_job_fit": {
                        "fit_score": 8.5,
                        "confidence": 0.8,
                        "recommendation": "지원 추천",
                        "why_fit": ["강점"],
                        "concerns": ["<script>alert(1)</script>"],
                        "must_verify_before_joining": ["출장 빈도"],
                        "final_conclusion": "결론",
                    },
                }
            ],
        }

    def test_render_contains_ranking_and_escapes_values(self) -> None:
        html = render_comparison_html(self.comparison)

        self.assertIn("종합 순위", html)
        self.assertIn("테스트 &amp; 회사", html)
        self.assertIn("FDE &lt;Lead&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_write_comparison_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path, html_path = write_comparison_reports(
                Path(temp_dir),
                self.comparison,
            )

            self.assertEqual(json_path.name, "테스트_&_회사_fde_comparison.json")
            self.assertEqual(html_path.name, "테스트_&_회사_fde_comparison.html")
            self.assertEqual(
                json.loads(json_path.read_text(encoding="utf-8")),
                self.comparison,
            )


if __name__ == "__main__":
    unittest.main()
