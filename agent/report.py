# agent/report.py
# Renders the K-4SH cycle report — ASCII box with portfolio, P&L, Mac Mini progress.

from datetime import datetime
from zoneinfo import ZoneInfo

from agent.flavor import get_decision_flavor

_WIDTH = 64   # inner width (between ║ chars)
ET = ZoneInfo("America/New_York")


def _row(text: str = "") -> str:
    """Format a single box row, padding to full width."""
    return f"║  {text:<{_WIDTH}}║"


def _divider() -> str:
    return "╠" + "═" * (_WIDTH + 2) + "╣"


def _top() -> str:
    return "╔" + "═" * (_WIDTH + 2) + "╗"


def _bottom() -> str:
    return "╚" + "═" * (_WIDTH + 2) + "╝"


def _progress_bar(realized: float, target: float = 800.0, width: int = 10) -> str:
    pct = min(1.0, max(0.0, realized / target)) if target > 0 else 0.0
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
    lines.append(_row(header.center(_WIDTH)))

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
        connector = "├─"
        pl = pos["unrealized_pl"]
        plpc = pos["unrealized_plpc"]
        pl_str = f"+${pl:,.2f}" if pl >= 0 else f"-${abs(pl):,.2f}"
        plpc_str = f"+{plpc:.2f}%" if plpc >= 0 else f"{plpc:.2f}%"
        lines.append(_row(
            f"  {connector} {pos['ticker']:<6} {pos['qty']:.0f} shares"
            f"  ${pos['current_price']:.2f}"
            f"   {pl_str}  ({plpc_str})"
        ))
    cash = portfolio.get("cash", 0.0)
    lines.append(_row(f"  └─ Cash   ${cash:,.2f}"))
    lines.append(_row())

    total_val = portfolio.get("portfolio_value", 0.0)
    unreal = portfolio.get("total_unrealized_pl", 0.0)
    unreal_str = f"+${unreal:,.2f}" if unreal >= 0 else f"-${abs(unreal):,.2f}"
    real_str = f"+${realized_pnl:,.2f}" if realized_pnl >= 0 else f"-${abs(realized_pnl):,.2f}"

    lines.append(_row(f"Total Value:      ${total_val:>12,.2f}"))
    lines.append(_row(f"Unrealized P&L:   {unreal_str:<16}  (still at risk)"))
    lines.append(_row(f"Realized P&L:     {real_str:<16}  (locked in)"))

    lines.append(_divider())

    # Mac Mini progress bar
    bar_str = _progress_bar(realized_pnl, 800.0)
    remaining = max(0.0, 800.0 - realized_pnl)
    lines.append(_row(f"[MAC] Upgrade:  {realized_pnl:.2f} / 800 credits  {bar_str}"))
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
