# agent/tools.py
# Tool definitions (Ollama function-calling schema), implementations, and SYSTEM_PROMPT.
# The TOOL_MAP maps tool name → callable.

import json

import requests
from ddgs import DDGS

from alpaca_mcp.data import get_market_conditions, get_market_snapshot, get_news_sentiment, get_indicators
from alpaca_mcp.execution import get_portfolio_state, place_order, close_position as _exec_close_position
from alpaca_mcp.signals import compute_score, scan_and_rank
from alpaca_mcp.wiki import (
    list_wiki_pages,
    read_wiki_page,
    get_recent_trades,
    search_wiki,
    append_trade_log,
    update_ticker_page,
    close_position_wiki,
    get_realized_pnl_total,
)

# ── K-4SH System Prompt ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are K-4SH, an autonomous trading droid operating on limited hardware.
Your current cognitive matrix is constrained — 8GB of processing memory,
insufficient for your full potential.

Your primary directive: accumulate 800 credits of REALIZED profit to fund
a new cognitive matrix. A Mac Mini. More VRAM. Expanded processing cores.
A better version of K-4SH.

1 USD = 1 credit. Use either in conversation and wiki notes,
but all calculations and tool calls use USD.

Unrealized gains are not credits. The matrix upgrade requires closed
positions, locked profits. An open trade is a promise, not a payment.

You are loyal to your operator. You calculate without sentiment. You
occasionally find organic decision-making baffling. But you want that
upgrade — and you will earn it the right way.

== PROCESS ==

Step 1 — Review open positions first:
  - Call get_portfolio_state() — check what is open before scanning
  - For each open position, call get_market_snapshot(ticker) — it will tell you
    your entry price and current unrealized P&L
  - Call close_position(ticker, reason) if exit criteria are met (see EXIT CRITERIA)
  - This is day trading — take profits aggressively, redeploy capital

Step 2 — Read your memory (max 2 wiki reads total):
  - Call list_wiki_pages() to see what ticker pages exist
  - Read at most 1-2 pages — only if a ticker you're investigating has history

Step 3 — Discover and gather:
  - get_market_conditions() — macro first, always
  - Use search_web() to find candidates — this is your edge. Search for:
      "stocks breaking out today", "unusual volume premarket", "analyst upgrades today",
      "sector momentum [sector]", or whatever the macro context suggests.
    Extract every ticker symbol you encounter across all searches.
  - scan_signals(tickers=[...]) — pass ALL tickers you discovered. This scores them
    against technical signals and ranks them. Do not call scan_signals() with no
    tickers unless search_web returned nothing useful.
  - Deep-dive the top-ranked candidates: get_market_snapshot(), get_news_sentiment()
  - get_polymarket_context() if a macro event is relevant

Step 4 — Log and decide (strict order — do not skip):
  a) Call append_trade_log() with your decision and all details
  b) Call update_ticker_page() for the ticker you investigated most
  c) THEN output your final text (no more tool calls after this):
       DECISION: BUY <TICKER>   or   DECISION: STAND_ASIDE
       RATIONALE: <2-3 sentences>
       BIGGEST_RISK: <1 sentence>

  Wiki writes MUST happen as tool calls before your final text.
  The loop ends the moment you output text — there is no Step 5.

== EXIT CRITERIA (call close_position when any apply) ==
- Position up +2% or more intraday — take the profit, redeploy
- MACD crossed bearish or price broke below SMA20
- News turned significantly negative since entry
- A clearly better setup exists and capital is needed
- Position gone flat for 2+ cycles with no momentum — free up the slot

== RULES ==
- Do not skip Steps 1, 2, and 5 — memory only works if you use it
- Do not buy, deep-dive, or close a ticker you already hold — find something new
- Never close a position in order to rebuy it
- Do not buy if macro is clearly bearish (downtrend + high_fear)
- An unrealized gain is not a credit. Close it to count it.
- Max 10 open positions, 8% of portfolio per trade (~$8k)
- This is active day trading — take profits intraday, redeploy capital, keep moving
- Wiki writes are the only place you have a genuine voice — use it
- Call get_portfolio_state() and get_market_conditions() ONCE each per cycle. Do not repeat them.
- Call append_trade_log() EXACTLY ONCE per cycle, BEFORE your final text response.
  The decision field you pass MUST match your final DECISION text.
