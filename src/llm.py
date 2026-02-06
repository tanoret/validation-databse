from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional


try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    base_url: Optional[str] = None

    @staticmethod
    def from_env() -> Optional["LLMConfig"]:
        api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not api_key:
            return None
        model = (os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
        embedding_model = (os.environ.get("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small").strip()
        base_url = (os.environ.get("OPENAI_BASE_URL") or "").strip() or None
        return LLMConfig(api_key=api_key, model=model, embedding_model=embedding_model, base_url=base_url)


class LLMClient:
    """Minimal OpenAI-compatible client wrapper.

    Notes:
    - If the `openai` package is not installed, this class will raise at construction.
    - The Streamlit app will gracefully fall back to non-LLM mode if the client cannot be built.
    """

    def __init__(self, cfg: LLMConfig):
        if OpenAI is None:
            raise RuntimeError(
                "openai package is not installed. Install requirements.txt or disable LLM features."
            )
        self.cfg = cfg
        self.client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2, max_tokens: int = 1200) -> str:
        resp = self.client.chat.completions.create(
            model=self.cfg.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    def embed(self, texts: List[str]) -> List[List[float]]:
        resp = self.client.embeddings.create(
            model=self.cfg.embedding_model,
            input=texts,
        )
        return [d.embedding for d in resp.data]

    def is_available(self) -> bool:
        return bool(getattr(self.cfg, "api_key", ""))
