# ── Stage 1: Build ──────────────────────────────────────────
FROM python:3.11-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Security: non-root user
RUN groupadd -r vassure && useradd -r -g vassure -d /app -s /sbin/nologin vassure

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code (excluding test/dev files via .dockerignore)
COPY . .

# Create runtime directories
RUN mkdir -p outputs data && chown -R vassure:vassure /app

# Switch to non-root user
USER vassure

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

EXPOSE 8000

# Start application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--timeout-keep-alive", "30", \
     "--log-level", "info"]
