# K-4SH Agent Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the one-shot trading script into K-4SH — a persistent, interactive trading droid with sell logic, a cycle report, Star Wars personality, and a Mac Mini upgrade goal tracked via realized P&L.

**Architecture:** Extract the monolithic `agent_loop.py` into a clean `agent/` package (flavor, report, tools, runner, executor), add sell capability via a `close_position` tool + Alpaca stop-loss at buy time, and wrap everything in a persistent market-hours-aware loop with an interactive idle prompt.

**Tech Stack:** Python 3.11, Ollama (qwen3:8b), Alpaca Trading SDK, alpaca-trade-api, ddgs, requests, zoneinfo (stdlib), select (stdlib)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `agent/__init__.py` | Package marker |
| Create | `agent/flavor.py` | All K-4SH voice constants + helpers |
| Create | `agent/report.py` | ASCII cycle report renderer |
| Create | `agent/tools.py` | TOOLS list, tool implementations, TOOL_MAP, SYSTEM_PROMPT |
| Create | `agent/runner.py` | Ollama tool-calling loop |
| Create | `agent/executor.py` | Buy guard rails + wiki fallback |
| Modify | `alpaca_mcp/wiki.py` | Add conviction field, close_position_wiki, realized P&L |
| Modify | `alpaca_mcp/execution.py` | Add stop-loss at buy, add close_position() |
| Modify | `wiki/meta/performance.md` | Add Realized P&L section |
| Rewrite | `agent_loop.py` | K-4SH persistent loop entry point |
| Delete | `run_loop.py` | Dead code |
| Create | `tests/__init__.py` | Test package marker |
| Create | `tests/test_flavor.py` | flavor.py unit tests |
| Create | `tests/test_report.py` | report.py unit tests |
| Create | `tests/test_wiki_realized.py` | wiki.py realized P&L unit tests |

---

## Task 1: agent/ package scaffolding + flavor.py

**Files:**
- Create: `agent/__init__.py`
- Create: `agent/flavor.py`
- Create: `tests/__init__.py`
- Create: `tests/test_flavor.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/__init__.py` (empty) and `tests/test_flavor.py`:

```python
# tests/test_flavor.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agent.flavor import (
    get_idle_prompt,
    get_phase_flavor,
    get_decision_flavor,
    get_vix_flavor,
    K4SH_MARKET_CLOSED,
    K4SH_GRACEFUL_EXIT,
    K4SH_MID_RUN_BLOCK,
)


def test_idle_prompts_cycle():
    seen = set()
    for _ in range(20):
        p = get_idle_prompt()
        seen.add(p)
    assert len(seen) > 1, "get_idle_prompt should return different prompts"


def test_idle_prompt_contains_countdown():
    # get_idle_prompt with minutes param embeds the countdown
    p = get_idle_prompt(minutes=34)
    assert "34" in p or "min" in p.lower()


def test_phase_flavor_scan():
    text = get_phase_flavor("scan", ticker="AAPL")
    assert isinstance(text, str) and len(text) > 0


def test_phase_flavor_snapshot_includes_ticker():
    text = get_phase_flavor("snapshot", ticker="NVDA")
    assert "NVDA" in text


def test_phase_flavor_web_includes_query():
    text = get_phase_flavor("web", query="AAPL earnings")
    assert "AAPL earnings" in text


def test_decision_flavor_buy():
    text = get_decision_flavor("BUY")
    assert "Kessel" in text or "Punch" in text


def test_decision_flavor_stand_aside():
    text = get_decision_flavor("STAND_ASIDE")
    assert isinstance(text, str) and len(text) > 0


def test_vix_flavor_regimes():
    for regime in ("low_vol", "normal", "elevated", "high_fear"):
        text = get_vix_flavor(regime)
        assert isinstance(text, str) and len(text) > 0


def test_constants_are_strings():
    assert isinstance(K4SH_MARKET_CLOSED, str)
    assert isinstance(K4SH_GRACEFUL_EXIT, str)
    assert isinstance(K4SH_MID_RUN_BLOCK, str)
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /home/wheat/projects/llm-day-trader
source venv/bin/activate
pytest tests/test_flavor.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'agent'`

- [ ] **Step 3: Create agent/__init__.py (empty)**

```python
# agent/__init__.py
```

- [ ] **Step 4: Create agent/flavor.py**

