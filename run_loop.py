#!/usr/bin/env python3
"""
Trading loop orchestrator.

Usage:
    python run_loop.py          # scanner picks ticker
    python run_loop.py AAPL     # use specific ticker

Pipeline:
    Scanner  -> MarketBrief
    Strategist -> StrategySpec (up to 4 backtest iterations)
    Executor -> OrderResult (only if spec passes)
"""

import json
import math
import re
import sys
from datetime import date
from itertools import combinations

import requests
from dotenv import load_dotenv

from alpaca_mcp.data import (
    get_market_conditions,
    get_market_snapshot,
    get_news_sentiment,
)
from alpaca_mcp.backtester import backtest_strategy
from alpaca_mcp.execution import get_portfolio_state, place_order

load_dotenv()

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:4b"

ACCEPTANCE_CRITERIA = {
    "sortino_ratio": (">", 0.3),
    "total_return_pct": "beats_bah",
    "trade_count": (">=", 5),
    "max_drawdown_pct": (">", -20.0),
    "win_rate_pct": (">", 40.0),
}


# ── Ollama helpers ────────────────────────────────────────────────────────────

def _extract_json(text: str) -> str:
    """Extract the first {...} or [...] block from text, handling truncated/noisy output."""
    # Try as-is first
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    # Find first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    return text  # return raw; caller will raise


def chat(system: str, messages: list[dict], json_mode: bool = False, retries: int = 2) -> str:
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": False,
        "think": False,
    }
    if json_mode:
        payload["format"] = "json"
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
            resp.raise_for_status()
            content = resp.json()["message"]["content"].strip()
            if not content:
                raise ValueError("Empty response from model")
            if json_mode:
                content = _extract_json(content)
                json.loads(content)  # validate — raises if still invalid, triggers retry
            else:
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            return content
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"  [retry {attempt+1}/{retries}] {e}")
    raise last_err


# ── Phase 1: Scanner ──────────────────────────────────────────────────────────

SCANNER_SYSTEM = """You are a market scanner. You will be given macro market data and asked to pick the single best ticker candidate for a mean-reversion or momentum strategy today.

Rules:
- Only pick from this list of large-cap, liquid US equities:
  AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, JPM, BAC, GS,
  JNJ, PFE, ABBV, MRK, UNH, XOM, CVX, WMT, PG, KO,
  HD, LOW, DIS, NFLX, AMD, INTC, IBM, ORCL, CRM, V, MA
- In elevated/high_fear VIX regime, favor defensive names (JNJ, PFE, ABBV, MRK, UNH, PG, KO, WMT)
- In low_vol regime, momentum setups are viable (NVDA, AMD, MSFT, TSLA)
- Never pick leveraged ETFs, crypto, or options
- Output ONLY a JSON object with this exact schema:
  {"ticker": "SYMBOL", "reason": "one sentence"}
"""

BRIEF_SYSTEM = """You are a market scanner. Given raw market data for a ticker, produce a structured Market Brief.

Output ONLY a JSON object with this exact schema:
{
  "ticker": "SYMBOL",
  "date": "YYYY-MM-DD",
  "macro": {
    "vix_regime": "low_vol|elevated|high_fear",
    "spy_trend": "uptrend|downtrend|sideways",
    "spy_rsi": 0.0
  },
  "snapshot": {
    "trend": "uptrend|downtrend|sideways",
    "rsi_14": 0.0,
    "macd_signal": "bullish|bearish|neutral",
    "support": 0.0,
    "resistance": 0.0,
    "volume_ratio": 0.0,
    "sentiment_score": 0.0,
    "sentiment_label": "positive|neutral|negative"
  },
  "news_summary": "one or two sentence summary of key themes"
}
"""


