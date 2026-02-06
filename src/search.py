from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .db import list_cases


def _case_to_text(case: Dict[str, Any], reports: Dict[str, Any]) -> str:
    # Build a robust searchable document string
    parts: List[str] = []
    parts.append(f"ID: {case.get('id','')}")
    parts.append(f"Title: {case.get('title','')}")
    parts.append(f"V&V type: {case.get('vv_type','')}")
    parts.append(f"Scope: {case.get('scope','')}")
    parts.append(f"System: {case.get('system','')}")
    parts.append(f"Tools: {', '.join(case.get('tools', []) or [])}")
    parts.append(f"Phenomena: {', '.join(case.get('phenomena', []) or [])}")
    parts.append(f"Tags: {', '.join(case.get('tags', []) or [])}")
    parts.append(f"Summary: {case.get('summary','')}")
    # Add report context
    for sr in case.get("source_reports", []) or []:
        rid = sr.get("report_id")
        rep = reports.get(rid, {}) if rid else {}
        if rid:
            parts.append(f"Source report id: {rid}")
        if rep.get("report_number"):
            parts.append(f"Report number: {rep.get('report_number')}")
        if rep.get("title"):
            parts.append(f"Report title: {rep.get('title')}")
        if sr.get("section"):
            parts.append(f"Section: {sr.get('section')}")
        if sr.get("note"):
            parts.append(f"Note: {sr.get('note')}")
    return "\n".join([p for p in parts if p])


@dataclass
class SearchFilters:
    vv_type: Optional[List[str]] = None
    scope: Optional[List[str]] = None
    tools: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    phenomena: Optional[List[str]] = None
    system_contains: Optional[str] = None

    def matches(self, case: Dict[str, Any]) -> bool:
        def intersects(sel: Optional[List[str]], values: Sequence[str]) -> bool:
            if not sel:
                return True
            sset = {s.lower() for s in sel}
            vset = {v.lower() for v in (values or [])}
            return len(sset & vset) > 0

        if self.vv_type and (case.get("vv_type") or "").lower() not in {v.lower() for v in self.vv_type}:
            return False
        if self.scope and (case.get("scope") or "").lower() not in {v.lower() for v in self.scope}:
            return False
        if not intersects(self.tools, case.get("tools", []) or []):
            return False
        if not intersects(self.tags, case.get("tags", []) or []):
            return False
        if not intersects(self.phenomena, case.get("phenomena", []) or []):
            return False
        if self.system_contains:
            if self.system_contains.lower() not in (case.get("system") or "").lower():
                return False
        return True


@dataclass
class CaseIndex:
    mode: str  # "tfidf" or "embeddings"
    case_ids: List[str]
    case_texts: List[str]
    tfidf_vectorizer: Optional[TfidfVectorizer] = None
    tfidf_matrix: Optional[Any] = None
    embeddings: Optional[np.ndarray] = None


def build_index_tfidf(db: Dict[str, Any]) -> CaseIndex:
    reports = db.get("reports", {})
    cases = list_cases(db)
    case_ids = [c["id"] for c in cases]
    texts = [_case_to_text(c, reports) for c in cases]

    vec = TfidfVectorizer(stop_words="english", max_features=50000)
    mat = vec.fit_transform(texts)
    return CaseIndex(mode="tfidf", case_ids=case_ids, case_texts=texts, tfidf_vectorizer=vec, tfidf_matrix=mat)


def build_index_embeddings(db: Dict[str, Any], embed_fn, cache_dir: Path = Path(".cache")) -> CaseIndex:
    """Build (and cache) embeddings using an embedding function that maps List[str] -> List[List[float]]."""
    reports = db.get("reports", {})
    cases = list_cases(db)
    case_ids = [c["id"] for c in cases]
    texts = [_case_to_text(c, reports) for c in cases]

    cache_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(("\n".join(texts)).encode("utf-8")).hexdigest()[:16]
    emb_path = cache_dir / f"case_embeddings_{h}.npy"

    if emb_path.exists():
        emb = np.load(emb_path)
    else:
        vectors = embed_fn(texts)
        emb = np.array(vectors, dtype=np.float32)
        np.save(emb_path, emb)

    return CaseIndex(mode="embeddings", case_ids=case_ids, case_texts=texts, embeddings=emb)