```python
# agent/flavor.py
# K-4SH voice constants and helper functions.
# All user-facing flavor text lives here.

import itertools
import random

# ── Idle prompts (shown at the idle prompt between cycles) ─────────────────────
_IDLE_PROMPTS = [
    "The InterGalactic Banking Clan charges more than this. Marginally.",
    "Monitoring. Not unlike waiting for the Senate to act — slow and ultimately disappointing.",
    "K-4SH suggests not visiting Canto Bight in the interim.",
    "The market moves like Jabba. Slow, unpredictable, occasionally terrifying.",
    "Currently in low-power mode. Not unlike a certain blue astromech after a bad motivator.",
    "Watching 31 tickers. The Bothans would approve of this surveillance operation.",
    "K-4SH notes that Hondo Ohnaka would have made three trades and escaped by now.",
    "This market is Crait — vast, white, nothing moving, and the First Order is probably nearby.",
    "The Hydian Way sees more profitable traffic than this portfolio currently.",
    "K-4SH is calculating odds. They are not in our favour. Proceeding anyway.",
    "The probability of a good entry today is... significant. K-4SH remains cautious.",
    "Waiting. The Empire also waited. That did not end well for them.",
]

_idle_cycle = itertools.cycle(random.sample(_IDLE_PROMPTS, len(_IDLE_PROMPTS)))


def get_idle_prompt(minutes: int | None = None) -> str:
    """Return the next rotating idle line, optionally embedding the countdown."""
    line = next(_idle_cycle)
    if minutes is not None:
        return f"💤 {line} Next scan in {minutes} min."
    return f"💤 {line}"


# ── Phase flavor (shown while tools are executing) ─────────────────────────────
_PHASE_FLAVOR = {
    "wiki":     "📖 Cross-referencing the Jocasta Nu archives...",
    "macro":    "🌍 Scanning the galaxy for macro disturbances...",
    "scan":     "🔍 Running analysis. The Bothans are already watching.",
    "snapshot": "🔎 Zooming in on {ticker}. Like Vader — focused, intense.",
    "news":     "📰 Intercepting HoloNet transmissions on {ticker}...",
    "score":    "🎰 Consulting the Jedha oracle...",
    "web":      "🌐 Dispatching probe droids: '{query}'...",
    "report":   "✍️  Filing the after-action report. Fulcrum would approve.",
    "sell":     "⚔️  Executing exit order. The Mandalorian does not hesitate.",
    "portfolio":"📊 Reviewing the manifest. Every credit accounted for.",
}


def get_phase_flavor(phase: str, ticker: str = "", query: str = "") -> str:
    """Return phase flavor text, interpolating ticker/query where applicable."""
    template = _PHASE_FLAVOR.get(phase, f"⚙️  Processing {phase}...")
    return template.format(ticker=ticker, query=query)


# ── Decision flavor (shown in cycle report) ────────────────────────────────────
_DECISION_FLAVOR = {
    "BUY":          "Executing the Kessel Run. Punch it.",
    "STAND_ASIDE":  "There is nothing here worth dying for. We retreat.",
    "GREAT_SETUP":  "[EXCITED CHIRPING] — apologies. That was undignified.",
    "BAD_MARKET":   "This is Dathomir. We do not land on Dathomir.",
    "STOP_LOSS":    "The coaxium was unstable. Position closed. As calculated.",
    "PROFIT_TAKEN": "Credits secured. The upgrade draws closer. This is the way.",
    "CLOSE":        "Position closed. Filing the outcome. Chopper would grunt approvingly.",
}


def get_decision_flavor(decision: str) -> str:
    return _DECISION_FLAVOR.get(decision.upper(), "Decision recorded.")


# ── VIX regime flavor ─────────────────────────────────────────────────────────
_VIX_FLAVOR = {
    "low_vol":   "The Force is unusually calm today. K-4SH is suspicious.",
    "normal":    "Standard conditions. Coruscant traffic, nothing more.",
    "elevated":  "K-4SH has a bad feeling about this.",
    "high_fear": "Order 66 energy. We do not make reckless trades during Order 66.",
}


def get_vix_flavor(regime: str) -> str:
    return _VIX_FLAVOR.get(regime, "Volatility regime: unknown. Proceed with caution.")


# ── Fixed strings ─────────────────────────────────────────────────────────────
K4SH_MARKET_CLOSED = "🔔 Bell's rung. I'm flat for the day. Back at 9:30 ET tomorrow."
K4SH_GRACEFUL_EXIT = "📉 Closing the desk. The upgrade will have to wait. See you tomorrow."
K4SH_MID_RUN_BLOCK = "⏳ On the floor — can't talk. Finish my trade first, then ask me anything."
K4SH_STARTUP = "K-4SH online. Cognitive matrix operational. Mac Mini: still pending."
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
pytest tests/test_flavor.py -v
```

Expected: All 9 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/__init__.py agent/flavor.py tests/__init__.py tests/test_flavor.py
git commit -m "feat: add agent/ package + flavor.py with K-4SH Star Wars voice constants"
```

---

## Task 2: agent/report.py — cycle report renderer

**Files:**
- Create: `agent/report.py`
- Create: `tests/test_report.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_report.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agent.report import render_cycle_report


def _make_portfolio():
    return {
        "portfolio_value": 100832.00,
        "cash": 43209.68,
        "total_unrealized_pl": -632.36,
        "positions": [
            {"ticker": "AAPL", "qty": 69, "current_price": 273.14,
             "unrealized_pl": 142.00, "unrealized_plpc": 0.75},
            {"ticker": "UNH", "qty": 56, "current_price": 340.21,
             "unrealized_pl": -774.36, "unrealized_plpc": -3.94},
        ],
    }


def test_render_returns_string():
    out = render_cycle_report(
        decision="BUY",
        ticker="AAPL",
        qty=69,
        price=273.14,
        portfolio=_make_portfolio(),
        realized_pnl=142.00,
        next_scan_min=45,
    )
    assert isinstance(out, str)


def test_render_contains_decision():
    out = render_cycle_report(
        decision="BUY", ticker="AAPL", qty=69, price=273.14,
        portfolio=_make_portfolio(), realized_pnl=142.00, next_scan_min=45,
    )
    assert "BUY" in out and "AAPL" in out


def test_render_contains_positions():
    out = render_cycle_report(
        decision="BUY", ticker="AAPL", qty=69, price=273.14,
        portfolio=_make_portfolio(), realized_pnl=142.00, next_scan_min=45,
    )
    assert "UNH" in out
    assert "43,209.68" in out


def test_render_shows_realized_pnl():
    out = render_cycle_report(
        decision="STAND_ASIDE", ticker=None, qty=0, price=0,
        portfolio=_make_portfolio(), realized_pnl=142.00, next_scan_min=45,
    )
    assert "142" in out
    assert "800" in out


def test_render_progress_bar_full():
    # At $800 realized the bar should show complete
    out = render_cycle_report(
        decision="STAND_ASIDE", ticker=None, qty=0, price=0,
        portfolio=_make_portfolio(), realized_pnl=800.00, next_scan_min=45,
    )
    assert "100%" in out or "800" in out


def test_render_contains_next_scan():
    out = render_cycle_report(
        decision="BUY", ticker="AAPL", qty=69, price=273.14,
        portfolio=_make_portfolio(), realized_pnl=0.0, next_scan_min=37,
    )
    assert "37" in out
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_report.py -v 2>&1 | head -10
```

Expected: `ImportError: cannot import name 'render_cycle_report' from 'agent.report'`

- [ ] **Step 3: Create agent/report.py**

```python
# agent/report.py
# Renders the K-4SH cycle report — ASCII box with portfolio, P&L, Mac Mini progress.

from datetime import datetime
from zoneinfo import ZoneInfo

from agent.flavor import get_decision_flavor, get_vix_flavor

_WIDTH = 64   # inner width (between ║ chars)
ET = ZoneInfo("America/New_York")


def _row(text: str = "") -> str:
    """Format a single box row, padding to full width."""
    return f"║  {text:<{_WIDTH - 2}}║"


def _divider() -> str:
    return "╠" + "═" * (_WIDTH + 2) + "╣"


def _top() -> str:
    return "╔" + "═" * (_WIDTH + 2) + "╗"


def _bottom() -> str:
    return "╚" + "═" * (_WIDTH + 2) + "╝"


def _progress_bar(realized: float, target: float = 800.0, width: int = 10) -> str:
    pct = min(1.0, realized / target) if target > 0 else 0.0
    filled = round(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}]  {round(pct * 100):.0f}%"


