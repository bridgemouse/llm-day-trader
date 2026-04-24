# alpaca_mcp/wiki.py
# Persistent wiki for the trading agent.
#
# Design split:
#   - Structured fields (date, ticker, score, etc.) → Python assembles the markdown
#   - Freeform fields (observation, agent_note)     → agent writes freely, personality lives here
#
# The agent never writes raw markdown. It passes data; Python formats it.

import re
from datetime import datetime, timezone
from pathlib import Path

WIKI_DIR = Path(__file__).parent.parent / "wiki"


def _wiki_path(page: str) -> Path:
    """Resolve a page name to an absolute path, blocking path traversal."""
    page = page.lstrip("/").replace("..", "")
    if not page.endswith(".md"):
        page += ".md"
    return WIKI_DIR / page


# ── Read tools ────────────────────────────────────────────────────────────────

def list_wiki_pages() -> dict:
    """Return the wiki index — catalog of all pages."""
    index = WIKI_DIR / "index.md"
    if not index.exists():
        return {"error": "Wiki index not found"}
    return {"index": index.read_text()}


def read_wiki_page(page: str) -> dict:
    """Read a specific wiki page. Use list_wiki_pages() first to find page names."""
    path = _wiki_path(page)
    if not path.exists():
        return {"error": f"Page '{page}' not found", "available_hint": "Call list_wiki_pages to see what exists"}
    return {"page": page, "content": path.read_text()}


def get_recent_trades(n: int = 5) -> dict:
    """Return the last N trade log entries."""
    log_path = WIKI_DIR / "log.md"
    if not log_path.exists():
        return {"trades": [], "note": "No trade history yet"}

    content = log_path.read_text()
    # Split on entry headers: ## [date]
    entries = re.split(r"(?=^## \[)", content, flags=re.MULTILINE)
    entries = [e.strip() for e in entries if e.strip().startswith("## [")]

    return {
        "recent_trades": entries[-n:] if entries else [],
        "total_recorded": len(entries),
    }


def search_wiki(query: str, max_results: int = 6) -> dict:
    """Search wiki pages for a keyword. Returns snippets with page names."""
    results = []
    query_lower = query.lower()

    for md_file in WIKI_DIR.rglob("*.md"):
        if md_file.name == "log.md":
            continue  # log is accessed via get_recent_trades
        try:
            lines = md_file.read_text().splitlines()
        except Exception:
            continue

        for i, line in enumerate(lines):
            if query_lower in line.lower():
                start = max(0, i - 2)
                end = min(len(lines), i + 4)
                snippet = "\n".join(lines[start:end])
                page = str(md_file.relative_to(WIKI_DIR)).removesuffix(".md")
                results.append({"page": page, "snippet": snippet})
                break  # one hit per file to avoid flooding

        if len(results) >= max_results:
            break

    return {"results": results, "count": len(results)}


def get_realized_pnl_total() -> float:
    """Return total realized P&L from wiki/meta/performance.md."""
    perf_path = WIKI_DIR / "meta" / "performance.md"
    if not perf_path.exists():
        return 0.0
    content = perf_path.read_text()
    m = re.search(r"## Realized P&L\n- Total: ([+-]?\$[0-9,.]+)", content)
    if not m:
        return 0.0
    try:
        raw = m.group(1).lstrip("+").replace("$", "").replace(",", "")
        return float(raw)
    except ValueError:
        return 0.0


# ── Write tools ───────────────────────────────────────────────────────────────

def append_trade_log(
    ticker: str,
    decision: str,           # "BUY" or "STAND_ASIDE"
    score: float,
    regime: str,
    price: float,
    qty: int,
    rationale: str,          # freeform — agent writes this
    biggest_risk: str,       # freeform — agent writes this
    agent_note: str = "",    # freeform — personality, reflection, anything
    conviction: int = 0,
    conviction_note: str = "",
) -> dict:
    """
    Append a structured entry to the trade log.
    rationale, biggest_risk, and agent_note are freeform — write in your own voice.
    """
    log_path = WIKI_DIR / "log.md"
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    ticker_display = ticker if ticker and ticker != "NONE" else ""
    entry = f"""
## [{date_str}] {decision}{" " + ticker_display if ticker_display else ""}
- **Score:** {score:+.0f}
- **Regime:** {regime}
- **Price:** ${price:.2f}
- **Qty:** {qty if decision == "BUY" else "—"}
- **Rationale:** {rationale}
- **Biggest Risk:** {biggest_risk}
"""
    if agent_note:
        entry += f"- **Note:** {agent_note}\n"
    if conviction:
        entry += f"- **Conviction: {conviction}/10**"
        if conviction_note:
            entry += f" — {conviction_note}"
        entry += "\n"

    entry += "\n---\n"

    with open(log_path, "a") as f:
        f.write(entry)

    _update_performance(ticker, decision, score, regime, price)
    _update_index_if_needed()

    return {"status": "logged", "entry_date": date_str}