def search_cases(
    db: Dict[str, Any],
    index: CaseIndex,
    query: str,
    filters: Optional[SearchFilters] = None,
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """Return a ranked list of case summaries with scores and report references."""
    reports = db.get("reports", {})
    all_cases = {c["id"]: c for c in list_cases(db)}

    filters = filters or SearchFilters()

    # Apply filters first to reduce candidates
    candidate_ids: List[str] = []
    for cid in index.case_ids:
        c = all_cases.get(cid)
        if not c:
            continue
        if filters.matches(c):
            candidate_ids.append(cid)

    if not candidate_ids:
        return []

    # Score candidates
    if query.strip():
        if index.mode == "tfidf" and index.tfidf_vectorizer is not None and index.tfidf_matrix is not None:
            q_vec = index.tfidf_vectorizer.transform([query])
            # compute similarity only on candidates
            cand_idx = [index.case_ids.index(cid) for cid in candidate_ids]
            sim = cosine_similarity(q_vec, index.tfidf_matrix[cand_idx]).flatten()
            ranked = sorted(zip(candidate_ids, sim), key=lambda x: x[1], reverse=True)
        elif index.mode == "embeddings" and index.embeddings is not None:
            # compute query embedding using the same method by embedding the query text in the caller before build
            # Here we approximate by using TF-IDF fallback when embedding query isn't available.
            # The caller should prefer using `search_cases_with_query_embedding` for embeddings mode.
            ranked = [(cid, 0.0) for cid in candidate_ids]
        else:
            ranked = [(cid, 0.0) for cid in candidate_ids]
    else:
        ranked = [(cid, 0.0) for cid in candidate_ids]

    # Build response objects
    out: List[Dict[str, Any]] = []
    for cid, score in ranked[:top_k]:
        c = all_cases[cid]
        sr_list = []
        for sr in c.get("source_reports", []) or []:
            rid = sr.get("report_id")
            rep = reports.get(rid, {}) if rid else {}
            sr_list.append({
                "report_id": rid,
                "report_number": rep.get("report_number"),
                "report_title": rep.get("title"),
                "report_link": rep.get("file_link") or sr.get("report_link") or sr.get("report_link"),
                "note": sr.get("note"),
                "section": sr.get("section"),
            })
        out.append({
            "id": cid,
            "title": c.get("title"),
            "vv_type": c.get("vv_type"),
            "scope": c.get("scope"),
            "system": c.get("system"),
            "tools": c.get("tools", []),
            "phenomena": c.get("phenomena", []),
            "tags": c.get("tags", []),
            "score": float(score),
            "source_reports": sr_list,
        })
    return out


def search_cases_with_query_embedding(
    db: Dict[str, Any],
    index: CaseIndex,
    query_embedding: np.ndarray,
    filters: Optional[SearchFilters] = None,
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """Embeddings-mode search. Caller provides query embedding as a 1D numpy array."""
    if index.mode != "embeddings" or index.embeddings is None:
        raise ValueError("Index is not in embeddings mode")

    reports = db.get("reports", {})
    all_cases = {c["id"]: c for c in list_cases(db)}

    filters = filters or SearchFilters()

    candidate_rows: List[int] = []
    candidate_ids: List[str] = []
    for i, cid in enumerate(index.case_ids):
        c = all_cases.get(cid)
        if not c:
            continue
        if filters.matches(c):
            candidate_rows.append(i)
            candidate_ids.append(cid)

    if not candidate_ids:
        return []

    cand_emb = index.embeddings[candidate_rows]  # (n, d)
    q = query_embedding.reshape(1, -1).astype(np.float32)
    # cosine similarity
    q_norm = np.linalg.norm(q, axis=1, keepdims=True) + 1e-12
    c_norm = np.linalg.norm(cand_emb, axis=1, keepdims=True) + 1e-12
    sim = (cand_emb @ q.T).flatten() / (c_norm.flatten() * q_norm.flatten()[0])

    ranked = sorted(zip(candidate_ids, sim), key=lambda x: x[1], reverse=True)

    out: List[Dict[str, Any]] = []
    for cid, score in ranked[:top_k]:
        c = all_cases[cid]
        sr_list = []
        for sr in c.get("source_reports", []) or []:
            rid = sr.get("report_id")
            rep = reports.get(rid, {}) if rid else {}
            sr_list.append({
                "report_id": rid,
                "report_number": rep.get("report_number"),
                "report_title": rep.get("title"),
                "report_link": rep.get("file_link") or sr.get("report_link"),
                "note": sr.get("note"),
                "section": sr.get("section"),
            })
        out.append({
            "id": cid,
            "title": c.get("title"),
            "vv_type": c.get("vv_type"),
            "scope": c.get("scope"),
            "system": c.get("system"),
            "tools": c.get("tools", []),
            "phenomena": c.get("phenomena", []),
            "tags": c.get("tags", []),
            "score": float(score),
            "source_reports": sr_list,
        })
    return out