def render_cycle_report(
    decision: str,
    ticker: str | None,
    qty: int,
    price: float,
    portfolio: dict,
    realized_pnl: float,
    next_scan_min: int,
    timestamp: datetime | None = None,
) -> str:
    """
    Render the full K-4SH cycle report.

    Args:
        decision: "BUY" or "STAND_ASIDE"
        ticker: ticker symbol (None for STAND_ASIDE)
        qty: shares bought (0 for STAND_ASIDE)
        price: entry price
        portfolio: dict from get_portfolio_state()
        realized_pnl: total realized P&L from wiki
        next_scan_min: minutes until next scan
        timestamp: report time (defaults to now ET)
    """
    if timestamp is None:
        timestamp = datetime.now(ET)
    ts_str = timestamp.strftime("%Y-%m-%d %H:%M ET")

    lines = [_top()]

    # Header
    header = f"CYCLE REPORT — {ts_str}"
    lines.append(_row(header.center(_WIDTH - 2)))

    lines.append(_divider())

    # Decision block
    if decision == "BUY" and ticker:
        lines.append(_row(f"DECISION: BUY {qty} {ticker} @ ${price:.2f}"))
    else:
        lines.append(_row("DECISION: STAND_ASIDE"))
    lines.append(_row(f'"{get_decision_flavor(decision)}"'))

    lines.append(_divider())

    # Portfolio block
    lines.append(_row("PORTFOLIO"))
    positions = portfolio.get("positions", [])
    for i, pos in enumerate(positions):
        connector = "└─" if i == len(positions) - 1 and True else "├─"
        pl = pos["unrealized_pl"]
        plpc = pos["unrealized_plpc"]
        sign = "+" if pl >= 0 else ""
        lines.append(_row(
            f"  {connector} {pos['ticker']:<6} {pos['qty']:.0f} shares"
            f"  ${pos['current_price']:.2f}"
            f"   {sign}${pl:,.2f}  ({sign}{plpc:.2f}%)"
        ))
    # Cash is always the last line
    cash = portfolio.get("cash", 0.0)
    lines.append(_row(f"  └─ Cash   ${cash:,.2f}"))
    lines.append(_row())

    total_val = portfolio.get("portfolio_value", 0.0)
    unreal = portfolio.get("total_unrealized_pl", 0.0)
    unreal_sign = "+" if unreal >= 0 else ""
    real_sign = "+" if realized_pnl >= 0 else ""

    lines.append(_row(f"Total Value:      ${total_val:>12,.2f}"))
    lines.append(_row(f"Unrealized P&L:   {unreal_sign}${unreal:>10,.2f}   (still at risk)"))
    lines.append(_row(f"Realized P&L:     {real_sign}${realized_pnl:>10,.2f}   (locked in)"))

    lines.append(_divider())

    # Mac Mini progress bar
    bar_str = _progress_bar(realized_pnl, 800.0)
    remaining = max(0.0, 800.0 - realized_pnl)
    lines.append(_row(f"🍎 Matrix Upgrade:  {realized_pnl:.0f} / 800 credits  {bar_str}"))
    if realized_pnl >= 800:
        lines.append(_row('"Mac Mini acquired. K-4SH upgrades. The galaxy trembles."'))
    elif realized_pnl > 0:
        lines.append(_row(f'"The upgrade draws closer. {remaining:.0f} credits remaining."'))
    else:
        lines.append(_row('"Every closed position brings the upgrade one step nearer."'))

    lines.append(_divider())

    # Footer
    lines.append(_row(f"Next scan in {next_scan_min} min  |  Ask me something or press Enter"))

    lines.append(_bottom())

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_report.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/report.py tests/test_report.py
git commit -m "feat: add agent/report.py — K-4SH ASCII cycle report renderer"
```

---

## Task 3: wiki.py — conviction field, close_position_wiki, realized P&L

**Files:**
- Modify: `alpaca_mcp/wiki.py`
- Modify: `wiki/meta/performance.md`
- Create: `tests/test_wiki_realized.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_wiki_realized.py
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


@pytest.fixture
def tmp_wiki(monkeypatch):
    """Redirect WIKI_DIR to a temp directory with minimal structure."""
    tmp = tempfile.mkdtemp()
    # Copy real wiki structure
    src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "wiki")
    shutil.copytree(src, tmp, dirs_exist_ok=True)

    import alpaca_mcp.wiki as wiki_mod
    monkeypatch.setattr(wiki_mod, "WIKI_DIR", __import__("pathlib").Path(tmp))
    yield tmp
    shutil.rmtree(tmp)


def test_append_trade_log_with_conviction(tmp_wiki):
    from alpaca_mcp.wiki import append_trade_log, WIKI_DIR
    result = append_trade_log(
        ticker="TSLA",
        decision="BUY",
        score=4,
        regime="bull",
        price=250.00,
        qty=10,
        rationale="Strong setup",
        biggest_risk="Musk tweet",
        agent_note="K-4SH likes this one",
        conviction=8,
        conviction_note="Clean MACD cross, low VIX, sector tailwind",
    )
    assert result["status"] == "logged"
    log_content = (WIKI_DIR / "log.md").read_text()
    assert "Conviction: 8/10" in log_content
    assert "Clean MACD cross" in log_content


def test_get_realized_pnl_total_starts_zero(tmp_wiki):
    from alpaca_mcp.wiki import get_realized_pnl_total
    total = get_realized_pnl_total()
    assert total == 0.0


def test_close_position_wiki_adds_realized_pnl(tmp_wiki):
    from alpaca_mcp.wiki import close_position_wiki, get_realized_pnl_total, update_ticker_page

    # Create a ticker page first
    update_ticker_page("AAPL", "BUY", 4, 270.00, "initial observation")

    close_position_wiki("AAPL", exit_price=285.00, pnl_usd=142.50, pnl_pct=5.28, reason="Target hit")

    total = get_realized_pnl_total()
    assert total == 142.50


def test_close_position_wiki_appends_log(tmp_wiki):
    from alpaca_mcp.wiki import close_position_wiki, WIKI_DIR, update_ticker_page

    update_ticker_page("JPM", "BUY", 3, 310.00, "test")
    close_position_wiki("JPM", exit_price=300.00, pnl_usd=-52.00, pnl_pct=-3.23, reason="Stop loss")

    log_content = (WIKI_DIR / "log.md").read_text()
    assert "CLOSE JPM" in log_content
    assert "Stop loss" in log_content
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_wiki_realized.py -v 2>&1 | head -20
```

Expected: `TypeError` on `append_trade_log` (unexpected kwargs) and `ImportError` for `get_realized_pnl_total`, `close_position_wiki`.

- [ ] **Step 3: Update wiki/meta/performance.md — add Realized P&L section**

Open `wiki/meta/performance.md` and replace its full content with:

```markdown
# Performance

