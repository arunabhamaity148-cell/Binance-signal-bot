import pytest
from app.config import load_all
from app.strategies.s2_volatility_compression import S2VolatilityCompression
from app.strategies.s3_funding_crowding import S3FundingCrowding
from app.strategies.s4_oi_trend import S4OiTrend
from app.strategies.s5_oi_regime import S5OiRegime
import app.monitoring.diagnostics as diagnostics
import logging
from dataclasses import replace

@pytest.mark.asyncio
async def test_strategies_emit_stale_data_rejection_telemetry(monkeypatch, caplog):
    diagnostics.configure("summary")
    cfg = load_all()

    # 1. Test S2 OI Stale and Zero
    from tests.strategies.test_s2_volatility_compression import build_realistic_s2_long_snapshot
    s2_snap = build_realistic_s2_long_snapshot()
    stale_oi = replace(s2_snap.derivatives.open_interest_history_5m[-1], event_ts_ms=100, received_ts_ms=100)
    s2_snap.derivatives.open_interest_history_5m[-1] = stale_oi

    with caplog.at_level(logging.INFO):
        S2VolatilityCompression().evaluate(s2_snap, None, cfg.strategy)

    s2_stale = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S2"' in r.getMessage() and '"reason": "oi_data_stale"' in r.getMessage()]
    assert len(s2_stale) == 1
    caplog.clear()

    s2_snap = build_realistic_s2_long_snapshot()
    zero_oi = replace(s2_snap.derivatives.open_interest_history_5m[-2], value=0.0)
    s2_snap.derivatives.open_interest_history_5m[-2] = zero_oi

    with caplog.at_level(logging.INFO):
        S2VolatilityCompression().evaluate(s2_snap, None, cfg.strategy)

    s2_zero = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S2"' in r.getMessage() and '"reason": "oi_prior_zero"' in r.getMessage()]
    assert len(s2_zero) == 1
    caplog.clear()

    # 2. Test S3 Funding Stale
    from tests.strategies.test_s3_funding_crowding import build_realistic_s3_long_reversal_snapshot
    s3_snap = build_realistic_s3_long_reversal_snapshot()
    stale_funding = replace(s3_snap.derivatives.funding_rate_history[-1], event_ts_ms=100, received_ts_ms=100)
    s3_snap.derivatives.funding_rate_history[-1] = stale_funding

    with caplog.at_level(logging.INFO):
        S3FundingCrowding().evaluate(s3_snap, None, cfg.strategy)

    s3_funding_stale = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S3"' in r.getMessage() and '"reason": "funding_stale"' in r.getMessage()]
    assert len(s3_funding_stale) == 1
    caplog.clear()

    # 3. Test S3 OI Stale
    s3_snap = build_realistic_s3_long_reversal_snapshot()
    stale_oi = replace(s3_snap.derivatives.open_interest_history_5m[-1], event_ts_ms=100, received_ts_ms=100)
    s3_snap.derivatives.open_interest_history_5m[-1] = stale_oi

    with caplog.at_level(logging.INFO):
        S3FundingCrowding().evaluate(s3_snap, None, cfg.strategy)

    s3_oi_stale = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S3"' in r.getMessage() and '"reason": "oi_stale"' in r.getMessage()]
    assert len(s3_oi_stale) == 1
    caplog.clear()

    # 4. Test S3 Ratios Stale
    s3_snap = build_realistic_s3_long_reversal_snapshot()
    stale_ratio = replace(s3_snap.derivatives.long_short_account_ratio_history[-1], event_ts_ms=100, received_ts_ms=100)
    s3_snap.derivatives.long_short_account_ratio_history[-1] = stale_ratio

    with caplog.at_level(logging.INFO):
        S3FundingCrowding().evaluate(s3_snap, None, cfg.strategy)

    s3_ratios_stale = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S3"' in r.getMessage() and '"reason": "ls_ratio_stale"' in r.getMessage()]
    assert len(s3_ratios_stale) == 1
    caplog.clear()

    # 5. Test S4 OI Stale
    from tests.integration.test_s4_can_trigger import _hh_hl_bars
    from tests.strategies.test_s4_oi_trend import _build_snapshot, _staircase_uptrend_bars
    from app.core.math import OHLC, ema_series
    from app.core.models import DerivativesState, TimestampedValue
    bars_5m = _staircase_uptrend_bars(70, 100.0, 0.5, 300_000)
    bars_1h = _staircase_uptrend_bars(80, 100.0, 1.5, 3_600_000)
    bars_4h = _staircase_uptrend_bars(60, 100.0, 2.0, 14_400_000)
    bars_15m = _hh_hl_bars()
    ema_fast = ema_series([bar.close for bar in bars_1h], 21)[-1]
    for j in range(3):
        old = bars_5m[-3 + j]
        close = ema_fast - 0.25 + j * 0.2
        bars_5m[-3 + j] = OHLC(close - 0.05, close + 0.35, close - 0.35, close, 100, old.close_time_ms)
    as_of = bars_5m[-1].close_time_ms + 1
    oi = [
        TimestampedValue(1_000_000 + i * 1_000, as_of - (49 - i) * 300_000, as_of - (49 - i) * 300_000)
        for i in range(49)
    ]
    oi.append(TimestampedValue(1_060_000, 100, 100)) # stale
    derivatives = DerivativesState("BTCUSDT", [], oi, [], [], [], [], [], None)
    s4_snap = _build_snapshot(
        bars_5m=bars_5m, bars_15m=bars_15m, bars_1h=bars_1h, bars_4h=bars_4h,
        derivatives=derivatives, as_of_ts_ms=as_of,
    )

    with caplog.at_level(logging.INFO):
        S4OiTrend().evaluate(s4_snap, None, cfg.strategy)

    s4_stale = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S4"' in r.getMessage() and ('"reason": "oi_data_stale"' in r.getMessage() or '"reason": "data_stale_or_unavailable"' in r.getMessage())]
    assert len(s4_stale) >= 1
    caplog.clear()

    # 6. Test S5 OI Stale
    from tests.strategies.test_s5_oi_regime import build_realistic_s5_long_snapshot
    s5_snap = build_realistic_s5_long_snapshot()
    stale_oi = replace(s5_snap.derivatives.open_interest_history_5m[-1], event_ts_ms=100, received_ts_ms=100)
    s5_snap.derivatives.open_interest_history_5m[-1] = stale_oi

    with caplog.at_level(logging.INFO):
        S5OiRegime().evaluate(s5_snap, None, cfg.strategy)

    s5_stale = [r for r in caplog.records if "diag_strategy" in r.getMessage() and '"strategy": "S5"' in r.getMessage() and ('"reason": "oi_data_stale"' in r.getMessage() or '"reason": "data_stale_or_unavailable"' in r.getMessage())]
    assert len(s5_stale) >= 1
    caplog.clear()
