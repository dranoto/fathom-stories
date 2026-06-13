# app/admin_api.py
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from . import config as app_config
from . import database
from .routers import events as events_router
from .routers import articles as articles_router
from .routers import admin as admin_router
from .routers import grouping as grouping_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.create_db_and_tables()
    yield


app = FastAPI(
    title="Fathom Stories — Admin",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS-open to localhost on main port so the admin UI can call the main API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{app_config.MAIN_PORT}", f"http://127.0.0.1:{app_config.MAIN_PORT}"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events_router.router)
app.include_router(articles_router.router)
app.include_router(admin_router.router)
app.include_router(grouping_router.router)


frontend_admin_dir = Path(__file__).resolve().parent.parent / "frontend_admin"
if frontend_admin_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_admin_dir)), name="admin_static")


@app.get("/")
async def root_index():
    index_path = frontend_admin_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"name": "fathom-stories-admin", "docs": "/docs"})


@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}