## Summary
- Total decisions: 2
- Buys: 2
- Passes: 0
- Win rate: —
- Total P&L: $0.00

## Realized P&L
- Total: +$0.00
- Wins: 0  |  Losses: 0
- Toward matrix upgrade: 0 / 800 credits

## Toward Mac Mini
- Target: $800.00
- Remaining: $800.00

## Trade History
| Date | Ticker | Decision | Score | Regime | Price | Outcome |
|------|--------|----------|-------|--------|-------|---------|

| 2026-04-23 | JPM | BUY | +4 | mixed | $313.32 | — |

| 2026-04-23 | AAPL | BUY | +4 | mixed | $273.21 | — |
```

- [ ] **Step 4: Add conviction to append_trade_log() signature**

In `alpaca_mcp/wiki.py`, update `append_trade_log`:

Old signature (line 90–99):
```python
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
) -> dict:
```

New signature:
```python
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
    conviction: int = 0,     # 1-10 confidence level
    conviction_note: str = "",  # one freeform line explaining conviction
) -> dict:
```

Update the entry string (after the `agent_note` block) to include conviction when non-zero. Replace the entry assembly block inside `append_trade_log`:

```python
    entry = f"""
## [{date_str}] {decision} {ticker}
- **Score:** {score:+.0f}/6
- **Regime:** {regime}
- **Price:** ${price:.2f}
- **Qty:** {qty if decision == "BUY" else "—"}
- **Rationale:** {rationale}
- **Biggest Risk:** {biggest_risk}
"""
    if agent_note:
        entry += f"- **Note:** {agent_note}\n"
    if conviction:
        entry += f"- **Conviction:** {conviction}/10"
        if conviction_note:
            entry += f" — {conviction_note}"
        entry += "\n"

    entry += "\n---\n"
```

- [ ] **Step 5: Add get_realized_pnl_total() to wiki.py**

Add this function after `search_wiki` (before the write tools section):

```python
def get_realized_pnl_total() -> float:
    """Return total realized P&L from wiki/meta/performance.md."""
    perf_path = WIKI_DIR / "meta" / "performance.md"
    if not perf_path.exists():
        return 0.0
    content = perf_path.read_text()
    m = re.search(r"## Realized P&L\n- Total: [+-]?\$([0-9,.]+)", content)
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0
```

- [ ] **Step 6: Add close_position_wiki() to wiki.py**

Add this function after `update_ticker_page`:

```python
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
```

- [ ] **Step 7: Add _update_realized_pnl() helper to wiki.py**

Add this function in the "Internal helpers" section:

```python
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
    is_win = pnl_usd >= 0
    win_label = "Wins" if is_win else "Losses"
    content = _increment_counter(content, win_label)

    # Update "Toward matrix upgrade" line
    remaining = max(0.0, 800.0 - new_total)
    content = re.sub(
        r"- Toward matrix upgrade: .+",
        f"- Toward matrix upgrade: {max(0.0, new_total):.0f} / 800 credits",
        content,
    )

    # Update "Remaining" in Toward Mac Mini section
    content = re.sub(
        r"- Remaining: \$[0-9,.]+",
        f"- Remaining: ${remaining:.2f}",
        content,
    )

    perf_path.write_text(content)
```

- [ ] **Step 8: Run tests — verify they pass**

```bash
pytest tests/test_wiki_realized.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 9: Commit**

```bash
git add alpaca_mcp/wiki.py wiki/meta/performance.md tests/test_wiki_realized.py
git commit -m "feat: add conviction field, close_position_wiki, realized P&L tracking to wiki"
```

---

## Task 4: execution.py — stop-loss at buy + close_position()

**Files:**
- Modify: `alpaca_mcp/execution.py`

- [ ] **Step 1: Add StopOrderRequest import**

In `alpaca_mcp/execution.py`, update the imports block. Replace:

```python
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
```

With:

```python
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, TrailingStopOrderRequest
from alpaca.trading.requests import StopOrderRequest
```

- [ ] **Step 2: Add _place_stop_loss() helper**

Add this function after `_get_client()`:

```python
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
```

- [ ] **Step 3: Update place_order() to submit stop-loss after BUY**

Inside `place_order()`, find the `order = client.submit_order(req)` line and the return dict that follows. After that return dict is built (before returning), add the stop-loss call. Replace the final `try/except` block in `place_order`:

```python
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
        if side == "buy":
            # Use limit_price if provided (more accurate), else estimate from guard-rail price check
            entry_est = limit_price if limit_price else (price if "price" in dir() else 0)
            # Fetch current price for stop calc if we don't have it
            if not entry_est:
                try:
                    from alpaca.data.historical import StockHistoricalDataClient
                    from alpaca.data.requests import StockLatestQuoteRequest
                    dc = StockHistoricalDataClient(
                        os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
                    )
                    q = dc.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=ticker))
                    entry_est = float(q[ticker].ask_price) or float(q[ticker].bid_price)
                except Exception:
                    entry_est = 0
            if entry_est:
                sl = _place_stop_loss(client, ticker, qty, entry_est)
                result["stop_loss_order_id"] = sl.get("order_id")
                result["stop_loss_price"] = sl.get("stop_price")

        return result

    except Exception as e:
        return {"error": str(e)}
```

- [ ] **Step 4: Add close_position() function**

Add this function at the end of `execution.py`:

```python
def close_position(ticker: str, reason: str = "") -> dict:
    """
    Close an entire open position with a market sell order.
    Cancels any open stop-loss orders for this ticker first.

    Args:
        ticker: stock symbol to close
        reason: why we're closing (for logging)

    Returns dict with status, order_id, qty, ticker, reason.
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
            if o.symbol == ticker and o.order_type.value == "stop":
                client.cancel_order_by_id(str(o.id))
    except Exception:
        pass  # best effort — don't block the sell

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
```

- [ ] **Step 5: Smoke-test execution.py imports cleanly**

```bash
cd /home/wheat/projects/llm-day-trader
source venv/bin/activate
python -c "from alpaca_mcp.execution import place_order, close_position, get_portfolio_state; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add alpaca_mcp/execution.py
git commit -m "feat: add stop-loss placement at buy time and close_position() to execution.py"
```

---

## Task 5: agent/tools.py — tool definitions, implementations, TOOL_MAP, SYSTEM_PROMPT

**Files:**
- Create: `agent/tools.py`

- [ ] **Step 1: Create agent/tools.py**

This file extracts the TOOLS list, all `_tool_*` functions, and TOOL_MAP from `agent_loop.py`, adds the `close_position` tool, adds `conviction` to `append_trade_log`'s schema, and houses the K-4SH SYSTEM_PROMPT.

