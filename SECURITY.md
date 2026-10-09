# Security Policy

The application reads public Binance market data and emits advisory signals. It must not access private exchange account state or place orders.

Never commit exchange credentials, Telegram tokens, `.env` files, databases, logs, VPS credentials or PATs. Before deployment run all three scanners:

```bash
python scripts/scan_signal_only.py
python scripts/scan_no_placeholders.py
python scripts/scan_forbidden_calls.py
```

Scanners are safeguards, not a profitability or reliability certification. Report exposed secrets privately and rotate them immediately.
