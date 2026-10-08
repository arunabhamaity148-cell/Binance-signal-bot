"""Tests for the shared minimum stop-distance filter (class E) on
StrategyBase. It refuses setups where costs would eat the stop."""
from __future__ import annotations

import pytest

from app.config import load_all
from app.core.models import CandidateSignal, ChannelName, Direction, MarketSnapshot
from app.strategies.base import StrategyBase, passes_min_stop_cost_multiple

CFG = load_all().strategy


def _snap(maker_bps=2.0):
    return MarketSnapshot(snapshot_version="v", symbol="BTCUSDT", as_of_ts_ms=1, klines={},
                          orderbook=None, taker_flow=None, derivatives=None, feed_health={},
                          price_tick=0.1, qty_step=0.001, min_qty=0.001,
                          fee_maker_bps=maker_bps, fee_taker_bps=5.0)


def _cand(entry_mid=100_000.0, stop_dist=100.0, direction=Direction.LONG):
    if direction == Direction.LONG:
        stop = entry_mid - stop_dist
        tps = [entry_mid + m * stop_dist for m in (1, 2, 3, 5)]
    else:
        stop = entry_mid + stop_dist
        tps = [entry_mid - m * stop_dist for m in (1, 2, 3, 5)]
    return CandidateSignal(symbol="BTCUSDT", direction=direction, strategy_source="S1", confidence=0.7,
                           channels=(ChannelName.LIQUIDITY,), entry_low=entry_mid, entry_high=entry_mid,
                           stop_loss=stop, tp1=tps[0], tp2=tps[1], tp3=tps[2], tp4=tps[3],
                           why_lines=("r",), meta={}, event_ts_ms=1)


def test_config_default_multiple_is_three():
    assert CFG["common"]["min_stop_cost_multiple"] == 3.0


def test_round_trip_at_tp_boundary_math():
    # entry 100_000, maker 2 bps -> round trip at TP = 2 * 100_000 * 2/1e4 = $40 ; 3x = $120
    snap = _snap()
    assert passes_min_stop_cost_multiple(_cand(stop_dist=120.0), snap, 3.0) is True       # exactly 3x
    assert passes_min_stop_cost_multiple(_cand(stop_dist=119.99), snap, 3.0) is False    # just under
    assert passes_min_stop_cost_multiple(_cand(stop_dist=200.0), snap, 3.0) is True


def test_short_direction_symmetric():
    snap = _snap()
    assert passes_min_stop_cost_multiple(_cand(stop_dist=120.0, direction=Direction.SHORT), snap, 3.0) is True
    assert passes_min_stop_cost_multiple(_cand(stop_dist=100.0, direction=Direction.SHORT), snap, 3.0) is False


def test_filter_scales_with_maker_fee():
    tight = _cand(stop_dist=100.0)
    assert passes_min_stop_cost_multiple(tight, _snap(maker_bps=1.0), 3.0) is True    # 3x$20 = $60
    assert passes_min_stop_cost_multiple(tight, _snap(maker_bps=2.0), 3.0) is False   # 3x$40 = $120


def test_zero_fee_always_passes():
    assert passes_min_stop_cost_multiple(_cand(stop_dist=1.0), _snap(maker_bps=0.0), 3.0) is True


class _Probe(StrategyBase):
    strategy_id = "PROBE"

    def evaluate(self, snapshot, news_state, config):
        return []


def test_finalize_candidates_drops_only_untradable():
    probe = _Probe()
    good, bad = _cand(stop_dist=300.0), _cand(stop_dist=10.0)
    out = probe.finalize_candidates([good, bad], _snap(), CFG)
    assert out == [good]


def test_finalize_candidates_missing_config_key_is_error_not_default():
    probe = _Probe()
    with pytest.raises(KeyError):
        probe.finalize_candidates([_cand()], _snap(), {"common": {}})


def test_every_registered_strategy_routes_through_finalize():
    """S1-S5 must all inherit and call the shared filter. Checks source
    of each registered strategy's evaluate for the call."""
    import inspect

    from app.strategies.registry import all_strategies

    for strat in all_strategies():
        src = inspect.getsource(type(strat))
        assert "finalize_candidates" in src, f"{strat.strategy_id} does not call finalize_candidates"