def run_scanner(ticker: str | None = None) -> dict:
    print("\n── Phase 1: Scanner ──────────────────────────────────────")

    conditions = get_market_conditions()
    print(f"  VIX regime: {conditions.get('vix_regime')}  |  SPY trend: {conditions.get('spy_trend')}  |  SPY RSI: {conditions.get('spy_rsi')}")

    if ticker is None:
        raw = chat(
            SCANNER_SYSTEM,
            [{"role": "user", "content": f"Macro data:\n{json.dumps(conditions, indent=2)}\n\nPick one ticker."}],
            json_mode=True,
        )
        pick = json.loads(raw)
        ticker = pick["ticker"].upper()
        print(f"  Scanner picked: {ticker} — {pick['reason']}")
    else:
        ticker = ticker.upper()
        print(f"  Using specified ticker: {ticker}")

    snapshot = get_market_snapshot(ticker)
    if snapshot.get("error"):
        raise RuntimeError(f"No market data for {ticker}: {snapshot['error']}")
    sentiment = get_news_sentiment(ticker, days=7)

    raw_brief = chat(
        BRIEF_SYSTEM,
        [{"role": "user", "content": (
            f"Macro:\n{json.dumps(conditions, indent=2)}\n\n"
            f"Snapshot:\n{json.dumps(snapshot, indent=2)}\n\n"
            f"Sentiment:\n{json.dumps(sentiment, indent=2)}"
        )}],
        json_mode=True,
    )
    brief = json.loads(raw_brief)
    brief["ticker"] = ticker
    brief["date"] = str(date.today())

    print(f"  Trend: {brief['snapshot']['trend']}  |  RSI: {brief['snapshot']['rsi_14']}  |  Sentiment: {brief['snapshot']['sentiment_label']}")
    return brief


# ── Phase 2: Strategist ───────────────────────────────────────────────────────

DEBATE_SYSTEM = """You are a strategy engineer. Given a Market Brief, argue the bull and bear case for a mean-reversion or momentum trade.

Output ONLY a JSON object:
{
  "bull_case": "2-3 sentences",
  "bear_case": "2-3 sentences",
  "setup_type": "mean_reversion|momentum|defensive"
}
"""

RULES_SYSTEM = """You are a strategy engineer. Given a Market Brief and trade setup, generate backtest entry/exit rules.

Supported indicators: rsi_14, sma_20, sma_50, ema_9, ema_21, macd_hist, trend, close, volume, volume_ratio
Operators: <, <=, >, >=, ==
trend values: "uptrend", "downtrend", "sideways"
IMPORTANT: value must always be a scalar number or string. NEVER put an indicator name as a value.
IMPORTANT: Never add two conditions on the same numeric indicator that conflict (e.g. rsi < 35 AND rsi > 50 is impossible — never do this).

Example — mean reversion (oversold bounce):
{
  "entry": [
    {"indicator": "rsi_14", "operator": "<", "value": 35}
  ],
  "exit": [
    {"indicator": "rsi_14", "operator": ">", "value": 60},
    {"type": "stop_loss", "pct": 0.05},
    {"type": "take_profit", "pct": 0.12}
  ],
  "position_size_pct": 0.20
}

Example — momentum:
{
  "entry": [
    {"indicator": "macd_hist", "operator": ">", "value": 0},
    {"indicator": "trend", "operator": "==", "value": "uptrend"}
  ],
  "exit": [
    {"indicator": "macd_hist", "operator": "<", "value": 0},
    {"type": "stop_loss", "pct": 0.05},
    {"type": "take_profit", "pct": 0.15}
  ],
  "position_size_pct": 0.20
}

Rules:
- Always include stop_loss in exit (minimum pct: 0.04)
- A single RSI entry condition is fine — don't over-constrain
- Mean reversion: entry when RSI < 40 (oversold), exit when RSI > 60
- Momentum: macd_hist > 0 AND trend == "uptrend"
"""

REFINE_SYSTEM = """You are a strategy engineer reviewing a failed backtest. Identify why it failed and output improved rules.

Acceptance criteria (ALL must pass):
- sortino_ratio > 0.3
- total_return_pct > buy_and_hold_return_pct
- trade_count >= 5
- max_drawdown_pct > -20.0
- win_rate_pct > 40.0

CRITICAL — if trade_count < 5:
  Entry conditions are TOO RESTRICTIVE. Fix: raise the RSI threshold (e.g. 30 → 45), or drop a condition.
  A single RSI condition (e.g. rsi_14 < 45) is often enough to get 10+ trades per year.
  NEVER add two conditions on the same indicator that conflict (e.g. rsi < 35 AND rsi > 50 — impossible).

CRITICAL — if sortino <= 0.3 but trade_count is OK:
  Do NOT add more entry conditions. Instead: tighten stop_loss_pct (e.g. 0.07 → 0.04) and add
  take_profit at 2–3x the stop (e.g. stop 0.04 → take_profit 0.10). Better risk:reward improves sortino.

Other fixes:
- Low win rate: use trend == "uptrend" as a filter (mean reversion bounces only in uptrends)
- Beats B&H: if the stock went up 40%+ over the year, mean reversion underperforms; switch to momentum
  (use macd_hist > 0 + trend == "uptrend" to ride the trend)

Valid indicators only: rsi_14, sma_20, sma_50, ema_9, ema_21, macd_hist, trend, close, volume, volume_ratio
Valid trend values: "uptrend", "downtrend", "sideways"
value must always be a number or one of the trend strings — never another indicator name.

Output ONLY a JSON object with the same rules schema as before.
"""