def update_ticker_page(
    ticker: str,
    decision: str,           # "BUY" or "STAND_ASIDE"
    score: float,
    price: float,
    observation: str,        # freeform — what the agent noticed about this ticker
) -> dict:
    """
    Create or update a ticker's wiki page.
    observation is freeform — write what you genuinely noticed. This compounds over time.
    """
    ticker = ticker.upper()
    page_path = WIKI_DIR / "tickers" / f"{ticker}.md"
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if page_path.exists():
        content = page_path.read_text()
        # Increment counters
        content = _increment_counter(content, "Times evaluated")
        if decision == "BUY":
            content = _increment_counter(content, "Times bought")
        else:
            content = _increment_counter(content, "Times passed")
        # Update last seen
        content = re.sub(r"- \*\*Last seen:\*\* .+", f"- **Last seen:** {date_str}", content)
        # Append to trade history table
        content = _append_to_table(content, f"| {date_str} | {decision} | {score:+.0f} | ${price:.2f} | — |")
        # Append observation
        if observation:
            content += f"\n_{date_str}:_ {observation}\n"
        page_path.write_text(content)
    else:
        # Create new page
        bought = 1 if decision == "BUY" else 0
        passed = 0 if decision == "BUY" else 1
        content = f"""# {ticker}

## Stats
- **Last seen:** {date_str}
- **Times evaluated:** 1
- **Times bought:** {bought}
- **Times passed:** {passed}

## Trade History
| Date | Decision | Score | Price | Outcome |
|------|----------|-------|-------|---------|
| {date_str} | {decision} | {score:+.0f} | ${price:.2f} | — |

## Observations
"""
        if observation:
            content += f"\n_{date_str}:_ {observation}\n"
        page_path.write_text(content)
        _add_to_index(ticker)

    return {"status": "updated", "ticker": ticker}


def close_position_wiki(
    ticker: str,
    exit_price: float,
    pnl_usd: float,
    pnl_pct: float,
    reason: str,
) -> dict:
    """
    Record a position close: append a CLOSE entry to log.md and update realized P&L.
    Called by the tools layer after a sell order is submitted.
    """
    ticker = ticker.upper()
    log_path = WIKI_DIR / "log.md"
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sign = "+" if pnl_usd >= 0 else ""
    outcome = "WIN" if pnl_usd >= 0 else "LOSS"

    entry = f"""
## [{date_str}] CLOSE {ticker}
- **Exit Price:** ${exit_price:.2f}
- **Realized P&L:** {sign}${pnl_usd:.2f}  ({sign}{pnl_pct:.2f}%)
- **Outcome:** {outcome}
- **Reason:** {reason}

---
"""
    with open(log_path, "a") as f:
        f.write(entry)

    _update_realized_pnl(pnl_usd)

    return {"status": "logged", "ticker": ticker, "pnl_usd": pnl_usd, "outcome": outcome}


# ── Internal helpers ───────────────────────────────────────────────────────────

def _update_realized_pnl(pnl_usd: float) -> None:
    """Update realized P&L total in wiki/meta/performance.md."""
    perf_path = WIKI_DIR / "meta" / "performance.md"
    if not perf_path.exists():
        return

    content = perf_path.read_text()

    # Parse current total
    m = re.search(r"(## Realized P&L\n- Total: )[+-]?\$([0-9,.]+)", content)
    if not m:
        return
    current = float(m.group(2).replace(",", ""))
    new_total = current + pnl_usd
    sign = "+" if new_total >= 0 else ""
    content = content[:m.start(1)] + f"## Realized P&L\n- Total: {sign}${new_total:.2f}" + content[m.end():]

    # Update win/loss counts
    win_label = "Wins" if pnl_usd >= 0 else "Losses"
    content = _increment_counter(content, win_label)

    # Update "Toward matrix upgrade" line
    content = re.sub(
        r"- Toward matrix upgrade: .+",
        f"- Toward matrix upgrade: {max(0.0, new_total):.0f} / 800 credits",
        content,
    )

    # Update "Remaining" in Toward Mac Mini section
    remaining = max(0.0, 800.0 - new_total)
    content = re.sub(
        r"- Remaining: \$[0-9,.]+",
        f"- Remaining: ${remaining:.2f}",
        content,
    )

    perf_path.write_text(content)


def _increment_counter(content: str, label: str) -> str:
    def inc(m):
        return f"- **{label}:** {int(m.group(1)) + 1}"
    return re.sub(rf"- \*\*{label}:\*\* (\d+)", inc, content)


def _append_to_table(content: str, row: str) -> str:
    """Append a row just before the ## Observations header."""
    marker = "## Observations"
    if marker in content:
        return content.replace(marker, f"{row}\n\n{marker}")
    return content + f"\n{row}\n"


def _update_performance(ticker: str, decision: str, score: float, regime: str, price: float) -> None:
    perf_path = WIKI_DIR / "meta" / "performance.md"
    if not perf_path.exists():
        return

    content = perf_path.read_text()

    # Increment total decisions
    content = _increment_counter(content, "Total decisions")
    if decision == "BUY":
        content = _increment_counter(content, "Buys")
    else:
        content = _increment_counter(content, "Passes")

    # Append row to history table
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = f"| {date_str} | {ticker} | {decision} | {score:+.0f} | {regime} | ${price:.2f} | — |"
    content = _append_to_table(content, row)

    perf_path.write_text(content)


def _add_to_index(ticker: str) -> None:
    index_path = WIKI_DIR / "index.md"
    content = index_path.read_text()
    placeholder = "_(none yet — created automatically after first evaluation)_"
    new_entry = f"- [{ticker}](tickers/{ticker}.md) — {ticker} observations and trade history"
    if placeholder in content:
        content = content.replace(placeholder, new_entry)
    else:
        # Find the Ticker Pages section and append
        content = re.sub(
            r"(## Ticker Pages\n)(.*?)(\n## )",
            lambda m: m.group(1) + m.group(2) + f"{new_entry}\n" + m.group(3),
            content,
            flags=re.DOTALL,
        )
    index_path.write_text(content)


def _update_index_if_needed() -> None:
    """Ensure index reflects all ticker pages on disk."""
    index_path = WIKI_DIR / "index.md"
    content = index_path.read_text()
    for ticker_file in sorted((WIKI_DIR / "tickers").glob("*.md")):
        ticker = ticker_file.stem
        if ticker not in content:
            _add_to_index(ticker)