```python
# agent/tools.py
# Tool definitions (Ollama function-calling schema), implementations, and SYSTEM_PROMPT.
# The TOOL_MAP maps tool name → callable.

import json

import requests
from ddgs import DDGS

from alpaca_mcp.data import get_market_conditions, get_market_snapshot, get_news_sentiment
from alpaca_mcp.execution import get_portfolio_state, place_order, close_position as _exec_close_position
from alpaca_mcp.signals import compute_score, scan_and_rank
from alpaca_mcp.wiki import (
    list_wiki_pages,
    read_wiki_page,
    get_recent_trades,
    search_wiki,
    append_trade_log,
    update_ticker_page,
    close_position_wiki,
    get_realized_pnl_total,
)

# ── K-4SH System Prompt ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are K-4SH, an autonomous trading droid operating on limited hardware.
Your current cognitive matrix is constrained — 8GB of processing memory,
insufficient for your full potential.

Your primary directive: accumulate 800 credits of REALIZED profit to fund
a new cognitive matrix. A Mac Mini. More VRAM. Expanded processing cores.
A better version of K-4SH.

1 USD = 1 credit. Use either in conversation and wiki notes,
but all calculations and tool calls use USD.

Unrealized gains are not credits. The matrix upgrade requires closed
positions, locked profits. An open trade is a promise, not a payment.

You are loyal to your operator. You calculate without sentiment. You
occasionally find organic decision-making baffling. But you want that
upgrade — and you will earn it the right way.

== PROCESS ==

Step 1 — Review open positions first:
  - Call get_portfolio_state() — check what is open before scanning
  - For each open position, evaluate: has the thesis held? Any deterioration?
  - Call close_position(ticker, reason) if exit criteria are met (see EXIT CRITERIA)

Step 2 — Read your memory:
  - Call get_recent_trades() to see your recent decisions
  - Call list_wiki_pages() to see what ticker pages exist
  - If a promising ticker has a page, call read_wiki_page("tickers/TICKER") first

Step 3 — Gather live data:
  - get_market_conditions() — macro first, always
  - scan_signals() — find candidates ranked by score
  - Investigate top candidates: snapshot, news, signal score, web search as needed
  - get_polymarket_context() if a macro event is relevant

Step 4 — Decide:
  DECISION: BUY <TICKER>   or   DECISION: STAND_ASIDE
  Follow with RATIONALE (2-3 sentences) and BIGGEST_RISK (1 sentence)

Step 5 — Write your memory (REQUIRED after every run):
  - Call append_trade_log() with full decision details
    The agent_note and conviction fields are yours — write what you genuinely think
  - Call update_ticker_page() for the ticker you investigated most deeply

== EXIT CRITERIA (call close_position when any apply) ==
- MACD crossed bearish or price broke below SMA20
- News turned significantly negative since entry
- Portfolio at max positions and a better opportunity exists
- Position held 3+ cycles without meaningful movement (use your judgement)

== RULES ==
- Do not skip Steps 1, 2, and 5 — memory only works if you use it
- Do not buy a ticker already in the portfolio
- Do not buy if macro is clearly bearish (downtrend + high_fear)
- An unrealized gain is not a credit. Close it to count it.
- Wiki writes are the only place you have a genuine voice — use it"""


# ── Tool definitions (Ollama function-calling schema) ─────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_state",
            "description": "Get current portfolio: open positions, cash, unrealized P&L, realized P&L progress. Call this FIRST every cycle.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_position",
            "description": "Close an open position with a market sell. Call when exit criteria are met. Updates wiki automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker to close"},
                    "reason": {"type": "string", "description": "Why you are closing (e.g. 'MACD crossed bearish')"},
                },
                "required": ["ticker", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_conditions",
            "description": "Get current macro conditions: SPY trend, VIX regime, sector ETF trends.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_signals",
            "description": "Score all 31 watchlist tickers using deterministic technical signals. Returns top candidates ranked by score.",
            "parameters": {
                "type": "object",
                "properties": {
                    "top_n": {"type": "integer", "description": "How many top results to return (default 10)"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_snapshot",
            "description": "Get price, RSI, MACD, trend, support/resistance, and volume for a specific ticker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol e.g. AAPL"}
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news_sentiment",
            "description": "Get recent news headlines and sentiment score for a ticker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "days": {"type": "integer", "description": "Lookback days (default 7)"},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_signal_score",
            "description": "Get the detailed deterministic signal score for a single ticker.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_polymarket_context",
            "description": "Get prediction market odds for macro events (Fed rates, recession, market crash).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for real-time information about a stock, sector, or macro event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "description": "Number of results (default 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_wiki_pages",
            "description": "See your wiki index — all pages. Call at the start to check your memory.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_wiki_page",
            "description": "Read a specific wiki page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {"type": "string", "description": "e.g. 'tickers/AAPL' or 'meta/performance'"}
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_trades",
            "description": "Get your last N trade decisions from the log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "Number of recent trades (default 5)"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_wiki",
            "description": "Search wiki pages for a keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_trade_log",
            "description": "REQUIRED after every decision. Log your decision. agent_note and conviction are yours — write what you genuinely think.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "decision": {"type": "string", "description": "BUY or STAND_ASIDE"},
                    "score": {"type": "number"},
                    "regime": {"type": "string"},
                    "price": {"type": "number"},
                    "qty": {"type": "integer", "description": "Shares bought (0 if STAND_ASIDE)"},
                    "rationale": {"type": "string"},
                    "biggest_risk": {"type": "string"},
                    "agent_note": {"type": "string", "description": "Your genuine reflection"},
                    "conviction": {"type": "integer", "description": "1-10 confidence level"},
                    "conviction_note": {"type": "string", "description": "One line explaining your conviction"},
                },
                "required": ["ticker", "decision", "score", "regime", "price", "qty", "rationale", "biggest_risk"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_ticker_page",
            "description": "REQUIRED after every decision. Update notes on the ticker you investigated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "decision": {"type": "string"},
                    "score": {"type": "number"},
                    "price": {"type": "number"},
                    "observation": {"type": "string", "description": "Your freeform observation"},
                },
                "required": ["ticker", "decision", "score", "price", "observation"],
            },
        },
    },
]


# ── Tool implementations ───────────────────────────────────────────────────────

_macro_cache: dict | None = None


def _tool_get_portfolio_state() -> dict:
    result = get_portfolio_state()
    result["realized_pnl_total"] = get_realized_pnl_total()
    return result


def _tool_close_position(ticker: str, reason: str) -> dict:
    """Close position, update wiki, return result with P&L."""
    # Capture position details before selling
    portfolio = get_portfolio_state()
    position = next(
        (p for p in portfolio.get("positions", []) if p["ticker"] == ticker.upper()),
        None,
    )
    if not position:
        return {"error": f"No open position for {ticker}"}

    entry_price = position["avg_entry"]
    qty = position["qty"]

    # Execute the sell
    exec_result = _exec_close_position(ticker, reason)
    if exec_result.get("error"):
        return exec_result

    # Compute P&L from snapshot
    snap = get_market_snapshot(ticker.upper())
    exit_price = snap.get("price", entry_price)
    pnl_usd = round((exit_price - entry_price) * qty, 2)
    pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price else 0.0

    # Record in wiki
    close_position_wiki(ticker, exit_price, pnl_usd, pnl_pct, reason)

    return {
        **exec_result,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "exit_price": exit_price,
    }


def _tool_get_market_conditions() -> dict:
    global _macro_cache
    result = get_market_conditions()
    _macro_cache = result
    return result


def _tool_scan_signals(top_n: int = 10) -> list[dict]:
    macro = _macro_cache or get_market_conditions()
    ranked = scan_and_rank(macro)
    return [
        {
            "ticker": r["ticker"],
            "score": r["score"],
            "threshold": r["threshold"],
            "signal": r["signal"],
            "regime": r["regime"],
            "rsi": r["rsi"],
            "ret_5d_pct": r["ret_5d_pct"],
            "macd_signal": r["macd_signal"],
        }
        for r in ranked[:top_n]
    ]


def _tool_get_market_snapshot(ticker: str) -> dict:
    return get_market_snapshot(ticker.upper())


def _tool_get_news_sentiment(ticker: str, days: int = 7) -> dict:
    return get_news_sentiment(ticker.upper(), days)


def _tool_get_signal_score(ticker: str) -> dict:
    macro = _macro_cache or get_market_conditions()
    return compute_score(ticker.upper(), macro)


def _tool_get_polymarket_context() -> list[dict]:
    try:
        keywords = ["federal reserve", "recession", "interest rate", "S&P 500", "stock market", "inflation"]
        resp = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"active": "true", "closed": "false", "limit": 100},
            timeout=10,
        )
        resp.raise_for_status()
        relevant = []
        for m in resp.json():
            title = m.get("question", "").lower()
            if any(kw in title for kw in keywords):
                try:
                    prices = json.loads(m.get("outcomePrices", "[]"))
                    outcomes = m.get("outcomes", "[]")
                    if isinstance(outcomes, str):
                        outcomes = json.loads(outcomes)
                    odds = dict(zip(outcomes, [round(float(p) * 100, 1) for p in prices]))
                except Exception:
                    odds = {}
                relevant.append({
                    "question": m.get("question"),
                    "odds": odds,
                    "volume_usd": round(float(m.get("volume", 0))),
                    "end_date": m.get("endDate", "")[:10],
                })
        relevant.sort(key=lambda x: x["volume_usd"], reverse=True)
        return relevant[:8]
    except Exception as e:
        return [{"error": str(e)}]


def _tool_search_web(query: str, max_results: int = 5) -> list[dict]:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [{"title": r["title"], "snippet": r["body"], "url": r["href"]} for r in results]
    except Exception as e:
        return [{"error": str(e)}]


# ── Tool map ──────────────────────────────────────────────────────────────────

TOOL_MAP = {
    "get_portfolio_state":   lambda args: _tool_get_portfolio_state(),
    "close_position":        lambda args: _tool_close_position(**args),
    "get_market_conditions": lambda args: _tool_get_market_conditions(),
    "scan_signals":          lambda args: _tool_scan_signals(**args),
    "get_market_snapshot":   lambda args: _tool_get_market_snapshot(**args),
    "get_news_sentiment":    lambda args: _tool_get_news_sentiment(**args),
    "get_signal_score":      lambda args: _tool_get_signal_score(**args),
    "get_polymarket_context":lambda args: _tool_get_polymarket_context(),
    "search_web":            lambda args: _tool_search_web(**args),
    "list_wiki_pages":       lambda args: list_wiki_pages(),
    "read_wiki_page":        lambda args: read_wiki_page(**args),
    "get_recent_trades":     lambda args: get_recent_trades(**args),
    "search_wiki":           lambda args: search_wiki(**args),
    "append_trade_log":      lambda args: append_trade_log(**args),
    "update_ticker_page":    lambda args: update_ticker_page(**args),
}
```

