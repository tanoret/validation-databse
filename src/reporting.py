from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib import pagesizes
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors


DEFAULT_CRITICAL_CHARACTERISTICS = [
    {
        "id": "CC-01",
        "name": "Functional correctness (verification)",
        "description": "Demonstrates the code solves the intended equations and numerical methods correctly for the targeted features.",
        "evidence_types": ["analytic benchmarks", "manufactured solutions", "regression tests", "code-to-code"],
    },
    {
        "id": "CC-02",
        "name": "Accuracy vs data (validation)",
        "description": "Demonstrates agreement with experimental/benchmark data within defined uncertainties for the intended domain.",
        "evidence_types": ["SET/IET validation", "uncertainty-aware comparisons"],
    },
    {
        "id": "CC-03",
        "name": "Applicability / intended-use coverage",
        "description": "Connects evidence to the user’s intended use and defines applicability boundaries (envelope of validity).",
        "evidence_types": ["use-case demonstrations", "range-of-applicability mapping"],
    },
    {
        "id": "CC-04",
        "name": "Reproducibility & configuration control",
        "description": "Shows controlled, repeatable execution with version/commit, run recipe, and traceability to inputs/outputs.",
        "evidence_types": ["build/installation checks", "run recipes", "hashes/artifact integrity"],
    },
    {
        "id": "CC-05",
        "name": "Problem reporting & corrective action",
        "description": "Known issues are documented; impacts assessed; fixes traceable to evidence re-runs (as applicable).",
        "evidence_types": ["issue log", "impact assessment", "re-run after fix"],
    },
]


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _md_escape(s: str) -> str:
    return (s or "").replace("\n", " ").strip()


def _mk_case_row(case: Dict[str, Any], reports: Dict[str, Any]) -> Dict[str, Any]:
    # Build a compact row for tables
    sr = case.get("source_reports", []) or []
    report_refs: List[str] = []
    for item in sr:
        rid = item.get("report_id")
        rep = reports.get(rid, {}) if rid else {}
        num = rep.get("report_number") or rid or "Unknown report"
        note = item.get("note") or ""
        report_refs.append(f"{num} — {note}".strip(" —"))
    return {
        "id": case.get("id"),
        "title": case.get("title"),
        "vv_type": case.get("vv_type"),
        "scope": case.get("scope"),
        "tools": ", ".join(case.get("tools", []) or []),
        "system": case.get("system"),
        "reports": "; ".join(report_refs),
    }


def generate_cgd_markdown(
    db: Dict[str, Any],
    selected_case_ids: List[str],
    user_inputs: Dict[str, Any],
    critical_characteristics: Optional[List[Dict[str, Any]]] = None,
) -> str:
    cases = db.get("cases", {})
    reports = db.get("reports", {})
    critical_characteristics = critical_characteristics or DEFAULT_CRITICAL_CHARACTERISTICS

    selected_cases: List[Dict[str, Any]] = []
    for cid in selected_case_ids:
        c = cases.get(cid)
        if c:
            cc = dict(c)
            cc.setdefault("id", cid)
            selected_cases.append(cc)

    title = user_inputs.get("package_title") or "Commercial Grade Dedication Evidence Package (Draft)"
    tool_name = user_inputs.get("tool_name") or "NEAMS Tool(s)"
    tool_version = user_inputs.get("tool_version") or "TBD"
    intended_use = user_inputs.get("intended_use") or "TBD"
    environment = user_inputs.get("execution_environment") or "TBD"
    requester = user_inputs.get("requester") or "TBD"
    reviewer = user_inputs.get("reviewer") or "TBD"

    md: List[str] = []
    md.append(f"# { _md_escape(title) }\n")
    md.append(f"**Generated:** {_now_iso()}  ")
    md.append(f"**Prepared for:** {_md_escape(requester)}  ")
    md.append(f"**Reviewer/Approver:** {_md_escape(reviewer)}\n")

    md.append("## 1. Purpose and scope\n")
    md.append(f"This document is a **draft CGD-ready evidence package** for **{_md_escape(tool_name)}** (version **{_md_escape(tool_version)}**).  ")
    md.append("It consolidates verification/validation/benchmark/demonstration evidence from the NEAMS validation database and provides traceability to the originating milestone reports.\n")
    md.append(f"**Intended use:** {_md_escape(intended_use)}\n")

    md.append("## 2. Software identification and configuration\n")
    md.append(f"- **Tool(s):** {_md_escape(tool_name)}\n- **Version/Commit:** {_md_escape(tool_version)}\n- **Execution environment:** {_md_escape(environment)}\n")
    md.append("(Add compiler, libraries, platform, container hash, and run recipes as available.)\n")

    md.append("## 3. Critical characteristics and evidence strategy\n")
    for cc in critical_characteristics:
        md.append(f"- **{cc['id']} – {cc['name']}**: {cc['description']}")
    md.append("\n")

    md.append("## 4. Evidence summary (selected cases)\n")
    md.append(f"Total selected cases: **{len(selected_cases)}**\n")

    md.append("### 4.1 Case catalog\n")
    md.append("| Case ID | Title | V&V type | Scope | Tools | Source reports |\n|---|---|---:|---:|---|---|")

    for c in selected_cases:
        row = _mk_case_row(c, reports)
        md.append(
            f"| `{_md_escape(row['id'])}` | {_md_escape(row['title'])} | {_md_escape(row['vv_type'])} | {_md_escape(row['scope'])} | {_md_escape(row['tools'])} | {_md_escape(row['reports'])} |"
        )
    md.append("\n")

    md.append("### 4.2 Case details (appendix-style)\n")
    for c in selected_cases:
        md.append(f"#### {_md_escape(c.get('id'))}: {_md_escape(c.get('title'))}\n")
        md.append(f"- **V&V type:** {_md_escape(c.get('vv_type'))}")
        md.append(f"- **Scope:** {_md_escape(c.get('scope'))}")
        md.append(f"- **System:** {_md_escape(c.get('system'))}")
        md.append(f"- **Tools:** {', '.join(c.get('tools', []) or [])}")
        md.append(f"- **Phenomena:** {', '.join(c.get('phenomena', []) or [])}")
        md.append(f"- **Summary:** {_md_escape(c.get('summary',''))}\n")
        # Metrics
        metrics = c.get("metrics", []) or []
        if metrics:
            md.append("**Metrics:**")
            for m in metrics:
                name = m.get("name")
                val = m.get("value")
                basis = m.get("basis")
                md.append(f"- {name}: {val} ({basis})")
        # References to reports
        md.append("**Source report traceability:**")
        for sr in c.get("source_reports", []) or []:
            rid = sr.get("report_id")
            rep = reports.get(rid, {}) if rid else {}
            num = rep.get("report_number") or rid or "Unknown report"
            rep_title = rep.get("title") or ""
            rep_link = rep.get("file_link") or sr.get("report_link") or ""
            note = sr.get("note") or ""
            sec = sr.get("section") or ""
            md.append(f"- {num} — {rep_title}  ")
            if rep_link:
                md.append(f"  - link: {rep_link}")
            if sec:
                md.append(f"  - section: {sec}")
            if note:
                md.append(f"  - note: {note}")
        md.append("\n")

    md.append("## 5. Limitations and required reviewer actions\n")
    md.append("- This package is automatically generated and requires independent technical review.\n")
    md.append("- Add explicit acceptance criteria and uncertainty treatment for each validation comparison as required by the dedication plan.\n")
    md.append("- Ensure the final package includes controlled input decks, run scripts, and output artifacts (checksummed).\n")

    return "\n".join(md).strip() + "\n"


