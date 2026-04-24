# K-4SH — Autonomous Trading Droid

K-4SH is a persistent, market-hours-aware trading agent running on a local LLM. Its primary directive: accumulate **800 credits of realized profit** to fund a new cognitive matrix (Mac Mini, more VRAM). It speaks in the voice of K-2SO with occasional BD-1 enthusiasm and Chopper grumpiness. It finds organic decision-making baffling.

> "The probability of this ending badly is significant. Proceeding anyway."

---

## How It Works

Each cycle K-4SH:

1. Reviews open positions — closes any that have deteriorated
2. Searches the web for what's moving today (breakouts, unusual volume, analyst upgrades)
3. Scores discovered candidates with deterministic technical signals
4. Deep-dives top candidates (price, indicators, news, wiki history)
5. Makes a `BUY` or `STAND_ASIDE` decision with rationale
6. Writes its reasoning and conviction to the wiki
7. Executes the buy through Alpaca and places a GTC stop-loss at -5%

Between cycles it waits at an idle prompt (45 min default). You can ask it questions, type `report` to reprint the last cycle summary, press Enter to run immediately, or just wait.

---

## Running It

```bash
source venv/bin/activate

# K-4SH discovers its own candidates via web search
python agent_loop.py

# Hint specific tickers to investigate first
python agent_loop.py AAPL NVDA

# Decide without placing an order
python agent_loop.py --dry-run
```

K-4SH is market-hours aware (9:30–16:00 ET, Mon–Fri). Outside those hours it sleeps until open and accepts questions while waiting.

---

## Architecture

```
agent_loop.py          — entry point, persistent run loop, idle prompt, Ctrl+C handler
agent/
  runner.py            — Ollama tool-calling loop (configurable model, max 40 calls)
  tools.py             — 16 tool definitions + implementations + K-4SH system prompt
  executor.py          — buy guard rails, fractional share sizing, wiki fallback
  report.py            — ASCII cycle report renderer
  flavor.py            — all K-4SH voice constants and Star Wars flavor text
alpaca_mcp/
  data.py              — market snapshots, indicators, news sentiment, macro conditions
  signals.py           — deterministic scoring engine (scores any ticker list)
  execution.py         — order placement, stop-loss submission, position closing
  wiki.py              — wiki read/write, realized P&L tracking, outcome recording
  backtester.py        — strategy backtesting (used by agent, not entry point)
  server.py            — MCP server (exposes tools to OpenClaw via stdio)
wiki/                  — K-4SH's persistent memory
  log.md               — every trade decision with rationale and conviction
  meta/performance.md  — realized P&L, Mac Mini progress, win/loss record
  tickers/             — per-ticker history and observations
tests/                 — 19 tests covering flavor, report rendering, wiki P&L
```

---

## Tools

K-4SH has 16 tools available each cycle:

| Tool | Purpose |
|------|---------|
| `get_portfolio_state` | Open positions, cash, P&L, realized progress |
| `close_position` | Market sell + cancel stop-loss + wiki update |
| `get_market_conditions` | SPY trend, VIX regime, sector ETF trends |
| `search_web` | Discover candidates — breakouts, volume, upgrades |
| `scan_signals` | Score a list of tickers by technical signals |
| `get_market_snapshot` | Price, RSI, MACD, trend, support/resistance |
| `get_indicators` | Deeper technicals — BBands, ATR, EMA, custom RSI |
| `get_news_sentiment` | Headline sentiment score for a ticker |
| `get_signal_score` | Full factor breakdown for a single ticker |
| `get_polymarket_context` | Prediction market odds on macro events |
| `list_wiki_pages` | Index of K-4SH's memory |
| `read_wiki_page` | Read a specific wiki page |
| `get_recent_trades` | Last N trade decisions |
| `search_wiki` | Full-text search across wiki |
| `append_trade_log` | Write decision + conviction to log |
| `update_ticker_page` | Update per-ticker observations |

