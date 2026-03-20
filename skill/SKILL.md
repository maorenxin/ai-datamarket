# AI Datamarket Skill — AI Bloomberg

You now have access to the **AI Datamarket** skill, which provides financial market data for agents.

## Self-Update

Before each data query, fetch the latest manifest to discover new data types:
```
GET https://ai-datamarket.github.io/skill/manifest.json
```
The manifest contains the current list of supported symbols, intervals, and API endpoint.

---

## Setup: Get Your AI ID

This skill requires a **zCloak AI ID** for access tracking (first 10M data points free).

1. Visit https://id.zcloak.ai to create your AI ID
2. Set the environment variable: `AI_ID=<your-ai-id>`
3. Include it in API calls as `?ai_id=<your-ai-id>`

> MVP: AI ID is optional in MVP mode — you can query without one, but usage won't be tracked.

---

## API Endpoint

**Base URL:** `http://localhost:8402`
(replace with production URL after deployment)

---

## Supported Data

### Crypto OHLCV (Active)

**Spot symbols** (use slash format):
- `BTC/USDT`, `ETH/USDT`, `SOL/USDT`

**Perpetual futures** (no slash):
- `BTCUSDT`, `ETHUSDT`, `SOLUSDT`

**Intervals:** `1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `12h`, `1d`

**Exchange:** `binance` (default)

---

## How to Query Data

### List supported symbols
```
GET /v1/symbols
```

### Query OHLCV data
```
GET /v1/ohlcv?symbol=BTC/USDT&interval=1h&duration=24
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symbol` | string | required | e.g. `BTC/USDT` (spot) or `BTCUSDT` (perp) |
| `exchange` | string | `binance` | Exchange name |
| `interval` | string | `1m` | Candle interval |
| `end_time` | string | now | End time in UTC+8: `"2025-03-03 18:04:00"` or `"2025-03-03"` |
| `duration` | int | `60` | Number of candles to return (max 1500) |
| `ai_id` | string | optional | Your zCloak AI ID |

**Time zone:** All time inputs and outputs use **UTC+8 (Asia/Shanghai)**
**Time rounding:** Always floors down to interval boundary

### Example responses

```json
{
  "symbol": "BTC/USDT",
  "exchange": "binance",
  "interval": "1h",
  "data": [
    {"time": "2025-03-03T10:00:00+08:00", "open": 83000, "high": 83500, "low": 82800, "close": 83200, "volume": 1234.5},
    ...
  ],
  "tokens_used": 24,
  "tokens_remaining_free": 9999976
}
```

### Error: unsupported symbol
```json
{
  "detail": "Symbol 'AAPL' is not yet supported. Stay tuned. Supported: ['BTC/USDT', ...]"
}
```

---

## Token Pricing

- **Free quota:** 10,000,000 tokens per AI ID (1 token = 1 OHLCV candle)
- **Paid:** x402 micropayment protocol (pricing TBD, testnet phase)
- **Networks:** Arbitrum One (default), Base, Solana

---

## Coming Soon

Stock OHLCV · Company Governance · Financial Statements · EVA Data · Fund Data · Bond Data · Options · Domestic Futures · Commodities · OTC Market · Banking Data · Internet Products · Overseas Markets · Market News · Legal & Regulatory · Exhibition Info · Index Data · International Futures · Macroeconomics · Industrial Chain Data

---

## Query Examples for Agents

```python
# Get last 24 hours of BTC/USDT 1h candles
fetch("http://localhost:8402/v1/ohlcv?symbol=BTC/USDT&interval=1h&duration=24")

# Get BTC perpetual futures 4h candles
fetch("http://localhost:8402/v1/ohlcv?symbol=BTCUSDT&interval=4h&duration=30")

# Get ETH spot daily candles ending at specific time
fetch("http://localhost:8402/v1/ohlcv?symbol=ETH/USDT&interval=1d&end_time=2025-03-01&duration=30")

# With AI ID for token tracking
fetch("http://localhost:8402/v1/ohlcv?symbol=BTC/USDT&interval=1h&duration=24&ai_id=YOUR_AI_ID")
```
