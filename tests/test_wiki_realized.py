# tests/test_wiki_realized.py
import shutil
import tempfile

import pytest


@pytest.fixture
def tmp_wiki(monkeypatch):
    """Redirect WIKI_DIR to a temp directory with minimal structure."""
    tmp = tempfile.mkdtemp()
    import os
    src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "wiki")
    shutil.copytree(src, tmp, dirs_exist_ok=True)

    import alpaca_mcp.wiki as wiki_mod
    from pathlib import Path
    monkeypatch.setattr(wiki_mod, "WIKI_DIR", Path(tmp))
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
