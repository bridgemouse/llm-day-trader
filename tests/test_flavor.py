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
