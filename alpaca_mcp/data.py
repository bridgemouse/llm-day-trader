# alpaca_mcp/data.py
# Market data tools — all pre-processing happens here, LLM never sees raw OHLCV

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import pandas_ta as ta
from alpaca.data.historical import StockHistoricalDataClient, NewsClient
from alpaca.data.requests import StockBarsRequest, NewsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from dotenv import load_dotenv

load_dotenv()

_data_client: Optional[StockHistoricalDataClient] = None
_news_client: Optional[NewsClient] = None


def _get_client() -> StockHistoricalDataClient:
    global _data_client
    if _data_client is None:
        _data_client = StockHistoricalDataClient(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
        )
    return _data_client


def _get_news_client() -> NewsClient:
    global _news_client
    if _news_client is None:
        _news_client = NewsClient(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
        )
    return _news_client


def _fetch_bars(ticker: str, days: int = 60, timeframe: TimeFrame = TimeFrame.Day) -> pd.DataFrame:
    """Fetch OHLCV bars and return as a DataFrame with a DatetimeIndex."""
    client = _get_client()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    req = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    bars = client.get_stock_bars(req)
    df = bars.df

    # Multi-index when multiple symbols — drop symbol level if present
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")

    df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()
    return df


def _classify_trend(df: pd.DataFrame) -> str:
    """Simple trend classifier using SMA20 vs SMA50."""
    if len(df) < 50:
        return "insufficient_data"
    sma20 = df["close"].rolling(20).mean().iloc[-1]
    sma50 = df["close"].rolling(50).mean().iloc[-1]
    price = df["close"].iloc[-1]
    if price > sma20 > sma50:
        return "uptrend"
    if price < sma20 < sma50:
        return "downtrend"
    return "sideways"


def _macd_signal(df: pd.DataFrame) -> str:
    """Returns a human-readable MACD signal string."""
    macd = ta.macd(df["close"])
    if macd is None or macd.empty:
        return "unavailable"

    hist_col = [c for c in macd.columns if "h" in c.lower()][0]
    hist = macd[hist_col]

    recent = hist.dropna().iloc[-5:]
    if len(recent) < 2:
        return "unavailable"

    last = recent.iloc[-1]
    prev = recent.iloc[-2]

    if prev < 0 and last > 0:
        return "bullish_cross"
    if prev > 0 and last < 0:
        return "bearish_cross"
    if last > 0:
        # Check how many bars since last cross
        cross_bar = None
        for i in range(len(hist.dropna()) - 1, 0, -1):
            if hist.dropna().iloc[i - 1] < 0:
                cross_bar = len(hist.dropna()) - i
                break
        if cross_bar is not None:
            return f"bullish_cross_{cross_bar}d_ago"
        return "bullish"
    else:
        cross_bar = None
        for i in range(len(hist.dropna()) - 1, 0, -1):
            if hist.dropna().iloc[i - 1] > 0:
                cross_bar = len(hist.dropna()) - i
                break
        if cross_bar is not None:
            return f"bearish_cross_{cross_bar}d_ago"
        return "bearish"


def _support_resistance(df: pd.DataFrame, window: int = 20) -> tuple[float, float]:
    """Rough support/resistance from recent high/low."""
    recent = df.iloc[-window:]
    return round(recent["low"].min(), 2), round(recent["high"].max(), 2)


def _vol_ratio(df: pd.DataFrame, short: int = 5, long: int = 30) -> float:
    """Recent avg volume vs 30-day avg volume."""
    if len(df) < long:
        return 1.0
    short_avg = df["volume"].iloc[-short:].mean()
    long_avg = df["volume"].iloc[-long:].mean()
    if long_avg == 0:
        return 1.0
    return round(short_avg / long_avg, 2)


def _fetch_sentiment(ticker: str, days: int = 7) -> dict:
    """Fetch news and return a sentiment summary."""
    news_client = _get_news_client()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    req = NewsRequest(
        symbols=ticker,
        start=start,
        end=end,
        limit=50,
    )
    try:
        news = news_client.get_news(req)
    except Exception:
        return {"score": 0.0, "article_count": 0, "negative_themes": [], "summary": "unavailable"}

    articles = news if isinstance(news, list) else list(news)
    if not articles:
        return {"score": 0.0, "article_count": 0, "negative_themes": [], "summary": "no recent news"}

    # Alpaca news items have a 'sentiment' field or we derive from headline keywords
    scores = []
    negative_themes = []
    positive_themes = []

    negative_keywords = [
        "miss", "loss", "decline", "fall", "drop", "cut", "layoff", "lawsuit",
        "investigation", "recall", "downgrade", "tariff", "fine", "breach",
    ]
    positive_keywords = [
        "beat", "record", "growth", "surge", "rally", "upgrade", "profit",
        "acquisition", "partnership", "launch", "expansion", "dividend",
    ]

    for article in articles:
        headline = (getattr(article, "headline", "") or "").lower()
        score = 0.0
        for kw in negative_keywords:
            if kw in headline:
                score -= 0.3
                negative_themes.append(kw)
        for kw in positive_keywords:
            if kw in headline:
                score += 0.3
                positive_themes.append(kw)
        scores.append(max(-1.0, min(1.0, score)))

    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
    unique_neg = list(set(negative_themes))[:3]

    sign = "+" if avg_score >= 0 else ""
    summary = f"{sign}{avg_score} ({len(articles)} articles"
    if unique_neg:
        summary += f", {len(unique_neg)} negative: {', '.join(unique_neg)}"
    summary += ")"

    return {
        "score": avg_score,
        "article_count": len(articles),
        "negative_themes": unique_neg,
        "summary": summary,
    }


# ── Public tool functions ─────────────────────────────────────────────────────