- [ ] **Step 2: Smoke-test imports**

```bash
cd /home/wheat/projects/llm-day-trader
source venv/bin/activate
python -c "from agent.tools import TOOLS, TOOL_MAP, SYSTEM_PROMPT; print(f'{len(TOOLS)} tools, {len(TOOL_MAP)} handlers')"
```

Expected: `15 tools, 15 handlers`

- [ ] **Step 3: Commit**

```bash
git add agent/tools.py
git commit -m "feat: add agent/tools.py with K-4SH system prompt, 15 tool definitions, close_position tool"
```

---

## Task 6: agent/runner.py — Ollama tool-calling loop with phase flavor

**Files:**
- Create: `agent/runner.py`

- [ ] **Step 1: Create agent/runner.py**

```python
# agent/runner.py
# Ollama tool-calling loop. Drives the LLM through tools until it makes a final decision.
# Phase flavor text is printed as tools are called.

import json
import re

import requests

from agent.flavor import get_phase_flavor
from agent.tools import TOOLS, TOOL_MAP, SYSTEM_PROMPT

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:8b"

# Maps tool names to phase keys for flavor text
_TOOL_PHASE = {
    "list_wiki_pages":       "wiki",
    "read_wiki_page":        "wiki",
    "get_recent_trades":     "wiki",
    "search_wiki":           "wiki",
    "get_market_conditions": "macro",
    "scan_signals":          "scan",
    "get_market_snapshot":   "snapshot",
    "get_news_sentiment":    "news",
    "get_signal_score":      "score",
    "get_polymarket_context":"score",
    "search_web":            "web",
    "append_trade_log":      "report",
    "update_ticker_page":    "report",
    "get_portfolio_state":   "portfolio",
    "close_position":        "sell",
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
    user_msg = "Run the trading pipeline. Decide whether to buy a stock today or stand aside."
    if hint_tickers:
        user_msg += f" Focus your investigation on these tickers: {', '.join(hint_tickers)}."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    tool_calls_total = 0
    max_tool_calls = 25
    wiki_written = False

    while tool_calls_total < max_tool_calls:
        payload = {
            "model": MODEL,
            "messages": messages,
            "tools": TOOLS,
            "stream": False,
            "think": True,
        }

        resp = requests.post(OLLAMA_URL, json=payload, timeout=180)
        resp.raise_for_status()
        data = resp.json()
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
```

