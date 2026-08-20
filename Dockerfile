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
# Pre-download the standalone Tailwind binary into the image layer.
RUN tailwindcss --help >/dev/null 2>&1 || true

COPY . .

# Bake the static manifest into the image; the key is build-only and secretless.
# The Tailwind source (static/src) is build input, never a served asset.
RUN SECRET_KEY=build-time-collectstatic DEBUG=false \
    python manage.py collectstatic --noinput --ignore "src"

EXPOSE 8000
# Shell form so Railway's injected $PORT is honored; 8000 remains the default.
CMD exec gunicorn config.wsgi --worker-class gthread --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:${PORT:-8000}
