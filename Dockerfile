FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/signal-bot
RUN groupadd --system app && useradd --system --gid app --home-dir /nonexistent app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY config ./config
COPY scripts ./scripts
COPY tests ./tests
COPY pyproject.toml README.md ./
RUN chown -R root:root /opt/signal-bot && chmod -R a-w /opt/signal-bot
USER app
# Validation-only default: there is no live orchestrator in this checkout.
CMD ["python", "scripts/validate_config.py"]
