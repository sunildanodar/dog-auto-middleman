from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from auth_system import db
from auth_system.control_routes import router as control_router
from auth_system.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    with db.get_connection() as conn:
        db.delete_expired_sessions(conn)
    yield


app = FastAPI(title="Auth System", lifespan=lifespan)
app.include_router(router)
app.include_router(control_router)
