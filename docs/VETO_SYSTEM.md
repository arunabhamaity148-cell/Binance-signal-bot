# Veto system (as built)

`app.risk.veto_engine.run_veto_engine` runs against a market snapshot, news state, optional candidate, config, and symbol tier. `app.bot.SignalBot.evaluate_symbol` invokes it for consensus-graded candidates before risk checks, signal persistence, and Telegram outbox delivery. A blocked candidate is recorded in the candidate audit, but it is not inserted as a final signal or queued for delivery. A `PASS` result may carry a restrictive grade cap, which the bot applies before risk evaluation.

## Guard order

The order is short-circuiting for hard blocks:

`G1 → G2 → G10 → G3 → G4 → G5 → G13 → G14 → G15 → G6 → G7 → G8 → G9 → G11 → G12`

- **G1** data integrity
- **G2** feed health
- **G10** order-book instability
- **G3** depth collapse
- **G4** spread explosion
- **G5** OI anomaly
- **G13** OI divergence
- **G14** OI stagnation (degrade rather than hard block)
- **G15** OI percentile extreme
- **G6** funding extreme (degrade-cap path)
- **G7** news shock
- **G8** volatility flash (configured degrade behavior)
- **G9** BTC regime (degrade-cap path)
- **G11** execution quality, with prior guard results
- **G12** signal self-consistency

Guard implementations are in `app/risk/veto.py`. A guard exception is converted by `_safe_call` into a critical `BLOCK`, not a silent pass. Standalone guard and integration tests cover selected paths, not every real-market condition.

## Runtime context limits

The current bot invokes the engine with `funding_z=None` and `btc_trend_direction=None`. Guards requiring those optional context values do not receive computed values from this path. Other snapshot/news inputs are passed through. The implementation has not been live-validated against real exchange feeds, news events, or end-to-end notifications. Passing tests are offline evidence only and do not establish threshold calibration.
