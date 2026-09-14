from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any


FACTOR_LABELS = {
    "salary": "연봉",
    "work_life_balance": "워라밸",
    "stability": "안정성",
    "growth": "성장성",
}


def _text(value: Any, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback if isinstance(fallback, str) else "-"
    return str(value)


def _score(value: Any) -> str:
    return f"{float(value):g}" if isinstance(value, (int, float)) else "-"


def _confidence(value: Any) -> str:
    return f"{float(value) * 100:.0f}%" if isinstance(value, (int, float)) else "-"


def _list(items: Any, empty: str = "정보 없음") -> str:
    if not isinstance(items, list) or not items:
        return f'<p class="muted">{escape(empty)}</p>'
    return "<ul>" + "".join(
        f"<li>{escape(_text(item))}</li>" for item in items
    ) + "</ul>"


def _factor_cells(job: dict[str, Any]) -> str:
    evaluation = job.get("four_factor_evaluation") or {}
    factors = evaluation.get("factors") or {}
    cells = []
    for key in FACTOR_LABELS:
        factor = factors.get(key) or {}
        cells.append(
            f'<td><strong>{escape(_score(factor.get("score")))}</strong>'
            f'<small>{escape(_confidence(factor.get("confidence")))}</small></td>'
        )
    return "".join(cells)


def _ranking_rows(result: dict[str, Any]) -> str:
    jobs = {
        str(job.get("record_key")): job
        for job in result.get("jobs") or []
        if isinstance(job, dict)
    }
    rows = []
    for ranking in result.get("rankings") or []:
        job = jobs.get(str(ranking.get("record_key")), {})
        source_url = escape(_text(ranking.get("source_url"), "#"), quote=True)
        rows.append(
            "<tr>"
            f'<td><span class="rank">{escape(_text(ranking.get("rank")))}</span></td>'
            f'<td><strong>{escape(_text(ranking.get("company_name"), "회사 미상"))}</strong>'
            f'<small>{escape(_text(ranking.get("platform")))}</small></td>'
            f'<td><a href="{source_url}" target="_blank" rel="noopener noreferrer">'
            f'{escape(_text(ranking.get("position")))}</a></td>'
            f'<td><strong>{escape(_score(ranking.get("fde_score")))}</strong></td>'
            f'<td><strong>{escape(_score(ranking.get("fit_score")))}</strong>'
            f'<small>{escape(_confidence(ranking.get("fit_confidence")))}</small></td>'
            f"{_factor_cells(job)}"
            f'<td>{escape(_text(ranking.get("recommendation")))}</td>'
            "</tr>"
        )
    if not rows:
        return '<tr><td colspan="11" class="muted">완료된 정밀 분석이 없습니다.</td></tr>'
    return "".join(rows)


def _job_cards(result: dict[str, Any]) -> str:
    jobs = {
        str(job.get("record_key")): job
        for job in result.get("jobs") or []
        if isinstance(job, dict)
    }
    cards = []
    for ranking in result.get("rankings") or []:
        job = jobs.get(str(ranking.get("record_key")), {})
        evaluation = job.get("four_factor_evaluation") or {}
        factors = evaluation.get("factors") or {}
        summary = evaluation.get("summary") or {}
        final = job.get("final_job_fit") or {}
        factor_rows = []
        for key, label in FACTOR_LABELS.items():
            factor = factors.get(key) or {}
            factor_rows.append(
                "<tr>"
                f"<th>{escape(label)}</th>"
                f'<td>{escape(_score(factor.get("score")))} / 10</td>'
                f'<td>{escape(_confidence(factor.get("confidence")))}</td>'
                f'<td>{escape(_text(factor.get("summary"), "정보 부족"))}</td>'
                "</tr>"
            )
        cards.append(
            f"""
<article class="job-card">
  <div class="job-title">
    <span class="rank large">#{escape(_text(ranking.get('rank')))}</span>
    <div><p class="eyebrow">{escape(_text(ranking.get('platform')))} ·
      {escape(_text(ranking.get('record_key')))}</p>
      <h2>{escape(_text(ranking.get('company_name'), '회사 미상'))}</h2>
      <a href="{escape(_text(ranking.get('source_url'), '#'), quote=True)}" target="_blank"
         rel="noopener noreferrer">{escape(_text(ranking.get('position')))} ↗</a></div>
  </div>
  <div class="metrics">
    <div><span>적합도</span><strong>{escape(_score(ranking.get('fit_score')))} / 10</strong></div>
    <div><span>적합도 신뢰도</span><strong>{escape(_confidence(ranking.get('fit_confidence')))}</strong></div>
    <div><span>FDE</span><strong>{escape(_score(ranking.get('fde_score')))} / 10</strong></div>
    <div><span>4팩터</span><strong>{escape(_score(summary.get('weighted_score')))} / 10</strong></div>
    <div><span>4팩터 신뢰도</span><strong>{escape(_confidence(summary.get('overall_confidence')))}</strong></div>
    <div><span>추천</span><strong>{escape(_text(ranking.get('recommendation')))}</strong></div>
  </div>
  <p class="conclusion">{escape(_text(final.get('final_conclusion'), summary.get('conclusion')))}</p>
  <div class="table-wrap"><table class="factor-table">
    <thead><tr><th>팩터</th><th>점수</th><th>신뢰도</th><th>근거</th></tr></thead>
    <tbody>{''.join(factor_rows)}</tbody>
  </table></div>
  <div class="details">
    <section><h3>적합한 이유</h3>{_list(final.get('why_fit'))}</section>
    <section><h3>주요 위험</h3>{_list(final.get('concerns'))}</section>
    <section><h3>확인할 사항</h3>{_list(final.get('must_verify_before_joining'))}</section>
  </div>
</article>"""
        )
    return "".join(cards)


def _failure_rows(result: dict[str, Any]) -> str:
    rows = []
    for job in result.get("jobs") or []:
        status = str(job.get("status") or "")
        if not status.endswith("_error"):
            continue
        source = job.get("source") or {}
        errors = job.get("errors") or []
        message = "; ".join(
            _text(error.get("message"))
            for error in errors
            if isinstance(error, dict)
        )
        rows.append(
            "<tr>"
            f"<td>{escape(_text(job.get('record_key')))}</td>"
            f"<td>{escape(_text(source.get('title')))}</td>"
            f"<td>{escape(status)}</td>"
            f"<td>{escape(message)}</td>"
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="4" class="muted">실패한 공고가 없습니다.</td></tr>'


def render_batch_analysis_html(result: dict[str, Any], json_filename: str) -> str:
    summary = result.get("summary") or {}
    status_counts = summary.get("status_counts") or {}
    status_chips = "".join(
        f'<span class="chip">{escape(_text(key))} {escape(_text(value))}</span>'
        for key, value in sorted(status_counts.items())
    )
    raw_summary = escape(json.dumps(summary, ensure_ascii=False, indent=2))
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>통합 FDE 채용공고 분석</title>
  <style>
    :root {{ --ink:#172033; --muted:#667085; --line:#e5e9f0; --paper:#fff;
      --bg:#f3f5f9; --primary:#5265e8; --navy:#171f39; }}
    * {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:var(--ink);
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif;
      line-height:1.55; }} main {{ width:min(1500px,calc(100% - 32px)); margin:30px auto 70px; }}
    .hero {{ padding:38px; border-radius:22px; color:white;
      background:linear-gradient(135deg,var(--navy),#394ba6); box-shadow:0 18px 42px #1b255126; }}
    .eyebrow {{ margin:0 0 5px; color:#cbd3ff; font-size:12px; letter-spacing:.07em;
      text-transform:uppercase; }} h1 {{ margin:0; font-size:clamp(30px,5vw,50px); }}
    .summary {{ display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin-top:24px; }}
    .summary div,.metrics div {{ padding:15px; border-radius:13px; background:#ffffff14; }}
    .summary span,.metrics span {{ display:block; font-size:12px; color:#cbd3e5; }}
    .summary strong,.metrics strong {{ display:block; margin-top:3px; font-size:21px; }}
    .panel,.job-card {{ margin-top:18px; padding:25px; border:1px solid var(--line);
      border-radius:18px; background:var(--paper); box-shadow:0 9px 28px #1f2a440b; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:8px; margin:12px 0; }} .chip {{ padding:5px 10px;
      border-radius:99px; background:#eef1ff; color:#3545a5; font-size:13px; font-weight:650; }}
    .table-wrap {{ overflow-x:auto; }} table {{ width:100%; border-collapse:collapse; }}
    th,td {{ padding:12px 10px; border-bottom:1px solid var(--line); text-align:left;
      vertical-align:top; }} th {{ color:var(--muted); font-size:12px; }} td small,td strong {{ display:block; }}
    td small {{ color:var(--muted); }} a {{ color:#4154cb; font-weight:650; text-decoration:none; }}
    .rank {{ display:inline-grid; place-items:center; width:30px; height:30px; border-radius:50%;
      color:white; background:var(--primary); font-weight:800; }} .rank.large {{ width:58px; height:58px;
      border-radius:17px; font-size:19px; }} .job-title {{ display:flex; gap:16px; align-items:flex-start; }}
    h2 {{ margin:0 0 3px; }} h3 {{ margin:0 0 9px; font-size:16px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin:20px 0; }}
    .metrics div {{ border:1px solid var(--line); background:#fafbfc; }}
    .metrics span {{ color:var(--muted); }} .metrics strong {{ color:var(--ink); font-size:18px; }}
    .conclusion {{ padding:15px; border-left:4px solid var(--primary); background:#f7f8ff;
      border-radius:8px; }} .details {{ display:grid; grid-template-columns:repeat(3,1fr); gap:13px;
      margin-top:17px; }} .details section {{ padding:16px; border-radius:12px; background:#f8f9fb; }}
    ul {{ margin:0; padding-left:20px; }} li+li {{ margin-top:6px; }} .muted {{ color:var(--muted); }}
    .source {{ margin-top:14px; }} pre {{ overflow:auto; padding:16px; color:#e5e7eb;
      background:#111827; border-radius:12px; }}
    @media(max-width:1000px) {{ .summary,.metrics {{ grid-template-columns:repeat(3,1fr); }} }}
    @media(max-width:700px) {{ .summary,.metrics,.details {{ grid-template-columns:1fr; }}
      main {{ margin-top:14px; }} .hero,.panel,.job-card {{ padding:19px; border-radius:14px; }} }}
  </style>
</head>
<body><main>
  <header class="hero">
    <p class="eyebrow">FDE Search Results Analysis</p><h1>통합 FDE 채용공고 분석</h1>
    <div class="summary">
      <div><span>입력</span><strong>{escape(_text(summary.get('total_input_records')))}</strong></div>
      <div><span>고유 공고</span><strong>{escape(_text(summary.get('total_search_jobs')))}</strong></div>
      <div><span>본문 확보</span><strong>{escape(_text(summary.get('detail_success_count')))}</strong></div>
      <div><span>FDE형</span><strong>{escape(_text(summary.get('fde_like_count')))}</strong></div>
      <div><span>정밀 분석 완료</span><strong>{escape(_text(summary.get('completed_analysis_count')))}</strong></div>
      <div><span>실패</span><strong>{escape(_text(summary.get('failed_count')))}</strong></div>
    </div>
  </header>
  <section class="panel"><h2>전체 순위</h2><div class="chips">{status_chips}</div>
    <div class="table-wrap"><table><thead><tr><th>#</th><th>회사·플랫폼</th><th>공고</th>
      <th>FDE</th><th>적합도·신뢰도</th><th>연봉</th><th>워라밸</th><th>안정성</th>
      <th>성장성</th><th>추천</th></tr></thead><tbody>{_ranking_rows(result)}</tbody></table></div>
  </section>
  {_job_cards(result)}
  <section class="panel"><h2>처리 실패</h2><div class="table-wrap"><table>
    <thead><tr><th>공고 키</th><th>공고</th><th>상태</th><th>원인</th></tr></thead>
    <tbody>{_failure_rows(result)}</tbody></table></div></section>
  <details class="panel"><summary>실행 요약 JSON</summary><pre>{raw_summary}</pre></details>
  <p class="source"><a href="{escape(json_filename, quote=True)}">전체 결과 JSON 열기</a></p>
</main></body></html>
"""


def write_batch_analysis_report(
    output_path: Path,
    result: dict[str, Any],
    *,
    json_filename: str,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_batch_analysis_html(result, json_filename),
        encoding="utf-8",
    )
    return output_path