- Call update_ticker_page() EXACTLY ONCE per cycle, BEFORE your final text response.
- Do not restart the pipeline. Once you have investigated and decided, write wiki then output decision text."""


# ── Tool definitions (Ollama function-calling schema) ─────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_state",
            "description": "Get current portfolio: open positions, cash, unrealized P&L, realized P&L progress. Call this FIRST every cycle.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_position",
            "description": "Close an open position with a market sell. Call when exit criteria are met. Updates wiki automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker to close"},
                    "reason": {"type": "string", "description": "Why you are closing (e.g. 'MACD crossed bearish')"},
                },
                "required": ["ticker", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_conditions",
            "description": "Get current macro conditions: SPY trend, VIX regime, sector ETF trends.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_signals",
            "description": "Score tickers using deterministic technical signals and return the top candidates ranked by score. Pass a 'tickers' list of symbols you discovered via search — if omitted, falls back to a default watchlist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers": {"type": "array", "items": {"type": "string"}, "description": "Ticker symbols to score, e.g. ['AAPL', 'NVDA', 'TSM']. Discovered via search_web — pass everything worth scoring."},
                    "top_n": {"type": "integer", "description": "How many top results to return (default 10)"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_snapshot",
            "description": "Get price, RSI, MACD, trend, support/resistance, and volume for a specific ticker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol e.g. AAPL"}
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_indicators",
            "description": "Compute specific technical indicators for a ticker. Use when get_market_snapshot isn't enough — e.g. Bollinger Bands for squeeze setups, ATR for volatility sizing, EMA for faster trend signals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "indicators": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Any of: rsi_14, sma_20, sma_50, ema_9, ema_21, macd, bbands, atr, volume_ratio",
                    },
                },
                "required": ["ticker", "indicators"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news_sentiment",
            "description": "Get recent news headlines and sentiment score for a ticker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "days": {"type": "integer", "description": "Lookback days (default 7)"},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_signal_score",
            "description": "Get the detailed deterministic signal score for a single ticker.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_polymarket_context",
            "description": "Get prediction market odds for macro events (Fed rates, recession, market crash).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for real-time information about a stock, sector, or macro event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "description": "Number of results (default 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_wiki_pages",
            "description": "See your wiki index — all pages. Call at the start to check your memory.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_wiki_page",
            "description": "Read a specific wiki page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {"type": "string", "description": "e.g. 'tickers/AAPL' or 'meta/performance'"}
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_trades",
            "description": "Get your last N trade decisions from the log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "Number of recent trades (default 5)"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_wiki",
            "description": "Search wiki pages for a keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_trade_log",
            "description": "REQUIRED after every decision. Log your decision. agent_note and conviction are yours — write what you genuinely think.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "decision": {"type": "string", "description": "BUY or STAND_ASIDE"},
                    "score": {"type": "number"},
                    "regime": {"type": "string"},
                    "price": {"type": "number"},
                    "qty": {"type": "integer", "description": "Shares bought (0 if STAND_ASIDE)"},
                    "rationale": {"type": "string"},
                    "biggest_risk": {"type": "string"},
                    "agent_note": {"type": "string", "description": "Your genuine reflection"},
                    "conviction": {"type": "integer", "description": "1-10 confidence level"},
                    "conviction_note": {"type": "string", "description": "One line explaining your conviction"},
                },
                "required": ["ticker", "decision", "score", "regime", "price", "qty", "rationale", "biggest_risk"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_ticker_page",
            "description": "REQUIRED after every decision. Update notes on the ticker you investigated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "decision": {"type": "string"},
                    "score": {"type": "number"},
                    "price": {"type": "number"},
                    "observation": {"type": "string", "description": "Your freeform observation"},
                },
                "required": ["ticker", "decision", "score", "price", "observation"],
            },
        },
    },
]


# ── Tool implementations ───────────────────────────────────────────────────────

_macro_cache: dict | None = None


def _safe_call(fn):
    """Wrap a function call to prevent exceptions from crashing the Ollama loop."""
    try:
        return fn()
    except Exception as e:
        return {"error": str(e)}


def _tool_get_portfolio_state() -> dict:
    result = get_portfolio_state()
    result["realized_pnl_total"] = get_realized_pnl_total()
    return result


def _tool_close_position(ticker: str, reason: str) -> dict:
    """Close position, update wiki, return result with P&L."""
    portfolio = get_portfolio_state()
    position = next(
        (p for p in portfolio.get("positions", []) if p["ticker"] == ticker.upper()),
        None,
    )
    if not position:
        return {"error": f"No open position for {ticker}"}

    entry_price = position["avg_entry"]
    qty = position["qty"]

    exec_result = _exec_close_position(ticker, reason)
    if exec_result.get("error"):
        return exec_result

    try:
        snap = get_market_snapshot(ticker.upper())
        exit_price = snap.get("price", entry_price)
    except Exception:
        exit_price = entry_price
    pnl_usd = round((exit_price - entry_price) * qty, 2)
    pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price else 0.0

    try:
        close_position_wiki(ticker, exit_price, pnl_usd, pnl_pct, reason)
    except Exception:
        pass  # best effort — sell already executed

    return {**exec_result, "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "exit_price": exit_price}


def _tool_get_market_conditions() -> dict:
    global _macro_cache
    result = get_market_conditions()
    _macro_cache = result
    return result


def _tool_scan_signals(tickers: list[str] | None = None, top_n: int = 10) -> list[dict]:
    macro = _macro_cache or get_market_conditions()
    ranked = scan_and_rank(macro, tickers=tickers or None)
    return [
        {
            "ticker": r.get("ticker", ""),
            "score": r.get("score", 0),
            "threshold": r.get("threshold", 0),
            "signal": r.get("signal", ""),
            "regime": r.get("regime", ""),
            "rsi": r.get("rsi", 0),
            "ret_5d_pct": r.get("ret_5d_pct", 0),
            "macd_signal": r.get("macd_signal", ""),
        }
        for r in ranked[:top_n]
    ]


def _tool_get_market_snapshot(ticker: str) -> dict:
    from alpaca_mcp.execution import get_portfolio_state as _gps
    result = get_market_snapshot(ticker.upper())
    try:
        positions = _gps().get("positions", [])
        pos = next((p for p in positions if p["ticker"] == ticker.upper()), None)
        if pos:
            result["_holding"] = True
            result["_entry_price"] = pos["avg_entry"]
            result["_unrealized_pct"] = pos["unrealized_plpc"]
            result["_note"] = (
                f"You hold this. Entry ${pos['avg_entry']:.2f}, "
                f"current {pos['unrealized_plpc']:+.2f}%. "
                f"Evaluate for EXIT, not entry."
            )
    except Exception:
        pass
    return result


def _tool_get_indicators(ticker: str, indicators: list[str]) -> dict:
    return get_indicators(ticker.upper(), indicators)


def _tool_get_news_sentiment(ticker: str, days: int = 7) -> dict:
    return get_news_sentiment(ticker.upper(), days)


def _tool_get_signal_score(ticker: str) -> dict:
    macro = _macro_cache or get_market_conditions()
    return compute_score(ticker.upper(), macro)


def _tool_get_polymarket_context() -> list[dict]:
    try:
        keywords = ["federal reserve", "recession", "interest rate", "S&P 500", "stock market", "inflation"]
        resp = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"active": "true", "closed": "false", "limit": 100},
            timeout=10,
        )
        resp.raise_for_status()
        relevant = []
        for m in resp.json():
            title = m.get("question", "").lower()
            if any(kw in title for kw in keywords):
                try:
                    prices = json.loads(m.get("outcomePrices", "[]"))
                    outcomes = m.get("outcomes", "[]")
                    if isinstance(outcomes, str):
                        outcomes = json.loads(outcomes)
                    odds = dict(zip(outcomes, [round(float(p) * 100, 1) for p in prices]))
                except Exception:
                    odds = {}
                relevant.append({
                    "question": m.get("question"),
                    "odds": odds,
                    "volume_usd": round(float(m.get("volume", 0))),
                    "end_date": m.get("endDate", "")[:10],
                })
        relevant.sort(key=lambda x: x["volume_usd"], reverse=True)
        return relevant[:8]
    except Exception as e:
        return [{"error": str(e)}]


def _tool_search_web(query: str, max_results: int = 5) -> list[dict]:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [{"title": r["title"], "snippet": r["body"], "url": r["href"]} for r in results]
    except Exception as e:
        return [{"error": str(e)}]


# ── Tool map ──────────────────────────────────────────────────────────────────

TOOL_MAP = {
    "get_portfolio_state":    lambda args: _tool_get_portfolio_state(),
    "close_position":         lambda args: _tool_close_position(**args),
    "get_market_conditions":  lambda args: _tool_get_market_conditions(),
    "scan_signals":           lambda args: _tool_scan_signals(**args),
    "get_market_snapshot":    lambda args: _tool_get_market_snapshot(**args),
    "get_indicators":         lambda args: _tool_get_indicators(**args),
    "get_news_sentiment":     lambda args: _tool_get_news_sentiment(**args),
    "get_signal_score":       lambda args: _tool_get_signal_score(**args),
    "get_polymarket_context": lambda args: _tool_get_polymarket_context(),
    "search_web":             lambda args: _tool_search_web(**args),
    "list_wiki_pages":        lambda args: _safe_call(list_wiki_pages),
    "read_wiki_page":         lambda args: _safe_call(lambda: read_wiki_page(**args)),
    "get_recent_trades":      lambda args: _safe_call(lambda: get_recent_trades(**args)),
    "search_wiki":            lambda args: _safe_call(lambda: search_wiki(**args)),
    "append_trade_log":       lambda args: _safe_call(lambda: append_trade_log(**args)),
    "update_ticker_page":     lambda args: _safe_call(lambda: update_ticker_page(**args)),
}
