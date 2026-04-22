# alpaca_mcp/backtester.py
# Rule-based backtesting engine
#
# Rule schema (JSON):
# {
#   "entry": [
#     {"indicator": "rsi_14", "operator": "<", "value": 35},
#     {"indicator": "trend",  "operator": "==", "value": "uptrend"}
#   ],
#   "exit": [
#     {"indicator": "rsi_14", "operator": ">", "value": 65},
#     {"type": "stop_loss",   "pct": 0.05},
#     {"type": "take_profit", "pct": 0.15}
#   ],
#   "position_size_pct": 0.20   # fraction of portfolio per trade
# }
#
# Supported indicators: rsi_14, sma_20, sma_50, ema_9, ema_21, macd_hist,
#                       trend (uptrend/downtrend/sideways), close, volume

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import pandas_ta as ta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from dotenv import load_dotenv

load_dotenv()

_data_client: Optional[StockHistoricalDataClient] = None


def _get_client() -> StockHistoricalDataClient:
    global _data_client
    if _data_client is None:
        _data_client = StockHistoricalDataClient(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
        )
    return _data_client


def _fetch_bars(ticker: str, days: int) -> pd.DataFrame:
    client = _get_client()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    req = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    bars = client.get_stock_bars(req)
    df = bars.df
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def _build_indicator_df(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all supported indicators and attach as columns."""
    out = df.copy()

    # RSI
    rsi = ta.rsi(out["close"], length=14)
    if rsi is not None:
        out["rsi_14"] = rsi

    # SMAs
    out["sma_20"] = out["close"].rolling(20).mean()
    out["sma_50"] = out["close"].rolling(50).mean()

    # EMAs
    ema9 = ta.ema(out["close"], length=9)
    ema21 = ta.ema(out["close"], length=21)
    if ema9 is not None:
        out["ema_9"] = ema9
    if ema21 is not None:
        out["ema_21"] = ema21

    # MACD histogram
    macd = ta.macd(out["close"])
    if macd is not None and not macd.empty:
        hist_col = [c for c in macd.columns if "h" in c.lower()][0]
        out["macd_hist"] = macd[hist_col]
        out["macd"] = out["macd_hist"]           # alias: macd
        out["macd_histogram"] = out["macd_hist"]  # alias: macd_histogram

    # Volume ratio (current volume vs 20-day average)
    vol_avg = out["volume"].rolling(20).mean()
    out["volume_ratio"] = out["volume"] / vol_avg

    # Trend (categorical — use numeric encoding for rule eval)
    def classify(row_idx):
        if row_idx < 50:
            return "sideways"
        sma20 = out["sma_20"].iloc[row_idx]
        sma50 = out["sma_50"].iloc[row_idx]
        price = out["close"].iloc[row_idx]
        if pd.isna(sma20) or pd.isna(sma50):
            return "sideways"
        if price > sma20 > sma50:
            return "uptrend"
        if price < sma20 < sma50:
            return "downtrend"
        return "sideways"

    out["trend"] = [classify(i) for i in range(len(out))]

    return out


def _eval_condition(row: pd.Series, cond: dict) -> bool:
    """Evaluate a single entry/exit condition against a bar's indicator row."""
    ind = cond.get("indicator")
    op = cond.get("operator")
    val = cond.get("value")

    if ind not in row.index or pd.isna(row[ind]):
        return False

    actual = row[ind]

    if val is None:
        return False

    # String equality (e.g. trend == "uptrend")
    if isinstance(val, str):
        return actual == val if op == "==" else actual != val

    # Numeric comparisons
    if op == "<":
        return actual < val
    if op == "<=":
        return actual <= val
    if op == ">":
        return actual > val
    if op == ">=":
        return actual >= val
    if op == "==":
        return actual == val

    return False


def _eval_rule_set(row: pd.Series, conditions: list[dict]) -> bool:
    """All conditions must be True (AND logic)."""
    return all(_eval_condition(row, c) for c in conditions)


def _sharpe(returns: pd.Series, risk_free: float = 0.0) -> float:
    if returns.std() == 0:
        return 0.0
    daily_rf = risk_free / 252
    excess = returns - daily_rf
    return round(float(excess.mean() / excess.std() * (252 ** 0.5)), 3)


def _sortino(returns: pd.Series, risk_free: float = 0.0) -> float:
    daily_rf = risk_free / 252
    excess = returns - daily_rf
    downside = excess[excess < 0]
    if downside.empty or downside.std() == 0:
        return 0.0
    return round(float(excess.mean() / downside.std() * (252 ** 0.5)), 3)


def _max_drawdown(equity_curve: pd.Series) -> float:
    roll_max = equity_curve.cummax()
    drawdown = (equity_curve - roll_max) / roll_max
    return round(float(drawdown.min()) * 100, 2)


def backtest_strategy(rules: dict, ticker: str, period: str = "6mo") -> dict:
    """
    Run a rule-based backtest against Alpaca historical data.

    Args:
        rules: dict with "entry" conditions, "exit" conditions, optional "position_size_pct"
        ticker: stock symbol
        period: lookback period — "1mo", "3mo", "6mo", "1y", "2y"

    Returns:
        Performance summary: total_return, sharpe, sortino, max_drawdown_pct,
        win_rate, trade_count, trade_log
    """
    ticker = ticker.upper()

    period_days = {"1mo": 35, "3mo": 95, "6mo": 185, "1y": 370, "2y": 740}
    days = period_days.get(period, 185)

    df = _fetch_bars(ticker, days=days)
    if df.empty or len(df) < 52:
        return {"error": f"Insufficient data for {ticker} over {period}"}

    df = _build_indicator_df(df)

    entry_rules = rules.get("entry", [])
    exit_rules = rules.get("exit", [])
    position_size_pct = rules.get("position_size_pct", 0.20)
    stop_loss_pct = next((c["pct"] for c in exit_rules if c.get("type") == "stop_loss"), 0.05)
    take_profit_pct = next((c["pct"] for c in exit_rules if c.get("type") == "take_profit"), None)

    # Strip special exit types — evaluate indicator-based exits separately
    indicator_exits = [c for c in exit_rules if "indicator" in c]

    # Simulate — cash account, T+2 settlement (simplified: no re-entry until settled)
    starting_capital = 100.0
    capital = starting_capital
    in_position = False
    entry_price = 0.0
    entry_date = None
    shares = 0.0
    settlement_until = None

    equity_curve = []
    trade_log = []
    daily_returns = []

    prev_equity = capital

    for i, (date, row) in enumerate(df.iterrows()):
        price = row["close"]

        # Settle T+2 — skip entry if recently sold
        can_buy = settlement_until is None or date > settlement_until

        if in_position:
            current_value = capital - (shares * entry_price) + (shares * price)
            equity_curve.append(current_value)

            # Check stop loss
            pnl_pct = (price - entry_price) / entry_price
            hit_stop = pnl_pct <= -stop_loss_pct
            hit_tp = take_profit_pct is not None and pnl_pct >= take_profit_pct
            hit_indicator_exit = _eval_rule_set(row, indicator_exits) if indicator_exits else False

            if hit_stop or hit_tp or hit_indicator_exit:
                # Exit
                proceeds = shares * price
                capital = (capital - shares * entry_price) + proceeds
                reason = "stop_loss" if hit_stop else "take_profit" if hit_tp else "indicator_exit"
                trade_log.append({
                    "entry_date": str(entry_date.date()),
                    "exit_date": str(date.date()),
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(price, 2),
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "reason": reason,
                })
                in_position = False
                settlement_until = date + timedelta(days=2)

        else:
            equity_curve.append(capital)

            # Check entry
            if can_buy and _eval_rule_set(row, entry_rules):
                invest = capital * position_size_pct
                shares = invest / price
                entry_price = price
                entry_date = date
                in_position = True

        daily_ret = (equity_curve[-1] - prev_equity) / prev_equity if prev_equity else 0.0
        daily_returns.append(daily_ret)
        prev_equity = equity_curve[-1]

    # Close any open position at last price
    if in_position:
        last_price = df["close"].iloc[-1]
        last_date = df.index[-1]
        pnl_pct = (last_price - entry_price) / entry_price
        proceeds = shares * last_price
        final_capital = (capital - shares * entry_price) + proceeds
        trade_log.append({
            "entry_date": str(entry_date.date()),
            "exit_date": str(last_date.date()),
            "entry_price": round(entry_price, 2),
            "exit_price": round(last_price, 2),
            "pnl_pct": round(pnl_pct * 100, 2),
            "reason": "end_of_period",
        })
    else:
        final_capital = equity_curve[-1] if equity_curve else capital

    # Metrics
    total_return_pct = round((final_capital - starting_capital) / starting_capital * 100, 2)

    returns_series = pd.Series(daily_returns)
    sharpe = _sharpe(returns_series)
    sortino = _sortino(returns_series)
    eq_series = pd.Series(equity_curve)
    mdd = _max_drawdown(eq_series) if len(eq_series) > 1 else 0.0

    wins = [t for t in trade_log if t["pnl_pct"] > 0]
    win_rate = round(len(wins) / len(trade_log) * 100, 1) if trade_log else 0.0

    # Buy-and-hold benchmark
    bah_return = round((df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100, 2)

    return {
        "ticker": ticker,
        "period": period,
        "total_return_pct": total_return_pct,
        "buy_and_hold_return_pct": bah_return,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown_pct": mdd,
        "trade_count": len(trade_log),
        "win_rate_pct": win_rate,
        "final_capital": round(final_capital, 2),
        "trade_log": trade_log,
    }
