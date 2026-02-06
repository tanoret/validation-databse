from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd
import streamlit as st

from src.db import (
    add_or_update_case,
    get_case,
    get_db_path,
    load_database,
    resolve_report_file,
    save_database,
    validate_case_minimum,
)
from src.llm import LLMClient, LLMConfig
from src.search import (
    CaseIndex,
    SearchFilters,
    build_index_embeddings,
    build_index_tfidf,
    search_cases,
    search_cases_with_query_embedding,
)
from src.agent import run_agent
from src.reporting import generate_cgd_markdown, render_cgd_pdf, DEFAULT_CRITICAL_CHARACTERISTICS


APP_TITLE = "NEAMS Validation Database – Demo"
REPORTS_DIR_DEFAULT = Path("data/reports")


# -----------------------
# Streamlit setup
# -----------------------
st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🧪 NEAMS Validation Database – Demo App")
st.caption("LLM-powered case discovery + CGD-ready evidence package drafts + workbook editing.")

with st.expander("What this app does", expanded=False):
    st.markdown(
        """
- **Main page (this page)**:
  - Chat-style **LLM search** that can explain and filter cases.
  - Case results always keep **traceability to the milestone report(s)**.
  - A **CGD Package Builder** that generates a draft evidence package (Markdown + PDF).

- **Workbook page** (left sidebar → *Case Workbook*):
  - Excel-like view for filtering/editing cases and adding new ones.
  - Export an updated JSON database for commit back to GitHub.
"""
    )


# -----------------------
# Helpers: secrets/env
# -----------------------
def _get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    # Prefer Streamlit secrets; fallback to env var.
    try:
        v = st.secrets.get(key)  # type: ignore[attr-defined]
        if v is not None:
            return str(v)
    except Exception:
        pass
    return os.environ.get(key, default)


@st.cache_data(show_spinner=False)
def load_db_cached(db_path_str: str) -> Dict[str, Any]:
    return load_database(Path(db_path_str))