# ---------------------------------------------------------------------------
# Entry-zone-width filter (Decision 1 follow-up)
# ---------------------------------------------------------------------------

def _wide_zone_cand(entry_low=99_900.0, entry_high=100_300.0, stop=99_500.0):
    """mid=100100, R=|100100-99500|=600, width=400, width/R=0.667 > 0.30 -> must be blocked."""
    from app.strategies.base import passes_max_entry_zone_width

    mid = (entry_low + entry_high) / 2
    r = abs(mid - stop)
    tps = [mid + m * r for m in (1, 2, 3, 5)]
    c = CandidateSignal(symbol="BTCUSDT", direction=Direction.LONG, strategy_source="S1", confidence=0.7,
                        channels=(ChannelName.LIQUIDITY,), entry_low=entry_low, entry_high=entry_high,
                        stop_loss=stop, tp1=tps[0], tp2=tps[1], tp3=tps[2], tp4=tps[3],
                        why_lines=("r",), meta={}, event_ts_ms=1)
    assert (entry_high - entry_low) / r > 0.30, "fixture must actually violate the 0.30x width cap"
    return c


def test_config_default_width_multiple_is_030():
    assert CFG["common"]["max_entry_zone_width_r_multiple"] == 0.30


def test_wide_entry_zone_blocked_by_finalize_candidates():
    """Explicit regression test (required): a candidate whose entry
    zone is wide relative to R must be dropped by
    StrategyBase.finalize_candidates, not just by the standalone
    predicate function."""
    probe = _Probe()
    wide = _wide_zone_cand()
    narrow = _cand(stop_dist=300.0)  # entry_low==entry_high==mid -> width 0, always passes
    out = probe.finalize_candidates([wide, narrow], _snap(), CFG)
    assert wide not in out
    assert narrow in out
    assert out == [narrow]


def test_entry_zone_width_boundary_exact_030_passes():
    from app.strategies.base import passes_max_entry_zone_width

    mid, stop = 100_000.0, 99_000.0  # R = 1000, boundary width = 300
    entry_low, entry_high = mid - 150.0, mid + 150.0  # width = 300 = 0.30 * R exactly
    c = CandidateSignal(symbol="BTCUSDT", direction=Direction.LONG, strategy_source="S1", confidence=0.7,
                        channels=(ChannelName.LIQUIDITY,), entry_low=entry_low, entry_high=entry_high,
                        stop_loss=stop, tp1=101000, tp2=102000, tp3=103000, tp4=105000,
                        why_lines=("r",), meta={}, event_ts_ms=1)
    assert passes_max_entry_zone_width(c, 0.30) is True


def test_entry_zone_width_just_over_030_fails():
    from app.strategies.base import passes_max_entry_zone_width

    mid, stop = 100_000.0, 99_000.0
    entry_low, entry_high = mid - 150.1, mid + 150.1  # width just over 300
    c = CandidateSignal(symbol="BTCUSDT", direction=Direction.LONG, strategy_source="S1", confidence=0.7,
                        channels=(ChannelName.LIQUIDITY,), entry_low=entry_low, entry_high=entry_high,
                        stop_loss=stop, tp1=101000, tp2=102000, tp3=103000, tp4=105000,
                        why_lines=("r",), meta={}, event_ts_ms=1)
    assert passes_max_entry_zone_width(c, 0.30) is False


def test_zero_r_fails_width_check_rather_than_dividing_by_zero():
    from app.strategies.base import passes_max_entry_zone_width

    c = CandidateSignal(symbol="BTCUSDT", direction=Direction.LONG, strategy_source="S1", confidence=0.7,
                        channels=(ChannelName.LIQUIDITY,), entry_low=100.0, entry_high=100.0,
                        stop_loss=100.0, tp1=101, tp2=102, tp3=103, tp4=105,
                        why_lines=("r",), meta={}, event_ts_ms=1)
    assert passes_max_entry_zone_width(c, 0.30) is False


def test_finalize_candidates_missing_width_config_key_is_error():
    probe = _Probe()
    with pytest.raises(KeyError):
        probe.finalize_candidates([_cand()], _snap(), {"common": {"min_stop_cost_multiple": 3.0}})
