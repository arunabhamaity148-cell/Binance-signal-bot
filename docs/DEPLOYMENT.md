# Deployment

Install Python 3.11+, create `venv`, install `requirements.txt`, export only the required advisory and Telegram variables, then run config validation, pytest and all scanners. Start one `python -m app.main` process under the VPS process manager.

Keep logs and the SQLite database outside Git, configure restart/retention, and confirm the active commit after every update. Production sign-off requires the paper-soak gate in [PAPER_SOAK.md](PAPER_SOAK.md).
