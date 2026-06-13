# app/database/kv.py
import logging
from typing import Any, Optional
from sqlalchemy.orm import Session

from . import SessionLocal
from .models import KVSetting

logger = logging.getLogger(__name__)


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.query(KVSetting).filter(KVSetting.key == key).first()
    if row is None:
        return default
    return row.value


def set_setting(db: Session, key: str, value: Any) -> None:
    text_value = str(value) if value is not None else None
    row = db.query(KVSetting).filter(KVSetting.key == key).first()
    if row is None:
        row = KVSetting(key=key, value=text_value)
        db.add(row)
    else:
        row.value = text_value
    db.commit()


def get_setting_scope(key: str, default: Optional[str] = None) -> Optional[str]:
    with SessionLocal() as db:
        return get_setting(db, key, default)
