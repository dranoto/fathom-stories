# app/summarizer.py
import logging
from typing import Optional
from langchain_openai import ChatOpenAI

from . import config as app_config

logger = logging.getLogger(__name__)


def initialize_llm(
    api_key: str,
    base_url: str,
    model_name: str,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> Optional[ChatOpenAI]:
    """
    Initializes a ChatOpenAI LLM instance for OpenAI-compatible endpoints.
    Returns None on failure (caller should handle).
    """
    try:
        llm = ChatOpenAI(
            model=model_name,
            openai_api_key=api_key,
            openai_api_base=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        logger.info(f"Initialized LLM: {model_name} at {base_url} (max_tokens={max_tokens})")
        return llm
    except Exception as e:
        logger.error(f"Error initializing LLM {model_name}: {e}", exc_info=True)
        return None
