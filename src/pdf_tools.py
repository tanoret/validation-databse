from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None  # type: ignore


@dataclass
class PdfMatch:
    page_index: int
    score: int
    snippet: str


def _normalize(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def find_best_page(pdf_path: Path, queries: Sequence[str], max_pages: int = 40) -> Optional[PdfMatch]:
    """Best-effort locator for where a case is mentioned in a report PDF.

    - Searches extracted text (no OCR).
    - Scans first `max_pages` pages for speed.
    """
    if not pdf_path.exists() or fitz is None:
        return None

    qnorm = [_normalize(q).lower() for q in (queries or []) if _normalize(q)]
    if not qnorm:
        return None

    doc = fitz.open(str(pdf_path))
    try:
        best: Optional[PdfMatch] = None
        n = min(doc.page_count, max_pages)
        for i in range(n):
            page = doc.load_page(i)
            text = page.get_text("text") or ""
            t = _normalize(text).lower()
            if not t:
                continue

            score = 0
            hit = None
            for q in qnorm:
                if q in t:
                    pos = t.find(q)
                    local = min(300, len(q) * 8) + max(0, 200 - pos // 40)
                    if local > score:
                        score = local
                        hit = q

            if score > 0:
                raw = _normalize(text)
                snippet = raw[:600]
                cand = PdfMatch(page_index=i, score=score, snippet=snippet)
                if best is None or cand.score > best.score:
                    best = cand
        return best
    finally:
        doc.close()


def render_page_png(pdf_path: Path, page_index: int, zoom: float = 2.0) -> Optional[bytes]:
    """Render a single page to PNG bytes."""
    if not pdf_path.exists() or fitz is None:
        return None
    doc = fitz.open(str(pdf_path))
    try:
        page_index = max(0, min(page_index, doc.page_count - 1))
        page = doc.load_page(page_index)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()


def build_pdf_iframe(pdf_bytes: bytes, page: int = 1, height: int = 650) -> str:
    import base64
    b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    return f'''
<iframe
  src="data:application/pdf;base64,{b64}#page={page}"
  width="100%"
  height="{height}"
  style="border: 1px solid rgba(0,0,0,0.12); border-radius: 10px;"
></iframe>
'''
