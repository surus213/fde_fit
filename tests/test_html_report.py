import json
import tempfile
import unittest
from pathlib import Path

from html_report import render_html_report, safe_company_filename, write_reports


class HtmlReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.result = {
            "fit_score": 8.5,
            "confidence": 0.8,
            "recommendation": "지원 추천",
            "why_fit": ["고객 문제 해결 경험"],
            "concerns": ["출장 빈도 확인 필요"],
            "must_verify_before_joining": ["제품 환원 비율"],
            "final_conclusion": "조건을 확인하고 지원할 가치가 있습니다.",
        }

    def test_safe_company_filename(self) -> None:
        self.assertEqual(safe_company_filename("회사 / 서울"), "회사_서울")

    def test_html_escapes_untrusted_values(self) -> None:
        html = render_html_report(
            {**self.result, "final_conclusion": "<script>alert(1)</script>"},
            "테스트 & 회사",
        )
        self.assertIn("테스트 &amp; 회사", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_write_reports_uses_company_name_and_preserves_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path, html_path = write_reports(
                Path(temp_dir),
                "테스트 회사",
                self.result,
                "FDE",
                "https://www.wanted.co.kr/wd/10",
            )

            self.assertEqual(json_path.name, "테스트_회사_final_job_fit.json")
            self.assertEqual(html_path.name, "테스트_회사_final_job_fit.html")
            self.assertEqual(
                json.loads(json_path.read_text(encoding="utf-8")),
                self.result,
            )
            self.assertIn("원본 공고 열기", html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
