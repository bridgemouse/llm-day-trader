# alpaca_mcp/server.py
# MCP server entry point — exposes all tools to OpenClaw via stdio transport

import json
from mcp.server.fastmcp import FastMCP

from alpaca_mcp.data import (
    get_market_snapshot,
    get_indicators,
    get_news_sentiment,
    get_market_conditions,
)
from alpaca_mcp.execution import get_portfolio_state, place_order
from alpaca_mcp.backtester import backtest_strategy

mcp = FastMCP("alpaca-trader")


# ── Market Data Tools ─────────────────────────────────────────────────────────

@mcp.tool()
def market_snapshot(ticker: str) -> str:
    """
    Get a pre-digested market snapshot for a ticker.
    Returns trend, RSI, MACD signal, support/resistance, sentiment, and volume ratio.
    Use this before generating any strategy hypothesis for a stock.
    """
    result = get_market_snapshot(ticker)
    return json.dumps(result, indent=2)


@mcp.tool()
def indicators(ticker: str, indicator_names: list[str]) -> str:
    """
    Compute specific technical indicators for a ticker.
    Supported: rsi_14, sma_20, sma_50, ema_9, ema_21, macd, bbands, atr, volume_ratio.
    Returns scalar values or signals — not raw arrays.
    Example: indicators("AAPL", ["rsi_14", "sma_20", "sma_50", "macd"])
    """
    result = get_indicators(ticker, indicator_names)
    return json.dumps(result, indent=2)


@mcp.tool()
def news_sentiment(ticker: str, days: int = 7) -> str:
    """
    Fetch and score recent news sentiment for a ticker.
    Returns a scored summary, article count, and key negative themes.
    Does NOT return raw article text.
    """
    result = get_news_sentiment(ticker, days)
    return json.dumps(result, indent=2)


@mcp.tool()
def market_conditions() -> str:
    """
    Get broad market context: SPY trend + RSI, VIX regime, sector ETF trends.
    Call this at the start of a strategy session to understand macro environment.
    """
    result = get_market_conditions()
    return json.dumps(result, indent=2)


# ── Backtesting Tool ──────────────────────────────────────────────────────────

@mcp.tool()
def backtest(rules: dict, ticker: str, period: str = "6mo") -> str:
    """
    Run a rule-based backtest against Alpaca historical data.

    rules format:
    {
      "entry": [
        {"indicator": "rsi_14", "operator": "<", "value": 35},
        {"indicator": "trend",  "operator": "==", "value": "uptrend"}
      ],
      "exit": [
        {"indicator": "rsi_14", "operator": ">", "value": 65},
        {"type": "stop_loss",   "pct": 0.05},
        {"type": "take_profit", "pct": 0.15}
      ],
      "position_size_pct": 0.20
    }

    period options: "1mo", "3mo", "6mo", "1y", "2y"

    Returns: total_return, sharpe, sortino, max_drawdown, win_rate, trade_log.
    Compare total_return_pct vs buy_and_hold_return_pct to assess alpha.
    """
    result = backtest_strategy(rules, ticker, period)
    return json.dumps(result, indent=2)


# ── Execution Tools ───────────────────────────────────────────────────────────

@mcp.tool()
def portfolio_state() -> str:
    """
    Get current portfolio state: open positions, cash, buying power, P&L.
    Check this before placing any order to verify position limits.
    Guard rails: max 3 open positions, max 20% per stock.
    """
    result = get_portfolio_state()
    return json.dumps(result, indent=2)


@mcp.tool()
def submit_order(ticker: str, side: str, qty: float, order_type: str = "market", limit_price: float = None) -> str:
    """
    Place a buy or sell order. Guard rails are enforced server-side.

    Args:
        ticker: stock symbol (e.g. "AAPL")
        side: "buy" or "sell"
        qty: number of shares
        order_type: "market" (default) or "limit"
        limit_price: required only if order_type == "limit"

    Guard rails enforced:
        - Max 3 open positions (won't open a 4th)
        - Max 20% of portfolio value per position
        - Paper trading only (Alpaca paper=True)

    Always call portfolio_state() first to check available cash and open positions.
    """
    result = place_order(ticker, side, qty, order_type, limit_price)
    return json.dumps(result, indent=2)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
