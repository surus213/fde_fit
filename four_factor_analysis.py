from __future__ import annotations

import json
from typing import Any

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field, model_validator


load_dotenv()


class FactorEvidence(BaseModel):
    claim: str
    source_url: str | None = None


class FactorAssessment(BaseModel):
    score: float | None = Field(default=None, ge=0, le=10)
    confidence: float | None = Field(default=None, ge=0, le=1)
    summary: str
    positive_signals: list[str]
    risks: list[str]
    unknowns: list[str]
    evidence: list[FactorEvidence]

    @model_validator(mode="after")
    def score_and_confidence_are_both_known_or_unknown(self) -> "FactorAssessment":
        if (self.score is None) != (self.confidence is None):
            raise ValueError("score와 confidence는 함께 값이 있거나 함께 null이어야 합니다.")
        return self


class FourFactorAssessment(BaseModel):
    salary: FactorAssessment
    work_life_balance: FactorAssessment
    stability: FactorAssessment
    growth: FactorAssessment
    key_strengths: list[str]
    key_risks: list[str]
    must_verify: list[str]
    conclusion: str


llm = init_chat_model(
    "openai:gpt-5-mini",
    temperature=0,
)

analyzer = llm.with_structured_output(FourFactorAssessment)


FACTOR_WEIGHTS = {
    "salary": 0.25,
    "work_life_balance": 0.25,
    "stability": 0.25,
    "growth": 0.25,
}


def confidence_label(value: float | None) -> str | None:
    if value is None:
        return None
    if value < 0.3:
        return "매우 낮음"
    if value < 0.5:
        return "낮음"
    if value < 0.7:
        return "보통"
    if value < 0.85:
        return "높음"
    return "매우 높음"


def _add_calculated_fields(result: FourFactorAssessment) -> dict[str, Any]:
    payload = result.model_dump()
    factors = {
        key: payload[key]
        for key in FACTOR_WEIGHTS
    }
    for factor in factors.values():
        factor["confidence_label"] = confidence_label(factor["confidence"])

    complete = all(
        factor["score"] is not None and factor["confidence"] is not None
        for factor in factors.values()
    )
    if complete:
        weighted_score = sum(
            float(factors[key]["score"]) * weight
            for key, weight in FACTOR_WEIGHTS.items()
        )
        overall_confidence = sum(
            float(factors[key]["confidence"]) * weight
            for key, weight in FACTOR_WEIGHTS.items()
        )
        confidence_adjusted_score = sum(
            (
                5
                + (float(factors[key]["score"]) - 5)
                * float(factors[key]["confidence"])
            )
            * weight
            for key, weight in FACTOR_WEIGHTS.items()
        )
        strongest_factor = max(
            FACTOR_WEIGHTS,
            key=lambda key: float(factors[key]["score"]),
        )
        weakest_factor = min(
            FACTOR_WEIGHTS,
            key=lambda key: float(factors[key]["score"]),
        )
    else:
        weighted_score = None
        overall_confidence = None
        confidence_adjusted_score = None
        strongest_factor = None
        weakest_factor = None

    return {
        "factor_weights": dict(FACTOR_WEIGHTS),
        "factors": factors,
        "summary": {
            "weighted_score": round(weighted_score, 1)
            if weighted_score is not None
            else None,
            "overall_confidence": round(overall_confidence, 2)
            if overall_confidence is not None
            else None,
            "confidence_adjusted_score": round(confidence_adjusted_score, 1)
            if confidence_adjusted_score is not None
            else None,
            "strongest_factor": strongest_factor,
            "weakest_factor": weakest_factor,
            "key_strengths": payload["key_strengths"],
            "key_risks": payload["key_risks"],
            "must_verify": payload["must_verify"],
            "conclusion": payload["conclusion"],
        },
    }


def evaluate_four_factors(
    job_info: dict[str, Any],
    company_info: dict[str, Any],
    workplace_evidence: dict[str, Any],
) -> dict[str, Any]:
    """공고와 회사 근거를 범용 4팩터 점수와 신뢰도로 평가합니다."""
    result = analyzer.invoke(
        f"""
당신은 채용공고와 회사를 비교 평가하는 분석가입니다.

아래 자료에 명시된 내용만 사용하여 네 가지 팩터를 평가하세요.

1. salary
   해당 직무의 기본급, 성과급, 스톡옵션, 복리후생과 보상 투명성
2. work_life_balance
   실제 근무시간, 초과근무, 휴가, 유연·원격근무, 출장·상주 부담
3. stability
   향후 최소 2년 동안 회사와 해당 직무가 존속하고 보상이 지급될 가능성
4. growth
   회사의 사업 성장과 해당 직무의 기술·역할·경력 자산 성장 가능성

각 팩터의 score는 0~10, confidence는 0~1입니다. confidence는 조건의 좋고
나쁨이 아니라 판단 근거의 품질과 충분성을 뜻합니다.

중요 규칙:

- 회사 전체 평균연봉을 특정 포지션의 실제 연봉으로 간주하지 마세요.
- 투자유치는 성장 신호일 수 있지만 안정성을 자동으로 보장하지 않습니다.
- 회사 공식 자료는 주장으로 취급하고 독립적인 출처보다 낮은 신뢰도를 주세요.
- 표본이 적은 익명 리뷰는 강한 근거로 사용하지 마세요.
- 출처가 충돌하면 risks와 unknowns에 남기고 confidence를 낮추세요.
- 근거가 전혀 없으면 score와 confidence를 모두 null로 반환하세요.
- evidence에는 실제 판단에 사용한 claim과 source_url만 넣으세요.
- 안정성과 성장성을 반드시 별개의 팩터로 평가하세요.

[채용공고]
{json.dumps(job_info, ensure_ascii=False, indent=2)}

[회사 조사]
{json.dumps(company_info, ensure_ascii=False, indent=2)}

[직장 관련 근거]
{json.dumps(workplace_evidence, ensure_ascii=False, indent=2)}
"""
    )
    return _add_calculated_fields(result)