def evaluate_backtest(result: dict) -> tuple[bool, list[str]]:
    failures = []
    if result.get("error"):
        return False, [f"backtest error: {result['error']}"]
    if result.get("sortino_ratio", 0) <= 0.3:
        failures.append(f"sortino {result['sortino_ratio']} <= 0.3")
    # B&H hurdle capped at 15% — we can't be expected to beat a 200% bull run with short-term trades
    bah_hurdle = min(result.get("buy_and_hold_return_pct", 0), 15.0)
    if result.get("total_return_pct", 0) <= bah_hurdle:
        failures.append(f"return {result['total_return_pct']}% <= hurdle {bah_hurdle:.1f}% (B&H {result.get('buy_and_hold_return_pct', 0)}%)")
    if result.get("trade_count", 0) < 5:
        failures.append(f"trade_count {result['trade_count']} < 5")
    if result.get("max_drawdown_pct", -100) <= -20.0:
        failures.append(f"drawdown {result['max_drawdown_pct']}% <= -20%")
    if result.get("win_rate_pct", 0) <= 40.0:
        failures.append(f"win_rate {result['win_rate_pct']}% <= 40%")
    return len(failures) == 0, failures


def run_strategist(brief: dict) -> dict:
    print("\n── Phase 2: Strategist ───────────────────────────────────")
    ticker = brief["ticker"]

    # Bull/bear debate
    debate_raw = chat(
        DEBATE_SYSTEM,
        [{"role": "user", "content": f"Market Brief:\n{json.dumps(brief, indent=2)}"}],
        json_mode=True,
    )
    debate = json.loads(debate_raw)
    print(f"  Setup type: {debate['setup_type']}")
    print(f"  Bull: {debate['bull_case']}")
    print(f"  Bear: {debate['bear_case']}")

    brief_str = json.dumps(brief, indent=2)
    debate_str = json.dumps(debate, indent=2)
    history = []
    period = "1y"
    failures = ["no attempts completed"]

    VALID_INDICATORS = {"rsi_14", "sma_20", "sma_50", "ema_9", "ema_21",
                        "macd_hist", "macd", "macd_histogram", "trend",
                        "close", "volume", "volume_ratio"}

    for attempt in range(1, 5):
        print(f"\n  Attempt {attempt}/4 — generating rules...")

        if attempt == 1:
            rules_raw = chat(
                RULES_SYSTEM,
                [{"role": "user", "content": f"Brief:\n{brief_str}\n\nDebate:\n{debate_str}\n\nGenerate entry/exit rules."}],
                json_mode=True,
            )
        else:
            rules_raw = chat(
                REFINE_SYSTEM,
                history + [{"role": "user", "content": "Generate improved rules fixing the failures above."}],
                json_mode=True,
            )

        rules = json.loads(rules_raw)

        # Auto-fix: move overbought RSI/indicator conditions from entry → exit
        # (model often puts "rsi > 65" in entry when it means "exit when overbought")
        entry = rules.get("entry", [])
        exit_ = rules.get("exit", [])
        hi_ops = {">", ">="}
        misplaced = [c for c in entry if c.get("indicator") in ("rsi_14",) and c.get("operator") in hi_ops]
        if misplaced:
            rules["entry"] = [c for c in entry if c not in misplaced]
            rules["exit"] = exit_ + misplaced
            print(f"  [auto-fix] moved overbought conditions from entry→exit: {misplaced}")

        # Validate rules before wasting a backtest
        entry_conds = rules.get("entry", [])
        exit_ind_conds = [c for c in rules.get("exit", []) if "indicator" in c]
        all_conditions = entry_conds + exit_ind_conds
        if not rules.get("entry"):
            msg = "entry is empty — at least 1 entry condition required"
            print(f"  ✗ Invalid rules: {msg}")
            history.append({"role": "assistant", "content": rules_raw})
            history.append({"role": "user", "content": f"Rules rejected — {msg}. Add at least one entry condition."})
            continue

        bad_ind = [c["indicator"] for c in all_conditions if c.get("indicator") not in VALID_INDICATORS]
        bad_val = [c for c in all_conditions if isinstance(c.get("value"), str)
                   and c["value"] not in ("uptrend", "downtrend", "sideways")]
        # Detect contradictory numeric conditions within entry only (entry conditions all apply simultaneously)
        contradictions = []
        numeric_conds = [c for c in entry_conds if isinstance(c.get("value"), (int, float))]
        for a, b in combinations(numeric_conds, 2):
            if a.get("indicator") != b.get("indicator"):
                continue
            # Check if a and b can never both be true simultaneously
            lo_ops, hi_ops = {"<", "<="}, {">", ">="}
            a_lo = a["operator"] in lo_ops
            b_hi = b["operator"] in hi_ops
            a_hi = a["operator"] in hi_ops
            b_lo = b["operator"] in lo_ops
            if (a_lo and b_hi and a["value"] <= b["value"]) or (a_hi and b_lo and a["value"] >= b["value"]):
                contradictions.append(f"{a['indicator']} {a['operator']} {a['value']} contradicts {b['operator']} {b['value']}")
        if bad_ind or bad_val or contradictions:
            msgs = []
            if bad_ind:
                msgs.append(f"unknown indicators: {bad_ind}")
            if bad_val:
                msgs.append(f"value must be a number or trend string, not another indicator: {[c['value'] for c in bad_val]}")
            if contradictions:
                msgs.append(f"contradictory conditions (can never both be true): {contradictions}")
            msg = "; ".join(msgs) + f". Valid indicators: {sorted(VALID_INDICATORS)}"
            print(f"  ✗ Invalid rules: {msg}")
            history.append({"role": "assistant", "content": rules_raw})
            history.append({"role": "user", "content": f"Rules rejected — {msg}. Fix and regenerate."})
            continue

        print(f"  Rules: {json.dumps(rules['entry'])} | exits: {len(rules['exit'])} conditions")

        result = backtest_strategy(rules, ticker, period)

        if result.get("error") and period == "1y":
            print(f"  1y insufficient data, retrying with 2y...")
            period = "2y"
            result = backtest_strategy(rules, ticker, period)

        if result.get("error"):
            print(f"  Backtest error: {result['error']}")
            history.append({"role": "assistant", "content": rules_raw})
            history.append({"role": "user", "content": f"Backtest failed: {result['error']}. Adjust rules."})
            continue

        passed, failures = evaluate_backtest(result)
        print(f"  Return: {result['total_return_pct']}% vs B&H {result['buy_and_hold_return_pct']}%  |  Sortino: {result['sortino_ratio']}  |  Trades: {result['trade_count']}  |  Win rate: {result['win_rate_pct']}%  |  Drawdown: {result['max_drawdown_pct']}%")

        if passed:
            print(f"  ✓ Strategy passed on attempt {attempt}")
            return {
                "status": "PASS",
                "ticker": ticker,
                "rules": rules,
                "period": period,
                "metrics": {
                    "total_return_pct": result["total_return_pct"],
                    "buy_and_hold_return_pct": result["buy_and_hold_return_pct"],
                    "sortino_ratio": result["sortino_ratio"],
                    "sharpe_ratio": result["sharpe_ratio"],
                    "max_drawdown_pct": result["max_drawdown_pct"],
                    "trade_count": result["trade_count"],
                    "win_rate_pct": result["win_rate_pct"],
                },
                "execute": True,
            }

        print(f"  ✗ Failed: {', '.join(failures)}")
        history.append({"role": "assistant", "content": rules_raw})
        history.append({
            "role": "user",
            "content": (
                f"Backtest results:\n{json.dumps(result, indent=2)}\n\n"
                f"Failed criteria: {', '.join(failures)}\n\n"
                f"Identify the root cause and generate improved rules."
            ),
        })

    print("  ✗ No edge found after 4 attempts")
    return {
        "status": "NO-EDGE",
        "ticker": ticker,
        "execute": False,
        "reason": f"Failed after 4 attempts. Last failures: {', '.join(failures)}",
    }


