from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz


DEFAULT_DB_PATH = Path("data/validation_db.json")


def get_db_path() -> Path:
    """Resolve database path from env var DB_PATH or default repo path."""
    p = os.environ.get("DB_PATH", "").strip()
    return Path(p) if p else DEFAULT_DB_PATH


def load_database(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Database file not found: {path}. Set DB_PATH or place a JSON at data/validation_db.json"
        )
    with path.open("r", encoding="utf-8") as f:
        db = json.load(f)

    # Minimal schema checks
    for key in ("cases", "reports"):
        if key not in db:
            raise ValueError(f"Invalid database schema: missing top-level key '{key}'")

    if not isinstance(db["cases"], dict):
        raise ValueError("Invalid database schema: 'cases' must be an object/dict keyed by case id")

    # Normalize lists for safer UI behavior
    for cid, case in db["cases"].items():
        _normalize_case_inplace(cid, case)

    return db


def save_database(db: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def _normalize_case_inplace(case_id: str, case: Dict[str, Any]) -> None:
    case["id"] = case.get("id") or case_id
    for k in ("tools", "fluids", "phenomena", "tags", "references", "source_reports"):
        v = case.get(k)
        if v is None:
            case[k] = []
        elif isinstance(v, str):
            case[k] = [v]
        elif isinstance(v, list):
            case[k] = v
        else:
            # best effort
            case[k] = list(v)

    # artifacts sub-object
    artifacts = case.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        artifacts = {}
    artifacts.setdefault("inputs", [])
    artifacts.setdefault("outputs", [])
    artifacts.setdefault("plots", [])
    case["artifacts"] = artifacts


def list_cases(db: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a stable list of case dicts with embedded id."""
    out = []
    for cid, c in db["cases"].items():
        if not isinstance(c, dict):
            continue
        cc = dict(c)
        cc.setdefault("id", cid)
        out.append(cc)
    return out


def get_case(db: Dict[str, Any], case_id: str) -> Optional[Dict[str, Any]]:
    c = db["cases"].get(case_id)
    if not c:
        return None
    if "id" not in c:
        c = dict(c)
        c["id"] = case_id
    _normalize_case_inplace(case_id, c)
    return c


def add_or_update_case(db: Dict[str, Any], case: Dict[str, Any]) -> None:
    case_id = case.get("id")
    if not case_id:
        raise ValueError("case.id is required")
    _normalize_case_inplace(case_id, case)
    db["cases"][case_id] = case


def delete_case(db: Dict[str, Any], case_id: str) -> bool:
    if case_id in db["cases"]:
        del db["cases"][case_id]
        return True
    return False


def list_reports(db: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return db.get("reports", {})


def add_or_update_report(db: Dict[str, Any], report_id: str, report: Dict[str, Any]) -> None:
    if not report_id:
        raise ValueError("report_id is required")
    db.setdefault("reports", {})
    db["reports"][report_id] = report


def resolve_report_file(report: Dict[str, Any], reports_dir: Any) -> Optional[Path]:
    """Locate a report PDF on disk.

    `reports_dir` may be:
      - a Path (single directory)
      - a list/tuple of Paths (searched in order)

    The app also automatically checks:
      - ./pdf
      - ./data/reports
    """
    if not report:
        return None

    # Normalize to list[Path]
    dirs: List[Path]
    if isinstance(reports_dir, (list, tuple)):
        dirs = [Path(p) for p in reports_dir]
    else:
        dirs = [Path(reports_dir)]

    for extra in [Path("pdf"), Path("data/reports")]:
        if extra not in dirs:
            dirs.append(extra)

    file_name = (report.get("file_name") or "").strip()
    link = (report.get("file_link") or report.get("report_link") or "").strip()

    candidates: List[str] = []
    if file_name:
        candidates.append(file_name)
    if link:
        base = os.path.basename(link.replace("sandbox:/", ""))
        if base and base not in candidates:
            candidates.append(base)

    for d in dirs:
        for name in candidates:
            p = d / name
            if p.exists():
                return p

    return None


def suggest_case_id(title: str, existing_ids: List[str], prefix: str = "CASE") -> str:
    """Generate a readable, stable-ish id from a title, avoiding collisions."""
    slug = "".join(ch.upper() if ch.isalnum() else "_" for ch in title).strip("_")
    slug = "_".join([s for s in slug.split("_") if s])[:40]
    base = f"{prefix}_{slug}" if slug else f"{prefix}"
    candidate = base
    i = 1
    while candidate in existing_ids:
        i += 1
        candidate = f"{base}_{i:03d}"
    return candidate


def fuzzy_find_case_ids(db: Dict[str, Any], query: str, limit: int = 10) -> List[Tuple[str, int]]:
    """Useful helper for UI autocompletion."""
    q = (query or "").strip().lower()
    if not q:
        return []
    scored = []
    for c in list_cases(db):
        title = (c.get("title") or "")
        cid = c.get("id") or ""
        score = max(
            fuzz.partial_ratio(q, cid.lower()),
            fuzz.partial_ratio(q, title.lower()),
        )
        scored.append((cid, int(score)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


def validate_case_minimum(case: Dict[str, Any]) -> List[str]:
    """Return a list of validation errors (empty = ok)."""
    errors: List[str] = []
    if not case.get("id"):
        errors.append("Missing case.id")
    if not case.get("title"):
        errors.append("Missing case.title")
    if not case.get("vv_type"):
        errors.append("Missing case.vv_type (verification|validation|benchmark|demonstration)")
    if "source_reports" not in case or not isinstance(case.get("source_reports"), list) or len(case.get("source_reports", [])) == 0:
        errors.append("Missing case.source_reports[] (must include at least one source report reference)")
    # Strong recommendations
    if not case.get("tools"):
        errors.append("Missing case.tools[] (recommended)")
    if not case.get("phenomena"):
        errors.append("Missing case.phenomena[] (recommended)")
    return errors
