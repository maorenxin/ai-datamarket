"""
AI Datamarket API Server — FastAPI, port 8402.

MVP: No x402 payment enforcement. Token usage tracked but not gated.
"""
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.ohlcv import router as ohlcv_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Datamarket API",
    description="AI Bloomberg — financial market data for agents",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Allow all origins for agent access (agents run from various environments)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(ohlcv_router, prefix="/v1")


@app.get("/")
async def root():
    return {
        "name": "AI Datamarket",
        "version": "0.1.0",
        "description": "AI Bloomberg — one-command financial data for agents",
        "docs": "/docs",
        "manifest": "https://ai-datamarket.github.io/skill/manifest.json",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    db_path = Path(__file__).parent.parent / "data" / "market.duckdb"
    return {
        "status": "ok",
        "db_exists": db_path.exists(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8402, reload=True)
