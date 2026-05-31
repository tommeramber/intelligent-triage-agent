"""
FastAPI application entry point.

Exposes:
  GET  /              — Minimal browser UI (HTML form for manual testing)
  GET  /health        — Liveness probe (cheap process-alive check, no external calls)
  GET  /health/ready  — Readiness probe (validates OpenAI key and reachability)
  POST /triage        — Main endpoint: accepts error log, returns structured analysis
  GET  /docs          — Auto-generated Swagger UI (FastAPI built-in)
  GET  /redoc         — ReDoc UI (FastAPI built-in)

Why liveness and readiness are split:
  If the OpenAI API key is wrong or OpenAI has an outage, a liveness probe failure
  would cause Kubernetes to restart all pods in a loop — turning a transient outage
  into a catastrophic one. A readiness probe failure is graceful: traffic stops flowing
  to the affected pod and the problem is surfaced, but no destructive restarts happen.
"""

import logging
import time
from contextlib import asynccontextmanager

import openai as openai_lib

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import settings
from app.models import HealthResponse, ReadinessResponse, TriageRequest, TriageResponse
from app.agent import run_triage

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── FastAPI lifespan: shared OpenAI client ────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create shared OpenAI client once at startup (connection pooling, timeout set)."""
    app.state.openai_client = openai_lib.AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=30.0,  # prevents indefinite hangs if OpenAI is slow/down
    )
    logger.info("OpenAI client initialised (model=%s, timeout=30s)", settings.openai_model)
    yield
    await app.state.openai_client.close()
    logger.info("OpenAI client closed")


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Intelligent Triage Agent",
    description=(
        "First-Responder AI agent that analyses raw error logs and returns "
        "structured remediation guidance."
    ),
    version=settings.app_version,
    docs_url="/docs",        # Swagger UI
    redoc_url="/redoc",      # ReDoc UI
    lifespan=lifespan,
)

# ── Prometheus metrics ────────────────────────────────────────────────────────
# Expose Prometheus metrics at /metrics.
# Phase 2: configure Prometheus to scrape this endpoint and Grafana to visualise.
# Even without Prometheus, the endpoint is useful for manual inspection.
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/metrics", "/health", "/health/ready"],
).instrument(app).expose(app, include_in_schema=True, tags=["ops"])


# ── Middleware: request timing log ────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s → %d (%.0f ms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health():
    """
    Kubernetes liveness probe — cheap process-alive check only.

    No external calls are made. If this returns 200, the process is up.
    See /health/ready for the deep readiness probe that validates OpenAI.
    """
    return HealthResponse(status="ok", version=settings.app_version)


@app.get("/health/ready", response_model=ReadinessResponse, tags=["ops"])
async def readiness(request: Request):
    """
    Deep readiness probe — validates OpenAI API key and reachability.

    Used as the Kubernetes readinessProbe (NOT liveness — see deployment.yaml comment).
    Returns 200 if ready to serve traffic, 503 if OpenAI is unreachable or key is invalid.

    Performance note: this endpoint makes a real OpenAI API call on every probe check.
    Set the probe periodSeconds high enough (30s recommended) to avoid excessive API usage.
    """
    try:
        client = request.app.state.openai_client
        # list models is the lightest possible API call — no token cost, just auth validation
        await client.models.list()
        return ReadinessResponse(
            status="ready",
            openai_reachable=True,
            model=settings.openai_model,
        )
    except openai_lib.AuthenticationError as exc:
        logger.warning("Readiness check failed — invalid OpenAI API key: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=ReadinessResponse(
                status="not_ready",
                openai_reachable=False,
                model=settings.openai_model,
                detail="OpenAI API key is invalid or expired.",
            ).model_dump(),
        )
    except Exception as exc:
        logger.warning("Readiness check failed — OpenAI unreachable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=ReadinessResponse(
                status="not_ready",
                openai_reachable=False,
                model=settings.openai_model,
                detail=f"OpenAI unreachable: {type(exc).__name__}",
            ).model_dump(),
        )


@app.post("/triage", response_model=TriageResponse, tags=["triage"])
async def triage(body: TriageRequest, request: Request):
    """
    Main triage endpoint.

    Accepts a raw error log, runs the LLM-powered agent against the
    company troubleshooting knowledge base, and returns a structured
    JSON analysis.

    Example request body:
    ```json
    {"500": "DB connection refused to postgres:5432"}
    ```

    Responses:
      200: Successful triage analysis
      400: Input rejected by content moderation
      422: Invalid request format (wrong error code, empty description, etc.)
      500: Agent internal error
    """
    error_code = body.error_code
    description = body.description

    logger.info(
        "Received triage request: error_code=%r description=%r",
        error_code,
        description[:120],
    )

    try:
        result = await run_triage(
            error_code=error_code,
            description=description,
            client=request.app.state.openai_client,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Triage agent raised an unhandled exception")
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    return result


@app.get("/", response_class=HTMLResponse, tags=["ui"], include_in_schema=False)
async def ui():
    """
    Minimal HTML UI for manual testing in a browser.
    Not production-grade — just enough for a demo / live review.
    """
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Intelligent Triage Agent</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f1117;
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 40px 16px;
    }
    h1 { font-size: 1.6rem; font-weight: 600; margin-bottom: 4px; color: #f8fafc; }
    .subtitle { color: #94a3b8; font-size: 0.9rem; margin-bottom: 32px; }
    .card {
      background: #1e2533;
      border: 1px solid #2d3748;
      border-radius: 10px;
      padding: 28px;
      width: 100%;
      max-width: 680px;
    }
    label { display: block; font-size: 0.85rem; color: #94a3b8; margin-bottom: 6px; }
    input, textarea {
      width: 100%;
      background: #0f1117;
      border: 1px solid #2d3748;
      border-radius: 6px;
      color: #e2e8f0;
      font-size: 0.9rem;
      padding: 10px 12px;
      margin-bottom: 16px;
      resize: vertical;
    }
    input:focus, textarea:focus { outline: none; border-color: #4f6ef7; }
    button {
      width: 100%;
      background: #4f6ef7;
      color: #fff;
      font-size: 0.95rem;
      font-weight: 600;
      padding: 12px;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      transition: background 0.15s;
    }
    button:hover { background: #3b5bdb; }
    button:disabled { background: #2d3748; color: #64748b; cursor: not-allowed; }
    #result { margin-top: 24px; display: none; }
    #result h2 { font-size: 1rem; color: #94a3b8; margin-bottom: 12px; }
    pre {
      background: #0f1117;
      border: 1px solid #2d3748;
      border-radius: 6px;
      padding: 16px;
      font-size: 0.82rem;
      overflow-x: auto;
      white-space: pre-wrap;
      color: #a5f3a0;
    }
    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 600;
      margin-left: 8px;
    }
    .badge-high   { background: #1a3a2a; color: #4ade80; }
    .badge-med    { background: #3a2a1a; color: #fb923c; }
    .badge-low    { background: #3a1a1a; color: #f87171; }
    .error-msg { color: #f87171; margin-top: 12px; font-size: 0.85rem; }
    .examples { margin-top: 20px; }
    .examples p { font-size: 0.8rem; color: #64748b; margin-bottom: 6px; }
    .example-chip {
      display: inline-block;
      background: #1e2533;
      border: 1px solid #2d3748;
      border-radius: 4px;
      padding: 4px 10px;
      font-size: 0.78rem;
      color: #94a3b8;
      cursor: pointer;
      margin: 3px 3px 0 0;
      transition: border-color 0.15s;
    }
    .example-chip:hover { border-color: #4f6ef7; color: #e2e8f0; }
  </style>
</head>
<body>
  <h1>Intelligent Triage Agent</h1>
  <p class="subtitle">Paste a JSON error object — get structured remediation guidance.</p>

  <div class="card">
    <label for="log">Error JSON <span style="color:#64748b">(format: {"error_code": "message"})</span></label>
    <textarea id="log" rows="3" placeholder='e.g. {"500": "DB connection refused to postgres:5432"}'></textarea>

    <button id="submit-btn" onclick="submitTriage()">Analyse</button>

    <div class="examples">
      <p>Quick examples:</p>
      <span class="example-chip" onclick="setExample('{&quot;500&quot;: &quot;DB connection refused to postgres:5432&quot;}')">500 DB refused</span>
      <span class="example-chip" onclick="setExample('{&quot;403&quot;: &quot;Forbidden — IAM policy denied GetObject on s3://prod-bucket&quot;}')">403 Forbidden</span>
      <span class="example-chip" onclick="setExample('{&quot;503&quot;: &quot;No healthy upstream — all pods in CrashLoopBackOff&quot;}')">503 CrashLoop</span>
      <span class="example-chip" onclick="setExample('{&quot;500&quot;: &quot;OOMKilled — container exceeded 512Mi memory limit&quot;}')">OOMKilled</span>
    </div>

    <div id="result">
      <h2>Analysis</h2>
      <pre id="output"></pre>
      <div id="error-msg" class="error-msg"></div>
    </div>
  </div>

  <script>
    function setExample(json) {
      document.getElementById('log').value = json;
    }

    async function submitTriage() {
      const logValue = document.getElementById('log').value.trim();
      const btn      = document.getElementById('submit-btn');
      const result   = document.getElementById('result');
      const output   = document.getElementById('output');
      const errMsg   = document.getElementById('error-msg');

      if (!logValue) { alert('Please enter a JSON error object.'); return; }

      let parsed;
      try {
        parsed = JSON.parse(logValue);
      } catch (e) {
        alert('Invalid JSON. Example: {"500": "DB connection refused to postgres:5432"}');
        return;
      }

      btn.disabled = true;
      btn.textContent = 'Analysing…';
      result.style.display = 'none';
      errMsg.textContent = '';

      try {
        const resp = await fetch('/triage', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(parsed),
        });

        const data = await resp.json();

        if (!resp.ok) {
          errMsg.textContent = data.detail || 'Unknown error from server.';
          result.style.display = 'block';
          return;
        }

        output.textContent = JSON.stringify(data, null, 2);
        result.style.display = 'block';
      } catch (err) {
        errMsg.textContent = 'Network error: ' + err.message;
        result.style.display = 'block';
      } finally {
        btn.disabled = false;
        btn.textContent = 'Analyse';
      }
    }
  </script>
</body>
</html>
"""
