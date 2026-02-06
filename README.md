# NEAMS Validation Database – Streamlit Demo App

This repo hosts a Streamlit web application that:
1) Provides an **LLM-powered search** over a JSON validation database with **report traceability**.
2) Provides an **Excel-like workbook** view for filtering/editing/adding cases.
3) Generates a **CGD-ready evidence package draft** (Markdown + PDF) from selected cases and user inputs.

## Quick start (local)

```bash
python -m venv .venv
source .venv/bin/activate   # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
streamlit run app.py
```

## Database

The default database file is:

- `data/validation_db.json`

You can point to a different DB using:

- `DB_PATH=/path/to/db.json`

## LLM configuration (optional, but enables the "Search LLM" and narrative in CGD reports)

Set in one of the following ways:
- Streamlit Cloud secrets: `.streamlit/secrets.toml`
- Environment variables:
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL` (default: `gpt-4o-mini`)
  - `OPENAI_EMBEDDING_MODEL` (default: `text-embedding-3-small`)
  - `OPENAI_BASE_URL` (optional)

> If no API key is present, the app falls back to a local TF‑IDF search and produces deterministic CGD report templates.

## Reports / attachments

Each case stores `source_reports[].report_id`, and the database stores report metadata in `reports{}`.

If you want the app to offer “Download source report” buttons, place the PDFs under:

- `data/reports/<file_name from reports registry>`

The app will automatically look up report PDFs by `file_name`.

## Notes for Streamlit Cloud hosting

Edits to the database file may not persist across sessions in Streamlit Cloud.  
The app therefore always offers a **Download updated database JSON** button so you can commit changes back to GitHub.

---

INL / NEAMS demo — adjust to your internal conventions as needed.
