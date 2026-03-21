# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

AI-powered data marketplace skill ("AI Bloomberg") — a Claude skill that gives agents one-command access to financial and market data (crypto, stocks, futures, macro, etc.). Targets Claude Code, Codex, OpenClaw and similar agent runtimes.

## Current Architecture

- **Skill definition** (`skill/SKILL.md`, `skill/manifest.json`) — MCP-compatible skill installable via a single prompt command
- **Data crawler** — Python asyncio, multi-exchange (Binance/OKX/Bybit), minute-level OHLCV stored in DuckDB, checkpoint-based resume
- **API layer** (`api/`) — FastAPI on port 8402, serves OHLCV with symbol/exchange/interval/time/duration params; higher timeframes aggregated from 1m data
- **Symbol config** (`config/symbols.py`) — Top 100 crypto bases, per-exchange target generation, symbol format conversion
- **Landing page** (`web/index.html`) — static single-page site, shows install command + supported data categories with lit/unlit indicators
- **Identity gate** — zCloak AI ID verification (stub, not yet enforced)

## Key Conventions

- Timezone: UTC+8 (Asia/Shanghai) for all time inputs/outputs
- Time rounding: always round **down** (floor) to the requested timeframe boundary
- Default duration: 60 candles when not specified
- Python 3.9: no `X | Y` union syntax, use `Optional[X]`
- Exchanges: Binance, OKX, Bybit (symbol formats differ per exchange, handled by `config/symbols.py`)

## Checklist: Adding New Data

- **Always update `skill/SKILL.md`** — any new data source, symbol, or exchange must be reflected in the skill definition so agents know what's available
- **If adding a new data category** (e.g. stocks, futures, macro), also update the lit/unlit status indicators in `web/index.html` to show the category as active
