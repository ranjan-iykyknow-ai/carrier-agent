FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependencies first so code edits don't bust this layer; the venv lives outside
# /app so the dev bind-mount cannot clobber it.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

COPY . .

EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "--worker-class", "gthread", "--workers", "2", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:8000"]
