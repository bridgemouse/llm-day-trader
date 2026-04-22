# alpaca_mcp/signals.py
# Deterministic signal scoring engine — no LLM involved.
#
# Scoring: 6 factors, each ±1 (or 0 for neutral), range -6 to +6.
# Buy threshold adjusts with macro regime:
#   bull  (spy uptrend   + low_vol/normal VIX)  → threshold 3
#   mixed (everything else)                      → threshold 4
#   bear  (spy downtrend + elevated/high_fear)   → threshold 5

import pandas as pd
import pandas_ta as ta

from alpaca_mcp.data import _fetch_bars, _macd_signal, _support_resistance


def _sma(series: pd.Series, length: int) -> float | None:
    result = series.rolling(length).mean().dropna()
    return float(result.iloc[-1]) if not result.empty else None


def compute_score(ticker: str, macro: dict) -> dict:
    """
    Score a single ticker deterministically.

    Returns a dict with:
      score        float   -6 to +6
      signal       str     BUY | WATCH
      threshold    int     regime-adjusted entry bar
      regime       str     bull | mixed | bear
      factors      dict    per-factor contribution (+1 / 0 / -1)
      price        float
      rsi          float
      ret_5d_pct   float
    """
    ticker = ticker.upper()
    df = _fetch_bars(ticker, days=90)

    if df.empty or len(df) < 22:
        return {"ticker": ticker, "error": "insufficient data", "signal": "WATCH", "score": 0}

    close = df["close"].iloc[-1]
    rsi_series = ta.rsi(df["close"], length=14)
    rsi = float(rsi_series.dropna().iloc[-1]) if rsi_series is not None and not rsi_series.dropna().empty else 50.0

    sma20 = _sma(df["close"], 20)
    sma50 = _sma(df["close"], 50)
    macd_sig = _macd_signal(df)
    support, resistance = _support_resistance(df)

    ret_5d = (close - df["close"].iloc[-6]) / df["close"].iloc[-6] if len(df) >= 6 else 0.0

    score = 0.0
    factors = {}

    # ── Factor 1: Price vs SMA(20) ────────────────────────────────────────────
    if sma20 is not None:
        if close > sma20:
            score += 1; factors["sma20"] = +1
        else:
            score -= 1; factors["sma20"] = -1
    else:
        factors["sma20"] = 0

    # ── Factor 2: Price vs SMA(50) ────────────────────────────────────────────
    if sma50 is not None:
        if close > sma50:
            score += 1; factors["sma50"] = +1
        else:
            score -= 1; factors["sma50"] = -1
    else:
        factors["sma50"] = 0

    # ── Factor 3: RSI — momentum zone ────────────────────────────────────────
    # 45–65 = healthy trend (bullish); <35 or >75 = extreme (caution)
    if 45 <= rsi <= 65:
        score += 1; factors["rsi"] = +1
    elif rsi < 35 or rsi > 75:
        score -= 1; factors["rsi"] = -1
    else:
        factors["rsi"] = 0

    # ── Factor 4: MACD direction ──────────────────────────────────────────────
    if "bullish" in macd_sig:
        score += 1; factors["macd"] = +1
    elif "bearish" in macd_sig:
        score -= 1; factors["macd"] = -1
    else:
        factors["macd"] = 0

    # ── Factor 5: 5-day price return ──────────────────────────────────────────
    if ret_5d > 0.02:
        score += 1; factors["ret_5d"] = +1
    elif ret_5d < -0.02:
        score -= 1; factors["ret_5d"] = -1
    else:
        factors["ret_5d"] = 0

    # ── Factor 6: Support / resistance proximity ──────────────────────────────
    pct_above_support = (close - support) / support if support else 1.0
    pct_below_resistance = (resistance - close) / close if resistance else 1.0
    if pct_above_support <= 0.03:          # within 3% above support → potential bounce
        score += 1; factors["proximity"] = +1
    elif pct_below_resistance <= 0.02:     # within 2% of resistance → potential ceiling
        score -= 1; factors["proximity"] = -1
    else:
        factors["proximity"] = 0

    # ── Macro regime → entry threshold ───────────────────────────────────────
    spy_trend = macro.get("spy_trend", "sideways")
    vix_regime = macro.get("vix_regime", "normal")

    if spy_trend == "uptrend" and vix_regime in ("low_vol", "normal"):
        threshold, regime = 3, "bull"
    elif spy_trend == "downtrend" and vix_regime in ("elevated", "high_fear"):
        threshold, regime = 5, "bear"
    else:
        threshold, regime = 4, "mixed"

    signal = "BUY" if score >= threshold else "WATCH"

    return {
        "ticker": ticker,
        "score": round(score, 1),
        "threshold": threshold,
        "regime": regime,
        "signal": signal,
        "factors": factors,
        "price": round(close, 2),
        "rsi": round(rsi, 1),
        "ret_5d_pct": round(ret_5d * 100, 2),
        "macd_signal": macd_sig,
        "support": support,
        "resistance": resistance,
    }


WHITELIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    "JPM", "BAC", "GS",
    "JNJ", "PFE", "ABBV", "MRK", "UNH",
    "XOM", "CVX",
    "WMT", "PG", "KO",
    "HD", "LOW", "DIS", "NFLX",
    "AMD", "INTC", "IBM", "ORCL", "CRM",
    "V", "MA",
]


def scan_and_rank(macro: dict, tickers: list[str] | None = None) -> list[dict]:
    """
    Score every ticker in the whitelist and return them sorted best→worst.
    Skips tickers with errors or insufficient data.
    """
    candidates = tickers or WHITELIST
    results = []
    for ticker in candidates:
        result = compute_score(ticker, macro)
        if "error" not in result:
            results.append(result)
    results.sort(key=lambda r: r["score"], reverse=True)
    return results
