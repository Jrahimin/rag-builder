# syntax=docker/dockerfile:1
# ===========================================================================
# AI Platform Engine - backend image (multi-stage).
# Build context is the repository ROOT so both backend/ and alembic.ini are
# available, keeping migration paths identical to local development.
# ===========================================================================
ARG PYTHON_VERSION=3.12

# --- Base: shared runtime settings -----------------------------------------
FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# --- Builder: install dependencies into an isolated virtualenv -------------
FROM base AS builder
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements/ requirements/
COPY requirements.txt ./
ARG INSTALL_DEV=false
RUN pip install --upgrade pip \
    && if [ "$INSTALL_DEV" = "true" ]; then \
         pip install -r requirements/dev.txt; \
       else \
         pip install -r requirements/prod.txt; \
       fi

# --- Runtime: minimal image with only what is needed to run ----------------
FROM base AS runtime
RUN groupadd --system ape && useradd --system --gid ape --create-home ape
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/backend
COPY backend/ backend/
COPY alembic.ini ./
RUN chown -R ape:ape /app
USER ape

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