def get_market_snapshot(ticker: str) -> dict:
    """
    Returns a pre-digested market snapshot for one ticker.
    LLM receives structured context, never raw OHLCV.
    """
    ticker = ticker.upper()
    df = _fetch_bars(ticker, days=90)

    if df.empty:
        return {"error": f"No data returned for {ticker}"}

    close = df["close"].iloc[-1]
    rsi_series = ta.rsi(df["close"], length=14)
    rsi = round(rsi_series.dropna().iloc[-1], 1) if rsi_series is not None else None

    trend = _classify_trend(df)
    macd = _macd_signal(df)
    support, resistance = _support_resistance(df)
    vol_ratio = _vol_ratio(df)
    sentiment = _fetch_sentiment(ticker)

    return {
        "ticker": ticker,
        "price": round(close, 2),
        "trend": trend,
        "rsi_14": rsi,
        "macd_signal": macd,
        "support": support,
        "resistance": resistance,
        "sentiment": sentiment["summary"],
        "vol_vs_30d_avg": vol_ratio,
    }


def get_indicators(ticker: str, indicators: list[str]) -> dict:
    """
    Compute specific indicators for a ticker.
    indicators: list of names like ["rsi_14", "sma_20", "sma_50", "ema_9", "macd", "bbands"]
    Returns named scalar values or signals — no raw arrays.
    """
    ticker = ticker.upper()
    df = _fetch_bars(ticker, days=120)

    if df.empty:
        return {"error": f"No data for {ticker}"}

    result = {"ticker": ticker}

    for ind in indicators:
        ind_lower = ind.lower()

        if ind_lower.startswith("rsi"):
            length = int(ind_lower.split("_")[1]) if "_" in ind_lower else 14
            series = ta.rsi(df["close"], length=length)
            result[ind] = round(series.dropna().iloc[-1], 1) if series is not None else None

        elif ind_lower.startswith("sma"):
            length = int(ind_lower.split("_")[1]) if "_" in ind_lower else 20
            series = df["close"].rolling(length).mean()
            result[ind] = round(series.dropna().iloc[-1], 2)

        elif ind_lower.startswith("ema"):
            length = int(ind_lower.split("_")[1]) if "_" in ind_lower else 9
            series = ta.ema(df["close"], length=length)
            result[ind] = round(series.dropna().iloc[-1], 2) if series is not None else None

        elif ind_lower == "macd":
            result["macd_signal"] = _macd_signal(df)

        elif ind_lower in ("bbands", "bollinger"):
            bb = ta.bbands(df["close"], length=20)
            if bb is not None and not bb.empty:
                upper_col = [c for c in bb.columns if "U" in c][0]
                lower_col = [c for c in bb.columns if "L" in c][0]
                mid_col = [c for c in bb.columns if "M" in c][0]
                result["bb_upper"] = round(bb[upper_col].iloc[-1], 2)
                result["bb_mid"] = round(bb[mid_col].iloc[-1], 2)
                result["bb_lower"] = round(bb[lower_col].iloc[-1], 2)
                price = df["close"].iloc[-1]
                result["bb_position"] = (
                    "above_upper" if price > bb[upper_col].iloc[-1]
                    else "below_lower" if price < bb[lower_col].iloc[-1]
                    else "mid_to_upper" if price > bb[mid_col].iloc[-1]
                    else "mid_to_lower"
                )

        elif ind_lower == "atr":
            series = ta.atr(df["high"], df["low"], df["close"], length=14)
            result["atr_14"] = round(series.dropna().iloc[-1], 2) if series is not None else None

        elif ind_lower == "volume_ratio":
            result["vol_vs_30d_avg"] = _vol_ratio(df)

        else:
            result[ind] = "unsupported_indicator"

    return result


def get_news_sentiment(ticker: str, days: int = 7) -> dict:
    """
    Fetch and score recent news sentiment for a ticker.
    Returns a summary — not raw article text.
    """
    ticker = ticker.upper()
    sentiment = _fetch_sentiment(ticker, days=days)
    return {"ticker": ticker, "days": days, **sentiment}


def get_market_conditions() -> dict:
    """
    Broad market context: SPY trend, VIX level, sector signals.
    Used to give the LLM macro context before strategy decisions.
    """
    spy_df = _fetch_bars("SPY", days=60)
    spy_trend = _classify_trend(spy_df) if not spy_df.empty else "unavailable"
    spy_rsi = None
    if not spy_df.empty:
        rsi_series = ta.rsi(spy_df["close"], length=14)
        if rsi_series is not None:
            spy_rsi = round(rsi_series.dropna().iloc[-1], 1)

    # VIX via Alpaca (symbol: VIX — available on market data API)
    vix_level = None
    vix_regime = "unavailable"
    try:
        vix_df = _fetch_bars("VIXY", days=10)  # VIXY is VIX proxy ETF
        if not vix_df.empty:
            vix_level = round(vix_df["close"].iloc[-1], 2)
            vix_regime = (
                "low_vol" if vix_level < 15
                else "normal" if vix_level < 20
                else "elevated" if vix_level < 30
                else "high_fear"
            )
    except Exception:
        pass

    # Simple sector check via key ETFs
    sectors = {}
    for etf, name in [("XLK", "tech"), ("XLF", "financials"), ("XLE", "energy"), ("XLV", "healthcare")]:
        try:
            df = _fetch_bars(etf, days=30)
            sectors[name] = _classify_trend(df) if not df.empty else "unavailable"
        except Exception:
            sectors[name] = "unavailable"

    return {
        "spy_trend": spy_trend,
        "spy_rsi_14": spy_rsi,
        "vix_proxy_price": vix_level,
        "vix_regime": vix_regime,
        "sector_trends": sectors,
    }
