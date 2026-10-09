# Strategies

The registry contains five fail-closed strategies:

- **S1** liquidity sweep and reclaim;
- **S2** volatility compression;
- **S3** funding crowding and extreme funding;
- **S4** open-interest trend;
- **S5** open-interest regime.

Each strategy returns candidates only when required snapshot history and derivatives/flow inputs are present. Candidates pass through consensus, the seven active guards and advisory risk before signal construction.