def render_cgd_pdf(
    db: Dict[str, Any],
    selected_case_ids: List[str],
    user_inputs: Dict[str, Any],
    out_path: Path,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cases = db.get("cases", {})
    reports = db.get("reports", {})

    selected_cases: List[Dict[str, Any]] = []
    for cid in selected_case_ids:
        c = cases.get(cid)
        if c:
            cc = dict(c)
            cc.setdefault("id", cid)
            selected_cases.append(cc)

    title = user_inputs.get("package_title") or "Commercial Grade Dedication Evidence Package (Draft)"
    tool_name = user_inputs.get("tool_name") or "NEAMS Tool(s)"
    tool_version = user_inputs.get("tool_version") or "TBD"
    intended_use = user_inputs.get("intended_use") or "TBD"
    environment = user_inputs.get("execution_environment") or "TBD"

    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph(f"Generated: {_now_iso()}", styles["Normal"]))
    story.append(Paragraph(f"Tool(s): {tool_name}", styles["Normal"]))
    story.append(Paragraph(f"Version/Commit: {tool_version}", styles["Normal"]))
    story.append(Paragraph(f"Execution environment: {environment}", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Purpose and scope", styles["Heading2"]))
    story.append(Paragraph(f"Intended use: {intended_use}", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Selected case catalog", styles["Heading2"]))
    data = [["Case ID", "Title", "V&V type", "Scope", "Tools", "Source reports"]]
    for c in selected_cases:
        row = _mk_case_row(c, reports)
        data.append([row["id"], row["title"], row["vv_type"], row["scope"], row["tools"], row["reports"]])

    tbl = Table(data, colWidths=[1.0*inch, 2.6*inch, 0.8*inch, 0.8*inch, 1.2*inch, 2.6*inch])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.25, colors.grey),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    story.append(tbl)
    story.append(PageBreak())

    story.append(Paragraph("Case details", styles["Heading2"]))
    for c in selected_cases:
        story.append(Paragraph(f"{c.get('id')}: {c.get('title')}", styles["Heading3"]))
        story.append(Paragraph(f"V&V type: {c.get('vv_type')} — Scope: {c.get('scope')} — System: {c.get('system')}", styles["Normal"]))
        story.append(Paragraph(f"Tools: {', '.join(c.get('tools', []) or [])}", styles["Normal"]))
        story.append(Paragraph(f"Phenomena: {', '.join(c.get('phenomena', []) or [])}", styles["Normal"]))
        if c.get("summary"):
            story.append(Paragraph(f"Summary: {c.get('summary')}", styles["Normal"]))
        # Metrics
        metrics = c.get("metrics", []) or []
        if metrics:
            story.append(Paragraph("Metrics:", styles["Normal"]))
            for m in metrics:
                story.append(Paragraph(f"- {m.get('name')}: {m.get('value')} ({m.get('basis','')})", styles["Normal"]))
        # Traceability
        story.append(Paragraph("Source report traceability:", styles["Normal"]))
        for sr in c.get("source_reports", []) or []:
            rid = sr.get("report_id")
            rep = reports.get(rid, {}) if rid else {}
            num = rep.get("report_number") or rid or "Unknown report"
            rep_title = rep.get("title") or ""
            note = sr.get("note") or ""
            story.append(Paragraph(f"- {num}: {rep_title} — {note}", styles["Normal"]))
        story.append(Spacer(1, 0.15 * inch))

    doc = SimpleDocTemplate(str(out_path), pagesize=pagesizes.LETTER, topMargin=0.8*inch, bottomMargin=0.8*inch)
    doc.build(story)
    return out_path
