"""Self-contained interactive HTML report generator for CDA statistical profiles."""

from __future__ import annotations

import html
from pathlib import Path

from ..models.profile import CorpusProfile


def generate_html_report(profile: CorpusProfile, output_path: Path) -> Path:
    """Render a self-contained HTML dashboard summarizing the CDA corpus profile."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    field_rows = []
    for fp in profile.field_profiles:
        has_type_issue = (
            fp.non_conforming_count > 0
            or fp.xsi_conflict_count > 0
            or fp.invalid_date_count > 0
        )
        has_missing_issue = (
            fp.effective_missing_rate > 0.0
            or fp.null_flavor_total > 0
            or fp.empty_count > 0
            or fp.disguised_null_count > 0
        )

        cov_pct = fp.doc_coverage_rate * 100
        miss_pct = fp.effective_missing_rate * 100
        conf_pct = fp.conformance_rate * 100

        nf_badges = " ".join(
            f'<span class="badge badge-nf">{html.escape(k)}: {v}</span>'
            for k, v in sorted(fp.null_flavor_breakdown.items())
        )
        if fp.empty_count:
            nf_badges += f' <span class="badge badge-warn">Empty: {fp.empty_count}</span>'
        if fp.disguised_null_count:
            nf_badges += (
                f' <span class="badge badge-danger">'
                f"Disguised Null: {fp.disguised_null_count}</span>"
            )
        if fp.docs_missing:
            nf_badges += (
                f' <span class="badge badge-muted">Omitted Docs: {fp.docs_missing}</span>'
            )
        if not nf_badges:
            nf_badges = '<span class="text-muted">None (100% present)</span>'

        type_dist_badges = " ".join(
            f'<span class="badge badge-type">{html.escape(k)}: {v}</span>'
            for k, v in sorted(
                fp.type_distribution.items(), key=lambda kv: kv[1], reverse=True
            )
        )
        if fp.dominant_pattern_mask:
            type_dist_badges += (
                f' <span class="badge badge-mask" title="Dominant string mask">'
                f"Mask: {html.escape(fp.dominant_pattern_mask)}</span>"
            )

        sample_items = []
        for a in fp.anomalies[:4]:
            sample_items.append(
                f"<li><code>{html.escape(a.raw_value)}</code> "
                f'<span class="badge badge-danger">{html.escape(a.category.value)}</span> '
                f'<small class="text-muted">({html.escape(a.file_name)}:{a.line_number})</small></li>'
            )
        samples_html = (
            f'<ul class="sample-list">{"".join(sample_items)}</ul>'
            if sample_items
            else '<span class="text-ok">Conforming</span>'
        )

        conf_class = (
            "bar-ok"
            if conf_pct >= 99.9
            else ("bar-warn" if conf_pct >= 90.0 else "bar-danger")
        )
        miss_class = (
            "bar-ok"
            if miss_pct == 0
            else ("bar-warn" if miss_pct <= 20.0 else "bar-danger")
        )

        field_rows.append(
            f'<tr data-type-issue="{str(has_type_issue).lower()}" '
            f'data-missing-issue="{str(has_missing_issue).lower()}">'
            f'<td class="path-cell"><code>{html.escape(fp.canonical_path)}</code></td>'
            f"<td>{fp.docs_present} / {fp.total_documents} ({cov_pct:.1f}%)</td>"
            f"<td>"
            f'<div class="progress"><div class="progress-bar {miss_class}" '
            f'style="width:{min(100.0, miss_pct):.1f}%"></div></div>'
            f"<small>{miss_pct:.1f}% effective missing</small><br>{nf_badges}"
            f"</td>"
            f'<td><span class="badge badge-primary">{html.escape(fp.apparent_type)}</span>'
            f"<br><small>{type_dist_badges}</small></td>"
            f"<td>"
            f'<div class="progress"><div class="progress-bar {conf_class}" '
            f'style="width:{min(100.0, conf_pct):.1f}%"></div></div>'
            f"<strong>{conf_pct:.1f}%</strong> "
            f"({fp.non_conforming_count} non-conforming)"
            f"</td>"
            f"<td>{samples_html}</td>"
            f"</tr>"
        )

    anomaly_rows = []
    for anom in profile.all_anomalies:
        anomaly_rows.append(
            f"<tr>"
            f"<td><code>{html.escape(anom.file_name)}:{anom.line_number}</code></td>"
            f'<td class="path-cell"><code>{html.escape(anom.canonical_path)}</code></td>'
            f'<td><span class="badge badge-danger">{html.escape(anom.category.value)}</span></td>'
            f"<td><code>{html.escape(anom.raw_value)}</code></td>"
            f"<td>{html.escape(anom.observed_type)}</td>"
            f"<td>{html.escape(anom.expected_type_or_rule)}</td>"
            f"<td>{html.escape(anom.reason)}</td>"
            f"</tr>"
        )

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CDA Statistical Data Profile Report</title>
<style>
  :root {{
    --bg: #0f172a;
    --card: #1e293b;
    --border: #334155;
    --text: #f8fafc;
    --muted: #94a3b8;
    --primary: #38bdf8;
    --ok: #22c55e;
    --warn: #f59e0b;
    --danger: #ef4444;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 24px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
  }}
  h1, h2 {{ margin-top: 0; }}
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
  }}
  .kpi-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .kpi-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
  }}
  .kpi-value {{
    font-size: 28px;
    font-weight: 700;
    color: var(--primary);
  }}
  .kpi-label {{
    font-size: 13px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  .controls {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 16px;
    align-items: center;
  }}
  input[type="search"] {{
    flex: 1;
    min-width: 260px;
    padding: 9px 14px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--card);
    color: var(--text);
    font-size: 14px;
  }}
  .btn {{
    padding: 8px 14px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--card);
    color: var(--text);
    cursor: pointer;
    font-size: 13px;
  }}
  .btn.active {{
    background: var(--primary);
    color: #0f172a;
    border-color: var(--primary);
    font-weight: 600;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--card);
    border-radius: 8px;
    overflow: hidden;
    font-size: 13px;
    margin-bottom: 32px;
  }}
  th, td {{
    padding: 10px 12px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }}
  th {{
    background: #0f172a;
    color: var(--muted);
    font-weight: 600;
    position: sticky;
    top: 0;
  }}
  tr:hover {{ background: rgba(255, 255, 255, 0.03); }}
  .path-cell {{
    word-break: break-all;
    max-width: 420px;
  }}
  code {{
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 12px;
    color: #e2e8f0;
  }}
  .badge {{
    display: inline-block;
    padding: 2px 7px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    margin: 2px 2px 2px 0;
  }}
  .badge-primary {{ background: rgba(56, 189, 248, 0.2); color: #38bdf8; }}
  .badge-type {{ background: rgba(148, 163, 184, 0.2); color: #cbd5e1; }}
  .badge-mask {{ background: rgba(168, 85, 247, 0.2); color: #c084fc; }}
  .badge-nf {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; }}
  .badge-warn {{ background: rgba(245, 158, 11, 0.25); color: #fcd34d; }}
  .badge-danger {{ background: rgba(239, 68, 68, 0.25); color: #fca5a5; }}
  .badge-muted {{ background: rgba(100, 116, 139, 0.25); color: #94a3b8; }}
  .text-muted {{ color: var(--muted); }}
  .text-ok {{ color: var(--ok); font-weight: 600; }}
  .progress {{
    height: 6px;
    background: #334155;
    border-radius: 3px;
    overflow: hidden;
    margin-bottom: 4px;
    width: 140px;
  }}
  .progress-bar {{ height: 100%; }}
  .bar-ok {{ background: var(--ok); }}
  .bar-warn {{ background: var(--warn); }}
  .bar-danger {{ background: var(--danger); }}
  .sample-list {{
    margin: 0;
    padding-left: 16px;
  }}
  .sample-list li {{ margin-bottom: 3px; }}
</style>
</head>
<body>
  <div class="header">
    <div>
      <h1>CDA Statistical Data Quality &amp; Type Conformance Profile</h1>
      <div class="text-muted">Source Corpus: <code>{html.escape(profile.input_folder)}</code></div>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-value">{profile.total_documents_parsed}</div>
      <div class="kpi-label">CDA Documents Parsed</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value">{profile.total_unique_fields}</div>
      <div class="kpi-label">Canonical Fields Profiled</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value" style="color: var(--warn)">{profile.fields_with_missing_values}</div>
      <div class="kpi-label">Fields w/ Missing or NullFlavor</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value" style="color: var(--danger)">{profile.fields_with_type_anomalies}</div>
      <div class="kpi-label">Fields w/ Type or Null Anomalies</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-value" style="color: var(--danger)">{profile.total_anomaly_instances}</div>
      <div class="kpi-label">Flagged Anomaly Samples</div>
    </div>
  </div>

  <h2>1. Field-Level Statistical Profile</h2>
  <div class="controls">
    <input type="search" id="fieldSearch" placeholder="Filter by XPath, section name (e.g. VitalSigns, birthTime), or type..." oninput="filterFields()">
    <button class="btn active" onclick="setFilter('all', this)">All Fields ({profile.total_unique_fields})</button>
    <button class="btn" onclick="setFilter('type', this)">Non-Conforming Types</button>
    <button class="btn" onclick="setFilter('missing', this)">Missing / NullFlavor</button>
  </div>

  <table id="fieldsTable">
    <thead>
      <tr>
        <th>Canonical Field Path</th>
        <th>Doc Coverage</th>
        <th>Missingness &amp; NullFlavors</th>
        <th>Apparent Data Type &amp; Distribution</th>
        <th>Type Conformance</th>
        <th>Non-Conforming / Anomaly Samples</th>
      </tr>
    </thead>
    <tbody>
      {"".join(field_rows)}
    </tbody>
  </table>

  <h2>2. Non-Conforming Value &amp; Anomaly Audit Log ({len(profile.all_anomalies)})</h2>
  <table id="anomaliesTable">
    <thead>
      <tr>
        <th>File : Line</th>
        <th>Canonical Field Path</th>
        <th>Category</th>
        <th>Raw Offending Value</th>
        <th>Observed</th>
        <th>Expected / Rule</th>
        <th>Explanation</th>
      </tr>
    </thead>
    <tbody>
      {"".join(anomaly_rows) if anomaly_rows else '<tr><td colspan="7" class="text-ok">No anomalies detected across the corpus.</td></tr>'}
    </tbody>
  </table>

<script>
  let currentFilter = 'all';
  function setFilter(mode, btn) {{
    currentFilter = mode;
    document.querySelectorAll('.controls .btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    filterFields();
  }}
  function filterFields() {{
    const q = document.getElementById('fieldSearch').value.toLowerCase();
    const rows = document.querySelectorAll('#fieldsTable tbody tr');
    rows.forEach(row => {{
      const textMatch = row.textContent.toLowerCase().includes(q);
      let modeMatch = true;
      if (currentFilter === 'type') {{
        modeMatch = row.getAttribute('data-type-issue') === 'true';
      }} else if (currentFilter === 'missing') {{
        modeMatch = row.getAttribute('data-missing-issue') === 'true';
      }}
      row.style.display = (textMatch && modeMatch) ? '' : 'none';
    }});
  }}
</script>
</body>
</html>
"""
    output_path.write_text(html_doc, encoding="utf-8")
    return output_path
