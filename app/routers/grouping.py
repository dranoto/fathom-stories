# app/routers/grouping.py
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session as SQLAlchemySession

from .. import database
from ..dependencies import get_llm_grouping
from ..grouping import engine as grouping_engine
from ..grouping import recluster as recluster_module
from ..grouping import lifecycle as lifecycle_module

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/grouping", tags=["grouping"])


@router.post("/run")
async def run_grouping(
    request: Request,
    db: SQLAlchemySession = Depends(database.get_db),
):
    try:
        llm = get_llm_grouping(request)
    except HTTPException:
        raise
    result = await grouping_engine.assign_new_articles(llm)
    return {"status": "ok", **result}


@router.post("/regroup")
async def run_regroup(
    request: Request,
    db: SQLAlchemySession = Depends(database.get_db),
):
    try:
        llm = get_llm_grouping(request)
    except HTTPException:
        raise
    result = await grouping_engine.regroup_uncategorized(llm)
    return {"status": "ok", **result}


@router.post("/recluster")
async def run_recluster(
    request: Request,
    auto_apply: bool = False,
    db: SQLAlchemySession = Depends(database.get_db),
):
    try:
        llm = get_llm_grouping(request)
    except HTTPException:
        raise
    recluster_module.mark_events_seen_in_recluster()
    result = await recluster_module.generate_recluster_diff(llm, auto_apply=auto_apply)
    return result


@router.post("/lifecycle")
async def run_lifecycle(
    db: SQLAlchemySession = Depends(database.get_db),
):
    return lifecycle_module.tick()
