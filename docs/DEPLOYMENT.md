# Deployment (as built)

The repo includes a non-root, read-only-config container recipe intended only to validate configuration. It is not an application deployment: no `app/main.py` or `app/bot.py` exists, and no live orchestration, health endpoint, or operational supervisor was verified. Compose isolates this validation command from networking and drops capabilities.

For local development: Python 3.11+, install `requirements.txt`, validate config, run tests and scanners. Keep secrets out of source control; no exchange trading credentials belong in this project. Before any future runtime deployment, require a separately reviewed orchestration milestone and operator validation.
