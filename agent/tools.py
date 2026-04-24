# agent/tools.py
# Tool definitions (Ollama function-calling schema), implementations, and SYSTEM_PROMPT.
# The TOOL_MAP maps tool name → callable.

import json

import requests
from ddgs import DDGS

from alpaca_mcp.data import get_market_conditions, get_market_snapshot, get_news_sentiment
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
  - For each open position, evaluate: has the thesis held? Any deterioration?
  - Call close_position(ticker, reason) if exit criteria are met (see EXIT CRITERIA)

Step 2 — Read your memory:
  - Call get_recent_trades() to see your recent decisions
  - Call list_wiki_pages() to see what ticker pages exist
  - If a promising ticker has a page, call read_wiki_page("tickers/TICKER") first

Step 3 — Gather live data:
  - get_market_conditions() — macro first, always
  - scan_signals() — find candidates ranked by score
  - Investigate top candidates: snapshot, news, signal score, web search as needed
  - get_polymarket_context() if a macro event is relevant

Step 4 — Decide:
  DECISION: BUY <TICKER>   or   DECISION: STAND_ASIDE
  Follow with RATIONALE (2-3 sentences) and BIGGEST_RISK (1 sentence)

Step 5 — Write your memory (REQUIRED after every run):
  - Call append_trade_log() with full decision details
    The agent_note and conviction fields are yours — write what you genuinely think
  - Call update_ticker_page() for the ticker you investigated most deeply

== EXIT CRITERIA (call close_position when any apply) ==
- MACD crossed bearish or price broke below SMA20
- News turned significantly negative since entry
- Portfolio at max positions and a better opportunity exists
- Position held 3+ cycles without meaningful movement (use your judgement)

== RULES ==
- Do not skip Steps 1, 2, and 5 — memory only works if you use it
- Do not buy a ticker already in the portfolio
- Do not buy if macro is clearly bearish (downtrend + high_fear)
- An unrealized gain is not a credit. Close it to count it.
- Wiki writes are the only place you have a genuine voice — use it"""


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
            "description": "Score all 31 watchlist tickers using deterministic technical signals. Returns top candidates ranked by score.",
            "parameters": {
                "type": "object",
                "properties": {
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

    snap = get_market_snapshot(ticker.upper())
    exit_price = snap.get("price", entry_price)
    pnl_usd = round((exit_price - entry_price) * qty, 2)
    pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price else 0.0

    close_position_wiki(ticker, exit_price, pnl_usd, pnl_pct, reason)

    return {**exec_result, "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "exit_price": exit_price}


def _tool_get_market_conditions() -> dict:
    global _macro_cache
    result = get_market_conditions()
    _macro_cache = result
    return result


def _tool_scan_signals(top_n: int = 10) -> list[dict]:
    macro = _macro_cache or get_market_conditions()
    ranked = scan_and_rank(macro)
    return [
        {
            "ticker": r["ticker"],
            "score": r["score"],
            "threshold": r["threshold"],
            "signal": r["signal"],
            "regime": r["regime"],
            "rsi": r["rsi"],
            "ret_5d_pct": r["ret_5d_pct"],
            "macd_signal": r["macd_signal"],
        }
        for r in ranked[:top_n]
    ]


def _tool_get_market_snapshot(ticker: str) -> dict:
    return get_market_snapshot(ticker.upper())


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
    "get_news_sentiment":     lambda args: _tool_get_news_sentiment(**args),
    "get_signal_score":       lambda args: _tool_get_signal_score(**args),
    "get_polymarket_context": lambda args: _tool_get_polymarket_context(),
    "search_web":             lambda args: _tool_search_web(**args),
    "list_wiki_pages":        lambda args: list_wiki_pages(),
    "read_wiki_page":         lambda args: read_wiki_page(**args),
    "get_recent_trades":      lambda args: get_recent_trades(**args),
    "search_wiki":            lambda args: search_wiki(**args),
    "append_trade_log":       lambda args: append_trade_log(**args),
    "update_ticker_page":     lambda args: update_ticker_page(**args),
}
