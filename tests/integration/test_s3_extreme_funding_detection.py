"""Regression verification for S3 funding extremes.

The fixture is the trailing 96 Binance USDⓈ-M BTCUSDT funding-rate
observations from 2021-05-26 16:00 UTC through 2021-06-27 08:00 UTC,
retrieved from the public endpoint:
https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&startTime=1619827200000&endTime=1625097600000&limit=1000

The final observation was -0.00052905 on 2021-06-27 08:00 UTC, during
the May-June 2021 crypto liquidation/cascade period. The values are kept
locally so this test remains deterministic and does not depend on live API
availability.
"""
from __future__ import annotations

from app.core.math import zscore


# Binance public API response, trailing 96 observations ending at the
# 2021-06-27 08:00 UTC BTCUSDT funding extreme (-0.00052905).
HISTORICAL_BTCUSDT_FUNDING_RATES = (
    0.0001, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001,
    0.0001, 0.00010375, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001,
    0.0001, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001,
    0.0001, 0.0001, 0.00020566, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001,
    0.0001, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001, 1.015e-05,
    0.0001, -5.397e-05, -0.00014842, -0.00016793, 6.027e-05, 0.0001, 0.0001, 2.612e-05,
    4.776e-05, -7.78e-05, 0.0001, -8.14e-06, 9.64e-05, 2.852e-05, -6.559e-05, 0.0001,
    0.0001, -3.144e-05, 8.002e-05, 0.0001, 0.0001, 0.0001, 0.0001, 0.0001,
    0.0001, 0.0001, 0.0001, 5.342e-05, 3.45e-06, 0.0001, -5.205e-05, -9.441e-05,
    7.475e-05, 0.0001, 0.0001, -0.00018437, -1.617e-05, 0.0001, -0.00019854, 7.947e-05,
    0.0001, -0.00032806, 6.7e-07, -0.00013693, -0.00019895, -7.785e-05, 0.0001, -0.00018863,
    2.372e-05, 0.0001, 0.0001, -0.00011029, -0.00013121, -0.00010371, -0.00023578, -0.00052905,
)


def test_s3_detects_known_historical_extreme_funding():
    assert len(HISTORICAL_BTCUSDT_FUNDING_RATES) == 96

    funding_z = zscore(
        HISTORICAL_BTCUSDT_FUNDING_RATES[-1],
        list(HISTORICAL_BTCUSDT_FUNDING_RATES),
    )

    assert funding_z < 0
    assert abs(funding_z) >= 2.0