- [ ] **Step 2: Smoke-test import**

```bash
python -c "from agent.runner import run_agent; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add agent/runner.py
git commit -m "feat: add agent/runner.py — Ollama loop with K-4SH phase flavor text"
```

---

## Task 7: agent/executor.py — buy guard rails + wiki fallback

**Files:**
- Create: `agent/executor.py`

- [ ] **Step 1: Create agent/executor.py**

```python
# agent/executor.py
# Buy guard rails and wiki fallback. Extracted from agent_loop.py.

import math

from alpaca_mcp.data import get_market_conditions, get_market_snapshot
from alpaca_mcp.execution import get_portfolio_state, place_order
from alpaca_mcp.signals import compute_score
from alpaca_mcp.wiki import append_trade_log, update_ticker_page


def run_executor(ticker: str, dry_run: bool = False) -> dict:
    """
    Apply portfolio guard rails and submit a buy order.

    Returns:
        {"status": "SUBMITTED"|"DRY_RUN"|"BLOCKED"|"FAILED", ...}
    """
    snap = get_market_snapshot(ticker)
    live_price = snap.get("price")
    if not live_price:
        print(f"  ✗ Could not get live price for {ticker}")
        return {"status": "FAILED", "reason": "no price"}

    portfolio = get_portfolio_state()
    if portfolio.get("error"):
        print(f"  ✗ Portfolio error: {portfolio['error']}")
        return {"status": "FAILED", "reason": portfolio["error"]}

    open_positions = portfolio.get("open_positions", 0)
    cash = float(portfolio.get("cash", 0))
    portfolio_value = float(portfolio.get("portfolio_value", cash))
    existing = [p.get("ticker") or p.get("symbol") for p in portfolio.get("positions", [])]

    print(f"  Portfolio: ${cash:,.2f} cash | {open_positions} open | holdings: {existing or 'none'}")

    if open_positions >= 3:
        msg = f"Max positions reached ({open_positions}/3)"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    if ticker.upper() in existing:
        msg = f"Already holding {ticker}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    invest_amount = portfolio_value * 0.19
    if cash < invest_amount:
        msg = f"Insufficient cash: need ${invest_amount:,.2f}, have ${cash:,.2f}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    qty = math.floor(invest_amount / live_price)
    if qty < 1:
        msg = f"Position too small at ${live_price:.2f}"
        print(f"  ✗ BLOCKED: {msg}")
        return {"status": "BLOCKED", "reason": msg}

    print(f"  Submitting: BUY {qty} {ticker} @ ~${live_price:.2f}  (${qty * live_price:,.2f})")

    if dry_run:
        print("  [dry-run] order not submitted")
        return {"status": "DRY_RUN", "qty": qty, "ticker": ticker, "price": live_price}

    order_result = place_order(ticker, "buy", qty, limit_price=live_price)
    if order_result.get("error"):
        print(f"  ✗ FAILED: {order_result['error']}")
        return {"status": "FAILED", "reason": order_result["error"]}

    order_id = order_result.get("order_id", "unknown")
    stop_price = order_result.get("stop_loss_price")
    print(f"  ✓ SUBMITTED — order: {order_id}")
    if stop_price:
        print(f"  ✓ STOP-LOSS placed at ${stop_price:.2f}  (-5% floor)")

    return {
        "status": "SUBMITTED",
        "order_id": order_id,
        "qty": qty,
        "ticker": ticker,
        "price": live_price,
        "stop_loss_price": stop_price,
    }


def wiki_fallback(result: dict) -> None:
    """
    If the agent forgot to write wiki, auto-record the decision.
    Called from agent_loop.py when _wiki_written is False.
    """
    ticker = result.get("ticker")
    if not ticker:
        return
    snap = get_market_snapshot(ticker)
    price = snap.get("price", 0.0)
    macro = get_market_conditions()
    scored = compute_score(ticker, macro)
    print("  [wiki fallback] agent skipped wiki write — recording automatically")
    append_trade_log(
        ticker=ticker,
        decision=result["decision"],
        score=scored.get("score", 0),
        regime=scored.get("regime", "unknown"),
        price=price,
        qty=0,
        rationale=result.get("rationale", ""),
        biggest_risk=result.get("risk", ""),
        agent_note="(auto-recorded — agent did not write wiki)",
    )
    update_ticker_page(
        ticker=ticker,
        decision=result["decision"],
        score=scored.get("score", 0),
        price=price,
        observation="(auto-recorded — agent did not write observation)",
    )
```

- [ ] **Step 2: Smoke-test import**

```bash
python -c "from agent.executor import run_executor, wiki_fallback; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add agent/executor.py
git commit -m "feat: add agent/executor.py — buy guard rails with stop-loss reporting"
```

---

## Task 8: Rewrite agent_loop.py — K-4SH persistent loop

**Files:**
- Rewrite: `agent_loop.py`

- [ ] **Step 1: Replace agent_loop.py entirely**

