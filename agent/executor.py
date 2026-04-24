# agent/executor.py
# Buy guard rails and wiki fallback. Extracted from agent_loop.py.

import re

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
    if not re.match(r"^[A-Z]{1,5}$", ticker.upper().strip()):
        print(f"  ✗ BLOCKED: '{ticker}' is not a valid ticker symbol")
        return {"status": "BLOCKED", "reason": f"invalid ticker: {ticker}"}

    ticker = ticker.upper().strip()
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

    if open_positions >= 10:
        msg = f"Max positions reached ({open_positions}/10)"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    if ticker.upper() in [s.upper() for s in existing if s]:
        msg = f"Already holding {ticker}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    invest_amount = portfolio_value * 0.08
    if cash < invest_amount:
        msg = f"Insufficient cash: need ${invest_amount:,.2f}, have ${cash:,.2f}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    if invest_amount < 1.00:
        msg = f"Invest amount ${invest_amount:.2f} below $1.00 minimum"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    qty = round(invest_amount / live_price, 6)

    qty_display = f"{qty:.6f}".rstrip("0").rstrip(".") if qty < 1 else f"{qty:g}"
    print(f"  Submitting: BUY {qty_display} {ticker} @ ~${live_price:.2f}  (${qty * live_price:,.2f})")

    if dry_run:
        print("  ~ dry-run — order not submitted")
        return {"status": "DRY_RUN", "qty": qty, "ticker": ticker, "price": live_price}

    order_result = place_order(ticker, "buy", qty, limit_price=live_price)
    if not order_result or order_result.get("error"):
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
    print("  ~ wiki fallback — agent skipped log write, recording automatically")
    ticker = result.get("ticker") or "NONE"
    decision = result.get("decision", "STAND_ASIDE")

    price = 0.0
    score = 0
    regime = "unknown"

    if ticker != "NONE":
        try:
            snap = get_market_snapshot(ticker)
            price = snap.get("price", 0.0)
        except Exception:
            pass
        try:
            macro = get_market_conditions()
            scored = compute_score(ticker, macro)
            score = scored.get("score", 0)
            regime = scored.get("regime", "unknown")
        except Exception:
            pass

    append_trade_log(
        ticker=ticker,
        decision=decision,
        score=score,
        regime=regime,
        price=price,
        qty=0,
        rationale=result.get("rationale", ""),
        biggest_risk=result.get("risk", ""),
        agent_note="(auto-recorded — agent did not write wiki)",
    )
    if ticker != "NONE":
        update_ticker_page(
            ticker=ticker,
            decision=decision,
            score=score,
            price=price,
            observation="(auto-recorded — agent did not write observation)",
        )
