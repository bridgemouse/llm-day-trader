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
