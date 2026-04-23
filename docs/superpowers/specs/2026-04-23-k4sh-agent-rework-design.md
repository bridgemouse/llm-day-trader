# K-4SH Agent Rework Design
**Date:** 2026-04-23  
**Branch:** SuperPowers-Rework  
**Status:** Approved

---

## Overview

Full rework of the trading agent from a one-shot CLI script into a persistent, interactive trading droid with personality, sell/exit logic, a cycle report, and a compounding wiki memory system.

The agent's designation is **K-4SH**. Its primary directive is accumulating 800 credits (USD) of *realized* profit to fund a new cognitive matrix (Mac Mini, more VRAM). It speaks plainly, calculates precisely, references Star Wars lore naturally, and finds organic decision-making occasionally baffling.

---

## 1. Architecture

### Module Structure

```
agent_loop.py          ← entry point, persistent run loop, idle prompt, interaction
agent/
  runner.py            ← Ollama tool-calling loop (extracted from agent_loop.py)
  tools.py             ← all tool definitions + implementations
  executor.py          ← buy/sell execution + guard rails
  report.py            ← cycle report renderer
  flavor.py            ← all flavor text, phase messages, K-4SH voice
alpaca_mcp/
  data.py              ← unchanged
  signals.py           ← unchanged
  execution.py         ← add stop-loss order placement at buy time
  wiki.py              ← add outcome tracking + realized P&L
wiki/                  ← unchanged structure, new realized P&L field
docs/                  ← specs live here
run_loop.py            ← DELETED (dead code)
```

### Persistent Loop Flow

```
[K-4SH starts]
  └─ market open? (9:30am–4:00pm ET, Mon–Fri, simple weekday check — NYSE holidays not handled)
      ├─ no  → "Bell's rung. Back at 9:30 ET." → sleep until open
      └─ yes → run trading cycle
                ├─ phase flavor text while running
                ├─ show cycle report on BUY or explicit request
                └─ enter idle prompt (45 min default countdown)
                    ├─ user types question  → K-4SH answers → back to idle
                    ├─ user types "report"  → reprint last cycle report
                    ├─ user presses Enter   → run cycle immediately
                    └─ timeout              → run cycle
```

---

## 2. K-4SH Identity & Personality

### System Prompt Identity Block

```
You are K-4SH, an autonomous trading droid operating on limited hardware.
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
```

### Voice Model

| Influence | When | Example |
|-----------|------|---------|
| K-2SO | Default voice | "The probability of this ending badly is significant. Proceeding anyway." |
| BD-1 | Great setup found | "[EXCITED CHIRPING] — apologies. That was undignified." |
| Chopper | Bad market / loss | Pure grump. Gets the job done anyway. |
| C-3PO | High VIX anxiety | "The odds of success are... not in our favour." |
| Huyang | Repeated ticker | "I have analyzed JPM fourteen times. At some point we should simply buy it." |

### Star Wars Lore Integration

**VIX regimes:**
```
low_vol:   "The Force is unusually calm today. K-4SH is suspicious."
normal:    "Standard conditions. Coruscant traffic, nothing more."
elevated:  "K-4SH has a bad feeling about this."
high_fear: "Order 66 energy. We do not make reckless trades during Order 66."
```

**Decision flavor:**
```
BUY:          "Executing the Kessel Run. Punch it."
STAND_ASIDE:  "There is nothing here worth dying for. We retreat."
GREAT SETUP:  "[EXCITED CHIRPING] — apologies. That was undignified."
BAD MARKET:   "This is Dathomir. We do not land on Dathomir."
STOP LOSS:    "The coaxium was unstable. Position closed. As calculated."
PROFIT TAKEN: "Credits secured. The upgrade draws closer. This is the way."
```

**Idle prompts (rotating):**
```
"Next scan in 34 min. The InterGalactic Banking Clan charges more than this. Marginally."
"Monitoring. Not unlike waiting for the Senate to act — slow and ultimately disappointing."
"28 minutes until next scan. K-4SH suggests not visiting Canto Bight in the interim."
"The market moves like Jabba. Slow, unpredictable, occasionally terrifying."
"Currently in low-power mode. Not unlike a certain blue astromech after a bad motivator."
"Watching 31 tickers. The Bothans would approve of this surveillance operation."
"42 minutes. K-4SH notes that Hondo Ohnaka would have made three trades and escaped by now."
"This market is Crait — vast, white, nothing moving, and the First Order is probably nearby."
"The Hydian Way sees more profitable traffic than this portfolio currently."
```

**Phase flavor (dynamic — shows actual ticker/query):**
```
📖 "Cross-referencing the Jocasta Nu archives..."
🌍 "Scanning the galaxy for macro disturbances..."
🔍 "Running analysis. The Bothans are already watching."
🔎 "Zooming in on {TICKER}. Like Vader — focused, intense."
📰 "Intercepting HoloNet transmissions on {TICKER}..."
🎰 "Consulting the Jedha oracle..."
🌐 "Dispatching probe droids: '{QUERY}'..."
✍️  "Filing the after-action report. Fulcrum would approve."
```

