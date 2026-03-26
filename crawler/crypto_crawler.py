"""
Crypto OHLCV Crawler — Binance, OKX, Bybit.

Each exchange writes to its own DuckDB file (data/crypto/{exchange}.duckdb),
eliminating cross-process lock contention.

Usage:
    python crypto_crawler.py --exchange binance --mode backfill --days 7
    python crypto_crawler.py --exchange okx --mode backfill
    python crypto_crawler.py --exchange bybit --mode backfill --symbol BTCUSDT --days 1
    python crypto_crawler.py --exchange all --mode live
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.symbols import get_targets
from crawler.base_crawler import BaseCrawler
from crawler.gate_registry import get_gate, close_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class CryptoCrawler(BaseCrawler):
    domain = "crypto"

    def get_targets(self, platform):
        return get_targets(platform)

    def get_gate(self, platform):
        return get_gate(platform)

    async def close_gates(self):
        await close_all()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crypto OHLCV Crawler (per-exchange DuckDB)")
    parser.add_argument("--mode", choices=["backfill", "live"], default="backfill")
    parser.add_argument("--exchange", default="all",
                        help="Exchange: binance, okx, bybit, or all (default: all)")
    parser.add_argument("--days", type=int, default=None,
                        help="Backfill only last N days (quick test)")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Only crawl this specific symbol (e.g. BTCUSDT, BTC-USDT)")
    args = parser.parse_args()

    if args.exchange == "all":
        exchange_list = ["binance", "okx", "bybit"]
    else:
        exchange_list = [args.exchange]

    crawler = CryptoCrawler()
    asyncio.run(crawler.run(args.mode, exchange_list, args.days, args.symbol))
