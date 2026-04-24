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
_PHASE_FLAVOR: dict[str, list[str]] = {
    "wiki": [
        "📖 Cross-referencing the Jocasta Nu archives...",
        "📖 Consulting the holocron. Jocasta Nu would be proud.",
        "📖 Pulling the scrolls. Every trade leaves a record.",
        "📖 Digging through the stacks. The archives never lie.",
    ],
    "macro": [
        "🌍 Scanning the galaxy for macro disturbances...",
        "🌍 Reading the currents. The Force flows through the tape.",
        "🌍 Checking the weather on Coruscant. VIX is the wind.",
        "🌍 Macro conditions incoming. Strap in.",
    ],
    "scan": [
        "🔍 Running analysis. The Bothans are already watching.",
        "🔍 Scoring the field. Many tickers. Few worthy.",
        "🔍 Signal sweep active. Looking for a clean setup.",
        "🔍 The Bothan network reports. Casualties: several bad tickers.",
    ],
    "snapshot": [
        "🔎 Zooming in on {ticker}. Like Vader — focused, intense.",
        "🔎 Pulling the tape on {ticker}. Every candle tells a story.",
        "🔎 {ticker} under the scope. No chart escapes K-4SH.",
        "🔎 Locking onto {ticker}. Target acquired.",
        "🔎 Running diagnostics on {ticker}. This is what I was built for.",
    ],
    "news": [
        "📰 Intercepting HoloNet transmissions on {ticker}...",
        "📰 Scanning the HoloNet for {ticker} chatter...",
        "📰 What are they saying about {ticker} out there?",
        "📰 News sweep on {ticker}. Propaganda filtered. Signal extracted.",
    ],
    "score": [
        "🎰 Consulting the Jedha oracle...",
        "🎰 Running the numbers. The kyber crystal doesn't lie.",
        "🎰 Factor breakdown incoming. This is where it gets interesting.",
        "🎰 Checking the signal score. Conviction must be earned.",
    ],
    "web": [
        "🌐 Dispatching probe droids: '{query}'...",
        "🌐 Probe droid away. Query: '{query}'",
        "🌐 Searching the outer rim for: '{query}'...",
        "🌐 The HoloNet has answers. Asking about '{query}'...",
    ],
    "report": [
        "✍️  Filing the after-action report. Fulcrum would approve.",
        "✍️  Logging the decision. The record must be maintained.",
        "✍️  Committing to the archives. K-4SH documents everything.",
        "✍️  Writing it up. Future K-4SH will want to know.",
    ],
    "sell": [
        "⚔️  Closing {ticker}. The Mandalorian does not hesitate.",
        "⚔️  Exiting {ticker}. This is the way.",
        "⚔️  Cutting {ticker}. Discipline over attachment.",
        "⚔️  {ticker} position closed. Credits secured or losses contained.",
    ],
    "portfolio": [
        "📊 Reviewing the manifest. Every credit accounted for.",
        "📊 Checking the ledger. K-4SH trusts numbers, not feelings.",
        "📊 Portfolio state loaded. Positions confirmed.",
        "📊 Auditing the hold. What do we have, what do we owe.",
    ],
}

# Per-phase cycling iterators — each phase rotates independently
_phase_cycles: dict[str, itertools.cycle] = {
    phase: itertools.cycle(random.sample(lines, len(lines)))
    for phase, lines in _PHASE_FLAVOR.items()
}


def get_phase_flavor(phase: str, ticker: str = "", query: str = "") -> str:
    """Return the next rotating flavor line for this phase, interpolating ticker/query."""
    if phase in _phase_cycles:
        template = next(_phase_cycles[phase])
    else:
        template = f"⚙️  Processing {phase}..."
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
