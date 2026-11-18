# One image for the API, the workers, the migrate step and the simulator. Each container runs
# a different entry point with its own database role (engineering baseline 3).
ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}" \
    PW_REPO_ROOT=/app

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/simulator/pyproject.toml services/simulator/pyproject.toml
COPY services/api/src services/api/src
COPY services/simulator/src services/simulator/src
RUN uv sync --frozen --no-dev --all-packages

COPY db db
COPY data/reference data/reference
COPY services/simulator/golden services/simulator/golden

RUN useradd --system --uid 10001 palletwise
USER palletwise
