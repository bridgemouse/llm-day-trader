#!/usr/bin/env python3
"""
Trading loop — deterministic signal scoring, no backtest loop.

Usage:
    python run_loop.py              # scan all 31 tickers, pick best signal
    python run_loop.py AAPL         # score a specific ticker only
    python run_loop.py --dry-run    # score without placing an order

Pipeline:
    1. get_market_conditions()       → macro regime
    2. scan_and_rank() or score one  → signal scores for all candidates
    3. top scorer >= threshold?      → BUY / WATCH
    4. place_order()                 → execute if BUY
    5. optional LLM thesis           → 60-word explanation (Ollama)
"""

import json
import math
import re
import sys

import requests
from dotenv import load_dotenv

from alpaca_mcp.data import get_market_conditions, get_market_snapshot
from alpaca_mcp.execution import get_portfolio_state, place_order
from alpaca_mcp.signals import compute_score, scan_and_rank, WHITELIST

load_dotenv()

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:4b"

THESIS_SYSTEM = """You are a trading analyst. Write ONE concise paragraph (under 60 words) explaining why this trade was triggered.
Rules:
- Ground every claim in the supplied metrics only
- Name the two strongest signals
- Do not mention AI, models, or uncertainty
- No bullet points"""


# ── Optional LLM thesis (60 words, non-blocking) ─────────────────────────────

def generate_thesis(result: dict, macro: dict) -> str:
    try:
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": THESIS_SYSTEM},
                {"role": "user", "content": (
                    f"Ticker: {result['ticker']} | Price: ${result['price']} | "
                    f"Score: {result['score']}/{result['threshold']} ({result['regime']} regime) | "
                    f"RSI: {result['rsi']} | 5d return: {result['ret_5d_pct']}% | "
                    f"MACD: {result['macd_signal']} | "
                    f"Factors: {result['factors']} | "
                    f"VIX regime: {macro.get('vix_regime')} | SPY: {macro.get('spy_trend')}"
                )},
            ],
            "stream": False,
            "think": False,
        }
        resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
        resp.raise_for_status()
        content = resp.json()["message"]["content"].strip()
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return content[:400]
    except Exception:
        return ""


# ── Score display ─────────────────────────────────────────────────────────────

def print_score(result: dict, highlight: bool = False) -> None:
    tag = "►" if highlight else " "
    factors = result["factors"]
    bar = "".join(
        "+" if factors.get(k, 0) > 0 else ("-" if factors.get(k, 0) < 0 else "·")
        for k in ("sma20", "sma50", "rsi", "macd", "ret_5d", "proximity")
    )
    signal_str = f"{'BUY':5}" if result["signal"] == "BUY" else f"{'WATCH':5}"
    print(
        f"  {tag} {result['ticker']:6} score={result['score']:+.0f}  "
        f"[{bar}]  RSI={result['rsi']:5.1f}  5d={result['ret_5d_pct']:+5.1f}%  "
        f"{signal_str}  (need {result['threshold']})"
    )


# ── Executor ──────────────────────────────────────────────────────────────────

