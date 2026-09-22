# app/summarizer.py
import logging
from typing import Any, Dict, Optional
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


def initialize_llm(
    api_key: str,
    base_url: str,
    model_name: str,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    request_timeout: float = 180.0,
    reasoning_effort: Optional[str] = None,
) -> Optional[ChatOpenAI]:
    """
    Initializes a ChatOpenAI LLM instance for OpenAI-compatible endpoints.
    Returns None on failure (caller should handle).
    """
    try:
        kwargs: Dict[str, Any] = dict(
            model=model_name,
            openai_api_key=api_key,
            openai_api_base=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
        )
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        llm = ChatOpenAI(**kwargs)
        logger.info(
            f"Initialized LLM: {model_name} at {base_url} "
            f"(max_tokens={max_tokens}, request_timeout={request_timeout}s, "
            f"reasoning_effort={reasoning_effort})"
        )
        return llm
    except Exception as e:
        logger.error(f"Error initializing LLM {model_name}: {e}", exc_info=True)
        return None
