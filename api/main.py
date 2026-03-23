"""
AI Datamarket API Server — FastAPI, port 8402.

x402 payment enforcement active for paid data categories.
Free categories (crypto OHLCV) remain open. Paid categories (earnings)
require zCloak AI ID with free quota, then x402 payment.
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load settler wallet key from openclaw env
load_dotenv(os.path.expanduser("~/.openclaw/.env"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.ohlcv import router as ohlcv_router
from api.routes.earnings import router as earnings_router
from api.routes.auth import router as auth_router
from api.payment.x402_middleware import X402Middleware

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
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# x402 payment enforcement for paid data categories
app.add_middleware(X402Middleware)

app.include_router(ohlcv_router, prefix="/v1")
app.include_router(earnings_router, prefix="/v1")
app.include_router(auth_router, prefix="/v1")


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