def run_executor(ticker: str, current_price: float, dry_run: bool = False) -> dict:
    print("\n── Phase 3: Executor ─────────────────────────────────────")

    portfolio = get_portfolio_state()
    if portfolio.get("error"):
        print(f"  ✗ Portfolio error: {portfolio['error']}")
        return {"status": "FAILED", "reason": portfolio["error"]}

    open_positions = portfolio.get("open_positions", 0)
    cash = float(portfolio.get("cash", 0))
    portfolio_value = float(portfolio.get("portfolio_value", cash))
    existing_tickers = [p.get("ticker") or p.get("symbol") for p in portfolio.get("positions", [])]

    print(f"  Portfolio: ${cash:,.2f} cash | {open_positions} open positions | holdings: {existing_tickers or 'none'}")

    if open_positions >= 3:
        msg = f"Max positions reached ({open_positions}/3)"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    if ticker in existing_tickers:
        msg = f"Already holding {ticker}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    # 19% (not 20%) to leave headroom for bid/ask spread at submission
    invest_amount = portfolio_value * 0.19
    if cash < invest_amount:
        msg = f"Insufficient cash: need ${invest_amount:,.2f}, have ${cash:,.2f}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    qty = math.floor(invest_amount / current_price)
    if qty < 1:
        msg = f"Position too small: ${invest_amount:,.2f} insufficient for 1 share at ${current_price:.2f}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    print(f"  Submitting: BUY {qty} {ticker} @ ~${current_price:.2f}  (${qty * current_price:,.2f})")

    if dry_run:
        print("  [dry-run] order not submitted")
        return {"status": "DRY_RUN", "qty": qty, "ticker": ticker}

    order_result = place_order(ticker, "buy", qty)
    if order_result.get("error"):
        print(f"  ✗ FAILED: {order_result['error']}")
        return {"status": "FAILED", "reason": order_result["error"]}

    order_id = order_result.get("id") or order_result.get("order_id", "unknown")
    print(f"  ✓ SUBMITTED — order ID: {order_id}")
    return {"status": "SUBMITTED", "order_id": order_id, "qty": qty, "ticker": ticker}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    ticker_arg = next((a for a in args if not a.startswith("--")), None)
    if ticker_arg:
        ticker_arg = ticker_arg.upper()

    print("=" * 60)
    print("  LLM Day Trader — Signal Scoring Loop")
    print("=" * 60)

    # ── Phase 1: Macro conditions ─────────────────────────────────────────────
    print("\n── Phase 1: Market Conditions ────────────────────────────")
    macro = get_market_conditions()
    vix = macro.get("vix_proxy_price") or macro.get("vix_regime")
    print(f"  VIX: {macro.get('vix_proxy_price')} ({macro.get('vix_regime')})  |  SPY: {macro.get('spy_trend')}")
    sectors = macro.get("sector_trends", {})
    if sectors:
        print(f"  Sectors: " + "  ".join(f"{k}={v}" for k, v in sectors.items()))

    # ── Phase 2: Signal scoring ───────────────────────────────────────────────
    print("\n── Phase 2: Signal Scoring ───────────────────────────────")
    print(f"  {'Ticker':6}  {'Score':6}  {'Factors':8}  {'RSI':6}  {'5d ret':7}  {'Signal':6}  Threshold")
    print("  " + "─" * 57)

    if ticker_arg:
        # Single ticker mode
        result = compute_score(ticker_arg, macro)
        if "error" in result:
            print(f"  ✗ {ticker_arg}: {result['error']}")
            return
        print_score(result, highlight=True)
        top = result
    else:
        # Scan all — score every ticker, show top 10
        print("  Scanning 31 tickers...", end="", flush=True)
        ranked = scan_and_rank(macro)
        print(f" done.")
        print()
        for r in ranked[:10]:
            print_score(r, highlight=(r is ranked[0]))
        if len(ranked) > 10:
            print(f"  ... ({len(ranked) - 10} more not shown)")
        top = ranked[0] if ranked else None

    if not top:
        print("\n  No scoreable tickers found")
        return

    # ── Decision ──────────────────────────────────────────────────────────────
    print(f"\n── Decision ──────────────────────────────────────────────")
    print(f"  Best candidate: {top['ticker']}  score={top['score']:+.0f}  threshold={top['threshold']}  regime={top['regime']}")

    if top["signal"] != "BUY":
        gap = top["threshold"] - top["score"]
        print(f"  No trade — score is {gap:.0f} point(s) below threshold for {top['regime']} regime")
        print(f"  Weakest factors: " + ", ".join(
            k for k, v in top["factors"].items() if v < 0
        ))
        return

    print(f"  ✓ BUY signal — score {top['score']:+.0f} meets {top['regime']} threshold of {top['threshold']}")

    # Get fresh price from snapshot (score used close from 90d bars)
    snap = get_market_snapshot(top["ticker"])
    live_price = snap.get("price") or top["price"]

    # ── Phase 3: Execute ──────────────────────────────────────────────────────
    result = run_executor(top["ticker"], live_price, dry_run=dry_run)

    # ── Optional thesis (non-blocking) ───────────────────────────────────────
    if result["status"] in ("SUBMITTED", "DRY_RUN"):
        thesis = generate_thesis(top, macro)
        if thesis:
            print(f"\n  Thesis: {thesis}")

    print("\n" + "=" * 60)
    print(f"  {result['status']}", end="")
    if result["status"] in ("SUBMITTED", "DRY_RUN"):
        print(f" — {result['qty']} shares of {result['ticker']}", end="")
        if result["status"] == "SUBMITTED":
            print(f" | order: {result['order_id']}", end="")
    else:
        print(f" — {result.get('reason', '')}", end="")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