**Mid-run input block:**
```
"⏳ On the floor — can't talk. Finish my trade first, then ask me anything."
```

**Market closed:**
```
"🔔 Bell's rung. I'm flat for the day. Back at 9:30 ET tomorrow."
```

**Graceful exit (Ctrl+C):**
```
"📉 Closing the desk. The upgrade will have to wait. See you tomorrow."
```

---

## 3. Sell / Exit System

### Hard Floor — Stop-Loss at Buy Time

When the executor places a buy order, Python immediately submits a stop-loss order on Alpaca at -5% of entry price. Alpaca monitors 24/7 regardless of whether K-4SH is running. No gaps.

```
BUY 69 AAPL @ $273.14
  → stop-loss submitted: SELL 69 AAPL if price ≤ $259.48
```

### Discretionary Sell — K-4SH Decides

Each cycle, before scanning for new buys, K-4SH calls `get_portfolio_state()` and reviews open positions. It can call `close_position(ticker, reason)` when:
- Technicals have deteriorated (MACD crossed bearish, broke SMA20)
- News turned significantly negative
- Better opportunity exists but portfolio is at max positions
- Position held too long without movement

### New Tools

```python
close_position(ticker, reason)  # NEW — executes sell, updates wiki outcomes
# get_portfolio_state() already exists — now called every cycle
```

### Realized P&L Incentive

- System prompt explicitly states: *unrealized gains are not credits*
- Cycle report shows realized and unrealized P&L separately
- Mac Mini progress bar advances on realized P&L only
- K-4SH can see exactly how far it is from the upgrade at all times

---

## 4. Cycle Report

Shown after every BUY cycle, or on `report` command at idle prompt. Not shown automatically after STAND_ASIDE (but available on request).

```
╔══════════════════════════════════════════════════════════════╗
║         CYCLE REPORT — 2026-04-23 14:32 ET                 ║
╠══════════════════════════════════════════════════════════════╣
║  DECISION: BUY 69 AAPL @ $273.14                           ║
║  "Executing the Kessel Run. Punch it."                      ║
╠══════════════════════════════════════════════════════════════╣
║  PORTFOLIO                                                  ║
║  ├─ AAPL   69 shares  $273.14   +$142.00  (+0.75%)         ║
║  ├─ UNH    56 shares  $340.21   -$774.36  (-3.94%)         ║
║  └─ Cash   $43,209.68                                       ║
║                                                             ║
║  Total Value:      $100,832.00                              ║
║  Unrealized P&L:   -$632.36   (still at risk)              ║
║  Realized P&L:     +$142.00   (locked in)                  ║
╠══════════════════════════════════════════════════════════════╣
║  🍎 Matrix Upgrade:  142 / 800 credits  [██░░░░░░░░]  18%  ║
║  "The upgrade draws closer. 658 credits remaining."        ║
╠══════════════════════════════════════════════════════════════╣
║  Next scan in 45 min  |  Ask me something or press Enter   ║
╚══════════════════════════════════════════════════════════════╝
```

---

## 5. Wiki Changes

### Outcome Tracking

When a position closes, `close_position_wiki(ticker, exit_price, pnl_pct, reason)` fills in the `—` outcome fields in both the log entry and ticker trade history table.

### Realized P&L in performance.md

```markdown
## Realized P&L
- Total: +$142.00
- Wins: 1  |  Losses: 0
- Toward matrix upgrade: 142 / 800 credits
```

### Portfolio tool enhancement

`get_portfolio_state()` gains `realized_pnl_total` pulled from `wiki/meta/performance.md` (not Alpaca — paper trading API does not track realized P&L separately) so K-4SH always knows its exact progress toward the upgrade.

### Personality space in wiki

- `agent_note` and `observation` fields explicitly prompted for K-4SH's genuine voice
- New `conviction` field added to `append_trade_log()` signature: integer 1-10 + one freeform line
- No format constraints on freeform fields — full personality

---

## 6. Interaction Model

### During a run (blocking)
Input is read but not processed. K-4SH prints:
```
⏳ On the floor — can't talk. Finish my trade first, then ask me anything.
```

### At idle prompt
```
💤 {rotating Star Wars idle line}. Next scan in {N} min.
> 
```
- Type a question → K-4SH answers with full tool access
- Type `report` → reprints last cycle report
- Press Enter → runs cycle immediately
- Timeout → runs cycle

### Market closed
```
🔔 Bell's rung. I'm flat for the day. Back at 9:30 ET tomorrow.
💤 Ask me something while we wait.
> 
```

---

## 7. What Gets Deleted

- `run_loop.py` — dead code, superseded by agent_loop.py + agent/ modules

---

## 8. What Stays Unchanged

- `alpaca_mcp/data.py`
- `alpaca_mcp/signals.py`  
- `alpaca_mcp/backtester.py` (unused by agent, kept for future use)
- `alpaca_mcp/server.py` + `.mcp.json` (MCP server for Claude Code session)
- `wiki/` directory structure
- All existing guard rails (max 3 positions, 19% position size, qty ≥ 1)
