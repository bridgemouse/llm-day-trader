#!/usr/bin/env python3
"""
K-4SH — Autonomous Trading Droid
Persistent run loop. Market-hours aware. Never exits unless you tell it to.

Usage:
    python agent_loop.py              # K-4SH picks its own tickers
    python agent_loop.py AAPL NVDA    # hint tickers to investigate
    python agent_loop.py --dry-run    # decide without placing an order
"""

import json
import os
import re
import select
import sys
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from agent.executor import run_executor, wiki_fallback
from agent.flavor import (
    K4SH_GRACEFUL_EXIT,
    K4SH_MARKET_CLOSED,
    K4SH_STARTUP,
    get_idle_prompt,
)
from agent.report import render_cycle_report
from agent.runner import run_agent
from agent.tools import TOOL_MAP, TOOLS, SYSTEM_PROMPT
from alpaca_mcp.execution import get_portfolio_state
from alpaca_mcp.wiki import get_realized_pnl_total

load_dotenv()

ET = ZoneInfo("America/New_York")
CYCLE_INTERVAL_MIN = 45
DRY_RUN = "--dry-run" in sys.argv
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")


# ── Market hours ──────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    """True if NYSE is currently open (9:30–16:00 ET, weekdays only)."""
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dt_time(9, 30) <= t <= dt_time(16, 0)


def seconds_until_market_open() -> int:
    """Return seconds until next 9:30 ET weekday open."""
    now = datetime.now(ET)
    candidate = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return max(60, int((candidate - now).total_seconds()))


# ── Interactive idle prompt ───────────────────────────────────────────────────

def _read_with_timeout(timeout_seconds: int) -> str | None:
    """
    Read a line from stdin with a timeout.
    Returns None on timeout, stripped line otherwise.
    """
    ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    if ready:
        return sys.stdin.readline().strip()
    print()  # newline after timeout
    return None


def idle_prompt(next_scan_min: int) -> str | None:
    """
    Show idle prompt and wait for user input or timeout.
    Returns user input string, "" if Enter pressed, None if timed out.
    """
    print(f"\n{get_idle_prompt(minutes=next_scan_min)}")
    print("> ", end="", flush=True)
    return _read_with_timeout(next_scan_min * 60)


# ── Trading cycle ─────────────────────────────────────────────────────────────

def run_cycle(hint_tickers: list[str]) -> dict:
    """Run a full K-4SH trading cycle: agent → executor."""
    print("\n" + "═" * 68)
    print("  K-4SH — Trading Cycle")
    print("═" * 68)

    result = run_agent(hint_tickers or None)

    if not result.get("_wiki_written") and result.get("ticker"):
        wiki_fallback(result)

    exec_result = {"status": "STAND_ASIDE"}

    if result["decision"] == "BUY" and result.get("ticker"):
        print("\n── Executor ──────────────────────────────────────────────────────")
        exec_result = run_executor(result["ticker"], dry_run=DRY_RUN)

    return {
        "decision": result["decision"],
        "ticker": result.get("ticker"),
        "qty": exec_result.get("qty", 0),
        "price": exec_result.get("price", 0.0),
        "exec_result": exec_result,
    }


# ── Cycle report ──────────────────────────────────────────────────────────────

def show_report(cycle_summary: dict, next_scan_min: int) -> None:
    """Render and print the cycle report."""
    try:
        portfolio = get_portfolio_state()
    except Exception as e:
        print(f"  [K-4SH] Could not fetch portfolio for report: {e}")
        return
    realized_pnl = get_realized_pnl_total()
    report = render_cycle_report(
        decision=cycle_summary["decision"],
        ticker=cycle_summary.get("ticker"),
        qty=cycle_summary.get("qty", 0),
        price=cycle_summary.get("price", 0.0),
        portfolio=portfolio,
        realized_pnl=realized_pnl,
        next_scan_min=next_scan_min,
    )
    print("\n" + report)


# ── Chat handler ──────────────────────────────────────────────────────────────

def _handle_chat(user_input: str) -> None:
    """Answer a user question at the idle prompt using the agent with full tool access."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]
    print("\n  K-4SH thinking...")

    for _ in range(15):
        try:
            resp = requests.post(OLLAMA_URL, json={
                "model": MODEL,
                "messages": messages,
                "tools": TOOLS,
                "stream": False,
                "think": False,
            }, timeout=120)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"\n  K-4SH: [Ollama error: {e}]")
            return

        msg = data["message"]
        content = msg.get("content", "") or ""
        visible = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            if visible:
                print(f"\n  K-4SH: {visible}\n")
            return

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            fn_args = tc["function"].get("arguments", {})
            if isinstance(fn_args, str):
                try:
                    fn_args = json.loads(fn_args)
                except Exception:
                    fn_args = {}
            result = TOOL_MAP[fn_name](fn_args) if fn_name in TOOL_MAP else {"error": f"Unknown tool: {fn_name}"}
            messages.append({"role": "tool", "content": json.dumps(result)})


# ── Main persistent loop ──────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    hint_tickers = [a.upper() for a in args if not a.startswith("--")]

    print("\n" + "═" * 68)
    print(f"  {K4SH_STARTUP}")
    print("═" * 68)

    last_cycle_summary: dict | None = None

    try:
        while True:
            if not is_market_open():
                wait_sec = seconds_until_market_open()
                wait_min = wait_sec // 60
                print(f"\n{K4SH_MARKET_CLOSED}")
                print(f"💤 Ask me something while we wait. Next open in ~{wait_min} min.")
                print("> ", end="", flush=True)
                # Poll every 5 min max so we re-check market open status
                line = _read_with_timeout(min(wait_sec, 300))
                if line is not None and line.strip():
                    _handle_chat(line.strip())
                continue

            # Run trading cycle
            cycle_summary = run_cycle(hint_tickers)
            last_cycle_summary = cycle_summary

            # Auto-show report on BUY
            if cycle_summary["decision"] == "BUY":
                show_report(cycle_summary, CYCLE_INTERVAL_MIN)
            else:
                print(f"\n  STAND_ASIDE")
                print("  (Type 'report' at the prompt for the full cycle view)")

            # Idle loop
            while True:
                line = idle_prompt(CYCLE_INTERVAL_MIN)

                if line is None:
                    break  # timeout → next cycle
                elif line == "":
                    print("  Running cycle now...")
                    break  # Enter → immediate cycle
                elif line.lower() == "report":
                    if last_cycle_summary:
                        show_report(last_cycle_summary, CYCLE_INTERVAL_MIN)
                    else:
                        print("  No cycle report available yet.")
                else:
                    _handle_chat(line)

    except KeyboardInterrupt:
        print(f"\n\n{K4SH_GRACEFUL_EXIT}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
