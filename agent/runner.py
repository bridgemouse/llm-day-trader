# agent/runner.py
# Ollama tool-calling loop. Drives the LLM through tools until it makes a final decision.
# Phase flavor text is printed as tools are called.

import json
import os
import re
import textwrap

import requests
from dotenv import load_dotenv

from agent.flavor import get_phase_flavor
from agent.tools import TOOLS, TOOL_MAP, SYSTEM_PROMPT
from alpaca_mcp.execution import get_portfolio_state as _get_portfolio_state

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

# Maps tool names to phase keys for flavor text
_TOOL_PHASE = {
    "list_wiki_pages":        "wiki",
    "read_wiki_page":         "wiki",
    "get_recent_trades":      "wiki",
    "search_wiki":            "wiki",
    "get_market_conditions":  "macro",
    "scan_signals":           "scan",
    "get_market_snapshot":    "snapshot",
    "get_indicators":         "snapshot",
    "get_news_sentiment":     "news",
    "get_signal_score":       "score",
    "get_polymarket_context": "score",
    "search_web":             "web",
    "append_trade_log":       "report",
    "update_ticker_page":     "report",
    "get_portfolio_state":    "portfolio",
    "close_position":         "sell",
}


def run_agent(hint_tickers: list[str] | None = None) -> dict:
    """
    Run the K-4SH agent loop against Ollama.

    Returns a decision dict:
        {
            "decision": "BUY" | "STAND_ASIDE",
            "ticker": str | None,
            "rationale": str,
            "risk": str,
            "_wiki_written": bool,
        }
    """
    # Front-load current holdings so the model can't ignore the constraint
    try:
        _pf = _get_portfolio_state()
        _held = [p["ticker"] for p in _pf.get("positions", [])]
    except Exception:
        _held = []

    user_msg = (
        "Run the trading pipeline. Decide whether to buy a stock today or stand aside. "
        "Begin immediately by calling get_portfolio_state() — your first response must be a tool call, not text."
    )
    if _held:
        user_msg += (
            f" You currently hold: {', '.join(_held)}. "
            f"Do NOT buy more of these — find new tickers. "
            f"You MAY close held positions if exit criteria are met."
        )
    if hint_tickers:
        user_msg += f" Focus your investigation on these tickers: {', '.join(hint_tickers)}."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    tool_calls_total = 0
    max_tool_calls = 25
    wiki_written = False
    _log_decision: str | None = None   # captured from append_trade_log args
    _log_ticker: str | None = None
    _log_rationale: str | None = None
    _log_risk: str | None = None
    # Track tools that should only be called once per cycle
    _once_called: set[str] = set()
    _once_only = {
        "get_portfolio_state", "get_market_conditions",
        "append_trade_log", "update_ticker_page",
    }
    _searched_queries: set[str] = set()  # prevent duplicate web searches
    _wiki_read_count: int = 0            # cap wiki reads at 3 per cycle
    _web_search_count: int = 0           # cap web searches at 3 per cycle
    _decision_forced: bool = False       # True after budget nudge — only wiki writes allowed

    while tool_calls_total < max_tool_calls:
        payload = {
            "model": MODEL,
            "messages": messages,
            "tools": TOOLS,
            "stream": False,
            "think": False,
        }

        try:
            resp = requests.post(OLLAMA_URL, json=payload, timeout=180)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  ! Ollama request failed: {e}")
            return {
                "decision": "STAND_ASIDE",
                "ticker": None,
                "rationale": f"Ollama request failed: {e}",
                "risk": "",
                "_wiki_written": wiki_written,
            }
        msg = data["message"]

        content = msg.get("content", "") or ""
        visible = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            # Only print model text if wiki wasn't already written via tool calls.
            # When _log_decision is set, the model's trailing text is noise
            # (often raw JSON or a redundant summary) — suppress it.
            if visible and not _log_decision:
                wrapped = textwrap.fill(
                    visible,
                    width=68,
                    initial_indent="  K-4SH: ",
                    subsequent_indent="          ",
                )
                print(f"\n{wrapped}")
            # Prefer decision captured from append_trade_log args over text parsing
            if _log_decision:
                raw_ticker = (_log_ticker or "").upper().strip()
                # Validate ticker is a real symbol (1-5 uppercase letters only)
                valid_ticker = raw_ticker if re.match(r"^[A-Z]{1,5}$", raw_ticker) else None
                if _log_decision == "BUY" and not valid_ticker:
                    print(f"  ! invalid ticker '{raw_ticker}' — downgrading to STAND_ASIDE")
                    _log_decision = "STAND_ASIDE"
                return {
                    "decision": _log_decision,
                    "ticker": valid_ticker if _log_decision == "BUY" else None,
                    "rationale": _log_rationale or "",
                    "risk": _log_risk or "",
                    "_wiki_written": wiki_written,
                }
            # Wiki not written yet — inject one hard forcing message and retry
            if not wiki_written and tool_calls_total < max_tool_calls:
                print("  ~ no wiki write detected — forcing decision log")
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [],
                })
                messages.append({
                    "role": "user",
                    "content": (
                        "You have not logged your decision yet. "
                        "You MUST call append_trade_log RIGHT NOW with your decision "
                        "(BUY <ticker> or STAND_ASIDE), rationale, and biggest_risk. "
                        "Then call update_ticker_page. Then output your final DECISION line. "
                        "Do not output any text before calling append_trade_log."
                    ),
                })
                continue  # re-enter the loop with the forcing message
            return _parse_decision(visible, wiki_written=wiki_written)

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            fn_args = tc["function"].get("arguments", {})
            if isinstance(fn_args, str):
                try:
                    fn_args = json.loads(fn_args)
                except Exception:
                    print(f"  ! Could not parse args for {fn_name}: {fn_args!r}")
                    fn_args = {}
            # Unwrap nested {"arguments": {...}} if the model double-wrapped them
            if isinstance(fn_args, dict) and list(fn_args.keys()) == ["arguments"]:
                inner = fn_args["arguments"]
                if isinstance(inner, str):
                    try:
                        inner = json.loads(inner)
                    except Exception:
                        inner = {}
                fn_args = inner if isinstance(inner, dict) else {}

            # --- Guards (all suppress flavor and short-circuit) ---

            # 0. After budget nudge: only wiki writes are allowed
            _wiki_writes = {"append_trade_log", "update_ticker_page"}
            if _decision_forced and fn_name not in _wiki_writes:
                result = {"warning": "Decision time. Only append_trade_log and update_ticker_page are allowed now."}
                messages.append({"role": "tool", "content": json.dumps(result)})
                tool_calls_total += 1
                continue

            # 1. Once-only tools
            if fn_name in _once_only and fn_name in _once_called:
                result = {"warning": f"{fn_name} already called this cycle. Do not call it again."}
                messages.append({"role": "tool", "content": json.dumps(result)})
                tool_calls_total += 1
                continue

            # 2. Web search cap (max 3 per cycle) + duplicate dedup
            if fn_name == "search_web":
                if _web_search_count >= 3:
                    result = {"warning": "Web search limit reached (3 max). Move on to scan_signals and analysis."}
                    messages.append({"role": "tool", "content": json.dumps(result)})
                    tool_calls_total += 1
                    continue
                q = fn_args.get("query", "").lower().strip()
                if q in _searched_queries:
                    result = {"warning": "Already searched this query. Use a different search term."}
                    messages.append({"role": "tool", "content": json.dumps(result)})
                    tool_calls_total += 1
                    continue
                _searched_queries.add(q)
                _web_search_count += 1

            # 3. Wiki read cap (max 3 per cycle)
            if fn_name in ("read_wiki_page", "list_wiki_pages", "search_wiki", "get_recent_trades"):
                if _wiki_read_count >= 3:
                    result = {"warning": "Wiki read limit reached for this cycle. Make your decision."}
                    messages.append({"role": "tool", "content": json.dumps(result)})
                    tool_calls_total += 1
                    continue
                _wiki_read_count += 1

            # 4. Block close_position on tickers not actually held
            if fn_name == "close_position":
                close_tkr = (fn_args.get("ticker") or "").upper()
                if close_tkr and close_tkr not in _held:
                    result = {"error": f"Cannot close {close_tkr} — you do not hold it. Held: {_held or 'none'}. Do not attempt to close positions you do not own."}
                    messages.append({"role": "tool", "content": json.dumps(result)})
                    tool_calls_total += 1
                    continue

            # 5. Hard block BUY on held ticker at wiki-write time
            if fn_name == "append_trade_log":
                log_dec = fn_args.get("decision", "").upper()
                log_tkr = (fn_args.get("ticker") or "").upper()
                if log_dec == "BUY" and log_tkr in _held:
                    result = {"error": f"Cannot log BUY {log_tkr} — you already hold it. Change decision to STAND_ASIDE or pick a different ticker."}
                    messages.append({"role": "tool", "content": json.dumps(result)})
                    tool_calls_total += 1
                    continue

            # --- Phase flavor (only shown for calls that will execute) ---
            phase = _TOOL_PHASE.get(fn_name, fn_name)
            ticker = fn_args.get("ticker", "")
            query = fn_args.get("query", "")
            print(f"  {get_phase_flavor(phase, ticker=ticker, query=query)}")

            if fn_name in _once_only:
                _once_called.add(fn_name)

            result = TOOL_MAP[fn_name](fn_args) if fn_name in TOOL_MAP else {"error": f"Unknown tool: {fn_name}"}

            # After get_portfolio_state: inject a loser alert for positions down -2%+
            if fn_name == "get_portfolio_state" and "positions" in result:
                losers = [
                    f"{p['ticker']} ({p['unrealized_plpc']:+.1f}%)"
                    for p in result["positions"]
                    if p.get("unrealized_plpc", 0) <= -2.0
                ]
                if losers:
                    messages.append({
                        "role": "tool",
                        "content": json.dumps(result),
                    })
                    messages.append({
                        "role": "user",
                        "content": (
                            f"ALERT: These positions are down -2% or more: {', '.join(losers)}. "
                            "Per your exit criteria, call get_market_snapshot() on each one "
                            "and close_position() if MACD is bearish or price broke SMA20. "
                            "Do this before researching new tickers."
                        ),
                    })
                    tool_calls_total += 1
                    continue  # skip the normal result append below

            # After a successful close, nudge the model to write its decision log
            if fn_name == "close_position" and "error" not in result:
                messages.append({
                    "role": "user",
                    "content": (
                        "Position closed. Now call append_trade_log with decision=STAND_ASIDE "
                        "(or BUY if you found a new ticker), then output your final DECISION line."
                    )
                })

            # Capture decision from first (real) append_trade_log call only
            if fn_name == "append_trade_log":
                wiki_written = True
                _log_decision = fn_args.get("decision", "STAND_ASIDE").upper()
                _log_ticker = fn_args.get("ticker") or None
                _log_rationale = fn_args.get("rationale", "")
                _log_risk = fn_args.get("biggest_risk", "")

            messages.append({"role": "tool", "content": json.dumps(result)})
            tool_calls_total += 1

            # Inject a hard "decide now" message when the agent has likely gathered enough data
            if tool_calls_total == 12 and not _decision_forced:
                _decision_forced = True
                try:
                    pf = _get_portfolio_state()
                    held = [p["ticker"] for p in pf.get("positions", [])]
                    slots = pf.get("max_positions_allowed", 3) - pf.get("open_positions", 0)
                    if held:
                        portfolio_note = (
                            f"You hold: {', '.join(held)}. These are your ONLY open positions. "
                            f"Do NOT close tickers you don't hold. Open slots: {slots}."
                        )
                    else:
                        portfolio_note = "You have no open positions. Open slots: 3."
                except Exception:
                    portfolio_note = ""
                messages.append({
                    "role": "user",
                    "content": (
                        f"[budget] STOP RESEARCHING. {portfolio_note} "
                        "You must now: (1) call append_trade_log with your decision, "
                        "(2) call update_ticker_page, "
                        "(3) output your final text: DECISION: BUY <TICKER> or DECISION: STAND_ASIDE "
                        "followed by RATIONALE and BIGGEST_RISK. No more tool calls after that."
                    )
                })

    return {
        "decision": "STAND_ASIDE",
        "ticker": None,
        "rationale": "Agent hit tool call limit without deciding.",
        "risk": "",
        "_wiki_written": wiki_written,
    }


def _parse_decision(text: str, wiki_written: bool = False) -> dict:
    """Extract structured decision from agent final message."""
    F = re.IGNORECASE | re.DOTALL
    buy_match = re.search(
        r"\*{0,2}decision:?\*{0,2}\s+\*{0,2}buy\*{0,2}\s+\*{0,2}([A-Z]{1,5})\*{0,2}", text, F
    )
    rationale_match = re.search(
        r"\*{0,2}rationale:?\*{0,2}\s*(.+?)(?=\*{0,2}biggest.?risk|$)", text, F
    )
    risk_match = re.search(r"\*{0,2}biggest.?risk:?\*{0,2}\s*(.+?)$", text, F)

    decision = "BUY" if buy_match else "STAND_ASIDE"
    ticker = buy_match.group(1).upper() if buy_match else None
    rationale = rationale_match.group(1).strip() if rationale_match else ""
    risk = risk_match.group(1).strip() if risk_match else ""

    return {
        "decision": decision,
        "ticker": ticker,
        "rationale": rationale,
        "risk": risk,
        "_wiki_written": wiki_written,
    }
