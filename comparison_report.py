from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from html_report import safe_company_filename


DIMENSION_LABELS = {
    "salary": "연봉·보상",
    "work_life_balance": "워라밸",
    "culture": "조직문화",
    "management": "경영진",
    "stability_growth": "안정성·성장성",
}

FOUR_FACTOR_LABELS = {
    "salary": "연봉",
    "work_life_balance": "워라밸",
    "stability": "안정성",
    "growth": "성장성",
}


def _text(value: Any, fallback: str = "정보 없음") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def _score(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(10.0, float(value)))
    return 0.0


def _list_html(items: Any, empty_message: str) -> str:
    if not isinstance(items, list) or not items:
        return f'<p class="empty">{escape(empty_message)}</p>'
    return "<ul>" + "".join(
        f"<li>{escape(_text(item))}</li>" for item in items
    ) + "</ul>"


def _metric(label: str, value: str, score: float | None = None) -> str:
    bar = ""
    if score is not None:
        width = max(0.0, min(100.0, score * 10))
        bar = (
            '<div class="bar"><span style="width:'
            f'{width:.0f}%"></span></div>'
        )
    return (
        '<div class="metric">'
        f'<span>{escape(label)}</span><strong>{escape(value)}</strong>{bar}</div>'
    )


def _ranking_summary_rows(rankings: list[dict[str, Any]]) -> str:
    rows = []
    for item in rankings:
        final = item.get("final_job_fit") or {}
        classification = item.get("fde_classification") or {}
        fde_si = item.get("fde_si_analysis") or {}
        four_factor = item.get("four_factor_evaluation") or {}
        four_factor_summary = four_factor.get("summary") or {}
        rows.append(
            "<tr>"
            f'<td><span class="rank-small">{escape(_text(item.get("rank")))}</span></td>'
            f'<td><a href="{escape(_text(item.get("source_url")), quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'{escape(_text(item.get("position")))}</a></td>'
            f'<td>{escape(_text(final.get("fit_score")))} / 10</td>'
            f'<td>{escape(_text(final.get("recommendation")))}</td>'
            f'<td>{escape(_text(classification.get("fde_score")))} / 10</td>'
            f'<td>{escape(_text(fde_si.get("fde_score")))} / '
            f'{escape(_text(fde_si.get("si_score")))}</td>'
            f'<td>{escape(_text(four_factor_summary.get("weighted_score"), "-"))} / 10</td>'
            "</tr>"
        )
    return "".join(rows)


def _ranking_cards(rankings: list[dict[str, Any]]) -> str:
    cards = []
    for item in rankings:
        final = item.get("final_job_fit") or {}
        classification = item.get("fde_classification") or {}
        fde_si = item.get("fde_si_analysis") or {}
        four_factor = item.get("four_factor_evaluation") or {}
        four_factor_summary = four_factor.get("summary") or {}
        confidence = final.get("confidence")
        confidence_text = (
            f"{float(confidence) * 100:.0f}%"
            if isinstance(confidence, (int, float))
            else "-"
        )
        fit_score = _score(final.get("fit_score"))
        fde_score = _score(classification.get("fde_score"))
        fde_si_score = _score(fde_si.get("fde_score"))
        si_score = _score(fde_si.get("si_score"))
        four_factor_score = _score(four_factor_summary.get("weighted_score"))
        four_factor_confidence = four_factor_summary.get("overall_confidence")
        four_factor_confidence_text = (
            f"{float(four_factor_confidence) * 100:.0f}%"
            if isinstance(four_factor_confidence, (int, float))
            else "-"
        )

        cards.append(
            f"""
<article class="job-card">
  <div class="job-heading">
    <span class="rank">#{escape(_text(item.get('rank')))}</span>
    <div>
      <p class="overline">공고 ID {escape(_text(item.get('job_id')))}</p>
      <h2>{escape(_text(item.get('position')))}</h2>
      <a href="{escape(_text(item.get('source_url')), quote=True)}" target="_blank"
         rel="noopener noreferrer">원본 공고 열기 ↗</a>
    </div>
  </div>
  <div class="metrics">
    {_metric('최종 적합도', f'{fit_score:g} / 10', fit_score)}
    {_metric('최종 신뢰도', confidence_text)}
    {_metric('탐색 FDE 유사도', f'{fde_score:g} / 10', fde_score)}
    {_metric('정밀 FDE 점수', f'{fde_si_score:g} / 10', fde_si_score)}
    {_metric('SI 점수', f'{si_score:g} / 10', si_score)}
    {_metric('4팩터 종합', f'{four_factor_score:g} / 10', four_factor_score)}
    {_metric('4팩터 신뢰도', four_factor_confidence_text)}
  </div>
  <div class="recommendation">
    <span>추천</span><strong>{escape(_text(final.get('recommendation')))}</strong>
  </div>
  <p class="conclusion">{escape(_text(final.get('final_conclusion')))}</p>
  <div class="details-grid">
    <section class="good"><h3>적합한 이유</h3>
      {_list_html(final.get('why_fit'), '확인된 강점이 없습니다.')}</section>
    <section class="risk"><h3>우려 사항</h3>
      {_list_html(final.get('concerns'), '확인된 우려 사항이 없습니다.')}</section>
  </div>
  <section class="verify"><h3>지원 전에 확인할 사항</h3>
    {_list_html(final.get('must_verify_before_joining'), '추가 확인 항목이 없습니다.')}</section>
  {_four_factor_table(four_factor)}
  <details><summary>FDE 후보 판정 근거</summary>
    {_list_html(classification.get('reasons'), '판정 근거가 없습니다.')}
    <h4>부족하거나 확인되지 않은 신호</h4>
    {_list_html(classification.get('missing_signals'), '누락된 핵심 신호가 없습니다.')}
  </details>
</article>
"""
        )
    return "".join(cards)


