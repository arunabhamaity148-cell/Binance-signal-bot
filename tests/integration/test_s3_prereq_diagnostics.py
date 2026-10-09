from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.config import load_all
from app.core.models import TimestampedValue
from app.strategies.s3_funding_crowding import S3FundingCrowding
from tests.strategies.test_s3_funding_crowding import (
    _empty_news,
    _flat,
    _swing_low_15m_bars,
    build_realistic_s3_short_reversal_snapshot,
)


CFG = load_all().strategy


def _funding_z_below_min(snapshot):
    values = [0.0001 + ((index % 5) - 2) * 0.00001 for index in range(99)] + [0.0001]
    history = [replace(point, value=value) for point, value in zip(snapshot.derivatives.funding_rate_history, values)]
    return replace(snapshot, derivatives=replace(snapshot.derivatives, funding_rate_history=history))


def _diag_record(caplog, reason):
    for record in caplog.records:
        if not record.getMessage().startswith("diag_strategy |"):
            continue
        payload = json.loads(record.getMessage().split(" | ", 1)[1])
        if payload["reason"] == reason:
            return payload
    raise AssertionError(f"no diagnostic with reason {reason!r}")


@pytest.mark.parametrize(
    ("reason", "mutate"),
    [
        (
            "s3_prereq_funding_z_below_min",
            _funding_z_below_min,
        ),
        (
            "s3_prereq_oi_rank_below_min",
            lambda snapshot: replace(
                snapshot,
                derivatives=replace(
                    snapshot.derivatives,
                    open_interest_history_5m=[
                        *snapshot.derivatives.open_interest_history_5m[:-1],
                        replace(snapshot.derivatives.open_interest_history_5m[-1], value=1_000_000),
                    ],
                ),
            ),
        ),
        (
            "s3_prereq_ls_ratio_not_extreme",
            lambda snapshot: replace(
                snapshot,
                derivatives=replace(
                    snapshot.derivatives,
                    long_short_account_ratio_history=[
                        *snapshot.derivatives.long_short_account_ratio_history[:-1],
                        replace(snapshot.derivatives.long_short_account_ratio_history[-1], value=1.2),
                    ],
                ),
            ),
        ),
        (
            "s3_prereq_price_disp_below_min",
            lambda snapshot: replace(
                snapshot,
                klines={
                    **snapshot.klines,
                    "5m": replace(
                        snapshot.klines["5m"],
                        bars=[
                            *snapshot.klines["5m"].bars[:-1],
                            replace(
                                snapshot.klines["5m"].bars[-1],
                                close=snapshot.klines["5m"].bars[-97].close,
                                high=max(
                                    snapshot.klines["5m"].bars[-1].open,
                                    snapshot.klines["5m"].bars[-97].close,
                                ) + 1,
                                low=min(
                                    snapshot.klines["5m"].bars[-1].open,
                                    snapshot.klines["5m"].bars[-97].close,
                                ) - 1,
                            ),
                        ],
                    ),
                },
            ),
        ),
    ],
)
def test_s3_value_prerequisite_diagnostics(caplog, reason, mutate):
    snapshot = mutate(build_realistic_s3_short_reversal_snapshot())
    with caplog.at_level("INFO"):
        S3FundingCrowding().evaluate(snapshot, _empty_news(snapshot.as_of_ts_ms), CFG)

    payload = _diag_record(caplog, reason)
    values = payload["values"]
    if reason == "s3_prereq_funding_z_below_min":
        assert values["min_required"] == 2.0
        assert "funding_z" in values
    elif reason == "s3_prereq_oi_rank_below_min":
        assert values["min_required"] == 0.85
        assert "oi_rank" in values
    elif reason == "s3_prereq_ls_ratio_not_extreme":
        assert values["high"] == 0.85 and values["low"] == 0.15
        assert "ls_ratio_pct" in values
    else:
        assert values["min_required"] == 2.5
        assert "price_disp_atr" in values


def test_s3_structure_break_diagnostic(caplog):
    snapshot = build_realistic_s3_short_reversal_snapshot()
    bars = _flat(40, 900_000, snapshot.klines["15m"].bars[-1].close)
    snapshot = replace(snapshot, klines={**snapshot.klines, "15m": replace(snapshot.klines["15m"], bars=bars)})
    with caplog.at_level("INFO"):
        S3FundingCrowding().evaluate(snapshot, _empty_news(snapshot.as_of_ts_ms), CFG)
    assert _diag_record(caplog, "s3_prereq_no_structure_break")["decision"] == "rejected"


def test_s3_taker_flip_diagnostic(caplog):
    snapshot = build_realistic_s3_short_reversal_snapshot()
    flow = replace(snapshot.taker_flow, taker_buy_base_last_bars=[8, 8, 8])
    snapshot = replace(snapshot, taker_flow=flow)
    with caplog.at_level("INFO"):
        S3FundingCrowding().evaluate(snapshot, _empty_news(snapshot.as_of_ts_ms), CFG)
    assert _diag_record(caplog, "s3_prereq_no_taker_flip")["decision"] == "rejected"