# ── Phase 3: Executor ─────────────────────────────────────────────────────────

def run_executor(spec: dict, brief: dict) -> dict:
    print("\n── Phase 3: Executor ─────────────────────────────────────")
    ticker = spec["ticker"]

    portfolio = get_portfolio_state()
    if portfolio.get("error"):
        print(f"  ✗ Portfolio error: {portfolio['error']}")
        return {"status": "FAILED", "reason": portfolio["error"]}

    open_positions = len(portfolio.get("positions", []))
    cash = float(portfolio.get("cash", 0))
    portfolio_value = float(portfolio.get("portfolio_value", cash))
    existing_tickers = [p.get("ticker") or p.get("symbol") for p in portfolio.get("positions", [])]

    print(f"  Portfolio: ${cash:.2f} cash | {open_positions} open positions | holdings: {existing_tickers or 'none'}")

    # Guard rails
    if open_positions >= 3:
        msg = f"Max positions reached ({open_positions}/3)"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    if ticker in existing_tickers:
        msg = f"Already holding {ticker}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    invest_amount = portfolio_value * 0.20
    if cash < invest_amount:
        msg = f"Insufficient cash: need ${invest_amount:.2f}, have ${cash:.2f}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    current_price = brief["snapshot"].get("resistance", 0)
    # Get a better price estimate from snapshot close if available
    raw_snapshot = get_market_snapshot(ticker)
    current_price = raw_snapshot.get("price") or raw_snapshot.get("close") or current_price

    if not current_price or current_price <= 0:
        msg = "Could not determine current price"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    qty = math.floor(invest_amount / current_price)
    if qty < 1:
        msg = f"Position too small: ${invest_amount:.2f} insufficient for 1 share at ${current_price:.2f}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    print(f"  Submitting: BUY {qty} {ticker} @ ~${current_price:.2f} (${qty * current_price:.2f})")
    order_result = place_order(ticker, "buy", qty)

    if order_result.get("error"):
        print(f"  ✗ FAILED: {order_result['error']}")
        return {"status": "FAILED", "reason": order_result["error"]}

    order_id = order_result.get("id") or order_result.get("order_id", "unknown")
    print(f"  ✓ SUBMITTED — order ID: {order_id}")
    return {"status": "SUBMITTED", "order_id": order_id, "qty": qty, "ticker": ticker}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ticker_arg = sys.argv[1].upper() if len(sys.argv) > 1 else None

    print("=" * 60)
    print("  LLM Day Trader — Strategy Loop")
    print("=" * 60)

    # If a specific ticker was passed, run once
    if ticker_arg:
        brief = run_scanner(ticker_arg)
        spec = run_strategist(brief)
    else:
        # Auto-mode: scanner picks, retry up to 3 different tickers if NO-EDGE
        spec = None
        tried = set()
        for _attempt in range(3):
            brief = run_scanner()
            ticker = brief["ticker"]
            if ticker in tried:
                print(f"  Skipping {ticker} (already tried)")
                continue
            tried.add(ticker)
            spec = run_strategist(brief)
            if spec["status"] == "PASS":
                break
            print(f"\n  No edge for {ticker}, trying another ticker...")
        if spec is None:
            print("  No candidates returned by scanner")
            return

    # Summary
    print("\n── Result ────────────────────────────────────────────────")
    if spec["status"] == "NO-EDGE":
        print(f"  No edge found for {spec['ticker']}: {spec['reason']}")
        return

    m = spec["metrics"]
    print(f"  Strategy: {spec['ticker']} | {spec['period']}")
    print(f"  Return: {m['total_return_pct']}% vs B&H {m['buy_and_hold_return_pct']}%")
    print(f"  Sortino: {m['sortino_ratio']} | Sharpe: {m['sharpe_ratio']} | Drawdown: {m['max_drawdown_pct']}%")
    print(f"  Trades: {m['trade_count']} | Win rate: {m['win_rate_pct']}%")

    # Phase 3
    result = run_executor(spec, brief)

    print("\n" + "=" * 60)
    print(f"  {result['status']}", end="")
    if result["status"] == "SUBMITTED":
        print(f" — {result['qty']} shares of {result['ticker']} | order: {result['order_id']}")
    else:
        print(f" — {result.get('reason', '')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
