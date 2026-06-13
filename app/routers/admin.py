# app/routers/admin.py
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlalchemy import desc

from .. import database
from ..database.models import ReclusterProposal, GroupingFeedback
from ..schemas.event import ReclusterProposalOut, GroupingFeedbackOut
from ..grouping.feedback import record_correction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/proposals", response_model=List[ReclusterProposalOut])
async def list_proposals(
    pending_only: bool = True,
    db: SQLAlchemySession = Depends(database.get_db),
):
    q = db.query(ReclusterProposal).order_by(desc(ReclusterProposal.created_at))
    if pending_only:
        q = q.filter(ReclusterProposal.applied == 0)
    return [ReclusterProposalOut.model_validate(p) for p in q.limit(200).all()]


@router.post("/proposals/{proposal_id}/apply")
async def apply_proposal(
    proposal_id: int,
    db: SQLAlchemySession = Depends(database.get_db),
):
    p = db.query(ReclusterProposal).filter(ReclusterProposal.id == proposal_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if p.applied:
        raise HTTPException(status_code=400, detail="Proposal already applied")
    from ..grouping import lifecycle as lifecycle_module
    try:
        payload = p.payload or {}
        if p.kind == "merge":
            a_id = payload.get("event_a_id")
            b_id = payload.get("event_b_id")
            if not a_id or not b_id:
                raise HTTPException(status_code=400, detail="merge proposal missing event ids")
            if a_id == b_id:
                raise HTTPException(status_code=400, detail="cannot merge event into itself")
            from ..security import verify_event_exists
            primary = verify_event_exists(db, a_id)
            secondary = verify_event_exists(db, b_id)
            db.query(database.Article).filter(database.Article.event_id == b_id).update(
                {database.Article.event_id: a_id}, synchronize_session=False
            )
            record_correction(
                article_id=0, kind="merge",
                original_event_id=b_id, corrected_event_id=a_id,
                note=p.rationale or f"merge approved via proposal {p.id}",
            )
            db.delete(secondary)
        elif p.kind == "split":
            new_name = payload.get("suggested_new_name")
            article_ids = payload.get("anchor_article_ids") or []
            if not new_name or not article_ids:
                raise HTTPException(status_code=400, detail="split proposal missing name or articles")
            new_ev = database.Event(name=new_name.strip(), status="active")
            db.add(new_ev)
            db.flush()
            db.query(database.Article).filter(
                database.Article.id.in_(article_ids),
                database.Article.event_id == p.payload.get("event_id"),
            ).update({database.Article.event_id: new_ev.id}, synchronize_session=False)
            record_correction(
                article_id=0, kind="split",
                original_event_id=p.payload.get("event_id"), corrected_event_id=new_ev.id,
                note=p.rationale or f"split approved via proposal {p.id}",
            )
        elif p.kind == "cool":
            lifecycle_module.set_event_status(payload.get("event_id"), "cooling")
        elif p.kind == "revive":
            lifecycle_module.revive_event(payload.get("event_id"))
        elif p.kind == "new":
            from ..database.models import Event
            new_ev = Event(name=payload.get("name", "Untitled").strip(), status="active")
            db.add(new_ev)
            db.flush()
            for aid in payload.get("anchor_article_ids") or []:
                art = db.query(database.Article).filter(database.Article.id == aid).first()
                if art:
                    art.event_id = new_ev.id
                    art.grouped_at = datetime.now(timezone.utc)
        else:
            raise HTTPException(status_code=400, detail=f"unknown proposal kind: {p.kind}")
        p.applied = 1
        p.applied_at = datetime.now(timezone.utc)
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error applying proposal {proposal_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to apply proposal: {e}")
    return {"message": "proposal applied", "proposal_id": proposal_id, "kind": p.kind}


@router.delete("/proposals/{proposal_id}")
async def dismiss_proposal(
    proposal_id: int,
    db: SQLAlchemySession = Depends(database.get_db),
):
    p = db.query(ReclusterProposal).filter(ReclusterProposal.id == proposal_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if p.applied:
        raise HTTPException(status_code=400, detail="Cannot dismiss an applied proposal")
    db.delete(p)
    db.commit()
    return {"message": "proposal dismissed", "proposal_id": proposal_id}


@router.get("/feedback", response_model=List[GroupingFeedbackOut])
async def list_feedback(
    limit: int = 50,
    db: SQLAlchemySession = Depends(database.get_db),
):
    rows = db.query(GroupingFeedback).order_by(desc(GroupingFeedback.created_at)).limit(limit).all()
    return [GroupingFeedbackOut.model_validate(r) for r in rows]
