# ── Stage 1: build / dependency layer ────────────────────────────────────────
# Using a slim Python image keeps the final image small (~120 MB vs ~900 MB for full).
# We pin the exact tag so builds are reproducible.
FROM python:3.12-slim AS builder

WORKDIR /build

# Install only production dependencies — test deps stay out of the image.
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user — best practice for container security.
# Kubernetes PSP / SCC "restricted" profile requires UID != 0.
RUN groupadd --gid 1001 appgroup && \
    useradd  --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app/   ./app/
COPY data/  ./data/

# Switch to non-root user BEFORE the CMD
USER appuser

# Expose the port the app listens on (informational — actual binding is runtime config)
EXPOSE 8080

# Health check so Docker / Kubernetes knows if the container is alive.
# --start-period gives the app time to initialise before the first check.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# Uvicorn in production mode:
#   --workers 1        — single worker (Kubernetes handles horizontal scaling via replicas)
#   --proxy-headers    — trust X-Forwarded-For from the Ingress / load balancer
#   --no-access-log    — access logging is done by the FastAPI middleware instead
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--workers", "1", \
     "--proxy-headers", \
     "--no-access-log"]
