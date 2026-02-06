from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from .db import get_case
from .search import CaseIndex, SearchFilters, search_cases, search_cases_with_query_embedding


TOOL_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_first_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from arbitrary model output."""
    if not text:
        return None
    m = TOOL_JSON_RE.search(text)
    if not m:
        return None
    blob = m.group(0)
    try:
        return json.loads(blob)
    except Exception:
        return None


SYSTEM_PROMPT = """You are an expert assistant for the NEAMS-MSRs Validation Database.

Your job:
- Help the user find, filter, and understand validation/verification/benchmark/demonstration cases.
- Always keep traceability to the originating milestone reports. Never invent case IDs or report numbers.

You have access to the following tools. To call a tool, respond with a SINGLE JSON object:

1) search_cases
{"tool":"search_cases","arguments":{"query":"...","filters":{...},"top_k":10}}

filters can include:
- vv_type: ["verification","validation","benchmark","demonstration"]
- scope: ["component","integral","system"]
- tools: ["SAM","Pronghorn", ...]
- tags: [...]
- phenomena: [...]
- system_contains: "MSRE" (substring match)

2) get_case
{"tool":"get_case","arguments":{"case_id":"MSR_TH_DO_VER_001"}}

3) build_cgd_report
{"tool":"build_cgd_report","arguments":{"case_ids":[...],"report_inputs":{...},"format":"markdown"}}

When you are ready to answer the user, respond with:
{"tool":"final","content":"..."}

Rules:
- If uncertain, call search_cases first.
- Cite report traceability by including the report number and the report link that comes from the database.
- Keep responses concise but technically correct.
"""


@dataclass
class AgentResult:
    content_markdown: str
    matched_case_ids: List[str]


def run_agent(
    llm_chat_fn: Callable[[List[Dict[str, str]]], str],
    db: Dict[str, Any],
    index: CaseIndex,
    user_message: str,
    user_filters: Optional[SearchFilters] = None,
    report_inputs: Optional[Dict[str, Any]] = None,
    embed_query_fn: Optional[Callable[[str], np.ndarray]] = None,
    max_steps: int = 4,
) -> AgentResult:
    """Simple tool-using loop that works with most chat LLMs."""
    user_filters = user_filters or SearchFilters()
    report_inputs = report_inputs or {}

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    matched: List[str] = []

    for _ in range(max_steps):
        raw = llm_chat_fn(messages)
        payload = _extract_first_json(raw)
        if not payload or payload.get("tool") == "final":
            content = payload.get("content") if payload and payload.get("tool") == "final" else raw
            return AgentResult(content_markdown=content, matched_case_ids=matched)

        tool = payload.get("tool")
        args = payload.get("arguments", {}) or {}

        if tool == "search_cases":
            query = (args.get("query") or user_message or "").strip()
            filters = args.get("filters") or {}
            merged_filters = SearchFilters(
                vv_type=filters.get("vv_type") or user_filters.vv_type,
                scope=filters.get("scope") or user_filters.scope,
                tools=filters.get("tools") or user_filters.tools,
                tags=filters.get("tags") or user_filters.tags,
                phenomena=filters.get("phenomena") or user_filters.phenomena,
                system_contains=filters.get("system_contains") or user_filters.system_contains,
            )
            top_k = int(args.get("top_k") or 10)

            if index.mode == "embeddings" and embed_query_fn is not None:
                q_emb = embed_query_fn(query)
                results = search_cases_with_query_embedding(db, index, q_emb, merged_filters, top_k=top_k)
            else:
                results = search_cases(db, index, query, merged_filters, top_k=top_k)

            matched = [r["id"] for r in results]
            tool_result = {
                "results": results,
                "note": "Use these case IDs and their source_reports for traceability; do not invent IDs.",
            }
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "tool", "content": json.dumps(tool_result, indent=2)})
            continue

        if tool == "get_case":
            cid = (args.get("case_id") or "").strip()
            c = get_case(db, cid)
            tool_result = {"case": c, "note": "Use report references from source_reports and db.reports."}
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "tool", "content": json.dumps(tool_result, indent=2)})
            continue

        if tool == "build_cgd_report":
            # The UI typically handles report generation, but support it here too.
            from .reporting import generate_cgd_markdown

            case_ids = args.get("case_ids") or matched
            fmt = (args.get("format") or "markdown").lower()
            r_inputs = args.get("report_inputs") or report_inputs

            md = generate_cgd_markdown(db, list(case_ids), r_inputs)
            tool_result = {"format": fmt, "content": md}
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "tool", "content": json.dumps(tool_result, indent=2)})
            continue

        # Unknown tool: stop
        return AgentResult(content_markdown=raw, matched_case_ids=matched)

    # Fallback if loop exhausts
    return AgentResult(content_markdown="I wasn't able to complete the request within the tool budget. Try a narrower query or specify filters (tool, system, vv_type).", matched_case_ids=matched)
