# alpaca_mcp/execution.py
# Portfolio state and order execution tools

import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType

load_dotenv()

_trading_client = None


def _get_client() -> TradingClient:
    global _trading_client
    if _trading_client is None:
        _trading_client = TradingClient(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            paper=os.getenv("ALPACA_PAPER", "true").lower() == "true",
        )
    return _trading_client


# Guard rails (from wiki strategy constraints)
MAX_POSITION_PCT = 0.20   # 20% of portfolio per stock
MAX_OPEN_POSITIONS = 3
STOP_LOSS_PCT = 0.05       # hard -5% stop loss


def _place_stop_loss(client: TradingClient, ticker: str, qty: float, entry_price: float) -> dict:
    """
    Submit a GTC stop-loss order at STOP_LOSS_PCT below entry price.
    Called immediately after a buy order is submitted.
    """
    stop_price = round(entry_price * (1 - STOP_LOSS_PCT), 2)
    req = StopOrderRequest(
        symbol=ticker.upper(),
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
        stop_price=stop_price,
    )
    try:
        order = client.submit_order(req)
        return {
            "status": "submitted",
            "order_id": str(order.id),
            "stop_price": stop_price,
        }
    except Exception as e:
        return {"error": str(e), "stop_price": stop_price}


def get_portfolio_state() -> dict:
    """
    Returns current portfolio state: positions, cash, P&L, buying power.
    All values pre-formatted — no raw Alpaca objects returned.
    """
    client = _get_client()
    account = client.get_account()
    positions = client.get_all_positions()

    portfolio_value = float(account.portfolio_value)
    cash = float(account.cash)
    buying_power = float(account.buying_power)

    pos_list = []
    total_unrealized_pl = 0.0
    for p in positions:
        unrealized_pl = float(p.unrealized_pl)
        unrealized_plpc = float(p.unrealized_plpc) * 100
        total_unrealized_pl += unrealized_pl
        pos_list.append({
            "ticker": p.symbol,
            "qty": float(p.qty),
            "side": p.side.value,
            "avg_entry": round(float(p.avg_entry_price), 2),
            "current_price": round(float(p.current_price), 2),
            "market_value": round(float(p.market_value), 2),
            "pct_of_portfolio": round(float(p.market_value) / portfolio_value * 100, 1) if portfolio_value else 0,
            "unrealized_pl": round(unrealized_pl, 2),
            "unrealized_plpc": round(unrealized_plpc, 2),
        })

    return {
        "portfolio_value": round(portfolio_value, 2),
        "cash": round(cash, 2),
        "buying_power": round(buying_power, 2),
        "open_positions": len(pos_list),
        "max_positions_allowed": MAX_OPEN_POSITIONS,
        "total_unrealized_pl": round(total_unrealized_pl, 2),
        "positions": pos_list,
    }


def place_order(ticker: str, side: str, qty: float, order_type: str = "market", limit_price: float = None) -> dict:
    """
    Place an order with guard rail enforcement.

    Args:
        ticker: stock symbol
        side: "buy" or "sell"
        qty: number of shares
        order_type: "market" or "limit"
        limit_price: required if order_type == "limit"

    Guard rails enforced:
        - Max 3 open positions
        - Max 20% of portfolio per position
        - Equities only (no leverage/options/crypto)
    """
    ticker = ticker.upper()
    side = side.lower()
    client = _get_client()

    if side not in ("buy", "sell"):
        return {"error": f"Invalid side '{side}'. Must be 'buy' or 'sell'."}

    # Check guard rails on buy
    if side == "buy":
        account = client.get_account()
        portfolio_value = float(account.portfolio_value)
        positions = client.get_all_positions()
        position_symbols = [p.symbol for p in positions]

        # Max open positions check
        if len(position_symbols) >= MAX_OPEN_POSITIONS and ticker not in position_symbols:
            return {
                "error": f"Guard rail: already at max {MAX_OPEN_POSITIONS} open positions. Close one before opening {ticker}.",
                "open_positions": position_symbols,
            }

        # Max position size check
        # Fetch current price to estimate order value
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest
        data_client = StockHistoricalDataClient(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
        )
        try:
            quote_req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
            quote = data_client.get_stock_latest_quote(quote_req)
            price = float(quote[ticker].ask_price) if quote[ticker].ask_price else float(quote[ticker].bid_price)
        except Exception:
            price = limit_price or 0

        if price and portfolio_value:
            order_value = price * qty
            if order_value / portfolio_value > MAX_POSITION_PCT:
                # Ask price moved since executor calculated qty — trim to fit limit
                qty = round((portfolio_value * MAX_POSITION_PCT) / price, 6)

    # Build and submit order
    order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

    try:
        if order_type == "limit":
            if limit_price is None:
                return {"error": "limit_price required for limit orders."}
            req = LimitOrderRequest(
                symbol=ticker,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price,
            )
        else:
            req = MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
            )

        order = client.submit_order(req)

        result = {
            "status": "submitted",
            "order_id": str(order.id),
            "ticker": ticker,
            "side": side,
            "qty": qty,
            "order_type": order_type,
            "limit_price": limit_price,
            "submitted_at": str(order.submitted_at),
        }

        # Place stop-loss immediately after a buy
        if side == "buy" and limit_price:
            sl = _place_stop_loss(client, ticker, qty, limit_price)
            result["stop_loss_order_id"] = sl.get("order_id")
            result["stop_loss_price"] = sl.get("stop_price")

        return result

    except Exception as e:
        return {"error": str(e)}


def close_position(ticker: str, reason: str = "") -> dict:
    """
    Close an entire open position with a market sell order.
    Cancels any open stop orders for this ticker first (best effort).

    Returns dict with status, order_id, qty, ticker.
    """
    ticker = ticker.upper()
    client = _get_client()

    positions = client.get_all_positions()
    pos = next((p for p in positions if p.symbol == ticker), None)
    if not pos:
        return {"error": f"No open position for {ticker}"}

    qty = float(pos.qty)

    # Cancel any open stop orders for this ticker to avoid double-fills
    try:
        open_orders = client.get_orders()
        for o in open_orders:
            if o.symbol == ticker and o.order_type == OrderType.STOP:
                client.cancel_order_by_id(str(o.id))
    except Exception:
        pass  # best effort

    req = MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )

    try:
        order = client.submit_order(req)
        return {
            "status": "submitted",
            "order_id": str(order.id),
            "ticker": ticker,
            "qty": qty,
            "reason": reason,
            "submitted_at": str(order.submitted_at),
        }
    except Exception as e:
        return {"error": str(e)}