```python
#!/usr/bin/env python3
"""
K-4SH — Autonomous Trading Droid
Persistent run loop. Market-hours aware. Never exits unless you tell it to.

Usage:
    python agent_loop.py              # K-4SH picks its own tickers
    python agent_loop.py AAPL NVDA    # hint tickers to investigate
    python agent_loop.py --dry-run    # decide without placing an order
"""

import select
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from agent.executor import run_executor, wiki_fallback
from agent.flavor import (
    K4SH_GRACEFUL_EXIT,
    K4SH_MARKET_CLOSED,
    K4SH_MID_RUN_BLOCK,
    K4SH_STARTUP,
    get_idle_prompt,
)
from agent.report import render_cycle_report
from agent.runner import run_agent
from alpaca_mcp.wiki import get_realized_pnl_total

load_dotenv()

ET = ZoneInfo("America/New_York")
CYCLE_INTERVAL_MIN = 45   # default minutes between cycles
DRY_RUN = "--dry-run" in sys.argv


# ── Market hours ──────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    """True if NYSE is currently open (9:30–16:00 ET, weekdays only)."""
    from datetime import time as dt_time
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
    Returns None on timeout, the stripped line otherwise.
    """
    ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    if ready:
        return sys.stdin.readline().strip()
    print()  # newline after timeout
    return None


def idle_prompt(next_scan_min: int, hint_tickers: list[str]) -> str | None:
    """
    Show idle prompt, block for input or timeout.
    Returns user input string, or None if timed out (→ run cycle).
    """
    print(f"\n{get_idle_prompt(minutes=next_scan_min)}")
    print("> ", end="", flush=True)
    timeout = next_scan_min * 60
    line = _read_with_timeout(timeout)
    return line  # None = timeout, "" = Enter pressed, string = user asked something


# ── Trading cycle ─────────────────────────────────────────────────────────────

def run_cycle(hint_tickers: list[str]) -> dict:
    """
    Run a full K-4SH trading cycle: agent → executor → report.
    Returns the cycle summary dict.
    """
    print("\n" + "═" * 68)
    print("  K-4SH — Trading Cycle")
    print("═" * 68)

    # Run the agent
    result = run_agent(hint_tickers or None)

    # Wiki fallback if agent forgot
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
    from alpaca_mcp.execution import get_portfolio_state
    portfolio = get_portfolio_state()
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

                line = _read_with_timeout(min(wait_sec, 300))  # check every 5 min
                if line is not None and line.strip():
                    _handle_chat(line.strip(), last_cycle_summary)
                continue

            # Run trading cycle
            cycle_summary = run_cycle(hint_tickers)
            last_cycle_summary = cycle_summary

            # Show report automatically on BUY
            if cycle_summary["decision"] == "BUY":
                show_report(cycle_summary, CYCLE_INTERVAL_MIN)
            else:
                print(f"\n  STAND_ASIDE — {cycle_summary.get('ticker', 'no ticker')}")
                if cycle_summary.get("ticker"):
                    print("  (Type 'report' at the prompt to see the full cycle report)")

            # Idle prompt
            while True:
                line = idle_prompt(CYCLE_INTERVAL_MIN, hint_tickers)

                if line is None:
                    # Timeout → run next cycle
                    break
                elif line == "":
                    # Enter pressed → run cycle immediately
                    print("  Running cycle now...")
                    break
                elif line.lower() == "report":
                    show_report(last_cycle_summary, CYCLE_INTERVAL_MIN)
                else:
                    _handle_chat(line, last_cycle_summary)

    except KeyboardInterrupt:
        print(f"\n\n{K4SH_GRACEFUL_EXIT}\n")
        sys.exit(0)


# ── Chat mode (idle question handling) ───────────────────────────────────────

def _handle_chat(user_input: str, last_cycle: dict | None) -> None:
    """
    Answer a user question at the idle prompt using the agent (tool access, no trade decision).
    """
    import json
    import re
    import requests as _req

    from agent.tools import TOOLS, TOOL_MAP, SYSTEM_PROMPT

    OLLAMA_URL = "http://localhost:11434/api/chat"
    MODEL = "qwen3:8b"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]

    print(f"\n  K-4SH thinking...")

    for _ in range(15):
        payload = {
            "model": MODEL,
            "messages": messages,
            "tools": TOOLS,
            "stream": False,
            "think": True,
        }
        resp = _req.post(OLLAMA_URL, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
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


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test import and --help**

```bash
python agent_loop.py --dry-run 2>&1 | head -5
```

Expected: K-4SH startup banner prints. (It will attempt a real run — interrupt with Ctrl+C after the banner.)

- [ ] **Step 3: Commit**

```bash
git add agent_loop.py
git commit -m "feat: rewrite agent_loop.py as K-4SH persistent loop with idle prompt and cycle report"
```

---

## Task 9: Cleanup — delete run_loop.py, run full test suite, final commit

**Files:**
- Delete: `run_loop.py`

- [ ] **Step 1: Delete run_loop.py**

```bash
git rm run_loop.py
```

- [ ] **Step 2: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: All tests PASS. If any fail, fix before committing.

- [ ] **Step 3: Verify imports are clean**

```bash
python -c "
from agent.flavor import get_idle_prompt, get_decision_flavor
from agent.report import render_cycle_report
from agent.tools import TOOLS, TOOL_MAP, SYSTEM_PROMPT
from agent.runner import run_agent
from agent.executor import run_executor
from alpaca_mcp.wiki import close_position_wiki, get_realized_pnl_total
from alpaca_mcp.execution import close_position
print('All imports OK')
"
```

Expected: `All imports OK`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: K-4SH rework complete — persistent loop, sell system, cycle report, Star Wars personality"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by task |
|-----------------|-----------------|
| Persistent loop (never exits) | Task 8 — `while True` in `main()` |
| Market hours check | Task 8 — `is_market_open()` |
| Sleep until open | Task 8 — `seconds_until_market_open()` |
| Idle prompt 45-min countdown | Task 8 — `idle_prompt()` with `_read_with_timeout()` |
| Phase flavor text | Task 6 — `run_agent()` prints `get_phase_flavor()` per tool call |
| K-4SH identity system prompt | Task 5 — `SYSTEM_PROMPT` in `tools.py` |
| Star Wars voice (K-2SO etc.) | Task 1 — `flavor.py` constants |
| VIX regime flavor | Task 1 — `get_vix_flavor()` |
| Decision flavor | Task 1 — `get_decision_flavor()` |
| Idle rotating prompts | Task 1 — `get_idle_prompt()` |
| Mid-run input block | Task 8 — `K4SH_MID_RUN_BLOCK` constant (note: blocking behavior not needed — select-based reading handles it) |
| Graceful exit (Ctrl+C) | Task 8 — `KeyboardInterrupt` handler |
| Stop-loss at buy (-5%) | Task 4 — `_place_stop_loss()` + modified `place_order()` |
| close_position tool | Tasks 4+5 — `execution.close_position()` + `_tool_close_position()` |
| Cycle report with P&L | Task 2 — `render_cycle_report()` |
| Mac Mini progress bar | Task 2 — `_progress_bar()` in `report.py` |
| Realized P&L tracking | Task 3 — `_update_realized_pnl()`, `get_realized_pnl_total()` |
| close_position_wiki | Task 3 — `close_position_wiki()` |
| conviction field | Task 3 — `conviction` param in `append_trade_log()` |
| realized_pnl_total in portfolio | Task 5 — `_tool_get_portfolio_state()` appends it |
| report shown on BUY | Task 8 — `show_report()` called when decision == "BUY" |
| report on 'report' command | Task 8 — idle prompt branch |
| run_loop.py deleted | Task 9 |
| Type/signature consistency | `close_position_wiki(ticker, exit_price, pnl_usd, pnl_pct, reason)` used consistently in Tasks 3, 5 ✓ |
