import unittest

from four_factor_analysis import (
    FactorAssessment,
    FactorEvidence,
    FourFactorAssessment,
    _add_calculated_fields,
    confidence_label,
)


def make_factor(score: float, confidence: float) -> FactorAssessment:
    return FactorAssessment(
        score=score,
        confidence=confidence,
        summary="평가",
        positive_signals=[],
        risks=[],
        unknowns=[],
        evidence=[FactorEvidence(claim="근거", source_url="https://example.com")],
    )


class FourFactorAnalysisTest(unittest.TestCase):
    def test_calculates_weighted_and_confidence_adjusted_scores(self) -> None:
        result = FourFactorAssessment(
            salary=make_factor(8, 1.0),
            work_life_balance=make_factor(6, 0.5),
            stability=make_factor(4, 0.5),
            growth=make_factor(10, 1.0),
            key_strengths=["성장성"],
            key_risks=["안정성"],
            must_verify=[],
            conclusion="종합",
        )

        payload = _add_calculated_fields(result)

        self.assertEqual(payload["summary"]["weighted_score"], 7.0)
        self.assertEqual(payload["summary"]["overall_confidence"], 0.75)
        self.assertEqual(payload["summary"]["confidence_adjusted_score"], 7.0)
        self.assertEqual(payload["summary"]["strongest_factor"], "growth")
        self.assertEqual(payload["summary"]["weakest_factor"], "stability")
        self.assertEqual(
            payload["factors"]["work_life_balance"]["confidence_label"],
            "보통",
        )

    def test_missing_factor_keeps_summary_scores_null(self) -> None:
        unknown = FactorAssessment(
            score=None,
            confidence=None,
            summary="정보 부족",
            positive_signals=[],
            risks=[],
            unknowns=["연봉 정보 없음"],
            evidence=[],
        )
        result = FourFactorAssessment(
            salary=unknown,
            work_life_balance=make_factor(6, 0.5),
            stability=make_factor(4, 0.5),
            growth=make_factor(8, 0.8),
            key_strengths=[],
            key_risks=[],
            must_verify=[],
            conclusion="일부 정보 부족",
        )

        payload = _add_calculated_fields(result)

        self.assertIsNone(payload["summary"]["weighted_score"])
        self.assertIsNone(payload["summary"]["overall_confidence"])
        self.assertIsNone(payload["summary"]["confidence_adjusted_score"])
        self.assertIsNone(payload["factors"]["salary"]["confidence_label"])

    def test_confidence_labels_cover_boundaries(self) -> None:
        self.assertEqual(confidence_label(0.29), "매우 낮음")
        self.assertEqual(confidence_label(0.3), "낮음")
        self.assertEqual(confidence_label(0.5), "보통")
        self.assertEqual(confidence_label(0.7), "높음")
        self.assertEqual(confidence_label(0.85), "매우 높음")


if __name__ == "__main__":
    unittest.main()
