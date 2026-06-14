# app/middleware/visitor.py
import logging
import re
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)

VISITOR_COOKIE_NAME = "fathom_visitor_id"
VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
_VISITOR_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _is_valid_visitor_id(value: str) -> bool:
    return bool(value) and len(value) <= 64 and bool(_VISITOR_ID_PATTERN.match(value))


class VisitorCookieMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        visitor_id = request.cookies.get(VISITOR_COOKIE_NAME)
        issued_now = False

        if not visitor_id or not _is_valid_visitor_id(visitor_id):
            visitor_id = uuid.uuid4().hex
            issued_now = True

        request.state.visitor_id = visitor_id

        response = await call_next(request)

        if issued_now:
            response.set_cookie(
                key=VISITOR_COOKIE_NAME,
                value=visitor_id,
                max_age=VISITOR_COOKIE_MAX_AGE,
                path="/",
                httponly=True,
                samesite="lax",
            )

        return response