def _four_factor_table(evaluation: dict[str, Any]) -> str:
    factors = evaluation.get("factors") or {}
    if not factors:
        return ""
    rows = []
    for key, label in FOUR_FACTOR_LABELS.items():
        factor = factors.get(key) or {}
        confidence = factor.get("confidence")
        confidence_text = (
            f"{float(confidence) * 100:.0f}%"
            if isinstance(confidence, (int, float))
            else "-"
        )
        score = factor.get("score")
        score_text = f"{float(score):g} / 10" if isinstance(score, (int, float)) else "-"
        rows.append(
            "<tr>"
            f"<th>{escape(label)}</th>"
            f"<td>{escape(score_text)}</td>"
            f"<td>{escape(confidence_text)}</td>"
            f"<td>{escape(_text(factor.get('summary')))}</td>"
            "</tr>"
        )
    return (
        '<section class="four-factor"><h3>4팩터 평가</h3>'
        '<div class="table-wrap"><table>'
        '<thead><tr><th>팩터</th><th>점수</th><th>신뢰도</th><th>근거</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _workplace_rows(workplace: dict[str, Any]) -> str:
    rows = []
    for key, label in DIMENSION_LABELS.items():
        dimension = workplace.get(key) or {}
        score = _score(dimension.get("score"))
        confidence = dimension.get("confidence")
        confidence_text = (
            f"{float(confidence) * 100:.0f}%"
            if isinstance(confidence, (int, float))
            else "-"
        )
        rows.append(
            "<tr>"
            f"<th>{escape(label)}</th>"
            f"<td>{score:g} / 10</td>"
            f"<td>{escape(confidence_text)}</td>"
            f"<td>{escape(_text(dimension.get('reason')))}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_comparison_html(comparison: dict[str, Any]) -> str:
    """공고별 최종 적합도 순위를 독립 HTML 비교 보고서로 렌더링합니다."""
    company = _text(comparison.get("company"), "회사")
    rankings = comparison.get("rankings") or []
    workplace = comparison.get("workplace_analysis") or {}
    raw_json = escape(json.dumps(comparison, ensure_ascii=False, indent=2))

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(company)} FDE 공고 비교</title>
  <style>
    :root {{ color-scheme:light; --ink:#172033; --muted:#667085; --line:#e6eaf0;
      --paper:#fff; --bg:#f4f6fa; --primary:#5367ff; --good:#087f5b; --risk:#c43f4f; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font-family:-apple-system,
      BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif; line-height:1.6; }}
    main {{ width:min(1180px,calc(100% - 32px)); margin:36px auto 72px; }}
    .hero {{ padding:38px; border-radius:22px; color:#fff;
      background:linear-gradient(135deg,#151d36,#3545a5); box-shadow:0 18px 48px #1d285029; }}
    .overline {{ margin:0 0 5px; color:#77809a; font-size:12px; letter-spacing:.06em;
      text-transform:uppercase; }}
    .hero .overline {{ color:#cbd2ff; }}
    h1 {{ margin:0; font-size:clamp(30px,5vw,50px); line-height:1.12; }}
    .hero p:last-child {{ margin:15px 0 0; color:#e0e4ff; }}
    .panel,.job-card {{ margin-top:18px; padding:28px; border:1px solid var(--line);
      border-radius:18px; background:var(--paper); box-shadow:0 10px 30px #1f2a440c; }}
    .table-wrap {{ overflow-x:auto; }} table {{ width:100%; border-collapse:collapse; }}
    th,td {{ padding:13px 12px; border-bottom:1px solid var(--line); text-align:left;
      white-space:nowrap; }} th {{ color:var(--muted); font-size:13px; }}
    td a,.job-heading a {{ color:#3f51d7; font-weight:600; text-decoration:none; }}
    .rank-small {{ display:inline-grid; place-items:center; width:28px; height:28px;
      border-radius:50%; color:#fff; background:var(--primary); font-weight:800; }}
    .job-heading {{ display:flex; gap:18px; align-items:flex-start; }}
    .rank {{ flex:0 0 auto; display:grid; place-items:center; width:62px; height:62px;
      border-radius:18px; color:#fff; background:var(--primary); font-size:22px; font-weight:800; }}
    h2 {{ margin:0 0 4px; font-size:25px; }} h3 {{ margin:0 0 10px; font-size:17px; }}
    h4 {{ margin:18px 0 8px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:22px 0; }}
    .metric {{ padding:15px; border:1px solid var(--line); border-radius:13px; }}
    .metric>span {{ display:block; color:var(--muted); font-size:12px; }}
    .metric strong {{ display:block; margin-top:3px; font-size:19px; }}
    .bar {{ height:5px; margin-top:10px; overflow:hidden; border-radius:99px; background:#edf0f5; }}
    .bar span {{ display:block; height:100%; border-radius:inherit; background:var(--primary); }}
    .recommendation {{ display:flex; align-items:center; gap:10px; }}
    .recommendation span {{ color:var(--muted); font-size:13px; }}
    .recommendation strong {{ padding:5px 11px; border-radius:99px; color:#26369e;
      background:#edf0ff; }}
    .conclusion {{ margin:16px 0 0; padding:17px; border-left:4px solid var(--primary);
      border-radius:8px; background:#f7f8ff; }}
    .details-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:18px; }}
    .details-grid section,.verify {{ padding:18px; border-radius:13px; background:#f8f9fb; }}
    .good h3 {{ color:var(--good); }} .risk h3 {{ color:var(--risk); }}
    ul {{ margin:0; padding-left:21px; }} li+li {{ margin-top:7px; }} .empty {{ color:var(--muted); }}
    details {{ margin-top:18px; }} summary {{ cursor:pointer; color:#4652a3; font-weight:650; }}
    pre {{ overflow:auto; padding:18px; border-radius:13px; color:#e5e7eb; background:#111827;
      font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace; }}
    footer {{ margin-top:24px; text-align:center; color:var(--muted); font-size:13px; }}
    @media(max-width:900px) {{ .metrics {{ grid-template-columns:repeat(2,1fr); }} }}
    @media(max-width:650px) {{ main {{ margin-top:16px; }} .hero,.panel,.job-card {{ padding:20px;
      border-radius:14px; }} .details-grid,.metrics {{ grid-template-columns:1fr; }}
      .job-heading {{ flex-direction:column; }} }}
  </style>
</head>
<body>
  <main>
    <header class="hero">
      <p class="overline">FDE Job Comparison Report</p>
      <h1>{escape(company)}</h1>
      <p>활성 공고 {escape(_text(comparison.get('total_active_jobs')))}개 중
        FDE형 {escape(_text(comparison.get('total_fde_like_jobs')))}개를 발견하고,
        {escape(_text(comparison.get('analyzed_job_count')))}개를 비교 분석했습니다.</p>
    </header>

    <section class="panel">
      <h2>종합 순위</h2>
      <p class="empty">정렬 기준: {escape(_text(comparison.get('ranking_method')))}</p>
      <div class="table-wrap"><table>
        <thead><tr><th>순위</th><th>포지션</th><th>적합도</th><th>추천</th>
          <th>FDE 유사도</th><th>FDE / SI</th><th>4팩터</th></tr></thead>
        <tbody>{_ranking_summary_rows(rankings)}</tbody>
      </table></div>
    </section>

    {_ranking_cards(rankings)}

    <section class="panel">
      <h2>공통 직장 평가</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>영역</th><th>점수</th><th>신뢰도</th><th>근거</th></tr></thead>
        <tbody>{_workplace_rows(workplace)}</tbody>
      </table></div>
      <p class="conclusion">{escape(_text(workplace.get('conclusion')))}</p>
    </section>

    <details class="panel"><summary>전체 분석 JSON 보기</summary><pre>{raw_json}</pre></details>
    <footer>fde-fit · 공개 정보와 후보자 기준을 바탕으로 생성된 참고용 비교 분석입니다.</footer>
  </main>
</body>
</html>
"""


def write_comparison_reports(
    output_dir: Path,
    comparison: dict[str, Any],
) -> tuple[Path, Path]:
    """회사별 FDE 공고 비교 결과를 JSON과 HTML로 저장합니다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    company = _text(comparison.get("company"), "company")
    stem = f"{safe_company_filename(company)}_fde_comparison"
    json_path = output_dir / f"{stem}.json"
    html_path = output_dir / f"{stem}.html"

    json_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path.write_text(
        render_comparison_html(comparison),
        encoding="utf-8",
    )
    return json_path, html_path
