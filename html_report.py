from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any


def safe_company_filename(company_name: str) -> str:
    """회사명을 사람이 읽을 수 있는 안전한 파일명으로 변환합니다."""
    name = unicodedata.normalize("NFKC", company_name).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name).strip("._")
    return name or "company"


def _text(value: Any, fallback: str = "정보 없음") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def _list_html(items: Any, empty_message: str) -> str:
    if not isinstance(items, list) or not items:
        return f'<p class="empty">{escape(empty_message)}</p>'
    return "<ul>" + "".join(f"<li>{escape(_text(item))}</li>" for item in items) + "</ul>"


def render_html_report(
    final_result: dict[str, Any],
    company_name: str,
    position: str = "",
    source_url: str = "",
) -> str:
    """final_job_fit JSON을 브라우저에서 읽기 좋은 독립 HTML로 렌더링합니다."""
    score = final_result.get("fit_score", "-")
    confidence = final_result.get("confidence")
    confidence_text = (
        f"{float(confidence) * 100:.0f}%"
        if isinstance(confidence, (int, float))
        else "-"
    )
    source_link = (
        f'<a href="{escape(source_url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">원본 공고 열기</a>'
        if source_url
        else ""
    )
    raw_json = escape(json.dumps(final_result, ensure_ascii=False, indent=2))

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(company_name)} 직무 적합도 분석</title>
  <style>
    :root {{ color-scheme: light; --ink:#172033; --muted:#667085; --line:#e7eaf0;
      --paper:#ffffff; --bg:#f4f6fa; --primary:#4255ff; --good:#0e8f66; --risk:#d14b4b; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font-family:-apple-system,
      BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif; line-height:1.65; }}
    main {{ width:min(1040px, calc(100% - 32px)); margin:40px auto 72px; }}
    .hero, section {{ background:var(--paper); border:1px solid var(--line);
      border-radius:18px; box-shadow:0 10px 30px rgba(31,42,68,.06); }}
    .hero {{ padding:34px; background:linear-gradient(135deg,#172033,#303f91); color:#fff; }}
    .eyebrow {{ margin:0 0 8px; color:#cdd4ff; font-size:13px; letter-spacing:.08em;
      text-transform:uppercase; }}
    h1 {{ margin:0; font-size:clamp(28px,5vw,46px); line-height:1.15; }}
    .position {{ margin:10px 0 0; color:#e5e8ff; font-size:18px; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:10px 18px; margin-top:22px; color:#d8dcf5; }}
    .meta a {{ color:#fff; }}
    .metrics {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin:18px 0; }}
    .metric {{ padding:22px; background:var(--paper); border:1px solid var(--line); border-radius:16px; }}
    .metric span {{ display:block; color:var(--muted); font-size:13px; }}
    .metric strong {{ display:block; margin-top:5px; font-size:26px; }}
    section {{ padding:28px; margin-top:18px; }}
    h2 {{ margin:0 0 14px; font-size:21px; }}
    p {{ margin:0; }} ul {{ margin:0; padding-left:22px; }} li + li {{ margin-top:9px; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
    .good h2 {{ color:var(--good); }} .risk h2 {{ color:var(--risk); }}
    .empty {{ color:var(--muted); }}
    details {{ margin-top:18px; }} summary {{ cursor:pointer; color:var(--muted); }}
    pre {{ overflow:auto; padding:18px; border-radius:12px; background:#111827; color:#e5e7eb;
      font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace; }}
    footer {{ margin-top:20px; text-align:center; color:var(--muted); font-size:13px; }}
    @media (max-width:720px) {{ .metrics, .grid {{ grid-template-columns:1fr; }}
      main {{ margin-top:16px; }} .hero, section {{ border-radius:14px; }} }}
  </style>
</head>
<body>
  <main>
    <header class="hero">
      <p class="eyebrow">FDE Job Fit Report</p>
      <h1>{escape(company_name)}</h1>
      <p class="position">{escape(_text(position, "채용공고 분석"))}</p>
      <div class="meta"><span>생성: {escape(datetime.now().astimezone().strftime('%Y-%m-%d %H:%M'))}</span>{source_link}</div>
    </header>

    <div class="metrics">
      <div class="metric"><span>적합도</span><strong>{escape(_text(score))} / 10</strong></div>
      <div class="metric"><span>신뢰도</span><strong>{escape(confidence_text)}</strong></div>
      <div class="metric"><span>추천</span><strong>{escape(_text(final_result.get('recommendation')))}</strong></div>
    </div>

    <section>
      <h2>최종 결론</h2>
      <p>{escape(_text(final_result.get('final_conclusion')))}</p>
    </section>

    <div class="grid">
      <section class="good">
        <h2>적합한 이유</h2>
        {_list_html(final_result.get('why_fit'), '확인된 강점이 없습니다.')}
      </section>
      <section class="risk">
        <h2>우려 사항</h2>
        {_list_html(final_result.get('concerns'), '확인된 우려 사항이 없습니다.')}
      </section>
    </div>

    <section>
      <h2>입사 전에 반드시 확인할 사항</h2>
      {_list_html(final_result.get('must_verify_before_joining'), '추가 확인 항목이 없습니다.')}
    </section>

    <details>
      <summary>원본 JSON 보기</summary>
      <pre>{raw_json}</pre>
    </details>
    <footer>fde-fit · 공개 정보와 후보자 기준을 바탕으로 생성된 참고용 분석입니다.</footer>
  </main>
</body>
</html>
"""


def write_reports(
    output_dir: Path,
    company_name: str,
    final_result: dict[str, Any],
    position: str = "",
    source_url: str = "",
    job_id: int | None = None,
) -> tuple[Path, Path]:
    """회사명 기반 JSON과 HTML 보고서를 함께 저장합니다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    job_suffix = f"_{job_id}" if job_id is not None else ""
    stem = f"{safe_company_filename(company_name)}{job_suffix}_final_job_fit"
    json_path = output_dir / f"{stem}.json"
    html_path = output_dir / f"{stem}.html"

    json_path.write_text(
        json.dumps(final_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path.write_text(
        render_html_report(final_result, company_name, position, source_url),
        encoding="utf-8",
    )
    return json_path, html_path
