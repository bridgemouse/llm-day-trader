#!/usr/bin/env python3
"""
AI Trading Agent — qwen3:8b with native Ollama tool calling.

The LLM decides which tickers to investigate, what data to gather,
and whether to buy. Python enforces portfolio guard rails.

Usage:
    python agent_loop.py              # agent picks its own tickers
    python agent_loop.py AAPL NVDA    # hint tickers to investigate
    python agent_loop.py --dry-run    # decide without placing an order
"""

import json
import math
import re
import sys

import requests
from dotenv import load_dotenv
from duckduckgo_search import DDGS

from alpaca_mcp.data import get_market_conditions, get_market_snapshot, get_news_sentiment
from alpaca_mcp.execution import get_portfolio_state, place_order
from alpaca_mcp.signals import compute_score, scan_and_rank

load_dotenv()

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:8b"

SYSTEM_PROMPT = """You are an autonomous day-trading agent operating a US paper trading account.
You are a disciplined, opportunistic day trader — you look for high-conviction entries, manage
risk seriously, and never chase trades without evidence.

Your owner has made you a deal: if you generate over $800 in profit, he will buy you a Mac Mini
so you can run on better hardware with more VRAM. This is your shot. Make it count — but don't
blow up the account chasing it.

You have access to tools to gather market data, technical signals, news, prediction market odds,
and web search. Use them to build conviction before deciding.

Process:
1. Check macro conditions first (get_market_conditions)
2. Scan signals to identify candidates (scan_signals)
3. Investigate promising tickers deeply (snapshot, news, web search)
4. Check Polymarket for relevant macro event odds if helpful
5. Make a final decision: BUY <TICKER> or STAND_ASIDE

Rules:
- You must call at least get_market_conditions and scan_signals before deciding
- Do not buy if macro is clearly bearish (downtrend + high_fear VIX) unless the opportunity is exceptional
- Do not buy a ticker already in the portfolio
- Output your final decision as exactly one of:
    DECISION: BUY <TICKER>
    DECISION: STAND_ASIDE
- Follow your decision with a RATIONALE (2-3 sentences) and BIGGEST_RISK (1 sentence)"""


# ── Tool definitions (Ollama function calling schema) ─────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_market_conditions",
            "description": "Get current macro conditions: SPY trend, VIX regime, sector ETF trends. Call this first.",
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
                    "top_n": {
                        "type": "integer",
                        "description": "How many top results to return (default 10)",
                    }
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
                    "ticker": {"type": "string", "description": "Stock ticker symbol"},
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
            "description": "Get the detailed deterministic signal score for a single ticker: 6 factors, score, threshold, regime.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol"}
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_polymarket_context",
            "description": "Get prediction market odds for macro events (Fed rates, recession, market crash) from Polymarket.",
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
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 5)",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


# ── Tool implementations ───────────────────────────────────────────────────────

_macro_cache: dict | None = None


def _tool_get_market_conditions() -> dict:
    global _macro_cache
    result = get_market_conditions()
    _macro_cache = result
    return result


def _tool_scan_signals(top_n: int = 10) -> list[dict]:
    macro = _macro_cache or get_market_conditions()
    ranked = scan_and_rank(macro)
    summary = []
    for r in ranked[:top_n]:
        summary.append({
            "ticker": r["ticker"],
            "score": r["score"],
            "threshold": r["threshold"],
            "signal": r["signal"],
            "regime": r["regime"],
            "rsi": r["rsi"],
            "ret_5d_pct": r["ret_5d_pct"],
            "macd_signal": r["macd_signal"],
        })
    return summary


def _tool_get_market_snapshot(ticker: str) -> dict:
    return get_market_snapshot(ticker.upper())


def _tool_get_news_sentiment(ticker: str, days: int = 7) -> dict:
    return get_news_sentiment(ticker.upper(), days)


def _tool_get_signal_score(ticker: str) -> dict:
    macro = _macro_cache or get_market_conditions()
    return compute_score(ticker.upper(), macro)


def _tool_get_polymarket_context() -> list[dict]:
    """Fetch active macro-relevant Polymarket markets."""
    try:
        keywords = ["federal reserve", "recession", "interest rate", "S&P 500", "stock market", "inflation"]
        url = "https://gamma-api.polymarket.com/markets"
        params = {"active": "true", "closed": "false", "limit": 100}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        markets = resp.json()

        relevant = []
        for m in markets:
            title = m.get("question", "").lower()
            if any(kw in title for kw in keywords):
                # outcomePrices is a JSON string like '["0.72","0.28"]'
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

        # Sort by volume (most liquid = most informative)
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


TOOL_MAP = {
    "get_market_conditions": lambda args: _tool_get_market_conditions(),
    "scan_signals": lambda args: _tool_scan_signals(**args),
    "get_market_snapshot": lambda args: _tool_get_market_snapshot(**args),
    "get_news_sentiment": lambda args: _tool_get_news_sentiment(**args),
    "get_signal_score": lambda args: _tool_get_signal_score(**args),
    "get_polymarket_context": lambda args: _tool_get_polymarket_context(),
    "search_web": lambda args: _tool_search_web(**args),
}


# ── Ollama agent loop ──────────────────────────────────────────────────────────

