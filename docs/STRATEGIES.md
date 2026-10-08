# Strategies (as built)

`app/strategies/registry.py` instantiates S1–S5: liquidity sweep, volatility compression, funding crowding, OI trend, and OI regime. Each is evaluated via its module's `evaluate` implementation against a `MarketSnapshot` and `NewsState`, producing zero or more candidate signals. The registry is shared with the backtest engine.

The implementations are fail-closed when required inputs are absent: S1 needs taker-flow information and S2–S5 need derivatives inputs as documented in the backtest engine. A replay with OHLCV alone can therefore legitimately generate no candidates. Parameters reside in YAML; do not treat them as empirically calibrated.

No strategy profitability or live performance claim is made. See uncertainty notes, especially threshold calibration and interaction with cost floors.
