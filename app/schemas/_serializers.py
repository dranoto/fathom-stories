# app/schemas/_serializers.py
from datetime import datetime, timezone
from typing import Annotated, Optional
from pydantic import PlainSerializer


def to_utc_iso(v: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime as a UTC ISO 8601 string with a 'Z' suffix.

    Naive datetimes are interpreted as UTC (matching how the rest of the
    codebase stores and handles them). The output is always in UTC with a
    trailing 'Z' so JavaScript's `new Date(s)` correctly parses it as UTC
    rather than as browser-local time.
    """
    if v is None:
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


UtcDateTime = Annotated[
    Optional[datetime],
    PlainSerializer(to_utc_iso, return_type=Optional[str]),
]
