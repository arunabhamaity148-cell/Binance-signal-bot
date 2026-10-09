# Architecture

```text
Public Binance REST/WebSocket
          ↓
Immutable snapshot
          ↓
S1–S5 strategy registry
          ↓
Consensus and grade
          ↓
G1 → G2 → G4 → G5 → G6 → G8 → G9
          ↓
Advisory risk and signal model
          ↓
SQLite / Telegram
```

The runtime has no private exchange client and no order execution path. News is optional enrichment. Delta data is public conversion metadata only.
