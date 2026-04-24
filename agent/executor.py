# agent/executor.py
# Buy guard rails and wiki fallback. Extracted from agent_loop.py.

import math

from alpaca_mcp.data import get_market_conditions, get_market_snapshot
from alpaca_mcp.execution import get_portfolio_state, place_order
from alpaca_mcp.signals import compute_score
from alpaca_mcp.wiki import append_trade_log, update_ticker_page


def run_executor(ticker: str, dry_run: bool = False) -> dict:
    """
    Apply portfolio guard rails and submit a buy order.

    Returns:
        {"status": "SUBMITTED"|"DRY_RUN"|"BLOCKED"|"FAILED", ...}
    """
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

    if ticker.upper() in existing:
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

    order_result = place_order(ticker, "buy", qty, limit_price=live_price)
    if order_result.get("error"):
        print(f"  ✗ FAILED: {order_result['error']}")
        return {"status": "FAILED", "reason": order_result["error"]}

    order_id = order_result.get("order_id", "unknown")
    stop_price = order_result.get("stop_loss_price")
    print(f"  ✓ SUBMITTED — order: {order_id}")
    if stop_price:
        print(f"  ✓ STOP-LOSS placed at ${stop_price:.2f}  (-5% floor)")

    return {
        "status": "SUBMITTED",
        "order_id": order_id,
        "qty": qty,
        "ticker": ticker,
        "price": live_price,
        "stop_loss_price": stop_price,
    }


def wiki_fallback(result: dict) -> None:
    """
    If the agent forgot to write wiki, auto-record the decision.
    Called from agent_loop.py when _wiki_written is False.
    """
    ticker = result.get("ticker")
    if not ticker:
        return
    snap = get_market_snapshot(ticker)
    price = snap.get("price", 0.0)
    macro = get_market_conditions()
    scored = compute_score(ticker, macro)
    print("  [wiki fallback] agent skipped wiki write — recording automatically")
    append_trade_log(
        ticker=ticker,
        decision=result["decision"],
        score=scored.get("score", 0),
        regime=scored.get("regime", "unknown"),
        price=price,
        qty=0,
        rationale=result.get("rationale", ""),
        biggest_risk=result.get("risk", ""),
        agent_note="(auto-recorded — agent did not write wiki)",
    )
    update_ticker_page(
        ticker=ticker,
        decision=result["decision"],
        score=scored.get("score", 0),
        price=price,
        observation="(auto-recorded — agent did not write observation)",
    )
