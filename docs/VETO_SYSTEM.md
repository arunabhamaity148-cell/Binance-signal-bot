# Veto System

The production order is **G1 → G2 → G4 → G5 → G6 → G8 → G9**.

G1 and G2 are hard data/feed safety blocks. G4 and G5 protect against spread and OI anomalies. G6, G8 and G9 degrade the maximum grade when funding, volatility or BTC-regime conditions are adverse. Guard exceptions fail closed as explicit blocks.

The former nine guards G3, G7, G10–G16 have been removed from runtime code and configuration because they were venue-mismatched, redundant or over-fitted for a signal-only manual-execution architecture.