def build_llm_from_settings() -> Optional[LLMClient]:
    api_key = (_get_setting("OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        return None
    cfg = LLMConfig(
        api_key=api_key,
        model=(_get_setting("OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini").strip(),
        embedding_model=(_get_setting("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small") or "text-embedding-3-small").strip(),
        base_url=(_get_setting("OPENAI_BASE_URL", "") or "").strip() or None,
    )
    try:
        return LLMClient(cfg)
    except Exception as e:
        # Missing package or misconfiguration; fall back gracefully.
        st.sidebar.warning(f"LLM unavailable: {e}")
        return None


@st.cache_resource(show_spinner=False)
def build_index_cached(db: Dict[str, Any], use_embeddings: bool, embedding_model_hint: str = "") -> CaseIndex:
    # NOTE: st.cache_resource uses hashing of inputs; for large dicts this is okay for this demo scale.
    if use_embeddings:
        raise RuntimeError("Embeddings index must be built via build_index_embeddings_cached()")
    return build_index_tfidf(db)


@st.cache_resource(show_spinner=True)
def build_index_embeddings_cached(db: Dict[str, Any], embedding_model_hint: str, api_key_present: bool) -> CaseIndex:
    # We cannot pass the LLM client itself into cached function (not hashable), so we build embeddings outside,
    # but we *can* pass a flag to control whether this path is used.
    # For caching stability, embeddings are additionally cached to disk via src/search.py
    raise RuntimeError("This function is a placeholder and is overridden at runtime.")


# Monkey patch approach: create embeddings index without caching on the LLM client object itself.
def build_embeddings_index(db: Dict[str, Any], llm: LLMClient) -> CaseIndex:
    return build_index_embeddings(db, embed_fn=llm.embed, cache_dir=Path(".cache"))


def _all_values(db: Dict[str, Any], field: str) -> List[str]:
    vals: Set[str] = set()
    for c in db.get("cases", {}).values():
        if not isinstance(c, dict):
            continue
        v = c.get(field)
        if isinstance(v, list):
            for x in v:
                if str(x).strip():
                    vals.add(str(x).strip())
        elif isinstance(v, str) and v.strip():
            vals.add(v.strip())
    return sorted(vals)


def _all_scalar_values(db: Dict[str, Any], field: str) -> List[str]:
    vals: Set[str] = set()
    for c in db.get("cases", {}).values():
        if not isinstance(c, dict):
            continue
        v = c.get(field)
        if isinstance(v, str) and v.strip():
            vals.add(v.strip())
    return sorted(vals)


def _render_case_card(db: Dict[str, Any], case_id: str, reports_dir: Path) -> None:
    reports = db.get("reports", {})
    c = get_case(db, case_id)
    if not c:
        st.warning(f"Case not found: {case_id}")
        return

    st.markdown(f"### `{c['id']}` — {c.get('title','')}")
    cols = st.columns([1, 1, 2])
    cols[0].markdown(f"**V&V type:** {c.get('vv_type','')}")
    cols[0].markdown(f"**Scope:** {c.get('scope','')}")
    cols[1].markdown(f"**System:** {c.get('system','')}")
    cols[1].markdown(f"**Tools:** {', '.join(c.get('tools',[]) or [])}")

    if c.get("summary"):
        st.markdown(f"**Summary:** {c.get('summary')}")

    if c.get("phenomena"):
        st.markdown(f"**Phenomena:** {', '.join(c.get('phenomena',[]) or [])}")

    # Metrics
    metrics = c.get("metrics", []) or []
    if metrics:
        with st.expander("Metrics", expanded=False):
            st.json(metrics, expanded=False)

    # Traceability
    with st.expander("Source report traceability", expanded=True):
        for sr in c.get("source_reports", []) or []:
            rid = sr.get("report_id")
            rep = reports.get(rid, {}) if rid else {}
            rep_num = rep.get("report_number") or rid or "Unknown report"
            rep_title = rep.get("title") or ""
            note = sr.get("note") or ""
            sec = sr.get("section") or ""
            st.markdown(f"- **{rep_num}** — {rep_title}")
            if sec:
                st.markdown(f"  - Section: {sec}")
            if note:
                st.markdown(f"  - Note: {note}")

            # Optional local PDF download
            local_pdf = resolve_report_file(rep, reports_dir)
            if local_pdf and local_pdf.exists():
                with open(local_pdf, "rb") as f:
                    st.download_button(
                        label=f"Download report PDF ({local_pdf.name})",
                        data=f.read(),
                        file_name=local_pdf.name,
                        mime="application/pdf",
                        key=f"dl_{case_id}_{rid}_{local_pdf.name}",
                    )
            else:
                link = rep.get("file_link") or sr.get("report_link") or sr.get("report_link") or ""
                if link:
                    st.markdown(f"  - Link (as stored in DB): `{link}`")

    st.divider()


# -----------------------
# Load DB and init services
# -----------------------
db_path = get_db_path()

# Optional: session-only DB override via file uploader
if "db_override" in st.session_state and isinstance(st.session_state.db_override, dict):
    db = st.session_state.db_override
else:
    db = load_db_cached(str(db_path))

llm = build_llm_from_settings()

# Determine index mode
index_mode = "TF‑IDF (local)"
index: CaseIndex
embed_query_fn = None

if llm is not None:
    try:
        index = build_embeddings_index(db, llm)
        index_mode = f"Embeddings ({llm.cfg.embedding_model})"
        def _embed_query(q: str) -> np.ndarray:
            vec = llm.embed([q])[0]
            return np.array(vec, dtype=np.float32)
        embed_query_fn = _embed_query
    except Exception as e:
        st.sidebar.warning(f"Embeddings index unavailable, falling back to TF‑IDF. Reason: {e}")
        index = build_index_cached(db, use_embeddings=False)
else:
    index = build_index_cached(db, use_embeddings=False)

# -----------------------
# Sidebar: filters + config
# -----------------------
st.sidebar.header("Search controls")

st.sidebar.markdown("### Database source")
uploaded_db = st.sidebar.file_uploader("Load database JSON (session only)", type=["json"])
if uploaded_db is not None:
    try:
        st.session_state.db_override = json.load(uploaded_db)
        st.sidebar.success("Loaded database into session. Rerunning…")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Failed to load JSON: {e}")

if st.sidebar.button("Use on-disk database", use_container_width=True):
    if "db_override" in st.session_state:
        del st.session_state.db_override
    st.rerun()

st.sidebar.caption(f"On-disk DB: {db_path}")
st.sidebar.divider()

st.sidebar.markdown(f"**Index mode:** {index_mode}")
if llm is None:
    st.sidebar.info("No LLM key detected → chat uses deterministic search + templated reporting.")

vv_types = sorted({(c.get("vv_type") or "").strip() for c in db.get("cases", {}).values() if isinstance(c, dict) and c.get("vv_type")})
scopes = sorted({(c.get("scope") or "").strip() for c in db.get("cases", {}).values() if isinstance(c, dict) and c.get("scope")})
tools = _all_values(db, "tools")
tags = _all_values(db, "tags")

selected_vv = st.sidebar.multiselect("V&V type", vv_types, default=[])
selected_scope = st.sidebar.multiselect("Scope", scopes, default=[])
selected_tools = st.sidebar.multiselect("Tools", tools, default=[])
selected_tags = st.sidebar.multiselect("Tags", tags, default=[])
system_contains = st.sidebar.text_input("System contains", value="", placeholder="e.g., MSRE, TAMU, L‑MSR")

top_k = st.sidebar.slider("Max results", min_value=3, max_value=30, value=10, step=1)

reports_dir = Path(_get_setting("REPORTS_DIR", str(REPORTS_DIR_DEFAULT)) or str(REPORTS_DIR_DEFAULT))

user_filters = SearchFilters(
    vv_type=selected_vv or None,
    scope=selected_scope or None,
    tools=selected_tools or None,
    tags=selected_tags or None,
    system_contains=system_contains.strip() or None,
)

# Selection set for CGD builder
if "selected_case_ids" not in st.session_state:
    st.session_state.selected_case_ids = set()
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []  # list[dict(role, content)]
if "last_matched_case_ids" not in st.session_state:
    st.session_state.last_matched_case_ids = []


# -----------------------
# Main UI tabs
# -----------------------
tab_chat, tab_cgd = st.tabs(["🔎 LLM Search", "📦 CGD Package Builder"])

# -----------------------
# Tab 1: Chat / LLM search
# -----------------------
with tab_chat:
    st.subheader("Ask the database")
    st.markdown(
        """
Use natural language to search and get explanations. Examples:
- “Show validation cases for MSRE pump transients and cite the report figures.”
- “Which cases exercise DO coupling between SAM and Pronghorn?”
- “Filter to TAMU MSFL two-phase cases and summarize the initial conditions.”
"""
    )

    # Render existing chat history
    for m in st.session_state.chat_history:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    prompt = st.chat_input("Ask about cases, tools, phenomena, or request a CGD evidence package draft…")
    if prompt:
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Compute deterministic suggestion list (used even if LLM is enabled)
        def _suggest_cases(query: str) -> List[Dict[str, Any]]:
            if index.mode == "embeddings" and embed_query_fn is not None:
                q_emb = embed_query_fn(query)
                return search_cases_with_query_embedding(db, index, q_emb, filters=user_filters, top_k=top_k)
            return search_cases(db, index, query, filters=user_filters, top_k=top_k)

        suggestions = _suggest_cases(prompt)

        # Agent response
        with st.chat_message("assistant"):
            if llm is None:
                # Deterministic response
                if suggestions:
                    st.markdown("Here are the most relevant cases I found (with report traceability):\n")
                    for r in suggestions[: min(8, len(suggestions))]:
                        st.markdown(f"- `{r['id']}` — {r['title']}  ")
                        for sr in r.get("source_reports", []) or []:
                            rep_num = sr.get("report_number") or sr.get("report_id") or "Unknown report"
                            rep_title = sr.get("report_title") or ""
                            note = sr.get("note") or ""
                            st.markdown(f"  - {rep_num}: {rep_title} ({note})")
                    st.markdown("\nTip: Add an API key to enable the chat agent for deeper explanations and CGD package drafting.")
                    answer_text = "(Deterministic search response shown above.)"
                else:
                    st.markdown("No cases matched your filters. Try removing filters or using different keywords.")
                    answer_text = "No cases matched."
                st.session_state.chat_history.append({"role": "assistant", "content": answer_text})
                st.session_state.last_matched_case_ids = [r["id"] for r in suggestions]
            else:
                def _chat_fn(msgs: List[Dict[str, str]]) -> str:
                    return llm.chat(msgs, temperature=0.2, max_tokens=1200)

                report_inputs = {}  # for future: bind to CGD tab inputs
                result = run_agent(
                    llm_chat_fn=_chat_fn,
                    db=db,
                    index=index,
                    user_message=prompt,
                    user_filters=user_filters,
                    report_inputs=report_inputs,
                    embed_query_fn=embed_query_fn,
                    max_steps=4,
                )
                st.markdown(result.content_markdown)
                st.session_state.chat_history.append({"role": "assistant", "content": result.content_markdown})
                st.session_state.last_matched_case_ids = result.matched_case_ids or [r["id"] for r in suggestions]

    # Results explorer (based on last chat turn)
    st.divider()
    st.subheader("Results explorer")
    matched_ids = st.session_state.last_matched_case_ids or []
    if not matched_ids:
        st.info("Ask a question above to populate case results here.")
    else:
        # Quick actions
        c1, c2, c3 = st.columns([1, 1, 2])
        if c1.button("➕ Add all matched to CGD builder", use_container_width=True):
            for cid in matched_ids:
                st.session_state.selected_case_ids.add(cid)
            st.success(f"Added {len(matched_ids)} case(s) to CGD builder selection.")
        if c2.button("🧹 Clear matched results", use_container_width=True):
            st.session_state.last_matched_case_ids = []
            st.rerun()

        # Render case cards
        for cid in matched_ids[:top_k]:
            with st.expander(f"{cid}", expanded=False):
                if st.button("Add to CGD builder", key=f"add_{cid}", use_container_width=True):
                    st.session_state.selected_case_ids.add(cid)
                    st.success(f"Added {cid}")
                _render_case_card(db, cid, reports_dir)


# -----------------------
# Tab 2: CGD package builder
# -----------------------
with tab_cgd:
    st.subheader("CGD Package Builder (Draft)")
    st.markdown(
        """
Build a **CGD-ready draft evidence package** by selecting cases and providing the intended use and configuration context.

This generates:
- A **Markdown package** (easy to review and version-control)
- A **PDF export** (for sharing/review)
"""
    )

    selected_case_ids = sorted(list(st.session_state.selected_case_ids))

    st.markdown("### 1) Select cases")
    col_a, col_b = st.columns([2, 1])

    with col_a:
        st.write(f"Currently selected: **{len(selected_case_ids)}** case(s)")
        if selected_case_ids:
            st.dataframe(pd.DataFrame({"case_id": selected_case_ids}), use_container_width=True, hide_index=True)

    with col_b:
        if st.button("🧹 Clear selection", use_container_width=True):
            st.session_state.selected_case_ids = set()
            st.rerun()

    st.markdown("#### Add cases by searching")
    add_query = st.text_input("Search to add cases", value="", placeholder="e.g., MSRE pump, DO coupling, TAMU MSFL two-phase")
    if add_query:
        if index.mode == "embeddings" and embed_query_fn is not None:
            q_emb = embed_query_fn(add_query)
            add_results = search_cases_with_query_embedding(db, index, q_emb, filters=user_filters, top_k=10)
        else:
            add_results = search_cases(db, index, add_query, filters=user_filters, top_k=10)

        for r in add_results:
            cols = st.columns([3, 1])
            cols[0].markdown(f"**`{r['id']}`** — {r['title']}")
            if cols[1].button("Add", key=f"add_from_search_{r['id']}"):
                st.session_state.selected_case_ids.add(r["id"])
                st.rerun()

    st.divider()
    st.markdown("### 2) Provide package inputs")

    with st.form("cgd_inputs_form"):
        package_title = st.text_input("Package title", value="Commercial Grade Dedication Evidence Package (Draft)")
        requester = st.text_input("Prepared for", value="")
        reviewer = st.text_input("Reviewer/Approver", value="")
        tool_name = st.text_input("Tool(s)", value="SAM / Pronghorn / Griffin / Thermochimica (as applicable)")
        tool_version = st.text_input("Tool version / commit", value="TBD")
        execution_environment = st.text_area(
            "Execution environment (OS, compiler, libraries, containers, HPC, etc.)",
            value="TBD",
            height=80,
        )
        intended_use = st.text_area(
            "Intended use statement",
            value="Use the selected cases as dedication evidence to support qualification for (describe decision context).",
            height=100,
        )

        cc_ids = [f"{cc['id']} – {cc['name']}" for cc in DEFAULT_CRITICAL_CHARACTERISTICS]
        selected_cc = st.multiselect(
            "Critical characteristics to include",
            options=cc_ids,
            default=cc_ids[:3],
        )

        enhance_with_llm = st.checkbox("Enhance narrative with LLM (adds executive summary & evidence argument)", value=False, disabled=(llm is None))

        submitted = st.form_submit_button("Generate report")

    if submitted:
        if not selected_case_ids:
            st.error("Select at least one case.")
        else:
            report_inputs = {
                "package_title": package_title,
                "requester": requester,
                "reviewer": reviewer,
                "tool_name": tool_name,
                "tool_version": tool_version,
                "execution_environment": execution_environment,
                "intended_use": intended_use,
            }

            # Build base draft
            md = generate_cgd_markdown(db, selected_case_ids, report_inputs)

            # Optional narrative enhancement
            if enhance_with_llm and llm is not None:
                prompt = [
                    {
                        "role": "system",
                        "content": (
                            "You are preparing a CGD-ready draft evidence package narrative. "
                            "You MUST NOT invent any case IDs, report numbers, or results. "
                            "You may only reorganize and clarify the writing. "
                            "Add two sections near the top: 'Executive summary' and 'Evidence argument'. "
                            "Keep the case catalog table and case details intact."
                        ),
                    },
                    {"role": "user", "content": md},
                ]
                try:
                    md = llm.chat(prompt, temperature=0.2, max_tokens=1600)
                except Exception as e:
                    st.warning(f"LLM narrative enhancement failed; using base draft. Reason: {e}")

            st.success("Report generated.")
            st.markdown("### 3) Preview")
            st.markdown(md)

            # Downloads
            md_bytes = md.encode("utf-8")
            st.download_button(
                "Download Markdown (.md)",
                data=md_bytes,
                file_name="cgd_evidence_package.md",
                mime="text/markdown",
                use_container_width=True,
            )

            # PDF export
            pdf_out = Path(".cache") / "cgd_evidence_package.pdf"
            try:
                render_cgd_pdf(db, selected_case_ids, report_inputs, pdf_out)
                with open(pdf_out, "rb") as f:
                    st.download_button(
                        "Download PDF (.pdf)",
                        data=f.read(),
                        file_name="cgd_evidence_package.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )
            except Exception as e:
                st.warning(f"PDF export failed: {e}")

    st.divider()
    st.markdown("### 4) Optional: validate selected cases for minimum CGD traceability")
    if selected_case_ids:
        issues = []
        for cid in selected_case_ids:
            c = get_case(db, cid) or {}
            errs = validate_case_minimum(c)
            if errs:
                issues.append({"case_id": cid, "issues": errs})
        if issues:
            st.warning("Some selected cases are missing recommended CGD fields. Review below:")
            st.json(issues, expanded=False)
        else:
            st.success("All selected cases pass minimum field checks (id/title/vv_type/source_reports present).")
