import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import init_db
from app.routers import chips, compare, fixtures, meta, myteam, optimizer, players, predictions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Fantasy Hunter API",
    version="0.1.0",
    description=(
        "FPL analytics. Every predicted-points number returned by this API carries "
        "the component breakdown that produced it."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (meta.router, players.router, fixtures.router, predictions.router,
               compare.router, myteam.router, optimizer.router, chips.router):
    app.include_router(router, prefix="/api")
