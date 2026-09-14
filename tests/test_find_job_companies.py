import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from find_job_companies import (
    find_companies_for_job_names,
    read_job_names,
    write_company_search_result,
)
from search_jobs import WantedPositionSearchResult


class FindJobCompaniesTest(unittest.TestCase):
    def test_read_job_names_ignores_blanks_comments_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "job_names.txt"
            path.write_text(
                "FDE\n\n# comment\nfde\nSolutions Engineer\n",
                encoding="utf-8",
            )

            self.assertEqual(read_job_names(path), ["FDE", "Solutions Engineer"])

    @patch("find_job_companies.search_positions")
    def test_results_are_filtered_deduplicated_and_grouped_by_company(
        self,
        search_positions_mock,
    ) -> None:
        shared_job = WantedPositionSearchResult(
            10, 7, "테스트 회사", "AI Solutions Engineer", 2, 7, "regular", False
        )
        fde_job = WantedPositionSearchResult(
            11, 7, "테스트 회사", "Forward Deployed Engineer", 3, 10, "regular", False
        )
        irrelevant_job = WantedPositionSearchResult(
            12, 8, "다른 회사", "Backend Engineer", 1, 5, "regular", False
        )
        search_positions_mock.side_effect = lambda query, **kwargs: {
            "Solutions Engineer": [shared_job, irrelevant_job],
            "Forward Deployed Engineer": [fde_job, irrelevant_job],
        }[query]

        result = find_companies_for_job_names(
            ["Solutions Engineer", "Forward Deployed Engineer"],
            max_results_per_query=50,
        )

        self.assertEqual(result["matched_company_count"], 1)
        self.assertEqual(result["matched_job_count"], 2)
        self.assertEqual(result["companies"][0]["company_name"], "테스트 회사")
        self.assertEqual(result["companies"][0]["matched_job_count"], 2)
        self.assertEqual(
            result["companies"][0]["matched_job_names"],
            ["Solutions Engineer", "Forward Deployed Engineer"],
        )
        for call in search_positions_mock.call_args_list:
            self.assertEqual(call.kwargs["max_results"], 50)

    def test_write_company_search_result_preserves_json(self) -> None:
        result = {"matched_company_count": 1, "companies": [{"company_id": 7}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = write_company_search_result(
                Path(temp_dir) / "nested" / "result.json",
                result,
            )

            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                result,
            )


if __name__ == "__main__":
    unittest.main()
