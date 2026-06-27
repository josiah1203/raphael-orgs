"""Raphael orgs service."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from raphael_contracts.db import ensure_migrations
from raphael_contracts.errors import ErrorResponse
from raphael_orgs.routes import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_migrations()
    yield


app = FastAPI(title="raphael-orgs", version="0.1.0", lifespan=lifespan)
app.include_router(router, prefix="/v1/orgs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "raphael-orgs"}


@app.exception_handler(Exception)
async def unhandled(_request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content=ErrorResponse(code="internal_error", message=str(exc)).model_dump())
