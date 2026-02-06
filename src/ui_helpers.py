from __future__ import annotations

from typing import Any, Dict, List, Tuple
import pandas as pd


def cases_to_dataframe(db: Dict[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    reports = db.get("reports", {})
    for cid, c in db.get("cases", {}).items():
        c = dict(c)
        c.setdefault("id", cid)
        # Flatten a few key fields for workbook
        sr = c.get("source_reports", []) or []
        report_nums = []
        for item in sr:
            rid = item.get("report_id")
            rep = reports.get(rid, {}) if rid else {}
            report_nums.append(rep.get("report_number") or rid or "")
        rows.append({
            "id": c.get("id"),
            "title": c.get("title"),
            "vv_type": c.get("vv_type"),
            "scope": c.get("scope"),
            "system": c.get("system"),
            "tools": ", ".join(c.get("tools", []) or []),
            "phenomena": ", ".join(c.get("phenomena", []) or []),
            "tags": ", ".join(c.get("tags", []) or []),
            "reports": "; ".join([r for r in report_nums if r]),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["vv_type", "id"], ascending=[True, True])
    return df


def split_csv_field(s: Any) -> List[str]:
    if s is None:
        return []
    if isinstance(s, list):
        return [str(x).strip() for x in s if str(x).strip()]
    s = str(s).strip()
    if not s:
        return []
    parts = [p.strip() for p in s.split(",")]
    return [p for p in parts if p]


def dataframe_row_to_case(row: Dict[str, Any], db: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a workbook row back into a case object (minimal)."""
    cid = str(row.get("id") or "").strip()
    c = db.get("cases", {}).get(cid, {})
    c = dict(c) if isinstance(c, dict) else {}
    c["id"] = cid
    c["title"] = (row.get("title") or "").strip()
    c["vv_type"] = (row.get("vv_type") or "").strip()
    c["scope"] = (row.get("scope") or "").strip()
    c["system"] = (row.get("system") or "").strip()
    c["tools"] = split_csv_field(row.get("tools"))
    c["phenomena"] = split_csv_field(row.get("phenomena"))
    c["tags"] = split_csv_field(row.get("tags"))
    # keep existing fields like summary, source_reports, etc.
    c.setdefault("source_reports", c.get("source_reports") or [])
    c.setdefault("summary", c.get("summary") or "")
    c.setdefault("artifacts", c.get("artifacts") or {"inputs": [], "outputs": [], "plots": []})
    return c


# -----------------------
# Report preview
# -----------------------
def display_report_excerpt(case: Dict[str, Any], db: Dict[str, Any], reports_dirs: Any) -> None:
    """Embed a best-effort excerpt of the source report PDF for a case.

    This expects the PDFs to be present on disk (repo root `pdf/` is preferred).
    """
    import streamlit as st
    from pathlib import Path
    from src.db import resolve_report_file
    from src.pdf_tools import find_best_page, render_page_png, build_pdf_iframe

    reports = db.get("reports", {}) or {}
    sources = case.get("source_reports") or []
    if not sources:
        st.info("No source report pointers available for this case.")
        return

    for idx, src in enumerate(sources, start=1):
        rid = src.get("report_id") or ""
        report = reports.get(rid, {}) if rid else {}
        note = src.get("note") or src.get("section") or ""
        title = report.get("title") or report.get("report_number") or rid or f"Source {idx}"

        st.markdown(f"**{idx}. {title}**")
        if note:
            st.caption(note)

        pdf_path = resolve_report_file(report, reports_dirs)
        if not pdf_path or not Path(pdf_path).exists():
            st.warning("PDF not found. Put the report under `./pdf/` (repo root) using the same filename as `reports[*].file_name` in the DB.")
            st.divider()
            continue

        queries = [case.get("title") or "", note]
        match = find_best_page(Path(pdf_path), queries=queries, max_pages=40)
        page_index = match.page_index if match else 0
        page_num = page_index + 1

        with st.expander(f"View excerpt – page {page_num}", expanded=False):
            pdf_bytes = Path(pdf_path).read_bytes()
            st.download_button(
                "Download report PDF",
                data=pdf_bytes,
                file_name=Path(pdf_path).name,
                mime="application/pdf",
                key=f"dl_{case.get('id','case')}_{idx}",
            )

            png = render_page_png(Path(pdf_path), page_index=page_index, zoom=2.0)
            if png:
                st.image(png, caption=f"Rendered page {page_num}", use_container_width=True)

            st.markdown(build_pdf_iframe(pdf_bytes, page=page_num, height=650), unsafe_allow_html=True)

            if match and match.snippet:
                st.caption("Match snippet (best-effort):")
                st.code(match.snippet[:700])

        st.divider()
