# Final Release Report

## Release boundary

This is a source release of a signal-only Binance public-data advisory bot. It never places exchange orders. Delta conversion is public metadata conversion for manual operator reference.

## Active pipeline

S1–S5 → consensus → G1/G2/G4/G5/G6/G8/G9 → advisory risk → Telegram signal.

## Removed surface

G3, G7, G10, G11, G12, G13, G14, G15 and G16 were removed rather than left disabled. All test files remain and now verify the retirement boundary.

## Sign-off

Run `python scripts/validate_config.py`, `pytest -q`, and all three scanners. Treat a 72-hour paper soak with healthy feeds and no unexplained critical starvation alert as a separate operational gate.