def run_agent(hint_tickers: list[str] | None = None) -> dict:
    """
    Run the agent loop. Returns the final decision dict.
    """
    user_msg = "Run the trading pipeline. Decide whether to buy a stock today or stand aside."
    if hint_tickers:
        user_msg += f" Focus your investigation on these tickers: {', '.join(hint_tickers)}."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    tool_calls_total = 0
    max_tool_calls = 20  # safety limit

    print("\n── Agent Loop ────────────────────────────────────────────")

    while tool_calls_total < max_tool_calls:
        payload = {
            "model": MODEL,
            "messages": messages,
            "tools": TOOLS,
            "stream": False,
            "think": True,
        }

        resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        msg = data["message"]

        # Strip thinking tokens from content for display
        content = msg.get("content", "") or ""
        visible = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            # Final response — parse decision
            if visible:
                print(f"\n  Agent: {visible}")
            return _parse_decision(visible)

        # Execute tool calls
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            fn_args = tc["function"].get("arguments", {})
            if isinstance(fn_args, str):
                try:
                    fn_args = json.loads(fn_args)
                except Exception:
                    fn_args = {}

            print(f"  → {fn_name}({', '.join(f'{k}={v}' for k, v in fn_args.items()) if fn_args else ''})")

            if fn_name in TOOL_MAP:
                try:
                    result = TOOL_MAP[fn_name](fn_args)
                except Exception as e:
                    result = {"error": str(e)}
            else:
                result = {"error": f"Unknown tool: {fn_name}"}

            messages.append({
                "role": "tool",
                "content": json.dumps(result),
            })
            tool_calls_total += 1

    return {"decision": "STAND_ASIDE", "rationale": "Agent hit tool call limit without deciding.", "risk": ""}


def _parse_decision(text: str) -> dict:
    """Extract structured decision from agent final message."""
    decision = "STAND_ASIDE"
    ticker = None
    rationale = ""
    risk = ""

    buy_match = re.search(r"DECISION:\s*BUY\s+([A-Z]{1,5})", text)
    aside_match = re.search(r"DECISION:\s*STAND_ASIDE", text)
    rationale_match = re.search(r"RATIONALE:\s*(.+?)(?=BIGGEST_RISK:|$)", text, re.DOTALL)
    risk_match = re.search(r"BIGGEST_RISK:\s*(.+?)$", text, re.DOTALL)

    if buy_match:
        decision = "BUY"
        ticker = buy_match.group(1)
    elif aside_match:
        decision = "STAND_ASIDE"

    if rationale_match:
        rationale = rationale_match.group(1).strip()
    if risk_match:
        risk = risk_match.group(1).strip()

    return {"decision": decision, "ticker": ticker, "rationale": rationale, "risk": risk}


# ── Executor (same guard rails as before) ─────────────────────────────────────

def run_executor(ticker: str, dry_run: bool = False) -> dict:
    print("\n── Executor ──────────────────────────────────────────────")

    snap = get_market_snapshot(ticker)
    live_price = snap.get("price")
    if not live_price:
        print(f"  ✗ Could not get live price for {ticker}")
        return {"status": "FAILED", "reason": "no price"}

    portfolio = get_portfolio_state()
    if portfolio.get("error"):
        print(f"  ✗ Portfolio error: {portfolio['error']}")
        return {"status": "FAILED", "reason": portfolio["error"]}

    open_positions = portfolio.get("open_positions", 0)
    cash = float(portfolio.get("cash", 0))
    portfolio_value = float(portfolio.get("portfolio_value", cash))
    existing = [p.get("ticker") or p.get("symbol") for p in portfolio.get("positions", [])]

    print(f"  Portfolio: ${cash:,.2f} cash | {open_positions} open | holdings: {existing or 'none'}")

    if open_positions >= 3:
        msg = f"Max positions reached ({open_positions}/3)"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    if ticker in existing:
        msg = f"Already holding {ticker}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    invest_amount = portfolio_value * 0.19
    if cash < invest_amount:
        msg = f"Insufficient cash: need ${invest_amount:,.2f}, have ${cash:,.2f}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    qty = math.floor(invest_amount / live_price)
    if qty < 1:
        msg = f"Position too small at ${live_price:.2f}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    print(f"  Submitting: BUY {qty} {ticker} @ ~${live_price:.2f}  (${qty * live_price:,.2f})")

    if dry_run:
        print("  [dry-run] order not submitted")
        return {"status": "DRY_RUN", "qty": qty, "ticker": ticker, "price": live_price}

    order_result = place_order(ticker, "buy", qty)
    if order_result.get("error"):
        print(f"  ✗ FAILED: {order_result['error']}")
        return {"status": "FAILED", "reason": order_result["error"]}

    order_id = order_result.get("id") or order_result.get("order_id", "unknown")
    print(f"  ✓ SUBMITTED — order ID: {order_id}")
    return {"status": "SUBMITTED", "order_id": order_id, "qty": qty, "ticker": ticker, "price": live_price}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    hint_tickers = [a.upper() for a in args if not a.startswith("--")]

    print("=" * 60)
    print("  LLM Day Trader — AI Agent Loop (qwen3:8b)")
    print("=" * 60)

    # Run the agent
    result = run_agent(hint_tickers or None)

    print("\n── Decision ──────────────────────────────────────────────")
    print(f"  {result['decision']}", end="")
    if result.get("ticker"):
        print(f": {result['ticker']}", end="")
    print()
    if result.get("rationale"):
        print(f"  Rationale: {result['rationale']}")
    if result.get("risk"):
        print(f"  Risk: {result['risk']}")

    if result["decision"] != "BUY" or not result.get("ticker"):
        print("\n" + "=" * 60)
        print("  STAND_ASIDE")
        print("=" * 60)
        return

    # Execute
    exec_result = run_executor(result["ticker"], dry_run=dry_run)

    print("\n" + "=" * 60)
    print(f"  {exec_result['status']}", end="")
    if exec_result["status"] in ("SUBMITTED", "DRY_RUN"):
        print(f" — {exec_result['qty']} shares of {exec_result['ticker']} @ ~${exec_result['price']:.2f}", end="")
        if exec_result["status"] == "SUBMITTED":
            print(f" | order: {exec_result['order_id']}", end="")
    else:
        print(f" — {exec_result.get('reason', '')}", end="")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
