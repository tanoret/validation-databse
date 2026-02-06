from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from src.db import (
    add_or_update_case,
    add_or_update_report,
    get_case,
    get_db_path,
    load_database,
    save_database,
    suggest_case_id,
    validate_case_minimum,
)
from src.ui_helpers import cases_to_dataframe, dataframe_row_to_case, split_csv_field


st.set_page_config(page_title="Case Workbook", page_icon="📒", layout="wide")
st.title("📒 Case Workbook")
st.caption("Excel-like view for filtering, editing, and adding cases. Export updated JSON for GitHub.")


def _get_setting(key: str, default: str = "") -> str:
    try:
        v = st.secrets.get(key)  # type: ignore[attr-defined]
        if v is not None:
            return str(v)
    except Exception:
        pass
    return os.environ.get(key, default)


db_path = get_db_path()
db = load_database(Path(db_path))

df = cases_to_dataframe(db)

# -----------------------
# Filters
# -----------------------
st.sidebar.header("Workbook filters")

uploaded_db = st.sidebar.file_uploader("Load database JSON (session only)", type=["json"])
if uploaded_db is not None:
    try:
        db = json.load(uploaded_db)
        st.sidebar.success("Loaded database into session (this page).")
    except Exception as e:
        st.sidebar.error(f"Failed to load JSON: {e}")

st.sidebar.caption(f"On-disk DB: {db_path}")
st.sidebar.divider()

search_text = st.sidebar.text_input("Search title/system/tools", value="")
vv_types = sorted([v for v in df["vv_type"].dropna().unique().tolist() if str(v).strip()])
scopes = sorted([v for v in df["scope"].dropna().unique().tolist() if str(v).strip()])

selected_vv = st.sidebar.multiselect("V&V type", vv_types, default=[])
selected_scope = st.sidebar.multiselect("Scope", scopes, default=[])

def _contains_any(hay: str, needles: List[str]) -> bool:
    h = (hay or "").lower()
    return any(n.lower() in h for n in needles)

fdf = df.copy()
if selected_vv:
    fdf = fdf[fdf["vv_type"].isin(selected_vv)]
if selected_scope:
    fdf = fdf[fdf["scope"].isin(selected_scope)]
if search_text.strip():
    q = search_text.strip().lower()
    fdf = fdf[
        fdf["title"].fillna("").str.lower().str.contains(q)
        | fdf["system"].fillna("").str.lower().str.contains(q)
        | fdf["tools"].fillna("").str.lower().str.contains(q)
        | fdf["phenomena"].fillna("").str.lower().str.contains(q)
        | fdf["tags"].fillna("").str.lower().str.contains(q)
    ]

st.subheader("Workbook")
st.write(f"Showing **{len(fdf)}** / **{len(df)}** cases")

edited = st.data_editor(
    fdf,
    use_container_width=True,
    hide_index=True,
    num_rows="dynamic",
    column_config={
        "id": st.column_config.TextColumn("id", required=True),
        "title": st.column_config.TextColumn("title", required=True),
        "vv_type": st.column_config.TextColumn("vv_type", required=True),
        "scope": st.column_config.TextColumn("scope", required=True),
        "system": st.column_config.TextColumn("system"),
        "tools": st.column_config.TextColumn("tools (comma-separated)"),
        "phenomena": st.column_config.TextColumn("phenomena (comma-separated)"),
        "tags": st.column_config.TextColumn("tags (comma-separated)"),
        "reports": st.column_config.TextColumn("reports (read-only)"),
    },
    disabled=["reports"],
)

st.divider()

# -----------------------
# Add new report
# -----------------------
with st.expander("➕ Add / update a report entry", expanded=False):
    reports = db.get("reports", {})
    existing_report_ids = sorted(list(reports.keys()))
    col1, col2 = st.columns([1, 2])
    with col1:
        report_id = st.text_input("Report ID (key)", value="", placeholder="e.g., INL-RPT-25-99999")
        select_existing = st.selectbox("…or select existing", options=[""] + existing_report_ids)
        if select_existing and not report_id:
            report_id = select_existing
    with col2:
        report_number = st.text_input("Report number", value="")
        report_title = st.text_input("Title", value="")
        report_date = st.text_input("Date", value="")
        file_name = st.text_input("File name (optional)", value="", placeholder="e.g., MyReport.pdf")
        file_link = st.text_input("File link (optional)", value="", placeholder="e.g., data/reports/MyReport.pdf or a URL")

    if st.button("Save report entry"):
        if not report_id.strip():
            st.error("Report ID is required.")
        else:
            add_or_update_report(
                db,
                report_id.strip(),
                {
                    "report_number": report_number.strip(),
                    "title": report_title.strip(),
                    "date": report_date.strip(),
                    "file_name": file_name.strip(),
                    "file_link": file_link.strip(),
                },
            )
            save_database(db, Path(db_path))
            st.success("Report saved. Reloading…")
            st.rerun()

