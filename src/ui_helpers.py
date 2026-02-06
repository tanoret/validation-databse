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
