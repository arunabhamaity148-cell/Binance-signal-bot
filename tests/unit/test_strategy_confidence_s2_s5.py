from types import SimpleNamespace

import pytest

from app.config import load_all
from app.core.math import OHLC
from app.strategies.s2_volatility_compression import S2VolatilityCompression
from app.strategies.s3_funding_crowding import S3FundingCrowding
from app.strategies.s4_oi_trend import S4OiTrend
from app.strategies.s5_oi_regime import S5OiRegime

CFG = load_all().strategy
SNAP = SimpleNamespace(symbol="BTCUSDT", snapshot_version="synthetic", fee_maker_bps=2.0,
                       fee_taker_bps=5.0, orderbook=None)
BAR = OHLC(100, 102, 99, 101, 200, 300_000)

@pytest.mark.parametrize("ratio,minimum", [(1.6, .55), (3.0, .75), (5.0, .95)])
def test_s2_confidence_weak_medium_strong(ratio, minimum):
    c = S2VolatilityCompression()._evaluate_long(
        snapshot=SNAP, cfg=CFG["s2_volatility_compression"], atr14=1.0,
        range_high=100.0, current=BAR, body=2.0, volume_confirm_ratio=ratio,
        oi_delta_pct=2.0, range_width_atr=2.0, atr_percentile=.1)
    assert c is not None and c.confidence >= minimum

@pytest.mark.parametrize("z,minimum", [(2.1, .55), (3.0, .65), (5.0, .85)])
def test_s3_confidence_weak_medium_strong(z, minimum):
    bars = [OHLC(100, 100, 99, 99.5, 100, 1), OHLC(100, 102, 99, 101, 2, 2)]
    c = S3FundingCrowding()._evaluate_long_reversal(
        snapshot=SNAP, cfg=CFG["s3_funding_crowding"], atr14=1.0,
        bars_15m=bars, highs_idx=[0], taker_buy_ratio=.6, funding_z=z,
        oi_percentile=.9, ls_ratio_pct=.1, price_disp_atr=3.0)
    assert c is not None and c.confidence >= minimum

@pytest.mark.parametrize("slope,minimum", [(.6, .55), (1.5, .64), (3.0, .79)])
def test_s4_confidence_weak_medium_strong(slope, minimum):
    c = S4OiTrend()._build_candidate(
        snapshot=SNAP, cfg=CFG["s4_oi_trend"], direction=__import__('app.core.models', fromlist=['Direction']).Direction.LONG,
        ema_fast=100.0, atr14_1h=1.0, oi_delta_pct=1.0,
        ema_slope_fast=slope, current_bar=BAR)
    assert c is not None and c.confidence >= minimum

@pytest.mark.parametrize("delta,minimum", [(.02, .59), (.05, .74), (.10, .99)])
def test_s5_confidence_weak_medium_strong(delta, minimum):
    c = S5OiRegime()._build_candidate(
        snapshot=SNAP, cfg=CFG["s5_oi_regime"], direction=__import__('app.core.models', fromlist=['Direction']).Direction.LONG,
        entry=100.0, stop_loss=99.0, atr14=1.0, quadrant="price_up_oi_up",
        oi_pct_now=.9, oi_pct_earlier=.1, oi_delta_5m=delta,
        oi_delta_15m=delta * 2, oi_delta_1h=delta * 3, taker_buy_ratio=.65,
        funding_z_value=0.0, entry_mode="retest", bars_5m=[BAR])
    assert c is not None and c.confidence >= minimum