# -----------------------
# Add new case
# -----------------------
st.subheader("Add a new case")
with st.form("add_case_form"):
    title = st.text_input("Title", value="")
    vv_type = st.selectbox("V&V type", options=["verification", "validation", "benchmark", "demonstration"])
    scope = st.selectbox("Scope", options=["component", "integral", "system"])
    system = st.text_input("System", value="", placeholder="e.g., MSRE primary loop")
    tools = st.text_input("Tools (comma-separated)", value="", placeholder="e.g., SAM, Pronghorn")
    phenomena = st.text_input("Phenomena (comma-separated)", value="", placeholder="e.g., natural circulation, void transport")
    tags = st.text_input("Tags (comma-separated)", value="")
    summary = st.text_area("Summary", value="", height=100)

    # Source report reference
    report_ids = sorted(list((db.get("reports") or {}).keys()))
    report_id = st.selectbox("Source report ID", options=[""] + report_ids)
    source_note = st.text_input("Source note (section/table/figure pointer)", value="", placeholder="e.g., Table 4; Section 3.4.2")
    report_link_override = st.text_input("Report link override (optional)", value="", placeholder="If different from report registry")

    submitted = st.form_submit_button("Add case")

if submitted:
    existing_ids = list((db.get("cases") or {}).keys())
    new_id = suggest_case_id(title=title or "NEW_CASE", existing_ids=existing_ids, prefix="CASE")

    case = {
        "id": new_id,
        "title": title.strip(),
        "vv_type": vv_type,
        "scope": scope,
        "system": system.strip(),
        "tools": split_csv_field(tools),
        "phenomena": split_csv_field(phenomena),
        "tags": split_csv_field(tags),
        "summary": summary.strip(),
        "metrics": [],
        "acceptance_criteria": None,
        "references": [],
        "artifacts": {"inputs": [], "outputs": [], "plots": []},
        "status": {"maturity": "draft", "review": "unconfirmed"},
        "source_reports": [],
    }

    if report_id.strip():
        sr = {"report_id": report_id.strip()}
        if report_link_override.strip():
            sr["report_link"] = report_link_override.strip()
        if source_note.strip():
            sr["note"] = source_note.strip()
        case["source_reports"].append(sr)

    errs = validate_case_minimum(case)
    if errs:
        st.warning("Case added but needs attention:")
        st.json(errs, expanded=False)

    add_or_update_case(db, case)
    save_database(db, Path(db_path))
    st.success(f"Added case `{new_id}`. Reloading…")
    st.rerun()

st.divider()

# -----------------------
# Save edited table back to DB
# -----------------------
st.subheader("Apply workbook edits")
st.markdown(
    """
Edits in the table above only include a **subset** of fields. Applying changes will update those fields and preserve
existing rich fields (metrics, source_reports, artifacts, etc.).
"""
)

if st.button("✅ Apply edits to database"):
    edited_df = edited.copy()
    edited_df = edited_df.fillna("")
    # Apply edits row by row
    for _, row in edited_df.iterrows():
        row_dict = row.to_dict()
        cid = str(row_dict.get("id") or "").strip()
        if not cid:
            continue
        case = dataframe_row_to_case(row_dict, db)
        add_or_update_case(db, case)

    save_database(db, Path(db_path))
    st.success("Database updated.")

st.markdown("### Export updated database JSON")
db_bytes = json.dumps(db, indent=2, ensure_ascii=False).encode("utf-8")
st.download_button(
    "Download updated validation_db.json",
    data=db_bytes,
    file_name="validation_db.json",
    mime="application/json",
    use_container_width=True,
)