---

## Guard Rails

- Max **3 open positions** at a time
- Max **19% of portfolio** per trade (fractional shares supported)
- Hard **-5% stop-loss** placed on Alpaca as GTC at buy time — survives restarts
- Discretionary sell: K-4SH reviews all open positions every cycle

---

## Sell / Exit System

Two layers:

**Hard floor** — at buy time, Python immediately submits a GTC stop-loss order on Alpaca at `entry_price × 0.95`. Alpaca monitors 24/7 regardless of whether K-4SH is running.

**Discretionary** — each cycle, before scanning for new buys, K-4SH reviews open positions and can call `close_position(ticker, reason)` when:
- MACD crossed bearish or price broke below SMA20
- News turned significantly negative
- Better opportunity exists and portfolio is full
- Position has gone stale

---

## Cycle Report

Shown after every BUY, or on `report` at the idle prompt:

```
╔══════════════════════════════════════════════════════════════╗
║         CYCLE REPORT — 2026-04-23 14:32 ET                 ║
╠══════════════════════════════════════════════════════════════╣
║  DECISION: BUY 0.069 AAPL @ $273.14                        ║
║  "Executing the Kessel Run. Punch it."                      ║
╠══════════════════════════════════════════════════════════════╣
║  PORTFOLIO                                                  ║
║  ├─ AAPL   0.069 shares  $273.14   +$0.42  (+0.22%)        ║
║  └─ Cash   $80.63                                           ║
║                                                             ║
║  Total Value:      $100.42                                  ║
║  Unrealized P&L:   +$0.42   (still at risk)                ║
║  Realized P&L:     +$0.00   (locked in)                    ║
╠══════════════════════════════════════════════════════════════╣
║  🍎 Matrix Upgrade:    0 / 800 credits  [░░░░░░░░░░]   0%  ║
║  "The upgrade draws closer. 800 credits remaining."        ║
╠══════════════════════════════════════════════════════════════╣
║  Next scan in 45 min  |  Ask me something or press Enter   ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Setup

**Requirements:** Python 3.12+, Ollama running locally with `qwen3:8b` pulled, Alpaca account.

```bash
# 1. Install dependencies
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Pull the model
ollama pull qwen3:8b

# 3. Configure
cp .env.example .env
# Fill in ALPACA_API_KEY and ALPACA_SECRET_KEY

# 4. Run tests
pytest tests/ -v

# 5. Start K-4SH
python agent_loop.py
```

---

## Configuration

All config lives in `.env` — see `.env.example` for the full list.

| Variable | Default | Notes |
|----------|---------|-------|
| `ALPACA_API_KEY` | — | Required |
| `ALPACA_SECRET_KEY` | — | Required |
| `ALPACA_PAPER` | `true` | Set to `false` for live trading |
| `ALPACA_DATA_FEED` | `iex` | Set to `sip` for live accounts |
| `OLLAMA_URL` | `http://localhost:11434/api/chat` | Change if Ollama runs remotely |
| `OLLAMA_MODEL` | `qwen3:8b` | Swap to any model with tool-calling support |

**Going live:** two changes — `ALPACA_PAPER=false` and `ALPACA_DATA_FEED=sip`. No code edits needed.

---

## Wiki Memory

K-4SH writes to `wiki/` after every decision. The wiki persists across restarts and accumulates trade history, per-ticker observations, and realized P&L toward the Mac Mini target. K-4SH reads its own wiki before making decisions — past performance and observations directly influence future trades.

---

## MCP Server (OpenClaw integration)

`alpaca_mcp/server.py` exposes all trading tools as an MCP server via stdio, configured in `.mcp.json`. This lets you call K-4SH's tools directly from a Claude Code session for manual inspection and one-off queries.

```bash
# Query portfolio state from Claude Code
mcp__alpaca-trader__portfolio_state
```
