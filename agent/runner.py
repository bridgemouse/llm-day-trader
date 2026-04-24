# agent/runner.py
# Ollama tool-calling loop. Drives the LLM through tools until it makes a final decision.
# Phase flavor text is printed as tools are called.

import json
import os
import re

import requests
from dotenv import load_dotenv

from agent.flavor import get_phase_flavor
from agent.tools import TOOLS, TOOL_MAP, SYSTEM_PROMPT

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
    user_msg = (
        "Run the trading pipeline. Decide whether to buy a stock today or stand aside. "
        "Begin immediately by calling get_portfolio_state() — your first response must be a tool call, not text."
    )
    if hint_tickers:
        user_msg += f" Focus your investigation on these tickers: {', '.join(hint_tickers)}."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    tool_calls_total = 0
    max_tool_calls = 40
    wiki_written = False

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
            print(f"  [K-4SH] Ollama request failed: {e}")
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
            if visible:
                print(f"\n  K-4SH: {visible}")
            return _parse_decision(visible, wiki_written=wiki_written)

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            fn_args = tc["function"].get("arguments", {})
            if isinstance(fn_args, str):
                try:
                    fn_args = json.loads(fn_args)
                except Exception:
                    print(f"  [K-4SH] Could not parse args for {fn_name}: {fn_args!r}")
                    fn_args = {}

            # Phase flavor
            phase = _TOOL_PHASE.get(fn_name, fn_name)
            ticker = fn_args.get("ticker", "")
            query = fn_args.get("query", "")
            print(f"  {get_phase_flavor(phase, ticker=ticker, query=query)}")

            if fn_name == "append_trade_log":
                wiki_written = True

            result = TOOL_MAP[fn_name](fn_args) if fn_name in TOOL_MAP else {"error": f"Unknown tool: {fn_name}"}

            messages.append({"role": "tool", "content": json.dumps(result)})
            tool_calls_total += 1

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
