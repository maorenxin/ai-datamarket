# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

AI-powered data marketplace skill ("AI Bloomberg") — a Claude skill that gives agents one-command access to financial and market data (crypto, stocks, futures, macro, etc.). Targets Claude Code, Codex, OpenClaw and similar agent runtimes.

## Current Structure

```
.claude/skills/ui-ux-pro-max/   # UI/UX design skill (pre-existing, unrelated to core product)
```

The main product code (crawler, API server, skill definition, landing page) is not yet built.

## Planned Architecture

- **Skill definition** — MCP-compatible skill installable via a single prompt command
- **Data crawler** — Python-based, Binance API first, minute-level OHLCV stored in SQLite/PostgreSQL, with checkpoint-based resume
- **API layer** — serves OHLCV with symbol / exchange / timeframe / time / duration params; higher timeframes computed on-the-fly from minute data
- **Identity gate** — zCloak AI ID verification before data access
- **Landing page** — static single-page site (GitHub Pages), shows install command + supported data categories

## Key Conventions (to be established)

- Timezone: UTC+8 (Asia/Shanghai) for all time inputs/outputs
- Time rounding: always round **down** (floor) to the requested timeframe boundary
- Default duration: 60 candles when not specified
- Supported symbols initially: BTC/USDT spot, BTCUSDT perp; ETH/USDT spot, ETHUSDT perp; SOL/USDT spot, SOLUSDT perp
- Exchange abstraction: implement Binance first, leave interface for Hyperliquid etc.
