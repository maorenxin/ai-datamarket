# AI Bloomberg — AI Datamarket

> One command. All financial data. For AI agents.

The Bloomberg terminal for the agentic era. Install as a skill in Claude Code, OpenClaw, or Codex — then query crypto, stock, macro, and other financial data with a single API call.

## Install the Skill

Paste this into any agent:

```
Install the AI Datamarket skill from https://ai-datamarket.github.io/skill/SKILL.md
```

## Quick Start (Local)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the API server
python -m api.main

# 3. Backfill last 7 days of data
python crawler/binance_crawler.py --mode backfill --days 7

# 4. Query data
curl "http://localhost:8402/v1/ohlcv?symbol=BTC/USDT&interval=1h&duration=24"
```

## API Reference

### `GET /v1/ohlcv`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symbol` | string | required | `BTC/USDT` (spot) or `BTCUSDT` (perp) |
| `exchange` | string | `binance` | Exchange |
| `interval` | string | `1m` | `1m/5m/15m/30m/1h/2h/4h/6h/12h/1d` |
| `end_time` | string | now | UTC+8 time, e.g. `2025-03-03 18:00:00` |
| `duration` | int | `60` | Number of candles (max 1500) |
| `ai_id` | string | optional | zCloak AI ID for token tracking |

### `GET /v1/symbols`

List all supported symbols.

## Architecture

```
crawler/   → Binance data crawler (historical + live)
api/       → FastAPI server (port 8402)
data/      → DuckDB database
skill/     → SKILL.md + manifest.json (for GitHub Pages)
web/       → Landing page (for GitHub Pages)
config/    → Pricing and configuration
```

## Supported Data (MVP)

- **Crypto OHLCV** (Live): BTC/USDT, ETH/USDT, SOL/USDT — Spot & Perpetual Futures
- **Coming Soon**: Stocks, Bonds, Options, Futures, Macro, and 15+ more categories

## Token Pricing

- First **10,000,000 tokens** free per AI ID (1 token = 1 OHLCV candle)
- Paid tier: x402 micropayment protocol (TBD pricing)
- AI ID: [zCloak](https://id.zcloak.ai)

## License

MIT — Open source.
