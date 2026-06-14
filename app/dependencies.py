# app/dependencies.py
import logging
from fastapi import Request, HTTPException
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


def get_llm_summary(request: Request) -> ChatOpenAI:
    if not hasattr(request.app.state, 'llm_summary_instance') or request.app.state.llm_summary_instance is None:
        logger.error("Dependency Error: Summary LLM not found in app.state.")
        raise HTTPException(status_code=503, detail="Summary LLM has not been initialized.")
    return request.app.state.llm_summary_instance


def get_llm_grouping(request: Request) -> ChatOpenAI:
    if not hasattr(request.app.state, 'llm_grouping_instance') or request.app.state.llm_grouping_instance is None:
        logger.error("Dependency Error: Grouping LLM not found in app.state.")
        raise HTTPException(status_code=503, detail="Grouping LLM has not been initialized.")
    return request.app.state.llm_grouping_instance


def get_visitor_id(request: Request) -> str:
    visitor_id = getattr(request.state, "visitor_id", None)
    if not visitor_id:
        logger.error("Dependency Error: visitor_id missing from request.state.")
        raise HTTPException(status_code=500, detail="Visitor identity not established.")
    return visitor_id
